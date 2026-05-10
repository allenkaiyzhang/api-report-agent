#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/api-report-agent"
SERVICE_NAME="api-report-agent"
LOG_FILE="${APP_DIR}/deploy.log"
PYTHON_BIN="${APP_DIR}/.venv/bin/python"
PIP_BIN="${PYTHON_BIN} -m pip"

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG_FILE"
}

cd "$APP_DIR"
touch "$LOG_FILE"

log "redeploy started"

if [ ! -f ".env" ]; then
  log "missing required .env"
  exit 1
fi

log "git pull"
git pull 2>&1 | tee -a "$LOG_FILE"

if [ ! -d ".venv" ]; then
  log "creating virtualenv"
  python3 -m venv .venv 2>&1 | tee -a "$LOG_FILE"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  log "virtualenv python not executable: $PYTHON_BIN"
  exit 1
fi

log "install requirements"
${PIP_BIN} install -r requirements.txt 2>&1 | tee -a "$LOG_FILE"

log "check key files"
for path in ".env" "requirements.txt" "scripts/run_pipeline.py" "config/symbols.json"; do
  if [ ! -e "$path" ]; then
    log "missing required file: $path"
    exit 1
  fi
done

log "systemctl daemon-reload"
systemctl daemon-reload 2>&1 | tee -a "$LOG_FILE"

log "restart ${SERVICE_NAME}"
systemctl restart "$SERVICE_NAME" 2>&1 | tee -a "$LOG_FILE"

log "service status"
systemctl --no-pager --full status "$SERVICE_NAME" 2>&1 | tee -a "$LOG_FILE"

log "redeploy finished"
