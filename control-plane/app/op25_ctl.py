"""Control commands to the running OP25 process over its UDP JSON terminal.

multi_rx.py listens for JSON datagrams on the UDP terminal port (cfg.json
"terminal.terminal_type", default 5600) and accepts commands such as
"set_debug" and "dump_tgids". These give live insight into whether op25 is
tuning and decoding without restarting the receiver.
"""
import json
import os
import re
import socket

TERM_PORT = int(os.environ.get("OP25_TERM_PORT", "5600"))
KEEPALIVE_TIME = 60.0

# op25 verbosity levels (multi_rx.py / tk_p25.py debug thresholds)
LEVELS = {
    0: "quiet (errors only)",
    1: "normal (startup, tuning failures)",
    2: "verbose (tag file loading)",
    5: "debug (call/CC activity, TDMA masks)",
    9: "tuning (hardware tune details)",
    10: "full (channel-control trace - very spammy)",
}


def terminal_port_from_config(cfg):
    """Derive the UDP terminal port from a parsed cfg.json (mirrors multi_rx)."""
    t = (cfg or {}).get("terminal", {}).get("terminal_type", TERM_PORT)
    if isinstance(t, (int, float)):
        return int(t)
    m = re.match(r"^(\d+)", str(t))
    return int(m.group(1)) if m else TERM_PORT


def send(command, arg1=0, arg2=0, port=TERM_PORT, timeout=2.0):
    """Send a JSON command to the op25 terminal.

    Returns the decoded JSON reply list (op25 responds to the most recent
    client) or None if there is no terminal / no reply within the timeout.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        msg = json.dumps({"command": command, "arg1": arg1, "arg2": arg2})
        sock.sendto(msg.encode("utf-8"), ("127.0.0.1", port))
        try:
            data, _ = sock.recvfrom(65536)
        except socket.timeout:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
    except OSError:
        return None
    finally:
        sock.close()


def set_debug(level, port=TERM_PORT):
    """Change op25's live log verbosity without a restart."""
    level = max(0, min(int(level), 10))
    reply = send("set_debug", level, 0, port=port)
    return {"level": level, "ok": reply is not None}


def dump_tgids(port=TERM_PORT):
    """Ask op25 to print all decoded talkgroups (with counters) to its log."""
    reply = send("dump_tgids", 0, 0, port=port)
    return {"ok": reply is not None}
