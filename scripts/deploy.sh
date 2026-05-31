#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-api-report-agent}"
VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON="$VENV_DIR/bin/python"
LOG_FILE="$PROJECT_ROOT/deploy.log"

mkdir -p "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs"
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

cd "$PROJECT_ROOT"

echo "Deploying $SERVICE_NAME from $PROJECT_ROOT"

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  echo "ERROR: $PROJECT_ROOT/.env is missing. Copy .env.example to .env and configure it first." >&2
  exit 1
fi

if [[ ! -f "$PROJECT_ROOT/requirements.txt" ]]; then
  echo "ERROR: $PROJECT_ROOT/requirements.txt is missing." >&2
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "Virtualenv python is missing or not executable; recreating $VENV_DIR"
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

"$PYTHON" --version
"$PYTHON" -m pip --version
"$PYTHON" -m pip install -U pip
"$PYTHON" -m pip install -r requirements.txt

sudo cp systemd/api-report-agent.service /etc/systemd/system/api-report-agent.service
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

sleep 2
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true

echo "Deployment finished for $SERVICE_NAME"
