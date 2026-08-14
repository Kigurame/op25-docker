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
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr


def md5_hash(password):
    """Icecast 2.4 htpasswd module stores the bare hex MD5 of the password."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()


CRYPT_ALLOW = 0   # op25: attempt decode of encrypted traffic (keys loaded)
CRYPT_SKIP = 2    # op25: ignore encrypted talkgroups entirely (no key)


def _resolve_keyfile(keyfile, conf_dir):
    if not keyfile:
        return ""
    if os.path.isabs(keyfile):
        return keyfile
    return os.path.join(conf_dir, keyfile)

def _chown_icecast(path):
    try:
        import grp
        import pwd
        os.chown(path, pwd.getpwnam("icecast2").pw_uid, grp.getgrnam("icecast").gr_gid)
        os.chmod(path, 0o600)
    except (KeyError, OSError):
        os.chmod(path, 0o600)


def normalize_crypt(cfg_json, conf_dir):
    """Encrypted traffic is ignored unless the user provides a keys file.

    op25's `crypt_behavior` values: 0 = allow (decode when a key is loaded),
    2 = skip (never tune encrypted talkgroups). We derive it from the presence
    of an op25 keys file so the out-of-the-box default ignores encryption:

      - channels[i].crypt_keys is a path to a keys file
        (keyid -> {algid, key[]}, see example_keys.json). Empty/missing file =>
        crypt_behavior 2. Existing file => crypt_behavior 0 and the key path is
        made absolute so multi_rx can open it from its working directory.
      - trunking.chans[].crypt_behavior mirrors the channel behaviour so the
        trunking module skips encrypted grants instead of tuning them.
    """
    for ch in cfg_json.get("channels", []):
        keyfile = _resolve_keyfile(ch.get("crypt_keys", ""), conf_dir)
        has_keys = bool(keyfile) and os.path.isfile(keyfile)
        ch["crypt_keys"] = keyfile if has_keys else ""
        ch["crypt_behavior"] = CRYPT_ALLOW if has_keys else CRYPT_SKIP
    key_by_sys = {}
    for ch in cfg_json.get("channels", []):
        if ch.get("trunking_sysname") and ch.get("crypt_keys"):
            key_by_sys[ch["trunking_sysname"]] = ch["crypt_keys"]
    fallback_keys = next((ch.get("crypt_keys") for ch in cfg_json.get("channels", []) if ch.get("crypt_keys")), "")
    for sys in cfg_json.get("trunking", {}).get("chans", []):
        keys = key_by_sys.get(sys.get("sysname"), fallback_keys)
        sys["crypt_behavior"] = CRYPT_ALLOW if keys else CRYPT_SKIP
    return cfg_json


_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_INVALID_SINGLE_LINE = re.compile(r"[\x00-\x1f]")


def _xml_text(value):
    return escape(_INVALID_XML_CHARS.sub("", str(value)))


def _xml_attr(value):
    return quoteattr(_INVALID_XML_CHARS.sub("", str(value)))


def _assert_single_line(value, name):
    text = str(value)
    bad = _INVALID_SINGLE_LINE.search(text)
    if bad:
        raise ValueError("%s contains an illegal control character (0x%02x)"
                         % (name, ord(bad.group())))
    return text


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
        b.append("    <mount-name>%s</mount-name>" % _xml_text(mount))
        b.append("    <stream-name>%s</stream-name>" % _xml_text(name))
        b.append("    <stream-description>%s</stream-description>" % _xml_text(desc))
        b.append("    <stream-genre>%s</stream-genre>" % _xml_text(genre))
        if url:
            b.append("    <stream-url>%s</stream-url>" % _xml_text(url))
        b.append("    <max-listeners>%d</max-listeners>" % int(ml))
        b.append("    <public>0</public>")
        if listener_auth:
            b.append('    <authentication type="htpasswd">')
            b.append("        <option name=\"filename\" value=%s/>" % _xml_attr(htpasswd_file))
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
    out = out.replace("@MAX_CLIENTS@", str(int(ic.get("max_clients", 64))))
    out = out.replace("@SOURCE_PASSWORD@", _xml_text(ic.get("source_password", "changeme")))
    out = out.replace("@ADMIN_PASSWORD@", _xml_text(ic.get("admin_password", "changeme")))
    out = out.replace("@ICECAST_PORT@", str(int(ic.get("port", 8000))))
    out = out.replace("@MOUNTS@", mounts)

    try:
        ET.fromstring(out)
    except ET.ParseError as e:
        raise ValueError("generated icecast.xml is not well-formed; check stream.json values: %s" % e)

    with open(os.path.join(out_dir, "icecast.xml"), "w", encoding="utf-8") as fh:
        fh.write(out)

    # htpasswd for listener auth
    lines = []
    for u in listen_json.get("users", []):
        user = _assert_single_line(u["username"], "listen.json users[].username")
        pw = _assert_single_line(u.get("password", ""), "listen.json users[].password")
        lines.append("%s:%s" % (user, md5_hash(pw)))
    with open(htpasswd_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    _chown_icecast(htpasswd_file)


def render_supervisor(stream_json, out_path, tpl_dir, cfg_json):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tpl_path = _find_template([os.path.join(tpl_dir, "supervisor", "supervisord.conf.tpl"),
                               os.path.join(script_dir, "supervisor", "supervisord.conf.tpl")])
    with open(tpl_path, "r", encoding="utf-8") as fh:
        tpl = fh.read()
    ic = stream_json.get("icecast", {})
    verbosity = int(cfg_json.get("verbosity", 1)) if isinstance(cfg_json.get("verbosity"), int) else 1
    verbosity = max(0, min(verbosity, 11))
    password = _assert_single_line(ic.get("supervisor_password", "changeme"),
                                   "icecast.supervisor_password")
    out = tpl.replace("@SUPERVISOR_PASSWORD@", password)
    out = out.replace("@OP25_VERBOSITY@", str(verbosity))
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
    cfg_json = load("cfg.json")

    normalize_crypt(cfg_json, conf_dir)
    with open(os.path.join(out_dir, "cfg.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg_json, fh, indent=4, ensure_ascii=False)
        fh.write("\n")

    render_icecast(stream_json, listen_json, out_dir, args.tpl_dir)
    render_supervisor(stream_json, args.supervisor_conf, args.tpl_dir, cfg_json)

    keyed = [ch.get("crypt_keys") for ch in cfg_json.get("channels", []) if ch.get("crypt_keys")]
    if keyed:
        print("crypt: keys file(s) found -> encrypted traffic will be decrypted (%s)" % ", ".join(keyed))
    else:
        print("crypt: no keys file -> encrypted talkgroups are ignored")
    print("rendered /etc/op25/cfg.json, icecast.xml, htpasswd, supervisord.conf from %s" % conf_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
