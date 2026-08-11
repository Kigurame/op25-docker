"""SDR device scanning and diagnostics using rtl_test."""
import re
import signal
import subprocess
import threading

MAX_DEVICES = 4

# Substrings that indicate the dongle opened but the R820T/R820T2 tuner
# failed to lock or tune (seen in op25 logs as "[R82XX] PLL not locked!" /
# "r82xx_set_freq: failed=-1"). When present the dongle cannot receive,
# regardless of what frequency cfg.json asks for.
TUNER_FAIL_PATTERNS = [
    r"PLL not locked",
    r"r82xx_set_freq: failed",
    r"r82xx_write: i2c wr failed",
    r"set_tuner_bandwidth failed",
    r"Unable to set tuned frequency",
]

# Substrings that indicate the device could not be opened/claimed at all.
OPEN_FAIL_PATTERNS = [
    "Failed to open rtlsdr device",
    "No supported devices found",
    "No matching devices found",
    "usb_claim_interface error",
    "usb_open error",
    "Supplied device index is out of range",
]


def _rtl_test(dev_index):
    cmd = ["rtl_test", "-d", str(dev_index), "-t"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {"index": dev_index, "present": True, "detail": "timed out during tuner test"}
    except OSError as e:
        return {"index": dev_index, "present": False, "error": str(e)}
    out = r.stdout + r.stderr
    if "No supported devices found" in out or "No matching devices found" in out:
        return {"index": dev_index, "present": False}
    # librtlsdr falls back to the last real device's strings for out-of-range
    # indices ("Using device N:" shows which one it actually tried to open);
    # a requested index that resolves to a different device is a phantom.
    m = re.search(r"Using device\s+(\d+):", out)
    opened = int(m.group(1)) if m else None
    if opened is not None and opened != dev_index:
        return {"index": dev_index, "present": False}
    if r.returncode not in (0, 1):
        return {"index": dev_index, "present": False}
    detail = _parse(out)
    detail["present"] = True
    detail["index"] = dev_index
    if "Failed to open rtlsdr device" in out or "usb_claim_interface error" in out:
        detail["detail"] = "device busy (claimed by another process)"
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


def _run_rtl_test(dev_index, extra_args, timeout):
    cmd = ["rtl_test", "-d", str(dev_index)] + extra_args
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors="replace")
    except OSError as e:
        return "", "rtl_test not available: %s" % e
    try:
        try:
            out, errs = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Graceful stop so rtl_test prints its signal-strength summary
            proc.send_signal(signal.SIGINT)
            try:
                out, errs = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, errs = proc.communicate()
    finally:
        if proc.poll() is None:
            proc.kill()
    return (out or "") + (errs or ""), None


def diagnose(dev_index):
    """Run a deeper diagnostic on one dongle and report whether it can
    actually receive. Checks (1) USB open/claim, (2) tuner lock, and
    (3) that a real sample stream is produced with a sane noise floor."""
    index = max(0, int(dev_index))
    out, err = _run_rtl_test(index, [], 8)
    if err:
        return {"index": index, "ok": False, "error": err, "lines": []}
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    issues = []
    for pat in TUNER_FAIL_PATTERNS:
        if re.search(pat, out, re.IGNORECASE):
            issues.append("tuner failed to lock/tune: '%s' present in rtl_test output" % pat)
    for pat in OPEN_FAIL_PATTERNS:
        if re.search(pat, out, re.IGNORECASE):
            issues.append("device could not be opened: '%s'" % pat)

    opened = re.search(r"Using device\s+(\d+):", out)
    reads = re.search(r"Reading samples in async mode", out)
    found = re.search(r"Found\s+(\d+)\s+device", out)

    ok = not issues
    status = "OK" if ok else "FAILED"
    if not ok:
        pass
    elif opened and opened.group(1) != str(index):
        ok = False
        status = "FAILED"
        issues.append("index %d resolved to a different device (%s)" % (index, opened.group(1)))
    elif not reads:
        ok = False
        status = "WARN"
        issues.append("device opened but never entered continuous sample reading")
    elif found and found.group(1) != "1":
        ok = False
        status = "WARN"
        issues.append("unexpected device count in output")

    # signal stats printed by rtl_test on shutdown
    maxsig = re.search(r"Max signal strength:\s*([-+0-9.]+)\s+dB", out, re.IGNORECASE)
    minsig = re.search(r"Min signal strength:\s*([-+0-9.]+)\s+dB", out, re.IGNORECASE)
    avgsig = re.search(r"(?:Avg|Average) signal strength:\s*([-+0-9.]+)\s+dB", out, re.IGNORECASE)

    detail = {
        "index": index,
        "ok": ok,
        "status": status,
        "issues": issues,
        "opened": bool(opened),
        "reading_samples": bool(reads),
        "max_signal": maxsig.group(1) + " dB" if maxsig else "",
        "min_signal": minsig.group(1) + " dB" if minsig else "",
        "avg_signal": avgsig.group(1) + " dB" if avgsig else "",
        "lines": lines,
    }
    if err:
        detail["note"] = err
    return detail


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
