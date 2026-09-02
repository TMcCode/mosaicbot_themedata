#!/usr/bin/env bash
# Cron entrypoint for mosaicbot_themedata on Contabo (temporary host).
# Usage: /opt/mosaicbot_themedata/scripts/run_dispatch.sh [--dry-run|--force ...]
set -euo pipefail

ROOT="${THEMEDATA_ROOT:-/opt/mosaicbot_themedata}"
cd "$ROOT"

export THEMEDATA_HOST="${THEMEDATA_HOST:-vps}"
# shellcheck disable=SC1091
set -a
source "$ROOT/.env"
set +a

LOG_DIR="${THEMEDATA_LOG_DIR:-/opt/mosaic-deploy/logs}"
mkdir -p "$LOG_DIR" "$ROOT/data/scheduler"
STAMP=$(TZ=America/New_York date +%Y-%m-%d_%H-%M-%S)
LOG_FILE="${LOG_DIR}/themedata-dispatch-${STAMP}.log"

PYTHON="${ROOT}/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

{
  echo "=== themedata dispatch ${STAMP} host=${THEMEDATA_HOST} ==="
  "$PYTHON" "$ROOT/jobs/dispatch_due.py" --host "$THEMEDATA_HOST" "$@"
} >>"$LOG_FILE" 2>&1
