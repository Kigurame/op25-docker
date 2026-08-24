"""op25-docker control plane - FastAPI application."""
import base64
import copy
import hmac
import json
import logging
import os
import subprocess
import threading
import time
import urllib.parse

import httpx
from fastapi import Body, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import auth, config_store, op25_ctl, sdr, supervisor_ctl
from .telemetry import telemetry as TELEM

app = FastAPI(title="op25-docker control plane", docs_url=None, redoc_url=None)

log = logging.getLogger("op25.web")

CONF_DIR = os.environ.get("OP25_CONF_DIR", "/opt/op25/conf")
RENDER = ["/opt/op25/venv/bin/python", "/opt/op25/render_configs.py",
          "--conf-dir", CONF_DIR, "--tpl-dir", "/opt/op25/defaults",
          "--out-dir", "/etc/op25", "--supervisor-conf", "/etc/op25/supervisord.conf"]
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")
STATIC_REAL = os.path.realpath(STATIC_DIR)

CONFIG_LOCK = threading.Lock()


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
def api_add_user(request: Request, body: dict = Body(...)):
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    un = str(body.get("username", "")).strip()
    pw = str(body.get("password", ""))
    role = str(body.get("role", "viewer"))
    if not un or len(pw) < 6:
        return JSONResponse({"error": "username required, password min 6 characters"}, status_code=400)
    with CONFIG_LOCK:
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
def api_change_password(request: Request, body: dict = Body(...)):
    try:
        u = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    old = body.get("old_password", "")
    new = body.get("new_password", "")
    if len(new) < 6:
        return JSONResponse({"error": "new password must be at least 6 characters"}, status_code=400)
    with CONFIG_LOCK:
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
        payload = require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    try:
        data = config_store.read_all()
        if payload.get("r") != "admin":
            data = _redact_for_viewer(data)
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


VIEWER_REDACT = "********"


def _redact_for_viewer(data):
    """Strip credentials a viewer has no use for: icecast source/admin
    passwords (which would allow pushing a stream or administering icecast)
    and the pbkdf2 password hashes (which could be brute-forced offline).

    Listener passwords in listen.json are intentionally kept: the Listen/Copy
    URL links in the UI embed them so viewers can play the stream outside the
    web player, and viewers can't save config anyway (PUT is admin-only).
    """
    out = {}
    for name, value in data.items():
        if not isinstance(value, dict):
            out[name] = value
            continue
        v = copy.deepcopy(value)
        if name == "stream.json":
            ic = v.setdefault("icecast", {})
            for key in ("source_password", "admin_password", "supervisor_password"):
                if key in ic:
                    ic[key] = VIEWER_REDACT
        elif name == "users.json":
            for u in v.get("users", []):
                if "password_hash" in u:
                    u["password_hash"] = VIEWER_REDACT
        out[name] = v
    return out


@app.put("/api/config/{name:path}")
def api_config_put(name: str, request: Request, body: dict = Body(...)):
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    allowed = list(config_store.CONF_FILES)
    if name not in allowed:
        return JSONResponse({"error": "unknown config file"}, status_code=404)
    text = body.get("content", body.get("data"))
    with CONFIG_LOCK:
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
        ok, msg = _rerender()
        if not ok:
            # The file was saved to conf/ but /etc/op25 is stale; restarting
            # now would run the old config silently, so refuse.
            return JSONResponse({"error": "config saved but not applied - render failed: %s" % msg}, status_code=500)
        supervisor_ctl.restart("op25")
        applied.append("op25 restarted (config re-rendered)")
    elif name in ("stream.json", "listen.json"):
        ok, msg = _rerender()
        if not ok:
            return JSONResponse({"error": "config saved but not applied - render failed: %s" % msg}, status_code=500)
        supervisor_ctl.restart("icecast")
        supervisor_ctl.restart("streams")
        applied.append("icecast + streams restarted")
    elif name.startswith("tags/"):
        # op25 reads the tag files at startup, so a restart is required to
        # reload them; without it the edit would silently have no effect.
        supervisor_ctl.restart("op25")
        applied.append("op25 restarted (tag files reloaded)")
    return {"ok": True, "applied": applied}


