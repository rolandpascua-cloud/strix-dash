#!/usr/bin/env bash
# Copy src -> dist (no bundler; native ES modules ship verbatim) and rebuild CSS.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p frontend/dist/js
cp frontend/src/index.html frontend/dist/index.html
cp frontend/src/js/*.js    frontend/dist/js/
./scripts/build-css.sh
