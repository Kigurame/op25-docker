"""SDR device scanning using rtl_test."""
import re
import subprocess
import threading

MAX_DEVICES = 4


def _rtl_test(dev_index):
    cmd = ["rtl_test", "-d", str(dev_index), "-t"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {"index": dev_index, "present": True, "detail": "timed out during tuner test"}
    except OSError as e:
        return {"index": dev_index, "present": False, "error": str(e)}
    out = r.stdout + r.stderr
    if "No supported devices found" in out or "no devices" in out.lower() or r.returncode not in (0, 1):
        return {"index": dev_index, "present": False}
    detail = _parse(out)
    detail["present"] = True
    detail["index"] = dev_index
    return detail


def _parse(out):
    d = {"sn": "", "tuner": "", "lines": []}
    m = re.search(r"Realtek,\s+(\S+),\s+SN:\s+(\S+)", out)
    if m:
        d["tuner"] = m.group(1)
        d["sn"] = m.group(2)
    m = re.search(r"Tuner type:\s*(\S+)", out)
    if m:
        d["tuner"] = m.group(1)
    m = re.search(r"Found\s+(\d+)\s+device", out)
    if m:
        d["found"] = int(m.group(1))
    m = re.search(r"Max:\s*([-+0-9.]+\s+dB)", out)
    if m:
        d["max_signal"] = m.group(1).strip()
    m = re.search(r"Min:\s*([-+0-9.]+\s+dB)", out)
    if m:
        d["min_signal"] = m.group(1).strip()
    d["lines"] = [l.strip() for l in out.splitlines() if l.strip()][:12]
    return d


def scan():
    """Detect RTL-SDR dongles attached to the host."""
    result = {"devices": [], "ok": True, "error": ""}
    for i in range(MAX_DEVICES):
        dev = _rtl_test(i)
        if dev.get("present"):
            result["devices"].append(dev)
        else:
            break
    if not result["devices"]:
        result["ok"] = False
        result["error"] = "No RTL-SDR devices detected"
    return result


class SdrScanner:
    def __init__(self):
        self._lock = threading.Lock()
        self._result = None

    def run(self):
        with self._lock:
            self._result = scan()
            return self._result

    def result(self):
        with self._lock:
            return self._result or {"devices": [], "ok": False, "error": "not scanned yet"}


sdr_scanner = SdrScanner()
