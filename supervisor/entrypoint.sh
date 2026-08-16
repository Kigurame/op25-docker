#!/bin/sh
# op25-docker entrypoint
# - Populates the config volume with defaults on first start.
# - Renders icecast.xml, htpasswd and supervisord.conf from templates.
# - Starts everything under supervisord.

set -e

CONF_DIR="${OP25_CONF_DIR:-/opt/op25/conf}"

# supervisord.conf expands %(ENV_OP25_SESSION_SECRET)s, which hard-fails when
# the variable is absent. If it's unset or empty, generate one for this boot
# (sessions reset on restart). If it's set to the known-insecure default,
# leave it alone so auth.py's refuse-to-start check still applies.
if [ -z "${OP25_SESSION_SECRET:-}" ]; then
    OP25_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    export OP25_SESSION_SECRET
fi

# Copy default configs into the volume if it is empty (first boot with a mounted volume)
if [ -d "$CONF_DIR" ] && [ ! -f "$CONF_DIR/stream.json" ] \
   && [ "$(readlink -f "$CONF_DIR")" != "/opt/op25/defaults" ]; then
    echo "[entrypoint] initializing $CONF_DIR with defaults"
    cp -an /opt/op25/defaults/. "$CONF_DIR/"
    # Never ship the well-known admin123 default: replace the admin password
    # with a random one and print it to the log.
    python3 /opt/op25/set_admin_password.py --users-file "$CONF_DIR/users.json" || \
        echo "[entrypoint] warning: could not randomize admin password"
fi

# Render runtime configs (icecast.xml, htpasswd, supervisord.conf) from the
# (possibly volume-mapped) conf/ directory.
python3 /opt/op25/render_configs.py --conf-dir "$CONF_DIR" --tpl-dir /opt/op25/defaults \
    --out-dir /etc/op25 --supervisor-conf /etc/op25/supervisord.conf

mkdir -p /var/log/op25 /var/log/icecast2 /var/lib/icecast2 /var/run/icecast2 /var/run/op25
chown -R icecast2:icecast /var/log/icecast2 /var/lib/icecast2 /var/run/icecast2
chmod 0775 /var/run/icecast2 /var/run/op25
# Build the op25 perl-style caches? (not needed). Just inform.
echo "[entrypoint] op25-docker starting (op25 + icecast + streams + web)"

exec /usr/bin/supervisord -c /etc/op25/supervisord.conf
