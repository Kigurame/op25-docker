# conf/ — configuration

These files are the container's default configuration templates. On first boot
the entrypoint copies them into your mounted `./conf` volume, so **edit the
files on your host**, not inside the image.

Each JSON file contains a `_template_notes` key with inline guidance, and the
web UI (Config tab) lets you edit every file — saving re-renders runtime
configs and restarts the affected service. For the quickest start, use the
**Config → Set up my scanner** wizard: it asks for the SDR device index,
control channel frequency (MHz), NAC, band and Phase 2 option, then generates
`cfg.json` for you.

| file | purpose |
|------|---------|
| `cfg.json` | op25 receiver: SDR device, channel, P25 trunking system (control channel, NAC, modulation), log `verbosity` |
| `stream.json` | Icecast: ports, passwords, listener auth, and one stream entry per audio mount (optional `icecast.external_port` overrides the port shown in the web UI's Listen/Copy URL links) |
| `listen.json` | Icecast listener accounts (used when `listener_auth` is true) |
| `users.json` | Web control-plane accounts |
| `icecast-tpl.xml` | Icecast template (rendered by `render_configs.py`) |
| `tags/tgid.tsv`, `tags/rid.tsv` | talkgroup and radio call-sign tags (`number<TAB>label`) |

For a step-by-step setup guide see **[docs/SETUP.md](../docs/SETUP.md)**.
