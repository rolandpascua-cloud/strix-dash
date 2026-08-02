#!/usr/bin/env bash
#
# Remove strix-dash. Reverses install.sh in dependency order.
#
#   sudo ./scripts/uninstall.sh            # leave user, config and logs
#   sudo ./scripts/uninstall.sh --purge    # remove those too
#
set -euo pipefail

SERVICE=strix-dash.service
USER_NAME=strix-dash
PURGE=false
[ "${1:-}" = "--purge" ] && PURGE=true

info() { printf '  %s\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Must run as root: sudo $0"; exit 1; }

step "Stopping service"
systemctl disable --now "$SERVICE" 2>/dev/null || info "not running"
rm -f "/lib/systemd/system/$SERVICE"
systemctl daemon-reload
info "unit removed"

step "Removing privileges"
# sudoers first: never leave a grant pointing at a path that no longer exists.
rm -f /etc/sudoers.d/strix-dash
info "sudoers removed"
rm -f /usr/lib/tmpfiles.d/strix-dash.conf
info "tmpfiles removed (sysfs modes revert on next boot)"

step "Removing files"
rm -rf /usr/lib/strix-dash /usr/share/strix-dash
rm -f /usr/bin/strix-dash-server
info "application files removed"

if $PURGE; then
    step "Purging"
    rm -rf /etc/strix-dash /var/log/strix-dash /var/lib/strix-dash
    if getent passwd "$USER_NAME" >/dev/null; then
        deluser --system "$USER_NAME" 2>/dev/null || true
        info "user $USER_NAME removed"
    fi
    info "config, logs and state removed"
else
    info "kept /etc/strix-dash, /var/log/strix-dash and the $USER_NAME user"
    info "(use --purge to remove them)"
fi

printf '\n\033[32m  strix-dash uninstalled\033[0m\n'
