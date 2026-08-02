#!/usr/bin/env bash
#
# strix-dash requirement helper -- the ONLY code path that reaches the network
# or installs a package. Invoked as root via a single sudoers entry.
#
#   strix-dash-req-helper.sh check   <requirement-id>
#   strix-dash-req-helper.sh install <requirement-id>
#
# SECURITY MODEL
#
# The caller supplies a requirement ID and nothing else. It cannot pass a URL,
# a repository, a filename, or a path. Everything is re-resolved here from the
# hardcoded allowlist below, so a compromised backend still cannot make this
# script fetch or install something arbitrary.
#
# Downloads are verified against the SHA256 digest the release API reports for
# the asset. Note what that does and does not prove: it authenticates the
# integrity of the transfer, NOT the identity of the publisher -- the digest is
# served by the same host as the file. It defends against a corrupted or
# truncated download and an interfering proxy, not against a compromised
# upstream account.
#
set -euo pipefail

STAGING=/var/lib/strix-dash/staging
UA="strix-dash-req-helper"
CURL_OPTS=(--fail --silent --show-error --location --proto '=https' --tlsv1.2
           --max-time 600 --user-agent "$UA")

# --- Allowlist -------------------------------------------------------------
# id | repo | asset template ({version} and {distro_tag} are substituted)
allowlist_repo() {
    case "$1" in
        fastflowlm) printf 'FastFlowLM/FastFlowLM' ;;
        *)          return 1 ;;
    esac
}
allowlist_asset() {
    case "$1" in
        fastflowlm) printf 'fastflowlm_{version}_{distro_tag}_amd64.deb' ;;
        *)          return 1 ;;
    esac
}

die() { printf '{"error":%s}\n' "$(printf '%s' "$1" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root"

ACTION="${1:-}"
REQ_ID="${2:-}"

# Strict identifier: no slashes, dots, or shell metacharacters can enter here.
case "$REQ_ID" in
    ''|*[!a-z0-9-]*) die "invalid requirement id" ;;
esac

REPO="$(allowlist_repo "$REQ_ID")"  || die "requirement '$REQ_ID' is not installable"
ASSET_TPL="$(allowlist_asset "$REQ_ID")"

# --- Distro tag ------------------------------------------------------------
distro_tag() {
    local id like version major
    id=$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")
    like=$(. /etc/os-release 2>/dev/null && printf '%s' "${ID_LIKE:-}")
    version=$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_ID:-}")

    if [ "$id" = "ubuntu" ]; then
        printf 'ubuntu%s' "$version"; return
    fi
    if [ "$id" = "debian" ]; then
        printf 'debian%s' "${version%%.*}"; return
    fi
    case "$like" in
        *debian*)
            major=$(cut -d. -f1 /etc/debian_version 2>/dev/null || true)
            [ -n "$major" ] && printf 'debian%s' "$major" || printf 'debian'
            return ;;
    esac
    printf '%s' "${id:-unknown}"
}

RELEASE_JSON=$(curl "${CURL_OPTS[@]}" \
    -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${REPO}/releases/latest") \
    || die "could not reach the release API for ${REPO}"

# --- check -----------------------------------------------------------------
if [ "$ACTION" = "check" ]; then
    printf '%s' "$RELEASE_JSON"
    exit 0
fi

[ "$ACTION" = "install" ] || die "unknown action '$ACTION'"

# --- Resolve the asset for THIS distribution -------------------------------
read -r ASSET_NAME ASSET_URL ASSET_SIZE ASSET_DIGEST <<EOF
$(DISTRO_TAG="$(distro_tag)" ASSET_TPL="$ASSET_TPL" python3 - "$RELEASE_JSON" <<'PY'
import json, os, sys
data = json.loads(sys.argv[1])
version = (data.get("tag_name") or "").lstrip("v")
wanted = os.environ["ASSET_TPL"].format(
    version=version, distro_tag=os.environ["DISTRO_TAG"]
)
for asset in data.get("assets", []):
    if asset.get("name") == wanted:
        print(asset["name"], asset["browser_download_url"],
              asset.get("size", 0), asset.get("digest", ""))
        break
else:
    sys.exit(3)
PY
)
EOF

[ -n "${ASSET_NAME:-}" ] || die "no asset matching $(distro_tag) in the latest release"

# Only ever a .deb, and only a bare filename.
case "$ASSET_NAME" in
    */*|*..*)  die "refusing suspicious asset name" ;;
    *.deb)     ;;
    *)         die "refusing non-.deb asset '$ASSET_NAME'" ;;
esac
case "$ASSET_URL" in
    https://github.com/"$REPO"/releases/download/*) ;;
    *) die "asset URL is not on the expected release path" ;;
esac

install -d -m 0700 -o root -g root "$STAGING"
TARGET="$STAGING/$ASSET_NAME"
trap 'rm -f "$TARGET"' EXIT

curl "${CURL_OPTS[@]}" -o "$TARGET" "$ASSET_URL" || die "download failed"

# --- Verify ----------------------------------------------------------------
ACTUAL="sha256:$(sha256sum "$TARGET" | cut -d' ' -f1)"
if [ -n "${ASSET_DIGEST:-}" ] && [ "$ASSET_DIGEST" != "null" ]; then
    if [ "$ACTUAL" != "$ASSET_DIGEST" ]; then
        die "SHA256 mismatch: expected $ASSET_DIGEST, got $ACTUAL -- discarded"
    fi
else
    die "release provided no digest for '$ASSET_NAME'; refusing to install unverified"
fi

# --- Install ---------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
if ! APT_OUT=$(apt-get install -y --no-install-recommends "$TARGET" 2>&1); then
    die "apt-get install failed: $(printf '%s' "$APT_OUT" | tail -5)"
fi

INSTALLED=$(dpkg-query -W -f='${Version}' "$REQ_ID" 2>/dev/null || printf 'unknown')
printf '{"installed":true,"id":"%s","asset":"%s","size":%s,"digest":"%s","version":"%s"}\n' \
    "$REQ_ID" "$ASSET_NAME" "${ASSET_SIZE:-0}" "$ACTUAL" "$INSTALLED"
