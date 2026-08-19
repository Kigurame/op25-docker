# The big fat disclaimer of doom

This project was made with the help of an LLM initially just out of curiosity
Just to see how far current models can take things. But because the end result
was far more usable than initially imagined. I decided to set it free for anyone 
To use or build upon as a base for their own project.

**So please treat this for what it is**

# No vulnerability testing was done on this project so for all that is holy do not expose it to the internet.

# op25-docker

A **police scanner you run on your own computer.** Plug in a cheap USB radio
dongle (RTL-SDR), run one command, and listen to your local P25 digital police /
fire / EMS radio traffic from any browser on your network — no hardware scanner
needed.

Everything runs in a single Docker container: it tunes the radio to your area's
**control channel**, decodes the digital trunking system, and streams the audio
to a built-in web page.

```
  USB radio dongle ──▶ op25-docker container ──▶ web page at http://<your-pc>:8080
```

> **Legal note:** software-defined radio use is governed by local law. Only
> monitor radio traffic you are authorized to receive.

---

## What you need

- A computer running **Docker** (any modern Windows, macOS, or Linux PC).
- An **RTL-SDR USB radio dongle** (~$20–30 online; get the one with the metal
  case and proper antenna — it makes a big difference).
- **One piece of information about your local system:** the *control channel
  frequency* and the *NAC*. See [Finding your system's settings](#finding-your-systems-settings)
  below — it takes two minutes on a phone.

## Quick start (about 5 minutes)

**1. Create a folder and pull the ready-made image**

Open a terminal and run:

```bash
mkdir op25 && cd op25
curl -o docker-compose.yml https://raw.githubusercontent.com/Kigurame/op25-docker/main/examples/docker-compose.yml
docker compose up -d
```

Plug your USB radio dongle in first. Docker downloads the prebuilt image
(`kigurame2/op25-docker`).

**2. Open the web page**

Browse to `http://localhost:8080`. On first boot the container generates a
random admin password and prints it to the log — find it with:

```bash
docker compose logs op25 | grep -i password
```

Sign in with username `admin` and that password. There is no default
`admin123`; the shipped template hash is replaced on first boot. **Change the
password after signing in** (Config → *Change my password*) and set
`OP25_SESSION_SECRET` — see [Security](#security).

**3. Tell it about your radio and your local system**

Click **Config → Set up my scanner** and answer three things:

- **SDR device index** — click *Scan for devices* and it fills this in for you
  (your dongle is almost always device 0).
- **Control channel frequency (MHz)** — e.g. `856.1625`. See
  [Finding your system's settings](#finding-your-systems-settings).
- **NAC and band** — a short code like `0x4a2` and whether it's a 700/800 MHz
  or VHF/UHF system.

Click **Apply & restart**. The container restarts the radio with your settings.

**4. Listen**

Go to the **Live** tab and click **Listen** on a stream card — the audio opens
in your browser. Or use the **Copy URL** button and paste it into VLC / mpv /
any media player so you can listen on your phone across the house.

That's it. If you don't hear anything, the [Troubleshooting](#troubleshooting)
section covers the usual culprits.

## Finding your system's settings

The two numbers you need describe the digital radio system your local police /
fire departments use. They are **not** secrets, but you do need to look them up:

- **[RadioReference.com](https://www.radioreference.com)** (covers the US) —
  search your county or city. Open the system, and you'll see a "Trunked System"
  entry listing the **control channel(s)** (a frequency like `856.1625`) and the
  **NAC** (a short hex code like `0x4a2`) in the notes. The system page also
  says whether it's **Phase 1**, **Phase 2**, or both, and the band.
- **Your area's scanner community** — local forums and Facebook groups for
  scanner enthusiasts almost always have the control channels posted.
- **Discover them yourself** — free tools like *SDRTrunk* or *Unitrunker* can
  decode the system for you: just tune to the band and read off the control
  channel and NAC.

**Plain-English glossary** of the wizard's terms:

| term | what it means |
|------|---------------|
| **Control channel** | The one frequency the whole system uses to assign calls. The scanner locks onto it and follows along. |
| **NAC** | A short code (like `0x4a2`) that identifies the system. Think of it as the system's name in digital form. |
| **Band** | The chunk of radio spectrum: **700/800 MHz** (most US trunked systems) or **VHF/UHF** (many rural and smaller systems). |
| **Phase 2 TDMA** | A newer digital mode. Check the "no, Phase 1" / "yes, Phase 2" box if the system description mentions Phase 2. |
| **PPM correction** | Crystal drift compensation. Leave it at 0 unless the audio starts garbling after hours of use. |

## What the web page does

| tab | what it's for |
|-----|---------------|
| **Live** | What's happening right now: active talkgroups, who's transmitting, and stream cards with **Listen** / **Copy URL** so you can play the audio. Also a running **call log** showing every recent transmission. |
| **Config** | The **Set up my scanner** wizard (step 3 above) and, for tinkering, the raw config files. |
| **SDR** | Scan for your USB dongle and run a quick health check on it if audio is broken. |
| **Status** | Services and logs. If something is wrong, this is where you look — the op25 log and the *Dump decoded talkgroups* button tell you if the radio is receiving. |

## Streaming to other devices

The container also broadcasts audio like a mini radio station on port **8000**.
For example, to listen in VLC or on your phone:

```
http://<your-pc-ip>:8000/primary.mp3
username: scanner
password: listen123
```

The **Live** tab's **Copy URL** button gives you a ready-to-paste link
(credentials included) for any stream. Want no password? Set
`icecast.listener_auth` to `false` in `stream.json`.

**Exposed the stream on a different port?** The container's Icecast always
listens on port **8000** internally, but you might map it to another port on
the outside of the container (for example `8000:9000` in your
`docker-compose.yml`). If so, set `icecast.external_port` to the public port
(e.g. `9000`) in `stream.json` — the **Listen** and **Copy URL** links will
then point at the right port. Nothing else changes.

## Home Assistant integration

op25-docker integrates with Home Assistant via **Music Assistant** (audio
playback) and an optional **MQTT bridge** (sensor entities with auto-discovery).

```
  op25 container ──▶ Icecast (port 8000)
        │                  │
        │           Music Assistant fetches stream
        │                  │
        ▼                  ▼
  MQTT bridge ──▶ HA sensors (talkgroups, calls, status)
                         │
                         ▼
                  HA media players / dashboard
```

### Music Assistant (audio playback)

Music Assistant bridges op25's audio to any HA media player — Chromecast,
Sonos, AirPlay speakers, and more. It handles transcoding and multi-room sync.

**1. Add the Music Assistant container**

The `docker-compose.yml` already includes a `music-assistant` service. Pull and
start it:

```bash
docker compose pull
docker compose up -d music-assistant
```

**2. Connect MA to Home Assistant**

In HA: **Settings → Devices & Services → Add Integration → Music Assistant**.
Enter the MA server URL: `http://<your-host-ip>:8095`.

**3. Add op25 as a radio station**

Open the MA web UI at `http://<your-host-ip>:8095`. Go to **Radio** and add a
new station with the Icecast URL:

```
http://<your-pc-ip>:8000/primary.mp3
```

**4. Play on any speaker**

Use HA automations or the media browser to play the scanner on any connected
speaker:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.living_room
data:
  media_content_id: "http://<your-pc-ip>:8000/primary.mp3"
  media_content_type: music
```

### MQTT bridge (HA sensor entities)

The MQTT bridge publishes scanner data to your MQTT broker. Home Assistant
auto-discovers the entities — no manual configuration needed.

**1. Enable the bridge**

Uncomment and set the `OP25_MQTT_*` environment variables in
`docker-compose.yml`:

```yaml
environment:
  - OP25_MQTT_HOST=your-broker-host
  - OP25_MQTT_PORT=1883       # optional, default 1883
  - OP25_MQTT_USER=your-user  # optional
  - OP25_MQTT_PASS=your-pass  # optional
  - OP25_MQTT_PREFIX=op25     # optional, default "op25"
```

Restart the container:

```bash
docker compose up -d op25
```

**2. Auto-discovered entities in HA**

Once enabled, HA automatically creates these entities:

| Entity | Type | What it shows |
|--------|------|---------------|
| `binary_sensor.op25_op25` | Service health | RUNNING / STOPPED |
| `binary_sensor.op25_icecast` | Service health | RUNNING / STOPPED |
| `binary_sensor.op25_streams` | Service health | RUNNING / STOPPED |
| `binary_sensor.op25_web` | Service health | RUNNING / STOPPED |
| `sensor.op25_active_talkgroup` | Current talkgroup | Name or ID of active talkgroup |
| `sensor.op25_active_source` | Current source | Radio ID transmitting |
| `sensor.op25_active_frequency` | Current frequency | MHz of active channel |
| `sensor.op25_call_count` | Recent calls | Number of decoded calls |
| `sensor.op25_sdr_status` | SDR status | OK / Error |

**3. Example automation**

Trigger an alert when a specific talkgroup becomes active:

```yaml
trigger:
  - platform: state
    entity_id: sensor.op25_active_talkgroup
    to: "Fire Dispatch"
action:
  - service: notify.mobile_app
    data:
      message: "Active on Fire Dispatch"
```

## Nice extras

- **Name your talkgroups and radios** — add labels in **Config → tgid.tsv**
  (talkgroups) and **rid.tsv** (radios), one `ID<TAB>Label` per line, save, and
  the Live tab shows names instead of numbers.
- **Ignore or decode encrypted traffic** — encrypted talkgroups are skipped
  automatically. If you have the keys, drop a keys file into `conf/` and the
  container will decode them; see [docs/SETUP.md](docs/SETUP.md).
- **TSBK activity** — a Status-tab toggle that shows every signaling message
  the system sends (grants, affiliations, registration) live, for power users
  who want to watch the trunking "conversation".

## Security

A few defaults exist so you can start immediately — but they are only safe on
your own PC. Change them before exposing the service beyond that:

| where | default |
|-------|---------|
| web login (admin) | **generated at first boot** — printed to `docker compose logs op25 \| grep -i password`. Change it in **Config → Change my password** |
| listener login | `listen123` — change in `listen.json` |
| Icecast internal passwords | `changeme-*` — change in `stream.json` |

**Session secret.** Login sessions are HMAC-signed with `OP25_SESSION_SECRET`.
Set it once in your shell before starting so sessions survive restarts:

```bash
export OP25_SESSION_SECRET="$(openssl rand -hex 32)"
docker compose up -d
```

If it is left unset the container generates a random secret on each start
(sessions reset on every restart). Never set it to the old known-default value
`op25-docker-insecure-secret-change-me` — the control plane refuses to start
with that, because tokens signed with it are forgeable by anyone who reads this
repo.

Prefer not to publish ports 8080/8000 directly to the internet; run this behind
a reverse proxy with HTTPS if you want to listen from anywhere.

## Upgrading an existing container

Upgrading keeps all your data: the entrypoint only touches `./conf` on true
first boot, so `cfg.json`, `stream.json`, `listen.json`, and your user accounts
are left exactly as they are.

```bash
docker compose build && docker compose up -d
# or, for the prebuilt image:
docker compose pull && docker compose up -d
```

Two things to check after upgrading:

- **If you set `OP25_SESSION_SECRET` to the old default
  `op25-docker-insecure-secret-change-me`**, the container will refuse to
  start. Remove it or set a real one before restarting
  (`export OP25_SESSION_SECRET="$(openssl rand -hex 32)"`). If you never set
  it, nothing to do — the container generates one each start, which signs
  everyone out (see [Security](#security)).
- **If you never changed the admin password**, your `users.json` still holds
  the old template hash, so `admin` / `admin123` still works after upgrading
  (the generated-password protection only applies to fresh installs). Log in
  and change it via **Config → *Change my password***.

## Troubleshooting

| symptom | what's usually wrong / what to do |
|---------|-----------------------------------|
| Nothing loads, or op25 shows BACKOFF | The USB dongle isn't reachable. Replug it, run **SDR → Scan for devices**, and pick the right device index in the wizard. |
| Web page loads but no audio | The control channel or NAC is wrong. Double-check the frequency and NAC in the wizard — the two most common mistakes. See *Finding your system's settings*. |
| Audio garbles / drifts after a while | Tiny crystal drift. Bump **PPM correction** by a few (try 10–30) in the wizard until it stays clean. |
| Stream asks for a password | That's expected: `scanner` / `listen123` by default (change it in `listen.json`), or turn auth off in `stream.json`. |
| Still stuck? | **Status → op25 diagnostics** has two buttons that tell you exactly what the radio is hearing: set log level 9 and watch the op25 log, or click *Dump decoded talkgroups to log*. |

## Building from source (optional)

Most people use the prebuilt image above. Developers and tinkerers can build it
locally — the first build compiles the radio decoder from source and takes a
while:

```bash
git clone https://github.com/Kigurame/op25-docker.git
cd op25-docker
docker compose up -d --build
```

## More documentation

- **[docs/SETUP.md](docs/SETUP.md)** — a fuller step-by-step guide (including
  encrypted traffic and advanced troubleshooting).
- **[docs/SETUP.md#advanced-reference](docs/SETUP.md#advanced-reference)** —
  the technical reference: every config field, the control API, log-verbosity
  levels, and repository layout.

## License

This project wires together [OP25](https://github.com/boatbod/op25),
GNU Radio, Icecast and ffmpeg under Docker. OP25 and GNU Radio are GPL-licensed;
Icecast is GPL-2.0; ffmpeg is LGPL/GPL. Check each component's license terms
before redistribution. **Receiving radio traffic without authorization may be
illegal in your jurisdiction.**
