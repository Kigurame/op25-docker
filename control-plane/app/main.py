"""op25-docker control plane - FastAPI application."""
import json
import os
import subprocess
import time
import urllib.parse

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import auth, config_store, op25_ctl, sdr, supervisor_ctl
from .telemetry import telemetry as TELEM

app = FastAPI(title="op25-docker control plane", docs_url=None, redoc_url=None)

CONF_DIR = os.environ.get("OP25_CONF_DIR", "/opt/op25/conf")
RENDER = ["/opt/op25/venv/bin/python", "/opt/op25/render_configs.py",
          "--conf-dir", CONF_DIR, "--tpl-dir", "/opt/op25/defaults",
          "--out-dir", "/etc/op25", "--supervisor-conf", "/etc/op25/supervisord.conf"]
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")


# ---------------------------------------------------------------- auth helpers

def require_user(request: Request, role=None):
    token = request.cookies.get("op25_session")
    payload = auth.verify_token(token)
    if not payload:
        raise PermissionError("not authenticated")
    if role and payload.get("r") != role:
        raise PermissionError("insufficient role")
    return payload


def set_session_cookie(response: Response, payload):
    response.set_cookie("op25_session", auth.make_token(payload), httponly=True, samesite="lax", max_age=auth.TOKEN_TTL)


# --------------------------------------------------------------------- auth api

@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    user = auth.authenticate(body.get("username", ""), body.get("password", ""))
    if not user:
        return JSONResponse({"error": "invalid username or password"}, status_code=401)
    resp = JSONResponse({"ok": True, "user": user["username"], "name": user.get("name", user["username"]),
                         "role": user.get("role", "viewer")})
    set_session_cookie(resp, {"u": user["username"], "n": user.get("name", user["username"]),
                              "r": user.get("role", "viewer")})
    return resp


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("op25_session")
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    try:
        u = require_user(request)
        return {"user": u["u"], "name": u["n"], "role": u["r"]}
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)


