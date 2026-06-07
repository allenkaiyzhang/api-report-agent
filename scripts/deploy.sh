#!/usr/bin/env bash
# deploy.sh — Deploy api-report-agent and Market Report Agent
# Creates/updates venv, installs dependencies, sets up systemd service.
# Uses continuous service model (no timer — scheduler runs inside the Python process).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Override when deploying to ECS/VPS
DEPLOY_ROOT="${DEPLOY_ROOT:-$PROJECT_ROOT}"
VENV_DIR="${VENV_DIR:-$DEPLOY_ROOT/.venv}"
PYTHON="$VENV_DIR/bin/python"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_FILE="$PROJECT_ROOT/deploy.log"

SERVICE_NAME="${SERVICE_NAME:-api-report-agent}"
MCP_SERVICE_NAME="market-report-agent"
SYSTEMD_DIR="/etc/systemd/system"

mkdir -p "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs" "$PROJECT_ROOT/runtime"
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

cd "$PROJECT_ROOT"

echo "=== Deploying from $PROJECT_ROOT ==="
echo ""

# --- .env check ---
if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  echo "WARNING: .env is missing. Copy .env.example to .env and configure it."
  echo "  At minimum, set MARKET_DATA_PROVIDER=longbridge_mcp and LONGBRIDGE_MCP_AUTH_HEADER."
fi

# --- Create/update virtualenv ---
if [[ ! -x "$PYTHON" ]]; then
    echo "--- Creating virtualenv (missing or not executable) ---"
    rm -rf "$VENV_DIR"
    $PYTHON_BIN -m venv "$VENV_DIR"
fi

"$PYTHON" --version
"$PYTHON" -m pip --version
"$PYTHON" -m pip install --upgrade pip -q

echo "--- Installing dependencies ---"
"$PYTHON" -m pip install -r requirements.txt -q

# --- Quick import check ---
echo "--- Python import health check ---"
"$PYTHON" -c "
from clients.market_data_client import MarketDataClient
from clients.mock_market_data_client import MockMarketDataClient
from clients.longbridge_mcp_client import LongbridgeMcpClient
from app.policy.tool_policy import LongbridgeToolPolicy
from core.mcp_collector import McpDataCollector
from core.mcp_cleaner import McpDataCleaner
from core.mcp_validator import McpDataValidator
from core.mcp_report_generator import ReportGenerator
from core.mcp_scheduler import McpScheduler
from core.mcp_notifier import ConsoleNotifier, NotifierResult, create_notifiers
from core.mcp_datastore import McpDataStore
print('All imports OK')
"

# --- systemd: market-report-agent service (continuous scheduler) ---
MCP_SERVICE_FILE="$PROJECT_ROOT/systemd/$MCP_SERVICE_NAME.service"
if [ -f "$MCP_SERVICE_FILE" ]; then
    echo "--- Installing systemd: $MCP_SERVICE_NAME ---"
    cp "$MCP_SERVICE_FILE" "$SYSTEMD_DIR/$MCP_SERVICE_NAME.service"
    systemctl daemon-reload
    systemctl enable "$MCP_SERVICE_NAME"
    systemctl restart "$MCP_SERVICE_NAME"
    sleep 2
    if ! systemctl --no-pager --full is-active "$MCP_SERVICE_NAME" > /dev/null 2>&1; then
        echo "ERROR: $MCP_SERVICE_NAME failed to start"
        systemctl --no-pager --full status "$MCP_SERVICE_NAME" || true
        exit 1
    fi
    echo "$MCP_SERVICE_NAME is active"
else
    echo "WARNING: $MCP_SERVICE_FILE not found; skipping systemd install"
fi

# --- Health check ---
echo ""
echo "--- Health check ---"
if ! "$PYTHON" scripts/market_report_agent.py --health 2>&1; then
    echo "ERROR: Health check failed"
    exit 1
fi

# --- Smoke test (uses venv python) ---
echo ""
echo "--- Smoke test ---"
# Run smoke test via Python script for cross-platform compatibility
if [ -f "$PROJECT_ROOT/scripts/smoke_test.py" ]; then
    if ! "$PYTHON" scripts/smoke_test.py 2>&1; then
        echo "ERROR: Smoke test failed"
        exit 1
    fi
elif [ -f "$PROJECT_ROOT/scripts/smoke_test.sh" ]; then
    if ! bash scripts/smoke_test.sh 2>&1; then
        echo "ERROR: Smoke test failed"
        exit 1
    fi
else
    echo "WARNING: No smoke test script found"
fi

echo ""
echo "=== Deploy complete ==="
