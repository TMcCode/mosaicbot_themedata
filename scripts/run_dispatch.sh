#!/usr/bin/env bash
# Cron entrypoint for mosaicbot_themedata on Contabo (temporary host).
# Usage: /opt/mosaicbot_themedata/scripts/run_dispatch.sh [--dry-run|--force ...]
#
# Set THEMEDATA_SEND_DIGEST=1 on the cron that should email after this run
# (use only on the last tick of the day if you add a second dispatch).
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

DISPATCH_RC=0
{
  echo "=== themedata dispatch ${STAMP} host=${THEMEDATA_HOST} ==="
  set +e
  "$PYTHON" "$ROOT/jobs/dispatch_due.py" --host "$THEMEDATA_HOST" "$@"
  DISPATCH_RC=$?
  set -e
  echo "=== dispatch exit=${DISPATCH_RC} ==="
} >>"$LOG_FILE" 2>&1

# Post-dispatch email (SMTP via morning-ops-digest env / automation image).
if [[ "${THEMEDATA_SEND_DIGEST:-0}" == "1" ]]; then
  WRAPPER="${WRAPPER:-/opt/mosaic-deploy/scripts/job-wrapper.sh}"
  IMAGE="${THEMEDATA_DIGEST_IMAGE:-us-central1-docker.pkg.dev/lateral-raceway-321323/etl/mosaicbot-stockcontext-automation:latest}"
  if [[ -x "$WRAPPER" ]]; then
    {
      echo "=== themedata digest (post-dispatch) ==="
      export MOSAIC_JOB_LIGHT=1
      export MOSAIC_ENV_FILE="${MOSAIC_ENV_FILE:-/opt/mosaic-deploy/envs/morning-ops-digest.env}"
      # Inline import works with Contabo digest override before image rebuild.
      set +e
      "$WRAPPER" themedata-ops-digest "$IMAGE" \
        python -c "from admin_dashboard.utils.morning_ops_digest import run_themedata_ops_digest; import json; print(json.dumps(run_themedata_ops_digest(), default=str))"
      DIGEST_RC=$?
      set -e
      echo "=== digest exit=${DIGEST_RC} ==="
    } >>"$LOG_FILE" 2>&1 || true
  else
    echo "WARN: job-wrapper missing; skipped themedata digest" >>"$LOG_FILE"
  fi
fi

exit "$DISPATCH_RC"
