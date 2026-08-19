"""Telemetry: background poller for the OP25 UDP terminal + call log accumulator.

Optionally publishes to MQTT when OP25_MQTT_HOST is set.
"""
import json
import os
import socket
import subprocess
import threading
import time

from .mqtt_bridge import MqttPublisher

TERM_PORT = int(os.environ.get("OP25_TERM_PORT", "5600"))
SUPERVISOR_SOCK = "/var/run/op25/supervisor.sock"
CALL_LOG_MAX = 50
STATUS_POLL_INTERVAL = 10


class Telemetry:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = None
        self._updated = 0.0
        self._calls = []
        self._mqtt = MqttPublisher()
        self._poller = threading.Thread(target=self._run, daemon=True)
        self._poller.start()
        if self._mqtt.enabled:
            threading.Thread(target=self._status_poller, daemon=True).start()

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
        if self._mqtt.enabled:
            self._publish_telemetry(updates)

    def _publish_telemetry(self, updates):
        for item in updates:
            if not isinstance(item, dict):
                continue
            if item.get("json_type") == "channel_update":
                for _idx, ch in item.items():
                    if isinstance(ch, dict) and "tgid" in ch:
                        self._mqtt.publish_active_channel({
                            "tgid": ch.get("tgid"),
                            "tag": ch.get("tag", ""),
                            "src": ch.get("srcaddr"),
                            "freq": ch.get("freq"),
                            "encrypted": ch.get("encrypted", False),
                            "emergency": ch.get("emergency", False),
                        })
                        break
            elif item.get("json_type") == "call_log":
                calls = item.get("log", [])
                if calls:
                    self._mqtt.publish_calls(calls)

    def _status_poller(self):
        while True:
            try:
                cmd = ["supervisorctl", "-c", "/etc/op25/supervisord.conf",
                       "--serverurl", "unix://" + SUPERVISOR_SOCK, "status"]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    status = {}
                    for line in r.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0]:
                            status[parts[0]] = parts[1]
                    if status:
                        self._mqtt.publish_service_status(status)
            except Exception:
                pass
            time.sleep(STATUS_POLL_INTERVAL)

    def snapshot(self):
        with self._lock:
            return self._snapshot, self._updated, list(self._calls)


telemetry = Telemetry()
