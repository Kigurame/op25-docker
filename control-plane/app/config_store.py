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
    for ch in cfg["channels"]:
        if "device" not in ch:
            raise ConfigError("each channel needs a 'device' field")
        if "destination" not in ch:
            raise ConfigError("each channel needs a 'destination' field (udp://host:port)")
    for sys in cfg["trunking"]["chans"]:
        if "sysname" not in sys:
            raise ConfigError("each trunked system needs a 'sysname'")
        if not sys.get("control_channel_list"):
            raise ConfigError("system '%s' needs a non-empty control_channel_list" % sys.get("sysname"))
    return True


def validate_streams(s):
    if not isinstance(s, dict) or "icecast" not in s:
        raise ConfigError("stream.json must be an object with an 'icecast' section")
    for st in s.get("streams", []):
        if "name" not in st or "mount" not in st or "udp_port" not in st:
            raise ConfigError("each stream needs 'name', 'mount' and 'udp_port'")
    return True
