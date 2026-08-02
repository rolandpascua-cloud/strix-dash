#!/usr/bin/env bash
#
# Install strix-dash as a system service.
#
# Idempotent: safe to re-run after code changes. Refuses to make partial changes
# -- in particular the sudoers file is validated with visudo BEFORE installation,
# because a malformed file there can lock every user out of sudo.
#
#   sudo ./scripts/install.sh
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"

LIB=/usr/lib/strix-dash
SHARE=/usr/share/strix-dash
CONF_DIR=/etc/strix-dash
SERVICE=strix-dash.service
USER_NAME=strix-dash

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { red "Must run as root: sudo $0"; exit 1; }

# ---------------------------------------------------------------------------
step "Preflight"

missing=()
for pkg in python3-fastapi python3-uvicorn python3-psutil python3-pydantic; do
    dpkg-query -W -f='${db:Status-Abbrev}' "$pkg" 2>/dev/null | grep -q '^ii' \
        || missing+=("$pkg")
done
if [ ${#missing[@]} -gt 0 ]; then
    red "Missing dependencies: ${missing[*]}"
    info "sudo apt install ${missing[*]}"
    exit 1
fi
info "python dependencies present"

# The committed stylesheet must exist -- the service has no network and cannot
# build it at runtime.
if [ ! -s "$SRC/frontend/dist/css/strix-dash.css" ]; then
    red "frontend/dist/css/strix-dash.css is missing or empty"
    info "Run ./scripts/build-css.sh first (needs network, once)."
    exit 1
fi
info "frontend build present"

if ss -tlnH "sport = :10001" 2>/dev/null | grep -q . ; then
    if ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
        red "Port 10001 is already in use by something other than $SERVICE."
        info "Stop it, or set STRIX_DASH_PORT in $CONF_DIR/strix-dash.conf after install."
    fi
fi

# Validate sudoers BEFORE touching anything.
if ! visudo -cf "$SRC/packaging/sudoers.d/strix-dash" >/dev/null; then
    red "sudoers file failed validation -- aborting before any change was made."
    exit 1
fi
info "sudoers syntax validated"

# ---------------------------------------------------------------------------
step "Service account"

if getent passwd "$USER_NAME" >/dev/null; then
    info "user $USER_NAME already exists"
else
    adduser --system --group --no-create-home \
            --home /nonexistent --shell /usr/sbin/nologin "$USER_NAME"
    info "created system user $USER_NAME"
fi

for grp in video render; do
    if getent group "$grp" >/dev/null; then
        adduser "$USER_NAME" "$grp" >/dev/null 2>&1 || true
    fi
done
info "added to video/render groups (GPU + NPU device access)"

# ---------------------------------------------------------------------------
step "Application files"

rm -rf "$LIB/backend"
install -d -m 0755 "$LIB" "$SHARE"
cp -r "$SRC/backend" "$LIB/backend"
find "$LIB" -type d -exec chmod 0755 {} +
find "$LIB" -type f -exec chmod 0644 {} +
# Byte-compiled artefacts from development must not ship.
find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
info "backend  -> $LIB/backend"

rm -rf "${SHARE:?}/frontend"
cp -r "$SRC/frontend/dist" "$SHARE/frontend"
find "$SHARE" -type d -exec chmod 0755 {} +
find "$SHARE" -type f -exec chmod 0644 {} +
info "frontend -> $SHARE/frontend"

for helper in strix-dash-priv-helper.sh strix-dash-req-helper.sh; do
    if [ -f "$SRC/packaging/$helper" ]; then
        install -m 0755 -o root -g root "$SRC/packaging/$helper" "$LIB/backend/$helper"
        info "helper   -> $LIB/backend/$helper"
    fi
done

install -m 0755 -o root -g root "$SRC/packaging/strix-dash-server" /usr/bin/strix-dash-server
info "launcher -> /usr/bin/strix-dash-server"

# ---------------------------------------------------------------------------
step "Configuration"

install -d -m 0755 "$CONF_DIR"
if [ -f "$CONF_DIR/strix-dash.conf" ]; then
    info "keeping existing $CONF_DIR/strix-dash.conf"
else
    cat > "$CONF_DIR/strix-dash.conf" <<'EOF'
# strix-dash configuration.
#
# Keep the host on loopback. This service has no authentication -- its security
# boundary is that only local processes can reach it, plus the narrowly scoped
# sudoers rules. Binding 0.0.0.0 would expose hardware controls to the network.
STRIX_DASH_HOST="127.0.0.1"
STRIX_DASH_PORT="10001"
EOF
    chmod 0644 "$CONF_DIR/strix-dash.conf"
    info "wrote $CONF_DIR/strix-dash.conf"
fi

# ---------------------------------------------------------------------------
step "sysfs grants (tmpfiles.d)"

# Resolve the fan-curve hwmon by DRIVER NAME. The index is assigned in probe
# order and changes across reboots, so it can never be hardcoded.
fan_hwmon=""
for d in /sys/class/hwmon/hwmon*; do
    [ -r "$d/name" ] || continue
    if [ "$(cat "$d/name")" = "asus_custom_fan_curve" ]; then
        fan_hwmon="$(readlink -f "$d")"
        break
    fi
done

fan_lines=""
if [ -n "$fan_hwmon" ]; then
    info "fan curve hwmon: $fan_hwmon"
    for fan in 1 2; do
        [ -e "$fan_hwmon/pwm${fan}_enable" ] || continue
        fan_lines+="z $fan_hwmon/pwm${fan}_enable 0664 root strix-dash - -"$'\n'
        for pt in 1 2 3 4 5 6 7 8; do
            for kind in temp pwm; do
                node="$fan_hwmon/pwm${fan}_auto_point${pt}_${kind}"
                [ -e "$node" ] && fan_lines+="z $node 0664 root strix-dash - -"$'\n'
            done
        done
    done
    info "generated $(printf '%s' "$fan_lines" | grep -c . ) fan-curve grants"
else
    fan_lines="# asus_custom_fan_curve hwmon not present on this machine"
    info "fan curve hwmon NOT found -- fan controls will show as unavailable"
fi

python3 - "$SRC/packaging/tmpfiles.d/strix-dash.conf.in" /usr/lib/tmpfiles.d/strix-dash.conf <<PY
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src).read().replace("@FAN_CURVE_LINES@", """$fan_lines""")
open(dst, "w").write(text)
PY
chmod 0644 /usr/lib/tmpfiles.d/strix-dash.conf
info "wrote /usr/lib/tmpfiles.d/strix-dash.conf"

systemd-tmpfiles --create /usr/lib/tmpfiles.d/strix-dash.conf || \
    info "warning: some tmpfiles entries could not be applied (missing nodes are expected)"
info "applied sysfs grants"

# ---------------------------------------------------------------------------
step "sudoers"

install -m 0440 -o root -g root "$SRC/packaging/sudoers.d/strix-dash" /etc/sudoers.d/strix-dash
# Re-validate the whole sudoers tree now that the file is in place.
if ! visudo -c >/dev/null; then
    red "sudoers tree invalid after install -- removing our file"
    rm -f /etc/sudoers.d/strix-dash
    exit 1
fi
info "installed and validated /etc/sudoers.d/strix-dash"

# ---------------------------------------------------------------------------
step "systemd unit"

install -m 0644 -o root -g root "$SRC/packaging/$SERVICE" "/lib/systemd/system/$SERVICE"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
info "enabled and started $SERVICE"

# ---------------------------------------------------------------------------
step "Verifying"

ok=false
for _ in $(seq 1 30); do
    if curl -sf -m 2 http://127.0.0.1:10001/api/v1/health >/dev/null 2>&1; then
        ok=true; break
    fi
    sleep 0.5
done

if ! $ok; then
    red "Service did not become healthy."
    info "journalctl -u $SERVICE -n 40 --no-pager"
    systemctl is-active "$SERVICE" >/dev/null || journalctl -u "$SERVICE" -n 20 --no-pager
    exit 1
fi
grn "  service healthy on http://127.0.0.1:10001"

# Print the capability report so the operator sees immediately what degraded
# and why, rather than discovering it later in the UI.
step "Capability report"
curl -s http://127.0.0.1:10001/api/v1/capabilities | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
print(f"  tools: {d[\"summary\"][\"tools_available\"]}/{d[\"summary\"][\"tools_total\"]} available")
for name, cap in sorted(d["features"].items()):
    mark = "  OK " if cap["available"] else "  -- "
    print(f"{mark}{name:22} {cap.get(\"reason\",\"\")}")
' || true

step "Done"
grn "  http://127.0.0.1:10001"
info "logs:    journalctl -u $SERVICE -f"
info "remove:  sudo ./scripts/uninstall.sh"
