"""Supervisord process control via supervisorctl."""
import subprocess

SUPERVISOR_SOCK = "/var/run/op25/supervisor.sock"


def _ctl(args):
    cmd = ["supervisorctl", "-c", "/etc/op25/supervisord.conf", "--serverurl",
           "unix://" + SUPERVISOR_SOCK] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return -1, str(e)


def status():
    code, out = _ctl(["status"])
    if code != 0:
        return []
    procs = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] != "" and not parts[0].startswith("op25"):
            continue
        # parse "name            RUNNING   pid 123, uptime 0:01:23"
        m = parts[0] if parts else ""
        state = parts[1] if len(parts) > 1 else ""
        rest = " ".join(parts[2:])
        procs.append({"name": m, "state": state, "info": rest})
    return procs


def restart(program):
    return _ctl(["restart", program])


def stop(program):
    return _ctl(["stop", program])


def start(program):
    return _ctl(["start", program])


def reload():
    return _ctl(["reload"])


def tail(program, lines=200):
    code, out = _ctl(["tail", "-%d" % lines, program])
    return out if code == 0 else ""