def _rerender():
    """Render /etc/op25 from conf/. Returns (ok, message).

    Renders before any restart so hand-edits to the mounted conf volume are
    picked up; the rendered files under /etc/op25 would otherwise stay stale
    until the next container boot."""
    try:
        r = subprocess.run(RENDER, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "render_configs timed out"
    except OSError as e:
        return False, "render_configs failed to start: %s" % e
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return False, "render_configs exited %d: %s" % (r.returncode, out)
    return True, out


@app.post("/api/restart/{program}")
def api_restart(program: str, request: Request):
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    if program not in ("op25", "icecast", "streams", "web"):
        return JSONResponse({"error": "unknown program"}, status_code=404)
    # Re-render from conf/ first so the restart runs the latest config even
    # when it was edited by hand on the host volume.
    if program in ("op25", "icecast", "streams"):
        ok, msg = _rerender()
        if not ok:
            return JSONResponse({"error": "render failed: %s" % msg}, status_code=500)
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
        return JSONResponse({"error": "level must be an integer 0..11"}, status_code=400)
    if lvl < 0 or lvl > 11:
        return JSONResponse({"error": "level must be an integer 0..11"}, status_code=400)
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


# ------------------------------------------------------------------- tsbk feed

TSBK_LEVEL = 11
TSBK_PATTERNS = ("tsbk(0x", "mbt(0x", "unhandled")
_tsbk_base = None


def _tsbk_state():
    try:
        cfg = config_store.read_json("cfg.json")
    except (OSError, ValueError):
        cfg = {}
    level = cfg.get("verbosity", 2)
    if not isinstance(level, int):
        level = 2
    return cfg, level


@app.get("/api/op25/tsbk")
async def api_op25_tsbk(request: Request):
    """Whether TSBK detail (verbosity 11) is currently configured/persisted."""
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    _, level = _tsbk_state()
    return {"enabled": level == TSBK_LEVEL, "level": level}


@app.post("/api/op25/tsbk")
def api_op25_tsbk_set(request: Request, body: dict = Body(...)):
    """Toggle all-TSBK logging (verbosity 11) and persist it in cfg.json.

    Applying it live needs no restart; the persisted level also survives a
    later op25 restart.
    """
    try:
        require_user(request, role="admin")
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401 if "authenticated" in str(e) else 403)
    want = bool(body.get("enabled"))
    global _tsbk_base
    with CONFIG_LOCK:
        cfg, level = _tsbk_state()

        if want:
            if level == TSBK_LEVEL:
                return {"ok": True, "enabled": True, "level": TSBK_LEVEL, "running": True, "applied": "already on"}
            if _tsbk_base is None:
                _tsbk_base = level
            cfg["verbosity"] = TSBK_LEVEL
        else:
            if _tsbk_base is None:
                _tsbk_base = level
            restore = _tsbk_base if _tsbk_base != TSBK_LEVEL else 2
            _tsbk_base = None
            cfg["verbosity"] = restore

        try:
            config_store.write_json("cfg.json", cfg)
        except OSError as e:
            return JSONResponse({"error": "write failed: %s" % e}, status_code=500)

    ok, rmsg = _rerender()
    applied = "persisted to cfg.json"
    if not ok:
        applied = "persisted to cfg.json but re-render failed: %s" % rmsg
    live = op25_ctl.set_debug(cfg["verbosity"], port=_op25_terminal_port())
    if not live["ok"]:
        applied += " (op25 not running - will apply on restart)"
    return {"ok": True, "enabled": cfg["verbosity"] == TSBK_LEVEL,
            "level": cfg["verbosity"], "running": live["ok"], "applied": applied}


@app.get("/api/op25/tsbk/feed")
async def api_op25_tsbk_feed(request: Request, lines: int = 150):
    """Recent non-voice trunking-signaling lines from the op25 log."""
    try:
        require_user(request)
    except PermissionError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    text = supervisor_ctl.tail("op25", 1000)
    matched = [ln for ln in text.splitlines()
               if any(p in ln.lower() for p in TSBK_PATTERNS)]
    return {"lines": matched[-max(10, min(lines, 500)):]}


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


