#!/bin/sh
# op25-docker entrypoint
# - Populates the config volume with defaults on first start.
# - Renders icecast.xml, htpasswd and supervisord.conf from templates.
# - Starts everything under supervisord.

set -e

CONF_DIR="${OP25_CONF_DIR:-/opt/op25/conf}"

# Copy default configs into the volume if it is empty (first boot with a mounted volume)
if [ -d "$CONF_DIR" ] && [ ! -f "$CONF_DIR/stream.json" ] \
   && [ "$(readlink -f "$CONF_DIR")" != "/opt/op25/defaults" ]; then
    echo "[entrypoint] initializing $CONF_DIR with defaults"
    cp -an /opt/op25/defaults/. "$CONF_DIR/"
fi

# Render runtime configs (icecast.xml, htpasswd, supervisord.conf) from the
# (possibly volume-mapped) conf/ directory.
python3 /opt/op25/render_configs.py --conf-dir "$CONF_DIR" --tpl-dir /opt/op25/defaults \
    --out-dir /etc/op25 --supervisor-conf /etc/op25/supervisord.conf

mkdir -p /var/log/op25 /var/log/icecast2 /var/lib/icecast2 /var/run/icecast2 /var/run/op25
chown -R icecast2:icecast /var/log/icecast2 /var/lib/icecast2 /var/run/icecast2
chmod 0775 /var/run/icecast2 /var/run/op25
# icecast drops privileges via <changeowner>; the htpasswd db must be readable
# by the icecast2 user (render_configs.py writes it 0600 root).
chown icecast2:icecast /etc/op25/htpasswd

# Build the op25 perl-style caches? (not needed). Just inform.
echo "[entrypoint] op25-docker starting (op25 + icecast + streams + web)"

exec /usr/bin/supervisord -c /etc/op25/supervisord.conf
