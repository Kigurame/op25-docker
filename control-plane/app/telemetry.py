"""Telemetry: background poller for the OP25 UDP terminal + call log accumulator."""
import json
import os
import socket
import threading
import time

TERM_PORT = int(os.environ.get("OP25_TERM_PORT", "5600"))
CALL_LOG_MAX = 50


class Telemetry:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = None
        self._updated = 0.0
        self._calls = []
        self._poller = threading.Thread(target=self._run, daemon=True)
        self._poller.start()

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        while True:
            try:
                sock.sendto(b'{"command":"update","arg1":0,"arg2":0}', ("127.0.0.1", TERM_PORT))
                try:
                    data, _ = sock.recvfrom(65536)
                except socket.timeout:
                    data = None
                if data:
                    self._ingest(data)
            except OSError as e:
                print("telemetry: %s" % e, flush=True)
            time.sleep(1.0)

    def _ingest(self, data):
        try:
            updates = json.loads(data.decode())
        except (ValueError, UnicodeDecodeError):
            return
        with self._lock:
            self._snapshot = updates
            self._updated = time.time()
            for item in updates:
                if isinstance(item, dict) and item.get("json_type") == "call_log":
                    for call in reversed(item.get("log", [])):
                        self._calls.insert(0, call)
                    self._calls = self._calls[:CALL_LOG_MAX]

    def snapshot(self):
        with self._lock:
            return self._snapshot, self._updated, list(self._calls)


telemetry = Telemetry()
