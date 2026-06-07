#!/usr/bin/env bash
set -euo pipefail

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

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
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

if [[ "$DRY_RUN" == "true" ]]; then
  echo "DRY RUN PASS: rendered service at $RENDERED_SERVICE"
  exit 0
fi

if [[ ! -f "$DEPLOY_ROOT/.env" ]]; then
  echo "ERROR: required environment file missing: $DEPLOY_ROOT/.env" >&2
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: venv Python does not exist or is not executable: $PYTHON" >&2
  exit 1
fi

"$PYTHON" -m pip install --upgrade pip -q
"$PYTHON" -m pip install -r requirements.txt -q
"$PYTHON" -m pytest -q

cp "$RENDERED_SERVICE" "$SYSTEMD_DIR/$MCP_SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$MCP_SERVICE_NAME"
systemctl restart "$MCP_SERVICE_NAME"
sleep 2
if ! systemctl --no-pager --full is-active "$MCP_SERVICE_NAME" >/dev/null 2>&1; then
  echo "ERROR: $MCP_SERVICE_NAME failed to start" >&2
  systemctl --no-pager --full status "$MCP_SERVICE_NAME" || true
  exit 1
fi

"$PYTHON" scripts/market_report_agent.py --health

if [[ -f "$DEPLOY_ROOT/scripts/smoke_test.py" ]]; then
  "$PYTHON" scripts/smoke_test.py
elif [[ -f "$DEPLOY_ROOT/scripts/smoke_test.sh" ]]; then
  bash scripts/smoke_test.sh
else
  echo "ERROR: no smoke test script found" >&2
  exit 1
fi

echo "Deploy complete: root=$DEPLOY_ROOT venv=$VENV_DIR service=$MCP_SERVICE_NAME"
