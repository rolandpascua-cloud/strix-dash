#!/usr/bin/env bash
#
# Regenerate docs/screenshots/*.png from a running dashboard.
#
#   ./scripts/capture-screenshots.sh [url]
#
# Defaults to the INSTALLED service on :10001, which matters: it runs as the
# strix-dash user with the tmpfiles grants, so controls show their real
# writable state. A dev server started from a checkout runs as you and would
# render several controls read-only, which misrepresents the app.
#
# Requires chromium (headless) and Pillow for the crop step.
set -euo pipefail

cd "$(dirname "$0")/.."
URL="${1:-http://127.0.0.1:10001}"
OUT=docs/screenshots
PAGES=(overview requirements snapshots controls)

command -v chromium >/dev/null 2>&1 || { echo "chromium not found" >&2; exit 1; }
curl -sf -m 3 "$URL/api/v1/health" >/dev/null || { echo "no dashboard at $URL" >&2; exit 1; }

mkdir -p "$OUT"
profile=$(mktemp -d)
trap 'rm -rf "$profile"' EXIT

for page in "${PAGES[@]}"; do
    # A generous virtual-time budget lets the first poll land, so the header
    # reads "live" rather than being caught mid-connect.
    chromium --headless=new --disable-gpu --no-sandbox \
        --user-data-dir="$profile/$page" \
        --window-size=2560,1440 --force-device-scale-factor=1 \
        --hide-scrollbars --default-background-color=0d1014 \
        --virtual-time-budget=20000 \
        --screenshot="$OUT/$page.png" "$URL/#$page" >/dev/null 2>&1
    printf '  captured %-14s %s\n' "$page" "$(stat -c '%s bytes' "$OUT/$page.png")"
done

python3 - "$OUT" <<'PY'
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("  Pillow not installed; skipping crop")
    sys.exit(0)

BG, PAD, FOOTER = (13, 16, 20), 20, 70

for path in sorted(Path(sys.argv[1]).glob("*.png")):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    px = img.load()
    last = 0
    # Scan up from just above the footer, which is pinned to the viewport
    # bottom and would otherwise always count as content.
    for y in range(h - FOOTER, -1, -1):
        if any(px[x, y] != BG for x in range(0, w, 5)):
            last = y
            break
    new_h = min(h, last + PAD)
    if new_h < h - 40:
        img.crop((0, 0, w, new_h)).save(path, optimize=True)
        print(f"  cropped  {path.name:14} -> {w}x{new_h}")
PY
