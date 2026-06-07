#!/usr/bin/env bash
# ECS/VPS Post-Deployment Verification Script

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-market-report-agent}"

echo "============================================="
echo "   ECS/VPS Post-Deploy Verification Script"
echo "============================================="

# Ensure .venv is activated
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
  source "$PROJECT_ROOT/.venv/bin/activate"
else
  echo "ERROR: .venv/bin/activate not found! Run deploy.sh first." >&2
  exit 1
fi

# Load environment variables from .env if present
if [ -f "$PROJECT_ROOT/.env" ]; then
  MARKET_DATA_PROVIDER=$(python -c "
import os
from pathlib import Path
env_path = Path('$PROJECT_ROOT/.env')
val = ''
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8').splitlines():
        if line.strip().startswith('MARKET_DATA_PROVIDER='):
            val = line.split('=', 1)[1].strip().strip('\"\'')
print(val)
")
  LONGBRIDGE_MCP_AUTH_HEADER=$(python -c "
import os
from pathlib import Path
env_path = Path('$PROJECT_ROOT/.env')
val = ''
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8').splitlines():
        if line.strip().startswith('LONGBRIDGE_MCP_AUTH_HEADER='):
            val = line.split('=', 1)[1].strip().strip('\"\'')
print(val)
")
else
  MARKET_DATA_PROVIDER="${MARKET_DATA_PROVIDER:-}"
  LONGBRIDGE_MCP_AUTH_HEADER="${LONGBRIDGE_MCP_AUTH_HEADER:-}"
fi

if [ -z "$MARKET_DATA_PROVIDER" ]; then
  echo "ERROR: MARKET_DATA_PROVIDER is missing or empty in .env" >&2
  exit 1
fi

echo -e "\n--- Systemd Service Status ---"
if ! systemctl is-active --quiet "$MCP_SERVICE_NAME"; then
  echo "ERROR: $MCP_SERVICE_NAME is not active" >&2
  systemctl --no-pager --full status "$MCP_SERVICE_NAME" || true
  exit 1
fi
systemctl status "$MCP_SERVICE_NAME" --no-pager || true

echo -e "\n--- Recent Journald Logs ---"
journalctl -u "$MCP_SERVICE_NAME" -n 100 --no-pager || true

echo -e "\n--- Running Smoke Tests ---"
python scripts/smoke_test.py

echo -e "\n--- Running Provider-Specific Health Check ---"
echo "MARKET_DATA_PROVIDER=$MARKET_DATA_PROVIDER"

if [ "$MARKET_DATA_PROVIDER" = "longbridge_mcp" ]; then
  if [ -z "$LONGBRIDGE_MCP_AUTH_HEADER" ]; then
    echo "ERROR: MARKET_DATA_PROVIDER is set to longbridge_mcp but LONGBRIDGE_MCP_AUTH_HEADER is missing!" >&2
    exit 1
  fi
  echo "LONGBRIDGE_MCP_AUTH_HEADER configured. Running live Longbridge MCP health check..."
  python scripts/market_report_agent.py --health --provider longbridge_mcp
else
  python scripts/market_report_agent.py --health --provider "$MARKET_DATA_PROVIDER"
fi

echo -e "\n============================================="
echo "     POST-DEPLOY VERIFICATION COMPLETED"
echo "============================================="
