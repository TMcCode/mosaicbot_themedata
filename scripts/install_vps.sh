#!/usr/bin/env bash
# Install / refresh mosaicbot_themedata on Contabo VPS (temporary until Mac Mini).
#
# From your laptop (repo root):
#   ./scripts/install_vps.sh
#
# Env overrides:
#   VPS_HOST=144.126.148.147
#   VPS_USER=root
#   SSH_KEY=/path/to/contabo
#   REMOTE_ROOT=/opt/mosaicbot_themedata
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VPS_HOST="${VPS_HOST:-144.126.148.147}"
VPS_USER="${VPS_USER:-root}"
SSH_KEY="${SSH_KEY:-$ROOT/../MosaicBotMain_Local_Dev/admin_dashboard/contabo-actions}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/mosaicbot_themedata}"
CRON_FILE="/etc/cron.d/mosaic-themedata"

if [[ ! -f "$SSH_KEY" ]]; then
  echo "SSH key not found: $SSH_KEY" >&2
  exit 1
fi
chmod 600 "$SSH_KEY" 2>/dev/null || true

SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "${VPS_USER}@${VPS_HOST}")
SCP=(scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new)

echo "==> ensure remote dir ${REMOTE_ROOT}"
"${SSH[@]}" "mkdir -p '${REMOTE_ROOT}' /opt/mosaic-deploy/logs /opt/mosaic-deploy/envs"

echo "==> rsync repo (excludes .venv, data outputs, .git)"
rsync -az --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.git/' \
  --exclude 'data/**/*.json' \
  --exclude 'data/**/*.jsonl' \
  --exclude 'data/**/*.html' \
  --exclude 'data/**/*.csv' \
  --exclude 'data/**/*.xlsx' \
  -e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=accept-new" \
  "${ROOT}/" "${VPS_USER}@${VPS_HOST}:${REMOTE_ROOT}/"

if [[ -f "${ROOT}/.env" ]]; then
  echo "==> copy .env"
  "${SCP[@]}" "${ROOT}/.env" "${VPS_USER}@${VPS_HOST}:${REMOTE_ROOT}/.env"
  "${SSH[@]}" "chmod 600 '${REMOTE_ROOT}/.env'"
else
  echo "WARN: no local .env to copy" >&2
fi

echo "==> venv + deps"
"${SSH[@]}" bash -s <<EOF
set -euo pipefail
cd '${REMOTE_ROOT}'
if ! python3 -c 'import venv' 2>/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3-venv python3-pip
fi
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
chmod +x scripts/run_dispatch.sh scripts/install_vps.sh
EOF

echo "==> install daily cron (08:00 UTC ≈ 04:00 ET)"
"${SSH[@]}" bash -s <<EOF
set -euo pipefail
cat > '${CRON_FILE}' <<'CRON'
# mosaicbot_themedata — temporary on Contabo; move to Mac Mini later
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
THEMEDATA_HOST=vps
THEMEDATA_ROOT=${REMOTE_ROOT}
0 8 * * * root ${REMOTE_ROOT}/scripts/run_dispatch.sh
CRON
chmod 644 '${CRON_FILE}'
EOF

echo "==> dry-run dispatch"
"${SSH[@]}" "THEMEDATA_HOST=vps '${REMOTE_ROOT}/scripts/run_dispatch.sh' --dry-run"

echo "OK: installed on ${VPS_USER}@${VPS_HOST}:${REMOTE_ROOT}"
echo "    cron: ${CRON_FILE} (daily 08:00 UTC)"
echo "    force: ssh ... ${REMOTE_ROOT}/scripts/run_dispatch.sh --force lab_economics"
