#!/usr/bin/env python3
"""Render runtime configuration files for op25-docker.

Reads conf/stream.json, conf/listen.json and the templates, and produces:
  - icecast.xml        (icecast2 config, with per-mount listener auth)
  - htpasswd           (icecast listener credentials, {SHA} format)
  - supervisord.conf   (supervisor program definitions)

Usage:
  render_configs.py [--conf-dir DIR] [--out-dir DIR] [--supervisor-conf FILE]
"""
import argparse
import hashlib
import json
import os
import sys


def md5_hash(password):
    """Icecast 2.4 htpasswd module stores the bare hex MD5 of the password."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def render_mounts(streams, listener_auth, htpasswd_file, max_listeners):
    blocks = []
    for s in streams:
        if not s.get("enabled", True):
            continue
        mount = s.get("mount", "/stream.mp3")
        name = s.get("icecast_name", mount)
        desc = s.get("icecast_description", "")
        genre = s.get("icecast_genre", "Scanner")
        url = s.get("icecast_url", "")
        ml = s.get("max_listeners", max_listeners)
        b = []
        b.append('<mount type="normal">')
        b.append("    <mount-name>%s</mount-name>" % mount)
        b.append("    <stream-name>%s</stream-name>" % name)
        b.append("    <stream-description>%s</stream-description>" % desc)
        b.append("    <stream-genre>%s</stream-genre>" % genre)
        if url:
            b.append("    <stream-url>%s</stream-url>" % url)
        b.append("    <max-listeners>%d</max-listeners>" % int(ml))
        b.append("    <public>0</public>")
        if listener_auth:
            b.append('    <authentication type="htpasswd">')
            b.append('        <option name="filename" value="%s"/>' % htpasswd_file)
            b.append('        <option name="allow_duplicate_users" value="1"/>')
            b.append("    </authentication>")
        b.append("</mount>")
        blocks.append("\n".join(b))
    return "\n\n".join(blocks) if blocks else "<!-- no streams configured -->"


def _find_template(candidates):
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("template not found in: %s" % ", ".join(candidates))


def render_icecast(stream_json, listen_json, out_dir, tpl_dir):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tpl_path = _find_template([os.path.join(tpl_dir, "icecast-tpl.xml"),
                               os.path.join(script_dir, "conf", "icecast-tpl.xml")])
    with open(tpl_path, "r", encoding="utf-8") as fh:
        tpl = fh.read()

    ic = stream_json.get("icecast", {})
    listener_auth = bool(ic.get("listener_auth", listen_json.get("auth", False)))
    htpasswd_file = os.path.join(out_dir, "htpasswd")
    mounts = render_mounts(
        stream_json.get("streams", []),
        listener_auth,
        htpasswd_file,
        ic.get("max_listeners_per_mount", 16),
    )

    out = tpl
    out = out.replace("@MAX_CLIENTS@", str(ic.get("max_clients", 64)))
    out = out.replace("@SOURCE_PASSWORD@", ic.get("source_password", "changeme"))
    out = out.replace("@ADMIN_PASSWORD@", ic.get("admin_password", "changeme"))
    out = out.replace("@ICECAST_PORT@", str(ic.get("port", 8000)))
    out = out.replace("@MOUNTS@", mounts)

    with open(os.path.join(out_dir, "icecast.xml"), "w", encoding="utf-8") as fh:
        fh.write(out)

    # htpasswd for listener auth
    lines = []
    for u in listen_json.get("users", []):
        pw = u.get("password", "")
        lines.append("%s:%s" % (u["username"], md5_hash(pw)))
    with open(htpasswd_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(htpasswd_file, 0o600)


def render_supervisor(stream_json, out_path, tpl_dir):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tpl_path = _find_template([os.path.join(tpl_dir, "supervisor", "supervisord.conf.tpl"),
                               os.path.join(script_dir, "supervisor", "supervisord.conf.tpl")])
    with open(tpl_path, "r", encoding="utf-8") as fh:
        tpl = fh.read()
    ic = stream_json.get("icecast", {})
    out = tpl.replace("@SUPERVISOR_PASSWORD@", ic.get("supervisor_password", "changeme"))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf-dir", default=os.environ.get("OP25_CONF_DIR", "/opt/op25/conf"))
    ap.add_argument("--tpl-dir", default="/opt/op25/defaults")
    ap.add_argument("--out-dir", default="/etc/op25")
    ap.add_argument("--supervisor-conf", default="/etc/op25/supervisord.conf")
    args = ap.parse_args()

    conf_dir = args.conf_dir
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    def load(name):
        path = os.path.join(conf_dir, name)
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    stream_json = load("stream.json")
    listen_json = load("listen.json")

    render_icecast(stream_json, listen_json, out_dir, args.tpl_dir)
    render_supervisor(stream_json, args.supervisor_conf, args.tpl_dir)

    print("rendered icecast.xml, htpasswd, supervisord.conf from %s" % conf_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
