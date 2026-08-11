#!/usr/bin/env python3
"""stream_runner.py - OP25 UDP audio bridge to Icecast.

Listens on the UDP audio ports configured in conf/cfg.json (channel
'destination' ports, one per stream) and pipes decoded P25 audio to ffmpeg,
which encodes and publishes live streams to Icecast. Also updates Icecast
stream metadata from the OP25 UDP terminal telemetry.

Audio format received from OP25 (see sockaudio.py):
  - port N     : 320-byte datagrams = 160 x S16_LE samples @ 8000 Hz (slot A)
  - port N+1   : same for slot B (TDMA)
  - 2-byte datagram: control flag, int16: 0=drain (flush), 1=drop
"""
import base64
import json
import os
import re
import select
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
from urllib.error import URLError, HTTPError

CONF_DIR = os.environ.get("OP25_CONF_DIR", "/opt/op25/conf")
APPS_DIR = os.environ.get("OP25_APPS_DIR", "/opt/op25/apps")
FFMPEG = "/usr/bin/ffmpeg"
POLL_INTERVAL = 1.0          # seconds between telemetry polls
META_INTERVAL = 3.0          # seconds between metadata refresh attempts
AUDIO_FRAME = 160            # samples per channel per packet @8kHz
SILENCE_INTERVAL = 0.02      # seconds between silence frames


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def load_json(name, base=CONF_DIR):
    with open(os.path.join(base, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class TelemetryPoller(threading.Thread):
    """Polls the OP25 UDP terminal for trunk/channel/call-log updates."""
    def __init__(self, port, on_update):
        super().__init__(daemon=True)
        self.port = port
        self.on_update = on_update
        self._sock = None

    def run(self):
        log("telemetry poller started (udp 127.0.0.1:%d)" % self.port)
        while True:
            try:
                if self._sock is None:
                    self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._sock.settimeout(2.0)
                self._sock.sendto(b'{"command":"update","arg1":0,"arg2":0}', ("127.0.0.1", self.port))
                try:
                    data, _ = self._sock.recvfrom(65536)
                except socket.timeout:
                    data = None
                if data:
                    try:
                        updates = json.loads(data.decode())
                        self.on_update(updates)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
            except (OSError, ValueError) as e:
                log("telemetry poll error: %s" % e)
                time.sleep(2.0)
            time.sleep(POLL_INTERVAL)


class IcecastMetaUpdater:
    """Sends now-playing metadata to Icecast using the legacy admin API."""
    def __init__(self, host, port, admin_password):
        self.admin = "http://%s:%s/admin" % (host, port)
        self.base = self.admin + "/metadata"
        creds = base64.b64encode(("admin:%s" % admin_password).encode("utf-8")).decode("ascii")
        self.headers = {"Authorization": "Basic " + creds}
        self._last = {}
        self._fails = {}

    def wait_ready(self, mounts, timeout=30.0):
        """Wait until Icecast lists every mount as live (source connected)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                req = urllib.request.Request(self.admin + "/listmounts", headers=self.headers)
                with urllib.request.urlopen(req, timeout=2.0) as r:
                    body = r.read().decode("utf-8", "replace")
                if all(m in body for m in mounts):
                    return
            except (URLError, HTTPError, OSError, ValueError):
                pass
            time.sleep(1.0)
        log("warning: mounts not live on icecast after %ds: %s" % (int(timeout), ", ".join(mounts)))

    def update(self, mount, title):
        if self._last.get(mount) == title:
            return
        # song is a query-string value: encode everything (safe="") so spaces,
        # '+', '#' etc. are percent-encoded instead of producing invalid URLs.
        song = urllib.parse.quote(title, safe="")
        url = "%s?mount=%s&mode=updinfo&song=%s" % (self.base, urllib.parse.quote(mount), song)
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=1.0):
                pass
            self._last[mount] = title
            self._fails[mount] = 0
        except HTTPError as e:
            if e.code == 400:
                # Mount not live yet (source still connecting at boot): retry
                # silently on the next loop; only complain if it stays down.
                self._fails[mount] = self._fails.get(mount, 0) + 1
                if self._fails[mount] > 20:
                    log("metadata update failed for %s: %s (mount not live)" % (mount, e))
            else:
                log("metadata update failed for %s: %s" % (mount, e))
        except (URLError, OSError, ValueError) as e:
            log("metadata update failed for %s: %s" % (mount, e))

    def forget(self, mount):
        """Drop the cached title so the next update() re-pushes it."""
        self._last.pop(mount, None)


class StreamPump:
    """Binds a UDP audio pair and pipes S16_LE audio to an ffmpeg->icecast process."""
    def __init__(self, cfg, stream, icecast_cfg):
        self.cfg = cfg
        self.stream = stream
        self.name = stream["name"]
        self.udp_port = int(stream["udp_port"])
        self.mount = stream["mount"]
        self.enabled = bool(stream.get("enabled", True))
        self.channels = int(stream.get("channels", 2))
        self.bitrate = int(stream.get("bitrate_kbps", 48))
        self.proc = None
        self.last_audio = 0.0
        self.sock_a = None
        self.sock_b = None
        self.keep_running = True
        self.rx_id = None
        self.now_title = "idle"
        self.gen = 0
        self.icecast = icecast_cfg
        self._last_start = 0.0
        self._last_err = ""

    def ffmpeg_cmd(self):
        source_pw = self.icecast["source_password"]
        host = self.icecast["host"]
        port = self.icecast["port"]
        ac = self.channels
        url = "icecast://source:%s@%s:%s%s" % (urllib.parse.quote(source_pw), host, port, self.mount)
        codec = self.stream.get("codec", "mp3")
        if codec == "mp3":
            return [
                FFMPEG, "-loglevel", "warning",
                "-f", "s16le", "-ar", "8000", "-ac", str(ac), "-i", "pipe:0",
                "-c:a", "libmp3lame", "-b:a", "%dk" % self.bitrate,
                "-content_type", "audio/mpeg", "-f", "mp3", url,
            ]
        elif codec == "aac":
            return [
                FFMPEG, "-loglevel", "warning",
                "-f", "s16le", "-ar", "8000", "-ac", str(ac), "-i", "pipe:0",
                "-c:a", "aac", "-b:a", "%dk" % self.bitrate,
                "-content_type", "audio/aac", "-f", "adts", url,
            ]
        return None

    def start_ffmpeg(self):
        cmd = self.ffmpeg_cmd()
        if not cmd:
            log("stream %s: unsupported codec" % self.name)
            return None
        self._last_start = time.time()
        self.gen += 1
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        log("stream %s: ffmpeg started -> icecast %s%s" % (self.name, self.icecast["host"], self.mount))
        return self.proc

    def _drain_stderr(self):
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            data = proc.stderr.read()
        except OSError:
            return
        if data:
            self._last_err = data.decode(errors="replace")[-400:]

    def bind_sockets(self):
        self.sock_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_a.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_a.setblocking(0)
        self.sock_a.bind(("0.0.0.0", self.udp_port))
        self.sock_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_b.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_b.setblocking(0)
        self.sock_b.bind(("0.0.0.0", self.udp_port + 1))
        log("stream %s: listening udp %d/%d" % (self.name, self.udp_port, self.udp_port + 1))

    def silence(self):
        n = int(self.channels) * AUDIO_FRAME * 2
        return bytes(n)

    def run(self):
        self.bind_sockets()
        while self.keep_running:
            if self.proc is None or self.proc.poll() is not None:
                if self.proc is not None and self.proc.poll() is not None:
                    err = self._last_err or "no stderr captured"
                    log("stream %s: ffmpeg exited (%d): %s" % (self.name, self.proc.returncode, err))
                self.start_ffmpeg()
                if self.proc is None:
                    time.sleep(2.0)
                    continue
                if self.proc.poll() is not None:
                    time.sleep(2.0)

            try:
                readable, _, _ = select.select([self.sock_a, self.sock_b], [], [], SILENCE_INTERVAL)
            except (OSError, ValueError):
                time.sleep(0.1)
                continue

            buf_a = buf_b = None
            flag_a = flag_b = None
            if self.sock_a in readable:
                try:
                    d, _ = self.sock_a.recvfrom(4096)
                    if len(d) == 2:
                        flag_a = int.from_bytes(d, "little", signed=True)
                    else:
                        buf_a = d
                except OSError:
                    pass
            if self.sock_b in readable:
                try:
                    d, _ = self.sock_b.recvfrom(4096)
                    if len(d) == 2:
                        flag_b = int.from_bytes(d, "little", signed=True)
                    else:
                        buf_b = d
                except OSError:
                    pass

            # flags: 0 = drain (flush, end of transmission), 1 = drop
            if flag_a == 0 or flag_b == 0:
                self._write_flush()
                continue
            if (flag_a == 1 and flag_b == 1) or (flag_a == 1 and buf_b is None) or (flag_b == 1 and buf_a is None):
                continue  # drop queued audio

            if buf_a is None and buf_b is None:
                self._write(self.silence())
            else:
                self._write(self.interleave(buf_a, buf_b))

        self._close()

    def interleave(self, a, b):
        if a is None and b is None:
            return self.silence()
        if self.channels == 1:
            return a if a is not None else b
        import array
        arr = array.array("h")
        if a is not None and b is not None:
            sa = array.array("h"); sa.frombytes(a)
            sb = array.array("h"); sb.frombytes(b)
            n = min(len(sa), len(sb))
            for i in range(n):
                arr.append(sa[i]); arr.append(sb[i])
        else:
            src = a if a is not None else b
            arr.frombytes(src)
            # mirror single-channel audio onto both stereo channels
            sa = array.array("h"); sa.frombytes(src)
            arr = array.array("h")
            for s in sa:
                arr.append(s); arr.append(s)
        return arr.tobytes()

    def _write(self, data):
        try:
            if self.proc is not None and self.proc.poll() is None and self.proc.stdin:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
                self.last_audio = time.time()
        except (BrokenPipeError, OSError, ValueError):
            try:
                if self.proc:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None

    def _write_flush(self):
        try:
            if self.proc is not None and self.proc.poll() is None and self.proc.stdin:
                self.proc.stdin.flush()
        except Exception:
            pass

    def _close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2.0)
            except Exception:
                self.proc.kill()
        for s in (self.sock_a, self.sock_b):
            try:
                s.close()
            except Exception:
                pass


class StreamManager:
    def __init__(self):
        self.cfg = load_json("cfg.json")
        self.stream_cfg = load_json("stream.json")
        self.icecast = self.stream_cfg["icecast"]
        self.streams = [StreamPump(self.cfg, s, self.icecast) for s in self.stream_cfg.get("streams", [])]
        self.meta = IcecastMetaUpdater(self.icecast["host"], self.icecast["port"], self.icecast["admin_password"])
        # map stream name -> mount, and udp_port -> stream
        self.port_to_stream = {}
        self.name_to_mount = {}
        for s in self.streams:
            self.port_to_stream[s.udp_port] = s
            self.name_to_mount[s.name] = s.mount
        self.term_port = self._terminal_port()
        self._channel_index_by_port = self._map_channels()
        self.last_channel_update = {}
        self.last_trunk_update = {}
        self._meta_gen = {}

    def _terminal_port(self):
        t = self.cfg.get("terminal", {}).get("terminal_type", "5600")
        if isinstance(t, (int, float)):
            return int(t)
        m = re.match(r"^(\d+)", str(t))
        return int(m.group(1)) if m else 5600

    def _map_channels(self):
        """Map op25 channel destination udp port -> channel index (msgq_id)."""
        mapping = {}
        for i, ch in enumerate(self.cfg.get("channels", [])):
            dest = ch.get("destination", "")
            m = re.search(r"udp://[^:]+:(\d+)", dest)
            if m:
                mapping[int(m.group(1))] = i
        return mapping

    def on_telemetry(self, updates):
        if not isinstance(updates, list):
            return
        for item in updates:
            if not isinstance(item, dict):
                continue
            jt = item.get("json_type")
            if jt == "channel_update":
                self.last_channel_update = item
            elif jt == "trunk_update":
                self.last_trunk_update = item
        self._update_metadata()

    def _fmt_freq(self, hz):
        if not hz:
            return ""
        return "%d.%04d" % (hz // 1000000, (hz % 1000000) // 100)

    def _system_name_for_rx(self, rx_id):
        chans = self.last_channel_update.get("channels", [])
        if str(rx_id) in self.last_channel_update:
            d = self.last_channel_update[str(rx_id)]
            return d.get("system", "")
        return ""

    def _title_for_stream(self, pump):
        ch = self.last_channel_update.get(str(pump.rx_id)) if pump.rx_id is not None else None
        if not ch:
            return "idle"
        tgid = ch.get("tgid")
        if not tgid:
            return "idle"
        tag = ch.get("tag") or ""
        src = ch.get("srcaddr") or 0
        srctag = ch.get("srctag") or ""
        enc = " [ENC]" if ch.get("encrypted") else ""
        emg = " [EMERGENCY]" if ch.get("emergency") else ""
        freq = self._fmt_freq(ch.get("freq"))
        parts = ["TG %s" % tgid]
        if tag:
            parts.append(tag)
        if src:
            parts.append("Unit %s%s" % (src, (" " + srctag) if srctag else ""))
        if freq:
            parts.append(freq)
        return " ".join(parts) + enc + emg

    def _update_metadata(self):
        # assign rx_id to each pump from the port mapping once channel_update arrives
        for port, pump in self.port_to_stream.items():
            if pump.rx_id is None and port in self._channel_index_by_port:
                pump.rx_id = self._channel_index_by_port[port]
        for pump in self.streams:
            if not pump.enabled:
                continue
            # a fresh ffmpeg source reconnects the mount, which clears the
            # icecast title; force the next push after each source generation
            if self._meta_gen.get(pump.mount) != pump.gen:
                self.meta.forget(pump.mount)
                self._meta_gen[pump.mount] = pump.gen
            title = self._title_for_stream(pump)
            pump.now_title = title
            self.meta.update(pump.mount, title)

    def start(self):
        for s in self.streams:
            if s.enabled:
                threading.Thread(target=s.run, daemon=True, name="pump-%s" % s.name).start()
        if any(s.enabled for s in self.streams):
            TelemetryPoller(self.term_port, self.on_telemetry).start()
            # Wait for the ffmpeg sources to connect to Icecast before pushing
            # metadata; an early push hits a mount that doesn't exist yet and
            # Icecast answers 400. wait_ready logs a warning if they never come up.
            self.meta.wait_ready([s.mount for s in self.streams if s.enabled])
            # The op25 UDP terminal answers only the single most recent client,
            # and we share it with the web control plane's poller, so telemetry
            # reaches us intermittently. Push metadata on our own timer instead
            # of only on telemetry receipt (IcecastMetaUpdater dedupes + retries).
            threading.Thread(target=self._meta_loop, daemon=True, name="meta-loop").start()
        log("stream manager started (%d streams)" % len(self.streams))
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            for s in self.streams:
                s.keep_running = False

    def _meta_loop(self):
        while True:
            try:
                self._update_metadata()
            except Exception as e:
                log("metadata loop error: %s" % e)
            time.sleep(META_INTERVAL)


if __name__ == "__main__":
    StreamManager().start()