@app.post("/api/users")
async def api_add_user(request: Request):
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    body = await request.json()
    un = str(body.get("username", "")).strip()
    pw = str(body.get("password", ""))
    role = str(body.get("role", "viewer"))
    if not un or len(pw) < 6:
        return JSONResponse({"error": "username required, password min 6 characters"}, status_code=400)
    try:
        users = config_store.read_json("users.json")
    except (OSError, ValueError):
        return JSONResponse({"error": "cannot read users.json"}, status_code=500)
    if any(u.get("username") == un for u in users.get("users", [])):
        return JSONResponse({"error": "user already exists"}, status_code=409)
    users.setdefault("users", []).append({
        "username": un,
        "name": body.get("name", un),
        "role": role if role in ("admin", "viewer") else "viewer",
        "password_hash": auth.hash_password(pw),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    config_store.write_json("users.json", users)
    return {"ok": True}


@app.post("/api/change-password")
async def api_change_password(request: Request):
    try:
        u = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    body = await request.json()
    old = body.get("old_password", "")
    new = body.get("new_password", "")
    if len(new) < 6:
        return JSONResponse({"error": "new password must be at least 6 characters"}, status_code=400)
    try:
        users = config_store.read_json("users.json")
    except (OSError, ValueError):
        return JSONResponse({"error": "cannot read users.json"}, status_code=500)
    for entry in users.get("users", []):
        if entry.get("username") == u["u"]:
            if not auth.verify_password(old, entry.get("password_hash", "")):
                return JSONResponse({"error": "current password is incorrect"}, status_code=400)
            entry["password_hash"] = auth.hash_password(new)
            config_store.write_json("users.json", users)
            return {"ok": True}
    return JSONResponse({"error": "user not found"}, status_code=404)


# ------------------------------------------------------------------ config api

@app.get("/api/config")
async def api_config(request: Request):
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    try:
        data = config_store.read_all()
        meta = {}
        for name in config_store.CONF_FILES:
            p = os.path.join(CONF_DIR, name)
            try:
                meta[name] = int(os.path.getmtime(p))
            except OSError:
                meta[name] = 0
        return {"files": data, "mtime": meta}
    except OSError as e:
        return JSONResponse({"error": "cannot read config: %s" % e}, status_code=500)


@app.put("/api/config/{name}")
async def api_config_put(name: str, request: Request):
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    allowed = [os.path.basename(n) for n in config_store.CONF_FILES]
    if name not in allowed:
        return JSONResponse({"error": "unknown config file"}, status_code=404)
    body = await request.json()
    text = body.get("content", body.get("data"))
    try:
        if name.endswith(".json"):
            if isinstance(text, str):
                data = json.loads(text)
            else:
                data = text
            if name == "cfg.json":
                config_store.validate_cfg(data)
            elif name == "stream.json":
                config_store.validate_streams(data)
            config_store.write_json(name, data)
        else:
            if not isinstance(text, str):
                return JSONResponse({"error": "text content required"}, status_code=400)
            config_store.write_text(name, text)
    except (ValueError, config_store.ConfigError) as e:
        return JSONResponse({"error": "invalid config: %s" % e}, status_code=400)
    except OSError as e:
        return JSONResponse({"error": "write failed: %s" % e}, status_code=500)

    applied = []
    if name == "cfg.json":
        supervisor_ctl.restart("op25")
        applied.append("op25 restarted")
    elif name in ("stream.json", "listen.json"):
        _rerender()
        supervisor_ctl.restart("icecast")
        supervisor_ctl.restart("streams")
        applied.append("icecast + streams restarted")
    return {"ok": True, "applied": applied}


def _rerender():
    try:
        subprocess.run(RENDER, capture_output=True, text=True, timeout=30)
    except OSError:
        pass


@app.post("/api/restart/{program}")
async def api_restart(program: str, request: Request):
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    if program not in ("op25", "icecast", "streams", "web"):
        return JSONResponse({"error": "unknown program"}, status_code=404)
    if program in ("streams", "icecast"):
        _rerender()
    if program == "web":
        code, out = supervisor_ctl.restart_delayed("web")
    else:
        code, out = supervisor_ctl.restart(program)
    return {"ok": code == 0, "output": out}


# ----------------------------------------------------------------- telemetry api

@app.get("/api/telemetry")
async def api_telemetry(request: Request):
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    snap, updated, calls = TELEM.snapshot()
    return {"updated": updated, "stale": (time.time() - updated) if updated else None,
            "updates": snap, "calls": calls}


@app.get("/api/status")
async def api_status(request: Request):
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    _, updated, _ = TELEM.snapshot()
    return {
        "procs": supervisor_ctl.status(),
        "telemetry_age": (time.time() - updated) if updated else None,
        "time": time.time(),
    }


@app.get("/api/log/{program}")
async def api_log(program: str, request: Request, lines: int = 200):
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if program not in ("op25", "icecast", "streams", "web"):
        return JSONResponse({"error": "unknown program"}, status_code=404)
    return {"log": supervisor_ctl.tail(program, max(10, min(lines, 1000)))}


# --------------------------------------------------------------------- sdr api

@app.post("/api/sdr/scan")
async def api_sdr_scan(request: Request):
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    return sdr.sdr_scanner.run()


@app.get("/api/sdr/scan")
async def api_sdr_result(request: Request):
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return sdr.sdr_scanner.result()


@app.post("/api/sdr/diag/{index}")
async def api_sdr_diag(index: str, request: Request):
    """Deep-diagnose one dongle: run rtl_test and check for tuner/USB failures."""
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    try:
        dev = int(index)
    except ValueError:
        return JSONResponse({"error": "device index must be an integer"}, status_code=400)
    if dev < 0 or dev >= sdr.MAX_DEVICES:
        return JSONResponse({"error": "device index out of range"}, status_code=400)
    return sdr.diagnose(dev)


# ---------------------------------------------------------------- op25 control

@app.get("/api/op25/debug")
async def api_op25_debug(request: Request):
    """Current op25 verbosity (from cfg.json) and the available levels."""
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    try:
        cfg = config_store.read_json("cfg.json")
    except (OSError, ValueError):
        cfg = {}
    return {"configured": cfg.get("verbosity", 1), "levels": op25_ctl.LEVELS}


@app.post("/api/op25/debug/{level}")
async def api_op25_debug_set(level: str, request: Request):
    """Change op25's live log verbosity without restarting (set_debug)."""
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    try:
        lvl = int(level)
    except ValueError:
        return JSONResponse({"error": "level must be an integer 0..10"}, status_code=400)
    if lvl < 0 or lvl > 10:
        return JSONResponse({"error": "level must be an integer 0..10"}, status_code=400)
    port = _op25_terminal_port()
    return op25_ctl.set_debug(lvl, port=port)


@app.post("/api/op25/dump-tgids")
async def api_op25_dump_tgids(request: Request):
    """Ask op25 to print all decoded talkgroups (with counters) to its log."""
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    port = _op25_terminal_port()
    return op25_ctl.dump_tgids(port=port)


def _op25_terminal_port():
    try:
        cfg = config_store.read_json("cfg.json")
    except (OSError, ValueError):
        cfg = {}
    return op25_ctl.terminal_port_from_config(cfg)


# -------------------------------------------------------------- stream proxy

@app.get("/stream/{mount:path}")
async def proxy_stream(mount: str, request: Request):
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    try:
        stream_cfg = config_store.read_json("stream.json")
    except OSError:
        return JSONResponse({"error": "no stream config"}, status_code=500)

    host = stream_cfg.get("icecast", {}).get("host", "127.0.0.1")
    port = stream_cfg.get("icecast", {}).get("port", 8000)
    auth_enabled = stream_cfg.get("icecast", {}).get("listener_auth", False)
    creds = None
    if auth_enabled:
        try:
            listen = config_store.read_json("listen.json")
        except OSError:
            listen = {}
        users = listen.get("users", [])
        if users:
            creds = (users[0]["username"], users[0].get("password", ""))

    # mount arrives URL-decoded (e.g. "/primary.mp3" from "/stream/%2Fprimary.mp3"
    # as requested by the browser), so build the upstream path without a
    # duplicate leading slash, which Icecast rejects.
    url = "http://%s:%d/%s" % (host, port, mount.lstrip("/"))
    try:
        # Async client + streaming: a sync httpx.Client inside an async
        # generator would block the event loop on every socket read, starving
        # the telemetry endpoints the Live tab polls, and its 5s default read
        # timeout would kill the stream during brief pauses. timeout=None keeps
        # the connection open across silent gaps.
        client = httpx.AsyncClient(auth=creds if creds else None, follow_redirects=True, timeout=None)
        req = client.build_request("GET", url, headers={"User-Agent": "op25-web"})
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        return JSONResponse({"error": "icecast unreachable: %s" % e}, status_code=502)
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        return JSONResponse({"error": "icecast returned %d" % upstream.status_code}, status_code=502)

    headers = {}
    for h in ("content-type", "ice-audio-info", "icy-name", "icy-genre", "icy-br", "icy-pub", "icy-url", "icy-description"):
        v = upstream.headers.get(h)
        if v:
            headers[h] = v

    async def gen():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(gen(), media_type=headers.get("content-type", "audio/mpeg"), headers=headers)


@app.get("/api/health")
async def health():
    return {"ok": True, "time": time.time()}


# ------------------------------------------------------------------ static spa

NO_CACHE = {"Cache-Control": "no-store"}


@app.get("/")
async def spa():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=NO_CACHE)


@app.get("/{path}")
async def spa_fallback(path: str):
    if "." in path:
        fp = os.path.join(STATIC_DIR, path)
        if os.path.isfile(fp):
            return FileResponse(fp)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=NO_CACHE)
