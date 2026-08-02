#!/usr/bin/env bash
# Redeploy backend + frontend to the installed service and restart it.
# Use after code changes when strix-dash is already installed.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "Must run as root: sudo $0"; exit 1; }

rm -rf /usr/lib/strix-dash/backend
cp -r "$SRC/backend" /usr/lib/strix-dash/backend
find /usr/lib/strix-dash -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find /usr/lib/strix-dash -type d -exec chmod 0755 {} +
find /usr/lib/strix-dash -type f -exec chmod 0644 {} +

rm -rf /usr/share/strix-dash/frontend
cp -r "$SRC/frontend/dist" /usr/share/strix-dash/frontend
find /usr/share/strix-dash -type d -exec chmod 0755 {} +
find /usr/share/strix-dash -type f -exec chmod 0644 {} +

install -m 0644 -o root -g root "$SRC/packaging/strix-dash.service" \
        /lib/systemd/system/strix-dash.service
systemctl daemon-reload
systemctl restart strix-dash
for _ in $(seq 1 30); do
    curl -sf -m 2 http://127.0.0.1:10001/api/v1/health >/dev/null 2>&1 && break
    sleep 0.5
done
echo "redeployed; health: $(curl -s http://127.0.0.1:10001/api/v1/health | head -c 60)"
