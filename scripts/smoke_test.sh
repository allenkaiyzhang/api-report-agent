#!/usr/bin/env bash
# smoke_test.sh — Smoke test for Market Report Agent & api-report-agent
# Uses MockMarketDataClient and ConsoleNotifier — no real Longbridge OAuth needed.
# Also tests the FastAPI health endpoint if running.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PASS=0
FAIL=0

run_test() {
    local name="$1"
    shift
    echo "=== TEST: $name ==="
    if "$@" > /tmp/smoke_test_output.txt 2>&1; then
        echo "  PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $name"
        cat /tmp/smoke_test_output.txt
        FAIL=$((FAIL + 1))
    fi
}

echo "============================================"
echo "  Market Report Agent — Smoke Test"
echo "  $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "============================================"
echo ""

# --- Python and dependencies ---
echo "--- Environment ---"
run_test "Python available" python --version
run_test "Core imports" python -c "from clients.market_data_client import MarketDataClient; print('OK')"
run_test "Mock client import" python -c "from clients.mock_market_data_client import MockMarketDataClient; print('OK')"
run_test "MCP client import" python -c "from clients.longbridge_mcp_client import LongbridgeMcpClient; print('OK')"
run_test "Cleaner import" python -c "from core.mcp_cleaner import McpDataCleaner; print('OK')"
run_test "Validator import" python -c "from core.mcp_validator import McpDataValidator; print('OK')"
run_test "Collector import" python -c "from core.mcp_collector import McpDataCollector; print('OK')"
run_test "Scheduler import" python -c "from core.mcp_scheduler import McpScheduler; print('OK')"
run_test "Report generator import" python -c "from core.mcp_report_generator import ReportGenerator; print('OK')"
run_test "Notifier import" python -c "from core.mcp_notifier import ConsoleNotifier, create_notifiers; print('OK')"
run_test "Datastore import" python -c "from core.mcp_datastore import McpDataStore; print('OK')"

# --- MockClient smoke ---
echo ""
echo "--- Mock Data Client ---"
run_test "Mock health check" python -c "
from clients.mock_market_data_client import MockMarketDataClient
c = MockMarketDataClient()
h = c.health_check()
assert h['ok'], 'Health check failed'
print('Health: OK')
"

run_test "Mock quotes" python -c "
from clients.mock_market_data_client import MockMarketDataClient
c = MockMarketDataClient()
q = c.get_quotes(['QQQ'])
assert len(q) == 1, f'Expected 1 quote, got {len(q)}'
assert q[0].latest_price > 0, 'Price should be positive'
print(f'QQQ: {q[0].latest_price}')
"

run_test "Mock market status" python -c "
from clients.mock_market_data_client import MockMarketDataClient
c = MockMarketDataClient()
s = c.get_market_status(['US'])
assert len(s) == 1
assert s[0].is_open, 'US market should be open in mock'
print(f'US market: open={s[0].is_open}')
"

# --- Tool policy ---
echo ""
echo "--- Security Policy ---"
run_test "Trading tools blocked" python -c "
from app.policy.tool_policy import LongbridgeToolPolicy
policy = LongbridgeToolPolicy()
for t in policy.trading_tools:
    result = policy.check_tool(t)
    assert not result.allowed, f'Tool {t} should be blocked'
print(f'All {len(policy.trading_tools)} trading tools blocked')
"

run_test "Account-read blocked by default" python -c "
from app.policy.tool_policy import LongbridgeToolPolicy
policy = LongbridgeToolPolicy(account_read_enabled=False)
for t in policy.account_read_tools:
    result = policy.check_tool(t)
    assert not result.allowed, f'Account-read tool {t} should be blocked'
print(f'All {len(policy.account_read_tools)} account-read tools blocked')
"

# --- Data pipeline ---
echo ""
echo "--- Data Pipeline ---"
run_test "Collect + clean + validate" python -c "
from clients.mock_market_data_client import MockMarketDataClient
from core.mcp_collector import McpDataCollector
from core.mcp_cleaner import McpDataCleaner
from core.mcp_validator import McpDataValidator

c = MockMarketDataClient()
collector = McpDataCollector(c)
dataset = collector.collect(['QQQ', 'SGOV'], 'US', 'intraday_brief')

cleaner = McpDataCleaner()
dataset = cleaner.clean(dataset)

validator = McpDataValidator()
dataset = validator.validate(dataset)
assert dataset.validated, f'Validation failed: {dataset.validation_errors}'
print(f'Validated: {len(dataset.quotes)} quotes, {len(dataset.candles)} candles')
"

# --- Report generation ---
echo ""
echo "--- Report Generation ---"
run_test "Intraday brief" python -c "
from clients.mock_market_data_client import MockMarketDataClient
from core.mcp_collector import McpDataCollector
from core.mcp_cleaner import McpDataCleaner
from core.mcp_validator import McpDataValidator
from core.mcp_report_generator import ReportGenerator

