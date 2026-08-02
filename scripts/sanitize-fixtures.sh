#!/usr/bin/env bash
#
# Redact machine-identifying data from captured test fixtures.
#
# tests/fixtures/raw/ holds verbatim tool output from a real machine. Publishing
# a repository is irreversible and indexable, so anything identifying the host
# must be replaced with stable placeholders BEFORE the first commit.
#
# Note that this script contains NO hardcoded hostnames or usernames -- it would
# leak the very values it exists to remove. Host-specific values are derived at
# runtime; everything else is matched by shape.
#
# Placeholders keep the shape and length of what they replace, so the parsers
# under test still exercise the real formats.
#
#   ./scripts/sanitize-fixtures.sh --check   # report only, exit 1 if dirty
#   ./scripts/sanitize-fixtures.sh           # rewrite in place
#
set -euo pipefail

cd "$(dirname "$0")/.."
DIR=tests/fixtures/raw
CHECK_ONLY=false
[ "${1:-}" = "--check" ] && CHECK_ONLY=true

[ -d "$DIR" ] || { echo "no fixtures at $DIR"; exit 0; }

# --- Host-specific values, resolved at runtime ------------------------------
THIS_HOST=$(hostname 2>/dev/null || true)
THIS_USER=${SUDO_USER:-${LOGNAME:-${USER:-}}}

declare -a RULES=()

# Longer, host-specific literals first.
[ -n "$THIS_HOST" ] && RULES+=("s/\\b${THIS_HOST}\\b/example-host/g")
[ -n "$THIS_USER" ] && RULES+=("s/\\b${THIS_USER}\\b/user/g")

# --- Shape-based rules ------------------------------------------------------
RULES+=(
    # Home directories (any user)
    's#/home/[A-Za-z0-9._-]+#/home/user#g'
    # PCI bus:device.function
    's/\b[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]\b/0000:00:00.1/g'
    # BIOS build suffix: keep the board model, zero the build number
    's/\b([A-Z]{2}[0-9]{3}[A-Z]{2})\.[0-9]+\b/\1.000/g'
    # UUIDs (filesystem, snapshot, GPU)
    's/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/00000000-0000-0000-0000-000000000000/g'
    # machine-id style 32-hex strings
    's/\b[0-9a-f]{32}\b/00000000000000000000000000000000/g'
    # Serial-ish GPU identifiers
    's/(Uuid:[[:space:]]+)GPU-[0-9a-fA-F]+/\1GPU-000000000000/g'
    # MAC addresses
    's/\b([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b/00:00:00:00:00:00/g'
)

# Deliberately NOT redacting dotted-quad numbers. They are indistinguishable by
# shape from version strings, and these tools emit far more of the latter --
# the NPU firmware version is "1.1.2.65", which a naive IPv4 rule rewrites to
# "0.0.0.0" and silently corrupts every firmware assertion in the suite.
# None of the captured tools report network addresses.

changed=0
for file in "$DIR"/*; do
    [ -f "$file" ] || continue
    original=$(cat "$file")
    modified="$original"
    for rule in "${RULES[@]}"; do
        # -E for the common rules; the IPv4 lookahead needs perl, so tolerate
        # its absence rather than failing the whole run.
        if [[ "$rule" == *'(?!'* ]]; then
            command -v perl >/dev/null 2>&1 &&
                modified=$(printf '%s' "$modified" | perl -pe "$rule")
        else
            modified=$(printf '%s' "$modified" | sed -E "$rule")
        fi
    done

    if [ "$original" != "$modified" ]; then
        changed=$((changed + 1))
        if $CHECK_ONLY; then
            printf '  DIRTY  %s\n' "$file"
        else
            printf '%s' "$modified" > "$file"
            printf '  cleaned %s\n' "$file"
        fi
    fi
done

if $CHECK_ONLY; then
    if [ $changed -gt 0 ]; then
        printf '\n%s fixture(s) still contain identifying data.\n' "$changed"
        printf 'Run ./scripts/sanitize-fixtures.sh before committing.\n'
        exit 1
    fi
    printf '  all fixtures clean\n'
else
    printf '\n%s fixture(s) rewritten.\n' "$changed"
    printf 'Re-run the test suite: assertions may reference redacted values.\n'
fi
