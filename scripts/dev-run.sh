#!/usr/bin/env bash
# Development launcher: runs strix-dash as the invoking user, no systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
# Serve THIS checkout's frontend, not an installed copy under /usr/share.
export STRIX_DASH_FRONTEND="${STRIX_DASH_FRONTEND:-$PWD/frontend/dist}"
exec python3 -m uvicorn backend.main:app \
    --host "${STRIX_DASH_HOST:-127.0.0.1}" \
    --port "${STRIX_DASH_PORT:-10001}" \
    --workers 1 \
    "$@"
