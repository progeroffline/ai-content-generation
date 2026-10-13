#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${STUDIO_PASSWORD:?Set STUDIO_PASSWORD (at least 16 characters)}"
export STUDIO_DATA="${STUDIO_DATA:-/workspace/studio-data}"
export COMFY_URL="${COMFY_URL:-http://127.0.0.1:18188}"
export STUDIO_HOST="${STUDIO_HOST:-127.0.0.1}"
export STUDIO_PORT="${STUDIO_PORT:-8090}"
exec .venv/bin/python -m studio.server
