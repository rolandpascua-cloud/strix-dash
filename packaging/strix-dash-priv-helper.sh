#!/usr/bin/env bash
#
# strix-dash privileged helper -- a small, fixed set of verbs run as root via
# one sudoers entry.
#
#   strix-dash-priv-helper.sh batterylimit <40-100>
#   strix-dash-priv-helper.sh snapshot-create
#   strix-dash-priv-helper.sh fancurve <1|2> <t:p,t:p,... x8>
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
SNAPSHOT_DIR=/var/snapshots/system

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
    fancurve)
        # z13ctl's udev rules also claim the fan-curve hwmon, the same way they
        # claim the battery node. Same resolution: root does not care who owns it.
        case "$VALUE" in
            1|2) ;;
            *) die "fan must be 1 or 2" ;;
        esac
        points="${3:-}"
        case "$points" in
            ''|*[!0-9:,]*) die "curve must be 'temp:pwm' pairs separated by commas" ;;
        esac

        hwmon=""
        for d in /sys/class/hwmon/hwmon*; do
            [ -r "$d/name" ] || continue
            if [ "$(cat "$d/name")" = "asus_custom_fan_curve" ]; then
                hwmon="$(readlink -f "$d")"; break
            fi
        done
        [ -n "$hwmon" ] || die "asus_custom_fan_curve hwmon not present"

        i=0; prev_t=-1; prev_p=-1
        IFS=','; for pair in $points; do
            unset IFS
            i=$((i + 1))
            [ "$i" -le 8 ] || die "expected 8 points, got more"
            t="${pair%%:*}"; pv="${pair##*:}"
            [ -n "$t" ] && [ -n "$pv" ] || die "malformed point '$pair'"
            t=$((10#$t)); pv=$((10#$pv))
            [ "$t" -ge 20 ] && [ "$t" -le 105 ] || die "temp $t out of range 20-105"
            [ "$pv" -ge 0 ] && [ "$pv" -le 255 ] || die "pwm $pv out of range 0-255"
            # Reject a curve that slows the fan as it heats up.
            [ "$t" -ge "$prev_t" ] || die "temperatures must be non-decreasing"
            [ "$pv" -ge "$prev_p" ] || die "pwm values must be non-decreasing"
            prev_t=$t; prev_p=$pv
            printf '%s' "$t" > "$hwmon/pwm${VALUE}_auto_point${i}_temp" \
                || die "could not write point $i temp"
            printf '%s' "$pv" > "$hwmon/pwm${VALUE}_auto_point${i}_pwm" \
                || die "could not write point $i pwm"
        done
        [ "$i" -eq 8 ] || die "expected 8 points, got $i"
        printf 'ok\n'
        ;;
    snapshot-create)
        # The caller supplies NOTHING. The name is generated here from the
        # clock, so no caller-controlled string ever reaches a path.
        command -v btrfs >/dev/null 2>&1 || die "btrfs-progs is not installed"
        [ -d "$SNAPSHOT_DIR" ] || die "$SNAPSHOT_DIR is not present"

        name="$(date +%Y%m%d-%H%M%S).snapshot"
        target="$SNAPSHOT_DIR/$name"
        [ -e "$target" ] && die "a snapshot for this second already exists"

        # Read-only, matching how the platform's own tooling takes them.
        # Capture btrfs's own message rather than discarding it: "refused" on
        # its own gives the caller nothing to act on, and the cause is usually
        # environmental (a read-only bind mount, a missing subvolume) rather
        # than anything about the request.
        if ! out=$(btrfs subvolume snapshot -r / "$target" 2>&1); then
            die "btrfs could not snapshot / to $target: $(printf '%s' "$out" | tail -1)"
        fi

        printf '%s\n' "$name"
        ;;
    *)
        die "unknown verb '${VERB}'"
        ;;
esac
