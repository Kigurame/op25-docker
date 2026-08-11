# conf/ — configuration

These files are the container's default configuration templates. On first boot
the entrypoint copies them into your mounted `./conf` volume, so **edit the
files on your host**, not inside the image.

Each JSON file contains a `_template_notes` key with inline guidance, and the
web UI (Config tab) lets you edit every file — saving re-renders runtime
configs and restarts the affected service.

| file | purpose |
|------|---------|
| `cfg.json` | op25 receiver: SDR device, channel, P25 trunking system (control channel, NAC, modulation) |
| `stream.json` | Icecast: ports, passwords, listener auth, and one stream entry per audio mount |
| `listen.json` | Icecast listener accounts (used when `listener_auth` is true) |
| `users.json` | Web control-plane accounts |
| `icecast-tpl.xml` | Icecast template (rendered by `render_configs.py`) |
| `tags/tgid.tsv`, `tags/rid.tsv` | talkgroup and radio call-sign tags (`number<TAB>label`) |

For a step-by-step setup guide see **[docs/SETUP.md](../docs/SETUP.md)**.
