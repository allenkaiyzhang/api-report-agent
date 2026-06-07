#!/usr/bin/env bash
set -euo pipefail

# API Report Agent Hardened Deployment Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_ROOT="${DEPLOY_ROOT:-$PROJECT_ROOT}"
VENV_DIR="${VENV_DIR:-$DEPLOY_ROOT/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON="$VENV_DIR/bin/python"
SERVICE_USER="${SERVICE_USER:-deploy}"
SERVICE_GROUP="${SERVICE_GROUP:-$SERVICE_USER}"
MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-market-report-agent}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
DRY_RUN=false

# ------------------------------------------------------------------
# SUDO detection: passwordless sudo is required for systemd operations
# ------------------------------------------------------------------
if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
  echo "Running as root — no sudo prefix needed."
elif command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
  SUDO="sudo -n"
  echo "Running as non-root with passwordless sudo."
else
  echo "ERROR: Passwordless sudo is required for systemd deployment." >&2
  echo "Add to /etc/sudoers or /etc/sudoers.d/$USER:" >&2
  echo "  $USER ALL=(ALL) NOPASSWD: /usr/bin/install, /usr/bin/systemctl, /usr/bin/journalctl" >&2
  exit 1
fi

echo "============================================="
echo "   [STAGE 1/6] Initializing Deploy Environment"
echo "============================================="
echo "DEPLOY_ROOT: $DEPLOY_ROOT"
echo "VENV_DIR:    $VENV_DIR"
echo "SYSTEMD_DIR: $SYSTEMD_DIR"

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "Dry-run mode is ENABLED."
elif [[ $# -gt 0 ]]; then
  echo "ERROR: unsupported argument: $1" >&2
  exit 2
fi

if [[ ! -d "$DEPLOY_ROOT" ]]; then
  echo "ERROR: DEPLOY_ROOT does not exist: $DEPLOY_ROOT" >&2
  exit 1
fi

SERVICE_TEMPLATE="$DEPLOY_ROOT/systemd/$MCP_SERVICE_NAME.service.template"
RENDERED_SERVICE="$DEPLOY_ROOT/runtime/$MCP_SERVICE_NAME.service"
if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
  echo "ERROR: systemd template not found: $SERVICE_TEMPLATE" >&2
  exit 1
fi

mkdir -p "$DEPLOY_ROOT/data" "$DEPLOY_ROOT/logs" "$DEPLOY_ROOT/runtime"
cd "$DEPLOY_ROOT"

echo "============================================="
echo "   [STAGE 2/6] Rendering Systemd Service"
echo "============================================="
render_service() {
  "$PYTHON_BIN" scripts/render_systemd_service.py \
    --template "$SERVICE_TEMPLATE" \
    --output "$RENDERED_SERVICE" \
    --deploy-root "$DEPLOY_ROOT" \
    --venv-dir "$VENV_DIR" \
    --service-user "$SERVICE_USER" \
    --service-group "$SERVICE_GROUP"

  if grep -Eq '\{\{[^}]+\}\}' "$RENDERED_SERVICE"; then
    echo "ERROR: rendered service contains unresolved placeholders" >&2
    exit 1
  fi
}

render_service
echo "Systemd unit successfully rendered to $RENDERED_SERVICE"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "DRY RUN PASS: rendered service matches template guidelines."
  exit 0
fi

if [[ ! -f "$DEPLOY_ROOT/.env" ]]; then
  echo "ERROR: required environment file missing: $DEPLOY_ROOT/.env" >&2
  exit 1
fi

echo "============================================="
echo "   [STAGE 3/6] Setting Up Python Venv & Deps"
echo "============================================="
if [[ ! -x "$PYTHON" ]]; then
  echo "Creating virtual environment at $VENV_DIR..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: venv Python does not exist or is not executable: $PYTHON" >&2
  exit 1
fi

echo "Installing pip and requirements..."
PIP_FLAGS=""
if [ "${PIP_QUIET:-0}" = "1" ]; then
  PIP_FLAGS="-q"
fi
"$PYTHON" -m pip install --upgrade pip $PIP_FLAGS
"$PYTHON" -m pip install -r requirements.txt $PIP_FLAGS

echo "============================================="
echo "   [STAGE 4/6] Running Unit Tests (Optional)"
echo "============================================="
if [[ "${RUN_DEPLOY_TESTS:-0}" == "1" ]]; then
  echo "RUN_DEPLOY_TESTS is enabled. Executing pytest suite..."
  "$PYTHON" -m pytest -q
else
  echo "Skipping full pytest suite (RUN_DEPLOY_TESTS is not enabled; default for CD)."
fi

echo "============================================="
echo "   [STAGE 5/6] Copying and Restarting Service"
echo "============================================="
echo "Installing service unit to $SYSTEMD_DIR..."
$SUDO install -m 0644 "$RENDERED_SERVICE" "$SYSTEMD_DIR/$MCP_SERVICE_NAME.service"

echo "Reloading systemd daemon..."
$SUDO systemctl daemon-reload
echo "Enabling and restarting service $MCP_SERVICE_NAME..."
$SUDO systemctl enable "$MCP_SERVICE_NAME"
$SUDO systemctl restart "$MCP_SERVICE_NAME"

echo "Waiting for service initialization (2s)..."
sleep 2

if ! $SUDO systemctl --no-pager --full is-active "$MCP_SERVICE_NAME" >/dev/null 2>&1; then
  echo "ERROR: $MCP_SERVICE_NAME failed to start" >&2
  $SUDO systemctl --no-pager --full status "$MCP_SERVICE_NAME" || true
  exit 1
fi
echo "Service is successfully running!"

echo "============================================="
echo "   [STAGE 6/6] Verifying Service & Smoke Tests"
echo "============================================="
# Load MARKET_DATA_PROVIDER explicitly from .env
MARKET_DATA_PROVIDER=$("$PYTHON" -c "
import os
from pathlib import Path
env_path = Path('$DEPLOY_ROOT/.env')
val = ''
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8').splitlines():
        if line.strip().startswith('MARKET_DATA_PROVIDER='):
            val = line.split('=', 1)[1].strip().strip('\"\'')
print(val)
")

if [[ -z "$MARKET_DATA_PROVIDER" ]]; then
  echo "ERROR: MARKET_DATA_PROVIDER is missing or empty in .env" >&2
  exit 1
fi

echo "Validating health check using provider: $MARKET_DATA_PROVIDER"
"$PYTHON" scripts/market_report_agent.py --health --provider "$MARKET_DATA_PROVIDER"

echo "Running smoke tests..."
if [[ -f "$DEPLOY_ROOT/scripts/smoke_test.py" ]]; then
  "$PYTHON" scripts/smoke_test.py
elif [[ -f "$DEPLOY_ROOT/scripts/smoke_test.sh" ]]; then
  bash scripts/smoke_test.sh
else
  echo "ERROR: no smoke test script found" >&2
  exit 1
fi

echo "Deploy complete: root=$DEPLOY_ROOT venv=$VENV_DIR service=$MCP_SERVICE_NAME"
