#!/usr/bin/env bash
#
# Redeploy code to an already-installed strix-dash and restart it.
#
# Use after changing code when install.sh has already run. It does NOT create
# the service user, sudoers rules or tmpfiles grants -- run install.sh for those.
#
#   sudo ./scripts/deploy.sh
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
LIB=/usr/lib/strix-dash
SHARE=/usr/share/strix-dash

[ "$(id -u)" -eq 0 ] || { echo "Must run as root: sudo $0"; exit 1; }
[ -d "$LIB" ] || { echo "strix-dash is not installed; run ./scripts/install.sh"; exit 1; }

# --- backend ---------------------------------------------------------------
rm -rf "$LIB/backend"
cp -r "$SRC/backend" "$LIB/backend"
find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$LIB" -type d -exec chmod 0755 {} +
find "$LIB" -type f -exec chmod 0644 {} +

# --- privileged helpers ----------------------------------------------------
# These live in packaging/ but install INTO backend/, which the copy above has
# just replaced. Reinstalling them here is not optional: omitting it wipes them
# on every deploy and silently breaks the snapshot button, the requirement
# installer and the battery-limit fallback -- each of which then reports only
# "the privileged helper is not installed", pointing at install.sh rather than
# at the deploy that removed it.
for helper in strix-dash-priv-helper.sh strix-dash-req-helper.sh; do
    if [ -f "$SRC/packaging/$helper" ]; then
        install -m 0755 -o root -g root "$SRC/packaging/$helper" "$LIB/backend/$helper"
        echo "  helper: $helper"
    fi
done

# --- frontend --------------------------------------------------------------
rm -rf "${SHARE:?}/frontend"
cp -r "$SRC/frontend/dist" "$SHARE/frontend"
find "$SHARE" -type d -exec chmod 0755 {} +
find "$SHARE" -type f -exec chmod 0644 {} +

# --- launcher and unit -----------------------------------------------------
install -m 0755 -o root -g root "$SRC/packaging/strix-dash-server" /usr/bin/strix-dash-server
install -m 0644 -o root -g root "$SRC/packaging/strix-dash.service" \
        /lib/systemd/system/strix-dash.service

systemctl daemon-reload
systemctl restart strix-dash

for _ in $(seq 1 30); do
    curl -sf -m 2 http://127.0.0.1:10001/api/v1/health >/dev/null 2>&1 && break
    sleep 0.5
done

if curl -sf -m 2 http://127.0.0.1:10001/api/v1/health >/dev/null 2>&1; then
    echo "  redeployed and healthy: http://127.0.0.1:10001"
else
    echo "  service did not come back healthy; journalctl -u strix-dash -n 30" >&2
    exit 1
fi
