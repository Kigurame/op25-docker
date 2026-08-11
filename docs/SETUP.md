# Setting up op25-docker to receive

This guide takes you from a freshly pulled container to live P25 audio on
Icecast. The shipped default configs double as templates: each JSON file under
`conf/` has a `_template_notes` key explaining what to change, and the web UI
lets you edit all of them (saving re-renders runtime configs and restarts the
affected service automatically).

## 1. Prerequisites

- An **RTL-SDR dongle** plugged into the host. The compose files already pass
  `/dev/bus/usb` into the container.
- Your P25 system's **parameters**. You need at least:
  - the **control channel frequency** (in MHz),
  - the **NAC** (Network Access Code, in hex),
  - whether the system uses **Phase 1 or Phase 2 TDMA**,
  - the **band** — this decides `modulation`: `cqpsk` for 700/800 MHz,
    `fm` for VHF/UHF systems.

Where to find these:

- [RadioReference.com](https://www.radioreference.com) (US) — look up the
  system; the control channel(s) and band are listed, NAC is often in the
  notes.
- Local scanner forums / your area's frequency coordinator.
- If you don't know them, discover them with a tool like **SDRTrunk**,
  **Unitrunker**, or **gqrx**: sit on a known trunking frequency band and read
  the control channel / NAC from the decode output.

> **Legal note:** software-defined radio use is governed by local law. Only
> monitor systems you are authorized to receive.

## 2. Deploy

Follow the quick start in the [README](../README.md) (pull the published image
or build from source), then:

```bash
docker compose up -d
docker compose ps          # container should be Up
```

Open the web UI at <http://localhost:8080> and log in (`admin` / `admin123`).
**Change that password immediately** (Control plane → Change my password).

On first boot the entrypoint seeds `./conf` with the default configs; edit
those files, not the image internals.

## 3. Verify the SDR is visible

In the web UI go to **SDR → Scan for devices**. It lists the detected dongles
with their **0-based device index** (0, 1, ...). Note which index your dongle
is.

- If no device is found: check `docker compose ps`, replug the dongle, and if
  the host needs it, add `privileged: true` to the service in
  `docker-compose.yml`. Confirm the host itself sees the dongle (`lsusb`).

## 4. Configure `cfg.json`

Edit **`conf/cfg.json`** (on the host, or in the web UI under **Config → cfg.json**).
Follow the `_template_notes` embedded in the file. The values you must get right:

| field | what to set |
|-------|-------------|
| `devices[0].args` | `rtl=<index>` — the index from step 3, e.g. `rtl=0,rtl` |
| `devices[0].ppm` | crystal correction; `0` to start, tune later (see Troubleshooting) |
| `devices[0].frequency` | your control channel in **Hz**, e.g. `856000000` |
| `channels[0].frequency` | the same control channel in **Hz** |
| `channels[0].destination` | `udp://127.0.0.1:<udp_port>` — must match the stream in `stream.json` (default `23456`) |
| `trunking.chans[0].sysname` | any label for your system |
| `trunking.chans[0].control_channel_list` | control channel in **MHz**, e.g. `"856.0000"` (comma-separate alternates to allow CC hunting) |
| `trunking.chans[0].nac` | system NAC, e.g. `"0x4a2"` |
| `trunking.chans[0].sysid` / `.wacn` | optional, left empty is fine (learned from the control channel) |
| `trunking.chans[0].phase2_tdma` | `1` if the system uses Phase 2 TDMA, `0` for Phase 1 only |
| `trunking.chans[0].modulation` | `cqpsk` (700/800 MHz) or `fm` (VHF/UHF) |
| `trunking.chans[0].whitelist` / `.blacklist` | comma-separated talkgroup IDs to restrict/block decoding |

Everything else (demod/filter/symbol-rate defaults) can stay as shipped unless
you know it needs changing.

## 5. Configure `stream.json`

Edit **`conf/stream.json`**:

- `streams[0].udp_port` **must match** `channels[0].destination` port in
  `cfg.json`. Default is `23456` on both sides.
- Change the Icecast passwords from the defaults:
  `icecast.source_password`, `icecast.admin_password`,
  `icecast.supervisor_password`.
- `listener_auth: true` means listeners must authenticate with the accounts in
  `conf/listen.json`; set it to `false` to open the feed.
- `streams[0].mount` is the listening URL path (`/primary.mp3`),
  `codec` is `mp3` or `aac`, `bitrate_kbps` sets the encoder quality.

## 6. Save and verify

Saving a config in the web UI re-renders runtime configs and restarts the
affected services automatically. Then:

1. **Services** tab — `op25`, `streams`, `icecast`, `web` should all be
   `RUNNING` (op25 may sit in `BACKOFF`/`FATAL` if there is no SDR or the
   config is wrong — see below).
2. **Logs → op25** — look for a successful decode. With the correct control
   channel and NAC you should see trunking activity / talkgroup grants. A
   `Wrong rtlsdr device index` error means the `rtl=<index>` in step 4 is wrong.
3. **Telemetry** — after traffic starts you'll see the active channel,
   talkgroup and now-playing metadata.
4. Listen — open one of:

   ```
   http://<host>:8000/primary.mp3          # Icecast (scanner / listen123 by default)
   http://<host>:8080/stream/primary.mp3   # web proxy (requires web login)
   ```

## 7. Troubleshooting

| symptom | likely cause / fix |
|---------|---------------------|
| `op25` shows `BACKOFF` / no device | no dongle reachable, or wrong `rtl=<index>`. Scan in the SDR tab, fix `devices[0].args`, add `privileged: true` if USB is flaky. |
| No decode / "invalid" on the control channel | wrong control channel frequency or NAC. Confirm both in `cfg.json`; try adding CC alternates to `control_channel_list`. |
| Stream connects but silent | channel `destination` port does not match `streams[].udp_port`; or stream `enabled: false`; or listener auth blocks you (use `listen.json` creds). |
| Audio garbled / drift over time | tune `devices[0].ppm` (crystal error). Start near the correct value and adjust a few ppm at a time until the constellation/decode is clean. |
| Nothing but encrypted talkgroups | system uses full-time encryption; op25 cannot decode it (`crypt_behavior`/`crypt_keys` only cover systems you have keys for). |
| Frequency in logs looks off | remember: `frequency`/`center_frequency` are **Hz**, `control_channel_list` is **MHz**. |
| Save doesn't seem to apply | config validation failed — the web UI shows the error; check the JSON is valid before saving. |

## 8. Where to go from here

- **Tags** — add `conf/tags/tgid.tsv` (talkgroups) and `conf/tags/rid.tsv`
  (radios) as `number<TAB>label`; save and restart `op25` to show names in the
  UI instead of numbers.
- **More streams** — add entries to `stream.json` `streams` and matching UDP
  ports in `cfg.json`.
- **Locking down** — change all default passwords, put a TLS reverse proxy in
  front of 8080/8000 before exposing beyond localhost.
