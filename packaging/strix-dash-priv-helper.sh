#!/usr/bin/env bash
#
# strix-dash privileged helper -- a small, fixed set of verbs run as root via
# one sudoers entry.
#
#   strix-dash-priv-helper.sh batterylimit <40-100>
#
# SECURITY MODEL
#
# The caller supplies a verb and a bounded value, never a path. Every target is
# hardcoded here and every argument is validated in-script -- the sudoers rule
# cannot constrain a wildcard argument, so this script must not trust one.
#
# WHY THIS EXISTS FOR THE BATTERY LIMIT
#
# strix-dash normally writes that node directly, using the group ownership its
# tmpfiles.d rule grants. But z13ctl ships a udev rule that chgrps the same node
# to "users" on every matching event:
#
#   ACTION=="add", SUBSYSTEM=="platform-profile", KERNELS=="asus-nb-wmi",
#     RUN+="/usr/bin/chgrp users .../charge_control_end_threshold"
#
# Two packages cannot both own one node's group, and whichever udev rule runs
# last wins. Rather than fight over it -- which would break unpredictably for
# whoever installs second -- the backend falls back to this helper, which is
# root and does not care who owns the node.
#
set -euo pipefail

BATTERY_NODE=/sys/class/power_supply/BAT0/charge_control_end_threshold

# Matches z13ctl's documented range. The kernel itself accepts lower values,
# but the two paths must behave identically or the same request would succeed
# or fail depending on which backend happened to be used.
BATTERY_MIN=40
BATTERY_MAX=100

die() { printf 'strix-dash-priv-helper: %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root"

VERB="${1:-}"
VALUE="${2:-}"

case "$VERB" in
    batterylimit)
        case "$VALUE" in
            ''|*[!0-9]*) die "battery limit must be a plain integer" ;;
        esac
        # Strip leading zeros so 010 is not read as octal anywhere downstream.
        VALUE=$((10#$VALUE))
        if [ "$VALUE" -lt "$BATTERY_MIN" ] || [ "$VALUE" -gt "$BATTERY_MAX" ]; then
            die "battery limit must be between $BATTERY_MIN and $BATTERY_MAX"
        fi
        [ -w "$BATTERY_NODE" ] || die "$BATTERY_NODE is not writable even as root"
        printf '%s' "$VALUE" > "$BATTERY_NODE" || die "kernel rejected $VALUE"
        # Echo the read-back so the caller verifies hardware state, not intent.
        cat "$BATTERY_NODE"
        ;;
    *)
        die "unknown verb '${VERB}'"
        ;;
esac
