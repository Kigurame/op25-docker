# op25-docker

Single-container P25 trunked scanner appliance. Runs [OP25](https://github.com/boatbod/op25)
(`gr310` branch, GNU Radio 3.10) with an RTL-SDR, streams decoded audio to
[Icecast](https://icecast.org/), and exposes a web control plane for live
monitoring, call history, and configuration.

```
             ┌─────────────────────────────── container ──────────────────────────────┐
             │                                                                         │
  RTL-SDR ──▶│  op25 (multi_rx.py)  ──UDP audio──▶  stream_runner ──mp3/aac──▶  icecast │──▶ listeners (VLC etc.)
  /dev/bus/usb   │                                                                     │         │
                 │  telemetry (JSON over UDP)                                          │      web UI
                 ▼                                                                     │         │
             │  FastAPI control plane  ──reads conf/ + supervisor──▶  op25 / icecast / stream_runner │
             └─────────────────────────────────────────────────────────────────────────┘
                                        ports: 8080 web · 8000 icecast
```

## Features

- **Live scanner web UI** – P25 Phase 1/2 trunking status, active talkgroups, unit
  (radio) tags, recent call log with frequency/time, and now-playing metadata.
- **Streaming** – every channel defined in `cfg.json` gets its own Icecast mount.
  MP3 or AAC output at a configurable bitrate, silence-filled between calls so
  listeners stay connected.
- **Icecast with listener auth** – per-mount htpasswd authentication (independent
  of the web login) for sharing the feed over the LAN.
- **Config editing** – edit `cfg.json`, `stream.json`, `users.json`, `listen.json`
  and the talkgroup/radio tag tables from the web UI; saving re-renders runtime
  configs and restarts the affected service.
- **Role-based login** – `admin` (full access) and `viewer` (read-only) users,
  PBKDF2 password hashing, HMAC-signed session cookies.
- **Stream proxy** – authenticated `/stream/<mount>` endpoint on the web port for
  clients that should not talk to Icecast directly.
- **Supervisor-managed** – all four programs (op25, streams, icecast, web) are
  auto-restarted under supervisord, with logs viewable in the web UI.

## Repository layout

```
.
├── Dockerfile                  # multi-stage: builds op25 (gr310) then a slim runtime
├── docker-compose.yml          # ports, volumes, USB passthrough, healthcheck
├── render_configs.py           # renders icecast.xml / htpasswd / supervisord.conf from conf/
├── stream_runner.py            # UDP audio → ffmpeg → icecast pump + metadata updater
├── conf/                       # your editable configuration (volume-mounted)
│   ├── cfg.json                #   op25: devices, channels, trunking (P25 system)
│   ├── stream.json             #   icecast: ports, passwords, per-stream mounts
│   ├── users.json              #   control-plane accounts
│   ├── listen.json             #   icecast listener accounts
│   ├── icecast-tpl.xml         #   icecast template
│   └── tags/                   #   tgid.tsv / rid.tsv talkgroup & radio tags
├── control-plane/              # FastAPI app + SPA (static/index.html)
└── supervisor/                 # entrypoint.sh + supervisord.conf template
```

## Prerequisites

- Docker with the **Compose** plugin and **BuildKit** (`docker buildx version`
  should work). On Arch: `pacman -S docker docker-compose docker-buildx`; on
  Debian/Ubuntu install `docker-compose-plugin` and `docker-buildx-plugin`.
- Your user in the `docker` group (re-login after `usermod -aG docker $USER`).
- An RTL-SDR dongle. Only RTL-based devices are supported (see `rtl=<index>`
  device args in `cfg.json`).
- The P25 system's control channel frequency. **Software-defined radio use is
  governed by local law — only monitor systems you are authorized to receive.**

## Quick start

Clone, build, and run:

```bash
git clone https://github.com/Kigurame/op25-docker.git
cd op25-docker
docker compose up -d --build      # first build compiles op25; takes a while
```

Point the control channel at your system (`conf/cfg.json` →
`trunking.chans[0].control_channel_list`), then restart op25 from the web UI
(**Control plane → Services → restart op25**) or:

```bash
docker compose restart op25
```

Open the web UI at <http://localhost:8080> and log in with the default account:

```
username: admin
password: admin123
```

**Change this password immediately** (Control plane → Change my password) and
update the other defaults in `conf/stream.json` / `conf/listen.json` (see
Security below).

## Streaming

With everything up, each enabled stream in `conf/stream.json` is live on Icecast.
The bundled default mount:

```
http://<host>:8000/primary.mp3
username: scanner
password: listen123
```

`conf/stream.json` controls Icecast itself (source/admin/supervisor passwords,
listener auth on/off, max clients) and one or more streams:

| field | meaning |
|-------|---------|
| `udp_port` | base UDP port pair; must match the channel `destination` in `cfg.json` |
| `mount` | Icecast mount path, e.g. `/primary.mp3` |
| `codec` | `mp3` (default) or `aac` |
| `bitrate_kbps` | encoder bitrate |
| `channels` | channel count for the mixed stereo pipe |
| `icecast_name` / `icecast_description` / `icecast_genre` | stream metadata |

### Web stream proxy

If you prefer not to expose Icecast, the control plane proxies the feed
(using the configured Icecast listener credentials) at:

```
GET /stream/<mount>        # requires a logged-in web session
```

## Web control plane

SPA served at `/`; API at `/api/*`. Notable endpoints:

| endpoint | description |
|----------|-------------|
| `POST /api/login` · `POST /api/logout` · `GET /api/me` | session auth |
| `GET /api/telemetry` | live channel/trunk state + recent calls (UDP JSON from op25) |
| `GET /api/config` | read all config files |
| `PUT /api/config/{name}` | write a config file (validated; triggers service restart) |
| `POST /api/users` | add a control-plane user (`admin` only) |
| `POST /api/change-password` | rotate your own password |
| `GET /api/status` | supervisor program states |
| `POST /api/restart/{program}` | restart op25 / streams / icecast / web |
| `GET /api/log/{program}` | tail a program's log |
| `POST /api/sdr/scan` | list detected RTL-SDR devices (for setting `rtl=<index>`) |
| `GET /stream/{mount}` | authenticated Icecast proxy |
| `GET /api/health` | healthcheck for Docker |

Talkgroup / radio tagging: edit `conf/tags/tgid.tsv` and `conf/tags/rid.tsv`
(two-column `number<TAB>label`). Save from the UI and restart `op25`.

## Configuration details

### Device assignment

`cfg.json` uses `rtl=<index>`. Run **SDR → Scan for devices** in the web UI to
list detected dongles, then set `devices[].args` accordingly. USB devices must
reach the container — `docker-compose.yml` already passes
`/dev/bus/usb:/dev/bus/usb`.

### Channel ↔ stream mapping

Each enabled `stream.json` entry owns the UDP port pair `udp_port` /
`udp_port+1` (PCM audio and the two-byte call-flag datagrams). In `cfg.json`,
set the channel's `destination` to `udp://127.0.0.1:<udp_port>`, e.g.:

```json
{ "name": "primary", "destination": "udp://127.0.0.1:23456", ... }
```

`stream_runner` maps that port to the channel index reported by op25 telemetry,
so now-playing metadata follows the active talkgroup automatically.

### First-boot config

The entrypoint copies `conf/` defaults into the mounted volume only if
`stream.json` is absent, so your edits to the bind-mounted `conf/` directory
always win.

## Security notes

The default passwords are insecure and only intended to get you started:

| where | default |
|-------|---------|
| web `admin` | `admin123` |
| icecast listener `scanner` | `listen123` |
| icecast source / admin / supervisor | `changeme-source` / `changeme-admin` / `changeme-supervisor` |

Change all of them in `conf/` **before** exposing the service beyond localhost.
Control-plane passwords are PBKDF2-hashed; Icecast uses its htpasswd format
(hex MD5). If you want external access, put a reverse proxy with TLS in front of
ports 8080/8000 rather than publishing them directly.

## Troubleshooting

- **`op25` shows BACKOFF in the web UI** – most often a missing or wrong SDR
  index (`Wrong rtlsdr device index given`). Check **Control plane → Logs → op25**
  and adjust `devices[].args`. Without any USB dongle attached, op25 cannot run
  by design; the rest of the stack (web, streams, Icecast) still comes up.
- **Listeners get 401** – Icecast listener auth is on. Use the credentials from
  `conf/listen.json`, or disable auth by setting `icecast.listener_auth: false`
  in `conf/stream.json`.
- **No audio / silence** – silence is sent between calls; tune the control
  channel and check the op25 log for a successful decode. Confirm the channel
  `destination` port matches the stream `udp_port`.
- **ffmpeg repeatedly restarts in streams.log** – Icecast wasn't reachable at
  startup; the pump retries with backoff automatically.
- **USB device not visible in the container** – pass
  `--device=/dev/bus/usb:/dev/bus/usb` (already in `docker-compose.yml`) and, on
  some hosts, add `privileged: true`.

## License

This project wires together [OP25](https://github.com/boatbod/op25),
GNU Radio, Icecast and ffmpeg under Docker. OP25 and GNU Radio are GPL-licensed;
Icecast is GPL-2.0; ffmpeg is LGPL/GPL. Check each component's license terms
before redistribution. **Receiving radio traffic without authorization may be
illegal in your jurisdiction.**
