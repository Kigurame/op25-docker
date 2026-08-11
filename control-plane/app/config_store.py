"""Read/write of the on-disk JSON configuration files."""
import json
import os

CONF_DIR = os.environ.get("OP25_CONF_DIR", "/opt/op25/conf")
CONF_FILES = ["cfg.json", "stream.json", "users.json", "listen.json", "tags/tgid.tsv", "tags/rid.tsv"]


class ConfigError(Exception):
    pass


def _path(name):
    return os.path.join(CONF_DIR, name)


def read_json(name):
    with open(_path(name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(name, data):
    with open(_path(name), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4, ensure_ascii=False)
        fh.write("\n")


def read_text(name):
    with open(_path(name), "r", encoding="utf-8") as fh:
        return fh.read()


def write_text(name, data):
    with open(_path(name), "w", encoding="utf-8") as fh:
        fh.write(data)


def read_all():
    out = {}
    for name in CONF_FILES:
        p = _path(name)
        if os.path.exists(p):
            out[name] = read_json(name) if name.endswith(".json") else read_text(name)
        else:
            out[name] = None
    return out


def validate_cfg(cfg):
    """Minimal structural validation of the op25 multi_rx config."""
    if not isinstance(cfg, dict):
        raise ConfigError("cfg.json must be a JSON object")
    if "devices" not in cfg or not isinstance(cfg["devices"], list) or not cfg["devices"]:
        raise ConfigError("cfg.json must contain a non-empty 'devices' list")
    if "channels" not in cfg or not isinstance(cfg["channels"], list) or not cfg["channels"]:
        raise ConfigError("cfg.json must contain a non-empty 'channels' list")
    if "trunking" not in cfg or "chans" not in cfg["trunking"]:
        raise ConfigError("cfg.json must contain a 'trunking' section with 'chans'")
    sysnames = set(s.get("sysname") for s in cfg["trunking"]["chans"])
    for ch in cfg["channels"]:
        if "device" not in ch:
            raise ConfigError("each channel needs a 'device' field")
        if "destination" not in ch:
            raise ConfigError("each channel needs a 'destination' field (udp://host:port)")
        # A channel without a matching trunking_sysname becomes a plain
        # conventional receiver and never hunts the control channel.
        if not ch.get("trunking_sysname"):
            raise ConfigError("each channel needs a 'trunking_sysname' matching a system name in trunking.chans[]")
        if ch["trunking_sysname"] not in sysnames:
            raise ConfigError("channel '%s' trunking_sysname '%s' does not match any trunking.chans[].sysname (%s)"
                              % (ch.get("name", "?"), ch["trunking_sysname"], ", ".join(sorted(sysnames))))
    for sys in cfg["trunking"]["chans"]:
        if "sysname" not in sys:
            raise ConfigError("each trunked system needs a 'sysname'")
        if not sys.get("control_channel_list"):
            raise ConfigError("system '%s' needs a non-empty control_channel_list" % sys.get("sysname"))
    return True


def _require_int(value, name, minimum=1, maximum=None):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError("%s must be an integer" % name)
    if value < minimum:
        raise ConfigError("%s must be at least %d" % (name, minimum))
    if maximum is not None and value > maximum:
        raise ConfigError("%s must be at most %d" % (name, maximum))
    return True


def _require_str(value, name, allow_empty=False):
    if not isinstance(value, str):
        raise ConfigError("%s must be a string" % name)
    if not allow_empty and not value.strip():
        raise ConfigError("%s must not be empty" % name)
    return True


def validate_streams(s):
    if not isinstance(s, dict):
        raise ConfigError("stream.json must be a JSON object")
    ic = s.get("icecast")
    if not isinstance(ic, dict):
        raise ConfigError("stream.json must contain an 'icecast' section")
    _require_str(ic.get("host", "127.0.0.1"), "icecast.host")
    _require_int(ic.get("port", 8000), "icecast.port", 1, 65535)
    _require_int(ic.get("max_clients", 64), "icecast.max_clients")
    _require_int(ic.get("max_listeners_per_mount", 16), "icecast.max_listeners_per_mount")
    if not isinstance(ic.get("listener_auth", True), bool):
        raise ConfigError("icecast.listener_auth must be a boolean")
    for key in ("source_password", "admin_password", "supervisor_password"):
        _require_str(ic.get(key, "changeme"), "icecast.%s" % key)
    streams = s.get("streams")
    if streams is None:
        raise ConfigError("stream.json must contain a 'streams' list")
    if not isinstance(streams, list):
        raise ConfigError("'streams' must be a list")
    seen_mounts = set()
    seen_ports = set()
    for i, st in enumerate(streams):
        prefix = "streams[%d]" % i
        if not isinstance(st, dict):
            raise ConfigError("%s must be a JSON object" % prefix)
        _require_str(st.get("name", ""), "%s.name" % prefix)
        mount = st.get("mount", "")
        _require_str(mount, "%s.mount" % prefix)
        if not mount.startswith("/"):
            raise ConfigError("%s.mount must start with '/'" % prefix)
        if mount in seen_mounts:
            raise ConfigError("duplicate stream mount %s" % mount)
        seen_mounts.add(mount)
        port = st.get("udp_port")
        _require_int(port, "%s.udp_port" % prefix, 1, 65535)
        if port in seen_ports:
            raise ConfigError("duplicate stream udp_port %d" % port)
        seen_ports.add(port)
        if st.get("enabled", True) is not None and not isinstance(st.get("enabled", True), bool):
            raise ConfigError("%s.enabled must be a boolean" % prefix)
        codec = st.get("codec", "mp3")
        if codec not in ("mp3", "aac"):
            raise ConfigError("%s.codec must be 'mp3' or 'aac'" % prefix)
        _require_int(st.get("bitrate_kbps", 48), "%s.bitrate_kbps" % prefix, 1)
        _require_int(st.get("channels", 2), "%s.channels" % prefix, 1, 2)
        for key in ("icecast_name", "icecast_description", "icecast_genre", "icecast_url"):
            if key in st:
                _require_str(st[key], "%s.%s" % (prefix, key), allow_empty=True)
        if "max_listeners" in st:
            _require_int(st["max_listeners"], "%s.max_listeners" % prefix, 1)
    return True