@app.api_route("/playlist.m3u", methods=["GET", "HEAD"])
async def playlist_m3u(request: Request):
    """M3U playlist of every enabled Icecast mount, for players like Jellyfin.

    HEAD must be accepted too: Jellyfin probes tuners with HTTP HEAD, and a
    405 here surfaces in its UI as "error saving the TV provider".

    Auth (when listener_auth is on), any of:
      - a signed-in web session cookie ("Copy Jellyfin URL" in the browser)
      - HTTP Basic auth with a listen.json listener account
      - ?user=...&password=... query params matching a listener account

    Query params matter because players like Jellyfin validate the tuner URL
    with plain requests that never carry Basic credentials, even when the URL
    embeds user:pass@host. Stream URLs inside the playlist embed the listener
    login when listener_auth is on and point at the same host the playlist was
    fetched from, with icecast.external_port honored.
    """
    try:
        stream_cfg = config_store.read_json("stream.json")
    except OSError:
        return JSONResponse({"error": "no stream config"}, status_code=500)

    ic = stream_cfg.get("icecast", {})
    # With listener auth on, guard the playlist (it embeds credentials);
    # with it off, the feed itself is public, so serve it anonymously too.
    if ic.get("listener_auth"):
        try:
            require_user(request)
        except PermissionError:
            if not (_listener_basic_auth_ok(request)
                    or _listener_query_auth_ok(request)):
                _log_playlist_auth_rejected(request)
                return JSONResponse({"error": "not authenticated"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Basic realm="op25 streams"'})

    hostname = _request_hostname(request)
    port = int(ic.get("external_port") or ic.get("port") or 8000)
    base = "%s:%d" % (hostname, port)

    prefix = ""
    if ic.get("listener_auth"):
        try:
            users = config_store.read_json("listen.json").get("users", [])
        except OSError:
            users = []
        if users and users[0].get("username") and users[0].get("password", ""):
            u = users[0]
            prefix = "%s:%s@" % (urllib.parse.quote(str(u["username"]), safe=""),
                                 urllib.parse.quote(str(u["password"]), safe=""))

    lines = ["#EXTM3U"]
    for s in stream_cfg.get("streams", []):
        if not s.get("enabled"):
            continue
        mount = s.get("mount", "/")
        name = s.get("icecast_name") or s.get("name") or mount
        lines.append('#EXTINF:-1 group-title="Radio",%s' % name)
        lines.append("http://%s%s%s" % (prefix, base, mount))

    return Response("\n".join(lines) + "\n",
                    media_type="audio/x-mpegurl",
                    headers={"Cache-Control": "no-store"})


def _request_hostname(request: Request):
    """Hostname from the Host header, so the playlist points wherever the
    caller reached us (works no matter what IP/hostname they used)."""
    host_hdr = request.headers.get("host", "").strip()
    if not host_hdr:
        return "127.0.0.1"
    if host_hdr.startswith("["):  # IPv6 literal, e.g. [::1]:8080
        return host_hdr[1:host_hdr.index("]")]
    return host_hdr.rsplit(":", 1)[0] if ":" in host_hdr else host_hdr


def _listener_basic_auth_ok(request: Request):
    """True if HTTP Basic credentials match an account from listen.json."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(header[6:].strip()).decode("utf-8", "replace")
    except ValueError:
        return False
    username, _, password = raw.partition(":")
    return _listener_creds_ok(username, password)


def _listener_query_auth_ok(request: Request):
    """True if ?user=&password= query params match a listen.json account."""
    username = request.query_params.get("user") or ""
    password = request.query_params.get("password") or ""
    return _listener_creds_ok(username, password)


def _log_playlist_auth_rejected(request: Request):
    """Diagnose rejected /playlist.m3u hits without logging secret values.

    Common causes this pinpoints: a pre-query-param playlist URL, or an
    op25 container that predates query-param support (params present but
    ignored by the old image)."""
    qp = request.query_params
    signals = []
    if request.cookies.get("op25_session"):
        signals.append("session-cookie(present)")
    if request.headers.get("authorization", "").lower().startswith("basic "):
        signals.append("basic-header(present)")
    if "user" in qp or "password" in qp:
        signals.append("query-creds(present)")
    if not signals:
        signals.append("no-auth-signals")
    log.warning("[playlist] rejected fetch from %s: %s",
                _request_hostname(request), ", ".join(signals))


def _listener_creds_ok(username: str, password: str):
    if not username:
        return False
    try:
        listen = config_store.read_json("listen.json")
    except (OSError, ValueError):
        return False
    for u in listen.get("users", []):
        if (u.get("username") == username
                and hmac.compare_digest(str(u.get("password", "")), password)):
            return True
    return False


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
        fp = os.path.realpath(os.path.join(STATIC_REAL, path))
        if (os.path.commonpath([STATIC_REAL, fp]) == STATIC_REAL
                and os.path.isfile(fp)):
            return FileResponse(fp, headers=NO_CACHE)
    return FileResponse(os.path.join(STATIC_REAL, "index.html"), headers=NO_CACHE)
