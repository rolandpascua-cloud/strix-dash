#!/usr/bin/env bash
#
# NPU prerequisites for Debian 13, per the FastFlowLM setup instructions:
#   https://lemonade-server.ai/flm_npu_linux.html
#
# Idempotent and conservative -- it checks before it writes, and reports rather
# than installing anything without being asked.
#
#   sudo ./scripts/bootstrap-npu.sh          # check + enable backports
#   ./scripts/bootstrap-npu.sh --check-only  # report only, no root needed
#
set -euo pipefail

BACKPORTS=/etc/apt/sources.list.d/debian-backports.sources
CHECK_ONLY=false
[ "${1:-}" = "--check-only" ] && CHECK_ONLY=true

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33m--\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[31mNO\033[0m   %s\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

read -r -d '' WANTED <<'EOF' || true
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie-backports
Components: main
Enabled: yes
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

# ---------------------------------------------------------------------------
step "1. Debian backports"

needs_write=false
if [ -f "$BACKPORTS" ]; then
    # Compare ignoring comments and blank lines so a hand-annotated file that is
    # semantically identical is left alone.
    if diff -q <(grep -vE '^\s*#|^\s*$' "$BACKPORTS") <(printf '%s\n' "$WANTED") >/dev/null 2>&1; then
        ok "$BACKPORTS already present and matches the documented content"
    else
        warn "$BACKPORTS exists but differs from the documented content"
        warn "leaving it alone -- review it by hand if FastFlowLM installs fail"
    fi
else
    bad "$BACKPORTS is missing"
    needs_write=true
fi

if $needs_write; then
    if $CHECK_ONLY; then
        warn "would create $BACKPORTS (re-run as root without --check-only)"
    elif [ "$(id -u)" -ne 0 ]; then
        bad "need root to create $BACKPORTS"
        exit 1
    else
        printf '%s\n' "$WANTED" > "$BACKPORTS"
        chmod 0644 "$BACKPORTS"
        ok "created $BACKPORTS"
        apt-get update -qq && ok "apt cache refreshed"
    fi
fi

# ---------------------------------------------------------------------------
step "2. NPU driver and device"

if [ -e /dev/accel/accel0 ]; then
    ok "/dev/accel/accel0 present"
    drm=$(cat /sys/module/amdxdna/version 2>/dev/null || echo "")
    [ -n "$drm" ] && ok "amdxdna version $drm"
else
    bad "/dev/accel/accel0 missing -- the amdxdna driver did not bind"
    warn "in-tree on kernel 6.18+; on older kernels install amdxdna-dkms"
fi

if dpkg-query -W -f='${db:Status-Abbrev}' libxrt-npu2 2>/dev/null | grep -q '^ii'; then
    ok "libxrt-npu2 installed"
else
    warn "libxrt-npu2 not installed (sudo apt install libxrt-npu2)"
fi

# ---------------------------------------------------------------------------
step "3. memlock limit"

soft=$(ulimit -Sl 2>/dev/null || echo 0)
if [ "$soft" = "unlimited" ]; then
    ok "memlock is unlimited for this shell"
else
    warn "memlock soft limit is $soft KB"
    warn "FastFlowLM wants 'unlimited'. Add to /etc/security/limits.conf:"
    printf '         *    soft    memlock    unlimited\n'
    printf '         *    hard    memlock    unlimited\n'
    warn "then log out and back in. This script will not edit that file for you."
fi

# ---------------------------------------------------------------------------
step "4. FastFlowLM"

if command -v flm >/dev/null 2>&1 || [ -x /usr/bin/flm ]; then
    ver=$(dpkg-query -W -f='${Version}' fastflowlm 2>/dev/null || echo "unknown")
    ok "fastflowlm $ver installed"
    # A sideloaded .deb has no repository behind it, so apt will never upgrade
    # it -- worth stating plainly rather than letting it look up-to-date.
    if ! apt-cache policy fastflowlm 2>/dev/null | grep -qE '^\s+[0-9]+\s+https?://'; then
        warn "no repository origin: installed from a local .deb"
        warn "apt will NOT offer updates; use the dashboard's Requirements page"
    fi
else
    bad "fastflowlm not installed"
    warn "download the debian13 asset from the project's releases page, then:"
    printf '         sudo apt install ./fastflowlm_<version>_debian13_amd64.deb\n'
fi

printf '\n'
