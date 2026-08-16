# Setting up op25-docker to receive

A fuller version of the [README](../README.md) quick start. If you just want
audio up and running, the README's **Quick start** plus the web UI's **Setup
wizard** is all you need. This guide explains each step, covers encrypted
traffic, and ends with an [advanced reference](#advanced-reference) for power
users who want to touch the config files directly.

## 1. Prerequisites

- An **RTL-SDR USB dongle** plugged into the host computer. The compose files
  pass USB access (`/dev/bus/usb`) into the container automatically.
- Your P25 system's **parameters** — you need at least:
  - the **control channel frequency** (in MHz),
  - the **NAC** (Network Access Code, in hex),
  - whether the system is **Phase 1 or Phase 2 TDMA**,
  - the **band** (700/800 MHz vs VHF/UHF).

Where to find these:

- [RadioReference.com](https://www.radioreference.com) (US) — look up the
  system; control channel(s) and band are listed, NAC is often in the notes.
- Local scanner forums / your area's frequency coordinator.
- Discover them yourself with **SDRTrunk**, **Unitrunker**, or **gqrx**: sit on
  a known trunking band and read the control channel / NAC from the decode.

> **Legal note:** software-defined radio use is governed by local law. Only
> monitor systems you are authorized to receive.

## 2. Deploy

Either pull the published image (recommended) or build from source — see the
[README quick start](../README.md). Then:

```bash
docker compose up -d
docker compose ps          # container should be Up
```

Open the web UI at <http://localhost:8080>. On first boot the entrypoint
generates a random admin password and prints it to the container log — grab it
with:

```bash
docker compose logs op25 | grep -i password
```

Sign in as `admin` with that password, then **change it immediately** (Config
tab → *Change my password*). There is no default `admin123`; the template hash
in `conf/users.json` is only a placeholder and is replaced on first boot.

On first boot the entrypoint seeds `./conf` with default configs; always edit
those files, never the image internals.

## 3. Verify the SDR is visible

In the web UI go to **SDR → Scan for devices**. It lists detected dongles with
their 0-based index (0, 1, ...). Note your dongle's index.

- No device found? Check `docker compose ps`, replug the dongle, and if the
  host needs it add `privileged: true` to the service in `docker-compose.yml`.
  Confirm the host itself sees the dongle with `lsusb`.

## 4. Configure via the setup wizard (recommended)

**Config → Set up my scanner** is the no-JSON path:

1. **SDR device index** — click *Scan for devices* to fill it in, or type the
   index from step 3. Leave PPM at 0 and gain at the default to start.
2. **System** — enter the system name (any label), the control channel
   frequency in MHz, the band, the NAC, and check *Phase 2 TDMA* if your system
   uses it. Optional: alternate control channels (comma-separated MHz) so the
   receiver can hunt to a backup channel if the primary is down.
3. **Advanced** (optional) — whitelist/blacklist talkgroups.
4. **Review** — check the generated JSON, then **Apply & restart**.

The wizard writes `cfg.json` for you and restarts op25. Skip straight to
[step 7](#7-save-and-verify).

## 5. Encrypted talkgroups (optional)

By default the container **ignores encrypted traffic** — encrypted talkgroups
are never tuned, cost no CPU, and produce no audio. To listen to encrypted
calls you must supply an op25 keys file:

1. Create `conf/keys.json` in the op25
   [keys format](https://github.com/boatbod/op25/blob/gr310/op25/gr-op25_repeater/apps/example_keys.json):
   ```json
   { "0x1b50": { "algid": "0xaa", "key": ["0x12", "0x34", "0x56", "0x78", "0x90"] } }
   ```
   `algid` is `0xaa` (ADP), `0x81` (DES-OFB) or `0x84` (AES-OFB); `key` is the
   binary key as a byte array.
2. Set `channels[0].crypt_keys` to `/opt/op25/conf/keys.json`.
3. Save. `crypt_behavior` is managed automatically — `2` (skip) while no keys
   file is present, `0` (allow/decrypt) once a keys file exists. You can only
   decrypt what you have keys for.

## 6. Streams and listening

Each enabled entry in `stream.json` broadcasts one audio feed (mount). The
default stream is:

```
http://<host>:8000/primary.mp3      username: scanner   password: listen123
```

- The **Live** tab has **Listen** (open in a new tab) and **Copy URL**
  (ready-to-paste link with credentials for any external player: VLC, mpv, …).
- Set `icecast.listener_auth` to `false` in `stream.json` to open the feed with
  no password.
- Change `streams[].mount` (URL path), `codec` (`mp3` or `aac`) and
  `bitrate_kbps` (quality) to taste.
- Change the Icecast passwords (`source` / `admin` / `supervisor`) in
  `stream.json` from their `changeme-*` defaults.
- **Different public port?** The container's Icecast always listens on port
  8000 internally, but if you map it to another port on the outside (e.g.
  `8000:9000` in `docker-compose.yml`), set `icecast.external_port` to that
  public port in `stream.json` so the **Listen** / **Copy URL** links in the
  web UI point at it. It only affects the displayed link — the actual stream
  config is unchanged.

The control plane also proxies the feed at `GET /stream/<mount>` for logged-in
programmatic clients that shouldn't talk to Icecast directly.

## 7. Save and verify

Saving a config in the web UI re-renders runtime configs and restarts the
affected services automatically. Then:

1. **Status → Services** — `op25`, `streams`, `icecast`, `web` should all be
   RUNNING. (op25 sits in BACKOFF/FATAL if there is no SDR or the config is
   wrong — see Troubleshooting.)
2. **Status → Logs → op25** — with the correct control channel and NAC you'll
   see trunking activity / talkgroup grants. A `Wrong rtlsdr device index`
   error means the device index in the wizard is wrong.
3. **Live tab** — after traffic starts you'll see the active system, talkgroup
   and now-playing metadata, plus a call log.
4. Click **Listen** on a stream card (or open
   `http://<host>:8000/primary.mp3`).

## 8. Troubleshooting

| symptom | likely cause / fix |
|---------|---------------------|
| `op25` shows BACKOFF / no device | no dongle reachable, or wrong SDR index. Scan in the SDR tab and fix it in the wizard; add `privileged: true` if USB is flaky. |
| No decode / "invalid" on the control channel | wrong control channel frequency or NAC. Confirm both; try adding alternate control channels. |
| No tuning / not sure why | **Status → op25 diagnostics**: set log level 9 and watch the op25 log for `Tuning to frequency` / `attempt to assign control channel receiver`; click *Dump decoded talkgroups to log* (empty = no decode). A steady `conv process_qmsg: type(-1)` at level 5 means the channel is stuck in conventional mode (missing `trunking_sysname`). **SDR → Dongle diagnostics**: `PLL not locked!` / `r82xx_set_freq: failed` means the tuner can't lock (USB power / hardware). |
| Stream connects but silent | channel `destination` port doesn't match `streams[].udp_port`; stream `enabled: false`; or listener auth is blocking you. |
| Audio garbled / drifts over time | tune `devices[].ppm` (crystal error). Start near 0 and adjust a few ppm at a time until decode is clean. |
| Only encrypted talkgroups | full-time encryption. The container ignores these by default; see step 5. |
| Frequency in logs looks off | `frequency` / `center_frequency` are **Hz**; `control_channel_list` is **MHz**. |
| Save doesn't seem to apply | config validation failed — the web UI shows the error. |
| ffmpeg repeatedly restarts in streams.log | Icecast wasn't reachable at startup; the pump retries with backoff automatically. |
| Web UI shows `[object Object]` | your browser is serving a cached copy — hard-refresh (Ctrl+F5). After updating the repo, rebuild with `docker compose up -d --build`. |

## 9. Upgrading an existing container

Upgrades never touch your data: the entrypoint seeds `./conf` only on true
first boot (when `stream.json` is missing), so your configs, icecast passwords
and user accounts survive untouched.

```bash
docker compose build && docker compose up -d
# or, for the prebuilt image:
docker compose pull && docker compose up -d
```

Two things to check after upgrading:

- **`OP25_SESSION_SECRET` set to the old default
  `op25-docker-insecure-secret-change-me`** — the control plane refuses to
  start with that value. Remove it or set a real one before restarting
  (`export OP25_SESSION_SECRET="$(openssl rand -hex 32)"`). If you never set
  it, the container generates one each start, which signs everyone out (see
  [Users & session secret](#users--session-secret)).
- **Still on the old `admin` / `admin123` template hash** — that login keeps
  working after an upgrade because the password is only randomized when the
  volume is seeded fresh. Log in and rotate it via **Config → *Change my
  password*** once you've upgraded.

## Advanced reference

> For power users editing the config files directly. Most people never need
> this — the setup wizard covers it. The shipped defaults double as templates:
> each JSON file under `conf/` has a `_template_notes` key explaining what to
> change.

### Repository layout

```
.
├── Dockerfile                  # multi-stage: builds op25 (gr310) then a slim runtime
├── docker-compose.yml          # ports, volumes, USB passthrough, healthcheck (builds locally)
├── examples/docker-compose.yml # run the published Docker Hub image
├── docs/SETUP.md               # this guide
├── render_configs.py           # renders icecast.xml / htpasswd / supervisord.conf from conf/
├── set_admin_password.py       # first-boot helper: replaces the admin password with a random one
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

### `cfg.json` field reference

| field | what to set |
|-------|-------------|
| `devices[0].args` | `rtl=<index>` — dongle index from the SDR scan, e.g. `rtl=0,rtl` |
| `devices[0].ppm` | crystal correction; `0` to start, tune later |
| `devices[0].frequency` | your control channel in **Hz**, e.g. `856000000` |
| `channels[0].frequency` | the same control channel in **Hz** |
| `channels[0].destination` | `udp://127.0.0.1:<udp_port>` — must match the stream in `stream.json` (default `23456`) |
| `channels[0].trunking_sysname` | must equal `trunking.chans[0].sysname` — links the channel to the trunking system. Missing this = conventional mode, no CC decode |
| `channels[0].crypt_keys` | empty by default (encrypted traffic ignored). Set to a keys file path to decrypt |
| `trunking.chans[0].sysname` | any label for your system |
| `trunking.chans[0].control_channel_list` | control channel in **MHz**, e.g. `"856.0000"` (comma-separate alternates for CC hunting) |
| `trunking.chans[0].nac` | system NAC, e.g. `"0x4a2"` |
| `trunking.chans[0].sysid` / `.wacn` | optional; learned from the control channel if empty |
| `trunking.chans[0].phase2_tdma` | `1` for Phase 2 TDMA, `0` for Phase 1 only |
| `trunking.chans[0].modulation` | `cqpsk` (700/800 MHz) or `fm` (VHF/UHF) |
| `trunking.chans[0].whitelist` / `.blacklist` | comma-separated talkgroup IDs to restrict/block decoding |
| `verbosity` | log verbosity 0–11 (see table below) |

The config editor and setup wizard set `trunking_sysname` and `destination`
automatically; saving a `cfg.json` with a missing or mismatched
`trunking_sysname` is rejected with a clear error.

### `stream.json` field reference

| field | meaning |
|-------|---------|
| `udp_port` | base UDP port pair; must match the channel `destination` in `cfg.json` |
| `mount` | Icecast mount path, e.g. `/primary.mp3` |
| `codec` | `mp3` (default) or `aac` |
| `bitrate_kbps` | encoder bitrate |
| `channels` | channel count for the mixed stereo pipe |
| `gain_db` | volume boost in dB (-30..30, 0 = off) |
| `icecast_name` / `icecast_description` / `icecast_genre` | stream metadata |
| `icecast.listener_auth` | `true` = listeners must use a `listen.json` account; `false` = open feed |
| `icecast.external_port` | optional — the public port listeners use *outside* the container if you map `icecast.port` to something else (e.g. `8000:9000`). Only changes the Listen/Copy URL links, not the actual stream |
| `icecast.source/admin/supervisor_password` | Icecast credentials — change from `changeme-*` defaults |

### Channel ↔ stream mapping

Each enabled `stream.json` entry owns the UDP port pair `udp_port` /
`udp_port+1` (PCM audio and the two-byte call-flag datagrams). In `cfg.json`,
set the channel's `destination` to `udp://127.0.0.1:<udp_port>`. `stream_runner`
maps that port to the channel index reported by op25 telemetry, so now-playing
metadata follows the active talkgroup automatically.

### Log verbosity

`cfg.json` top-level `verbosity` (0–11, default 2) controls how much op25
writes to `op25.log`. Change it live from **Status → op25 diagnostics** — no
restart needed — or set it in `cfg.json` as the startup default.

| level | shows |
|-------|-------|
| 0 | errors only |
| 1 | startup, tuning failures (`Unable to tune ...`) |
| 2 | tag-file loading |
| 5 | call/conventional activity, talkgroup counters |
| 9 | every hardware tune step (`Tuning to frequency ...`, `Hardware tune ...`) |
| 10 | full channel-control trace (very spammy) |
| 11 | every decoded trunking signaling block (grants, patches, affiliations, registrations, band plans) — feeds the **Status → TSBK activity** panel |

The **TSBK detail** toggle (**Status → op25 diagnostics**) sets level 11 live
*and* persists it in `cfg.json`.

### Web control plane API

SPA served at `/`; API at `/api/*`:

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
| `POST /api/sdr/diag/{index}` | deep-diagnose one dongle (`rtl_test`): tuner lock, sample stream, noise floor |
| `GET /api/op25/debug` | configured op25 verbosity + the available levels |
| `POST /api/op25/debug/{level}` | change op25's live log verbosity (0–11) without a restart |
| `POST /api/op25/dump-tgids` | print all decoded talkgroups (with counters) to the op25 log |
| `GET /api/op25/tsbk` | whether TSBK detail (level 11) is currently enabled |
| `POST /api/op25/tsbk` | toggle TSBK detail and persist it in `cfg.json` (`{"enabled": true}`) |
| `GET /api/op25/tsbk/feed` | recent non-voice signaling lines from the op25 log |
| `GET /stream/{mount}` | authenticated Icecast proxy (programmatic clients) |
| `GET /api/health` | healthcheck for Docker |

### First-boot config

The entrypoint copies `conf/` defaults into the mounted volume only if
`stream.json` is absent, so your edits to the bind-mounted `conf/` directory
always win. When it does seed the volume it also calls `set_admin_password.py`,
which replaces the template `admin` password hash with a random password and
prints the credentials to the container log. Fresh installs therefore have no
known default login.

### Users & session secret

- `conf/users.json` holds the web control-plane accounts (`role`: `admin` /
  `viewer`) and `session_ttl_hours` (default 12). Add users from the UI
  (**Config → Add user**), change your own password with **Config → Change my
  password**.
- Logins are HMAC-signed session cookies. Set `OP25_SESSION_SECRET` (see the
  [README security section](../README.md#security)) so sessions survive
  restarts:
  ```bash
  export OP25_SESSION_SECRET="$(openssl rand -hex 32)"
  docker compose up -d
  ```
  If it's unset, a random secret is generated on each start and every restart
  signs everyone out. The control plane refuses to start with the known
  insecure default `op25-docker-insecure-secret-change-me`.

### Tags

Edit `conf/tags/tgid.tsv` (talkgroups) and `conf/tags/rid.tsv` (radios) as
`number<TAB>label`; save from the UI and restart `op25` to show names instead
of numbers. A third `TAB`-separated field in `tgid.tsv` sets a priority 0–9.