c = MockMarketDataClient()
dataset = McpDataCollector(c).collect(['QQQ'], 'US', 'intraday_brief')
dataset = McpDataCleaner().clean(dataset)
dataset = McpDataValidator().validate(dataset)

gen = ReportGenerator()
report = gen.generate_intraday_brief(dataset)
assert 'Intraday Brief' in report
assert 'QQQ' in report
print(f'Report length: {len(report)} chars')
"

run_test "Daily close report" python -c "
from datetime import datetime
from zoneinfo import ZoneInfo
from clients.mock_market_data_client import MockMarketDataClient
from core.mcp_collector import McpDataCollector
from core.mcp_cleaner import McpDataCleaner
from core.mcp_validator import McpDataValidator
from core.mcp_report_generator import ReportGenerator

c = MockMarketDataClient()
dataset = McpDataCollector(c).collect(['QQQ', 'HSBC.US'], 'US', 'daily_close_report')
dataset.market_status.is_open = False
dataset.market_status.session = 'closed'
dataset.market_status.current_session_close = '2026-06-05T16:00:00-04:00'
dataset = McpDataCleaner().clean(dataset)
now = datetime(2026, 6, 5, 16, 30, tzinfo=ZoneInfo('America/New_York'))
dataset = McpDataValidator(now_provider=lambda: now).validate(dataset)

gen = ReportGenerator()
report = gen.generate_daily_close_report(dataset)
assert 'Daily Close Report' in report
print(f'Report length: {len(report)} chars')
"

run_test "Event alert (no trigger)" python -c "
from clients.mock_market_data_client import MockMarketDataClient
from core.mcp_collector import McpDataCollector
from core.mcp_cleaner import McpDataCleaner
from core.mcp_validator import McpDataValidator
from core.mcp_report_generator import ReportGenerator

c = MockMarketDataClient()
dataset = McpDataCollector(c).collect(['SGOV'], 'US', 'event_alert')
dataset = McpDataCleaner().clean(dataset)
dataset = McpDataValidator().validate(dataset)

gen = ReportGenerator()
report = gen.generate_event_alert(dataset)
assert report is None, 'SGOV should not trigger event alert'
print('Event alert correctly not triggered')
"

# --- Notification ---
echo ""
echo "--- Notification ---"
run_test "Console notifier" python -c "
from core.mcp_notifier import ConsoleNotifier
n = ConsoleNotifier()
ok = n.send('Smoke Test', 'Test notification body', 'report')
assert ok, 'Notification failed'
print('Notification sent')
"

# --- Run log ---
echo ""
echo "--- Run Log Storage ---"
run_test "Run log write + read" python -c "
import os, tempfile
from datetime import datetime, timezone
from core.mcp_datastore import McpDataStore

tmp = tempfile.mkdtemp()
log_path = os.path.join(tmp, 'run_logs.jsonl')
store = McpDataStore(run_logs_path=log_path)

now = datetime.now(timezone.utc).isoformat()
store.log_run('smoke-001', 'intraday_brief', 'US', 'QQQ', 'DATA_COLLECTED', now)
store.log_run('smoke-001', 'intraday_brief', 'US', 'QQQ', 'REPORT_GENERATED', now)

runs = store.get_recent_runs('intraday_brief', 'US', limit=10)
assert len(runs) == 2
print(f'Logged {len(runs)} runs')
"

# --- Schema validation ---
echo ""
echo "--- Schema Validation ---"
run_test "Schema validation" python -c "
import jsonschema, json
from pathlib import Path
PROJECT_ROOT = Path('${PROJECT_DIR}')
schema = json.loads((PROJECT_ROOT / 'config/schemas/quote.schema.json').read_text(encoding='utf-8'))
jsonschema.validate({'symbol':'QQQ','market':'US','latest_price':445.20,'timestamp':'2026-06-06T10:00:00Z'}, schema)
try:
    jsonschema.validate({'symbol':'QQQ'}, schema)
    raise SystemExit('Should have failed')
except jsonschema.ValidationError:
    pass
print('Schema validation OK')
"

# --- Agent workflow ---
echo ""
echo "--- Agent Workflow ---"
run_test "Run once (mock)" python scripts/market_report_agent.py --once --provider mock 2>&1

# --- Health check ---
echo ""
echo "--- Health Check ---"
run_test "Health CLI" python scripts/market_report_agent.py --health 2>&1

# --- FastAPI health (if running) ---
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
echo ""
echo "--- FastAPI Health (optional) ---"
if response="$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null || true)"; then
    echo "  FastAPI health: $response"
    if [[ "$response" == *'"status":"ok"'* || "$response" == *'"status": "ok"'* ]]; then
        echo "  PASS: FastAPI health"
        PASS=$((PASS + 1))
    else
        echo "  SKIP: unexpected response"
    fi
else
    echo "  SKIP: FastAPI not running"
fi

# --- Summary ---
echo ""
echo "============================================"
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
    echo "SMOKE TEST FAILED"
    exit 1
else
    echo "SMOKE TEST PASSED"
    exit 0
fi
