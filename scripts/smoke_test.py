#!/usr/bin/env python
"""Cross-platform smoke test for Market Report Agent.

Uses MockMarketDataClient and ConsoleNotifier — no real Longbridge OAuth needed.
Tests all core paths: imports, mock data, tool policy, pipeline, reports, notification.

Usage:
  python scripts/smoke_test.py
  python scripts/smoke_test.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

PASS = 0
FAIL = 0


def run_test(name: str, fn) -> bool:
    global PASS, FAIL
    try:
        fn()
        print(f"  PASS: {name}")
        PASS += 1
        return True
    except Exception as exc:
        print(f"  FAIL: {name} — {exc}")
        FAIL += 1
        return False


def test_imports():
    """Verify all core modules import successfully."""
    from clients.market_data_client import MarketDataClient, MarketReportDataset, Quote
    from clients.mock_market_data_client import MockMarketDataClient
    from clients.longbridge_mcp_client import LongbridgeMcpClient
    from app.policy.tool_policy import LongbridgeToolPolicy, ToolCategory
    from core.mcp_cleaner import McpDataCleaner
    from core.mcp_validator import McpDataValidator
    from core.mcp_collector import McpDataCollector
    from core.mcp_scheduler import McpScheduler, RunStatus
    from core.mcp_report_generator import ReportGenerator
    from core.mcp_notifier import ConsoleNotifier, NotifierResult, create_notifiers
    from core.mcp_datastore import McpDataStore


def test_mock_health():
    from clients.mock_market_data_client import MockMarketDataClient
    c = MockMarketDataClient()
    h = c.health_check()
    assert h["ok"], "Health check failed"


def test_mock_quotes():
    from clients.mock_market_data_client import MockMarketDataClient
    c = MockMarketDataClient()
    q = c.get_quotes(["QQQ"])
    assert len(q) == 1
    assert q[0].latest_price > 0


def test_mock_market_status():
    from clients.mock_market_data_client import MockMarketDataClient
    c = MockMarketDataClient()
    s = c.get_market_status(["US"])
    assert len(s) == 1
    assert s[0].is_open


def test_tool_policy_trading_blocked():
    from app.policy.tool_policy import LongbridgeToolPolicy
    policy = LongbridgeToolPolicy()
    for tool in policy.trading_tools:
        result = policy.check_tool(tool)
        assert not result.allowed, f"Trading tool {tool} should be blocked"


def test_tool_policy_account_read_disabled():
    from app.policy.tool_policy import LongbridgeToolPolicy
    policy = LongbridgeToolPolicy(account_read_enabled=False)
    for tool in policy.account_read_tools:
        result = policy.check_tool(tool)
        assert not result.allowed, f"Account-read tool {tool} should be blocked"


def test_tool_policy_account_read_enabled():
    from app.policy.tool_policy import LongbridgeToolPolicy
    policy = LongbridgeToolPolicy(account_read_enabled=True)
    for tool in policy.account_read_tools:
        result = policy.check_tool(tool)
        assert result.allowed, f"Account-read tool {tool} should be allowed when enabled"


def test_tool_policy_default_deny_unknown():
    from app.policy.tool_policy import LongbridgeToolPolicy
    policy = LongbridgeToolPolicy()
    result = policy.check_tool("some_invented_tool")
    assert not result.allowed, "Unknown tool should be blocked"


def test_tool_policy_allowed_market_tools():
    from app.policy.tool_policy import LongbridgeToolPolicy
    policy = LongbridgeToolPolicy()
    discovered = [
        "quote", "candlesticks", "intraday", "market_status", "trading_session",
    ]
    policy.update_from_discovery(discovered)
    assert policy.allowed_market_tools == frozenset(discovered)
    for tool in discovered:
        result = policy.check_tool(tool)
        assert result.allowed, f"Market tool {tool} should be allowed"


def test_tool_policy_permission_error():
    from app.policy.tool_policy import LongbridgeToolPolicy
    policy = LongbridgeToolPolicy()
    try:
        policy.assert_allowed("submit_order")
        raise AssertionError("Should have raised PermissionError")
    except PermissionError:
        pass


def test_pipeline_collect_clean_validate():
    from clients.mock_market_data_client import MockMarketDataClient
    from core.mcp_collector import McpDataCollector
    from core.mcp_cleaner import McpDataCleaner
    from core.mcp_validator import McpDataValidator

    c = MockMarketDataClient()
    collector = McpDataCollector(c)
    dataset = collector.collect(["QQQ", "SGOV"], "US", "intraday_brief")

    cleaner = McpDataCleaner()
    dataset = cleaner.clean(dataset)

    validator = McpDataValidator()
    dataset = validator.validate(dataset)
    assert dataset.validated, f"Validation failed: {dataset.validation_errors}"


def test_intraday_brief():
    from clients.mock_market_data_client import MockMarketDataClient
    from core.mcp_collector import McpDataCollector
    from core.mcp_cleaner import McpDataCleaner
    from core.mcp_validator import McpDataValidator
    from core.mcp_report_generator import ReportGenerator

    c = MockMarketDataClient()
    dataset = McpDataCollector(c).collect(["QQQ"], "US", "intraday_brief")
    dataset = McpDataCleaner().clean(dataset)
    dataset = McpDataValidator().validate(dataset)

    gen = ReportGenerator()
    report = gen.generate_intraday_brief(dataset)
    assert "Intraday Brief" in report
    assert "QQQ" in report


def test_daily_close_report():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from core.mcp_validator import McpDataValidator
    from core.mcp_report_generator import ReportGenerator

    now = datetime(2026, 6, 5, 16, 30, tzinfo=ZoneInfo("America/New_York"))
    dataset = _valid_daily_close_dataset()
    dataset = McpDataValidator(now_provider=lambda: now).validate(dataset)

    gen = ReportGenerator()
    report = gen.generate_daily_close_report(dataset)
    assert "Daily Close Report" in report


def test_event_alert_no_trigger():
    from clients.mock_market_data_client import MockMarketDataClient
    from core.mcp_collector import McpDataCollector
    from core.mcp_cleaner import McpDataCleaner
    from core.mcp_validator import McpDataValidator
    from core.mcp_report_generator import ReportGenerator

    c = MockMarketDataClient()
    dataset = McpDataCollector(c).collect(["SGOV"], "US", "event_alert")
    dataset = McpDataCleaner().clean(dataset)
    dataset = McpDataValidator().validate(dataset)

    gen = ReportGenerator()
    report = gen.generate_event_alert(dataset)
    assert report is None


def test_console_notifier():
    from core.mcp_notifier import ConsoleNotifier
    n = ConsoleNotifier()
    ok = n.send("Smoke Test", "Test body", "report")
    assert ok


def test_notifier_result():
    from core.mcp_notifier import NotifierResult
    r = NotifierResult(success=True, channel="test")
    assert r.success
    r2 = NotifierResult(success=False, channel="test", error_message="fail")
    assert not r2.success
    assert r2.error_message == "fail"


def test_composite_notifier_partial_failure():
    from core.mcp_notifier import CompositeNotifier, ConsoleNotifier

    class FailingNotifier(ConsoleNotifier):
        def send(self, subject, body, report_type="report"):
            raise RuntimeError("simulated failure")

    composite = CompositeNotifier([FailingNotifier(), ConsoleNotifier()])
    results = composite.send("Subj", "Body")
    success_count = sum(1 for r in results if r.success)
    assert success_count == 1, f"Expected 1 success, got {success_count}"
    assert len(results) == 2


def test_run_log_write_read():
    from core.mcp_datastore import McpDataStore

    tmp = tempfile.mkdtemp()
    log_path = os.path.join(tmp, "run_logs.jsonl")
    store = McpDataStore(run_logs_path=log_path)

    now = datetime.now(timezone.utc).isoformat()
    store.log_run("smoke-001", "intraday_brief", "US", "QQQ", "DATA_COLLECTED", now)
    store.log_run("smoke-001", "intraday_brief", "US", "QQQ", "REPORT_GENERATED", now)

    runs = store.get_recent_runs("intraday_brief", "US", limit=10)
    assert len(runs) == 2


def test_dedup_persistence():
    from core.mcp_scheduler import DedupStore

    tmp = tempfile.mkdtemp()
    dedup_path = os.path.join(tmp, "dedup.jsonl")
    store = DedupStore(path=dedup_path)

    key = "US:QQQ:intraday_brief:2026-06-06:regular"
    assert not store.has_run(key)
    store.mark_run(key, "run-1", "intraday_brief", "US", "QQQ")
    assert store.has_run(key)

    # Simulate restart: new store instance loads from disk
    store2 = DedupStore(path=dedup_path)
    assert store2.has_run(key)


def test_schema_validation_quote():
    import jsonschema
    from pathlib import Path
    schema_dir = _PROJECT_ROOT / "config" / "schemas"
    schema = json.loads((schema_dir / "quote.schema.json").read_text(encoding="utf-8"))

    valid = {
        "symbol": "QQQ",
        "market": "US",
        "latest_price": 445.20,
        "timestamp": "2026-06-06T10:00:00Z",
    }
    jsonschema.validate(valid, schema)

    try:
        jsonschema.validate({"symbol": "QQQ"}, schema)
        raise AssertionError("Should have failed")
    except jsonschema.ValidationError:
        pass


def test_market_status_schema():
    import jsonschema
    schema_dir = _PROJECT_ROOT / "config" / "schemas"
    schema = json.loads((schema_dir / "market_status.schema.json").read_text(encoding="utf-8"))

    valid = {
        "market": "US",
        "is_open": True,
        "session": "regular",
    }
    jsonschema.validate(valid, schema)

    try:
        jsonschema.validate({"market": "US", "is_open": True, "session": "invalid"}, schema)
        raise AssertionError("Should have failed")
    except jsonschema.ValidationError:
        pass


def test_longbridge_client_no_oauth_fails():
    """LongbridgeMcpClient without auth should fail health check."""
    from clients.longbridge_mcp_client import LongbridgeMcpClient
    # Remove env vars
    old_auth = os.environ.pop("LONGBRIDGE_MCP_AUTH_HEADER", None)
    try:
        client = LongbridgeMcpClient(mcp_url="https://mcp.longbridge.com", auth_header="")
        h = client.health_check()
        assert not h["ok"], f"Should fail without auth, got: {h}"
        assert h["status"] == "not_configured"
    finally:
        if old_auth:
            os.environ["LONGBRIDGE_MCP_AUTH_HEADER"] = old_auth


def test_validation_intraday_blocked_when_closed():
    """Intraday report should be blocked when market is closed."""
    from datetime import datetime, timezone
    from clients.market_data_client import MarketReportDataset, MarketStatusInfo, Quote
    from core.mcp_validator import McpDataValidator

    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    friday_ts = "2026-06-05T10:00:00Z"  # Friday (not weekend)
    status = MarketStatusInfo(market="US", is_open=False, session="closed",
                              timestamp=friday_ts)
    quotes = [Quote(symbol="QQQ", market="US", latest_price=100.0, previous_close=100.0,
                    change_percent=0.0, open=100.0, high=100.0, low=100.0,
                    volume=1000, turnover=100000.0, bid=99.0, ask=101.0,
                    trade_status="normal", currency="USD",
                    timestamp=now_ts, source="mock")]
    dataset = MarketReportDataset(
        run_id="test", report_type="intraday_brief", market="US",
        symbols=["QQQ"], quotes=quotes, market_status=status,
        collected_at=now_ts,
    )
    validator = McpDataValidator()
    validated = validator.validate(dataset)
    assert not validated.validated
    assert any("Intraday report requires open" in e for e in validated.validation_errors)


def test_validation_daily_close_allowed_after_close():
    """Daily close report should be allowed when market is closed."""
    from datetime import datetime, timezone
    from clients.market_data_client import MarketReportDataset, MarketStatusInfo, Quote, Candle, IntradayPoint
    from core.mcp_validator import McpDataValidator

    from zoneinfo import ZoneInfo
    now = datetime(2026, 6, 5, 16, 30, tzinfo=ZoneInfo("America/New_York"))
    dataset = _valid_daily_close_dataset()
    validator = McpDataValidator(now_provider=lambda: now)
    validated = validator.validate(dataset)
    assert validated.validated, f"Should pass: {validated.validation_errors}"


def _valid_daily_close_dataset():
    from clients.market_data_client import (
        Candle, IntradayPoint, MarketReportDataset, MarketStatusInfo, Quote,
    )

    data_ts = "2026-06-05T16:20:00-04:00"
    status = MarketStatusInfo(
        market="US",
        is_open=False,
        session="closed",
        current_session_close="2026-06-05T16:00:00-04:00",
        timestamp="2026-06-05T16:30:00-04:00",
    )
    quote = Quote(
        symbol="QQQ", market="US", latest_price=100.0, previous_close=99.0,
        change_percent=1.01, open=99.0, high=101.0, low=98.0, volume=1000,
        turnover=100000.0, bid=99.9, ask=100.1, trade_status="normal",
        currency="USD", timestamp=data_ts, source="mock",
    )
    candle = Candle(
        symbol="QQQ", market="US", close=100.0, open=99.0, low=98.0,
        high=101.0, volume=1000, turnover=100000.0,
        timestamp="2026-06-05", trade_session="regular", source="mock",
    )
    point = IntradayPoint(
        symbol="QQQ", market="US", price=100.0, volume=500, turnover=50000.0,
        timestamp=data_ts, source="mock",
    )
    return MarketReportDataset(
        run_id="daily-smoke", report_type="daily_close_report", market="US",
        symbols=["QQQ"], quotes=[quote], candles=[candle], intraday=[point],
        market_status=status, collected_at=data_ts,
    )


def test_scheduler_dedup_key():
    from core.mcp_scheduler import McpScheduler, DedupStore
    from unittest.mock import MagicMock
    from core.mcp_datastore import McpDataStore
    import tempfile, os

    tmp = tempfile.mkdtemp()
    dedup_path = os.path.join(tmp, "dedup.jsonl")
    run_log_path = os.path.join(tmp, "run_logs.jsonl")

    client = MagicMock()
    store = McpDataStore(run_logs_path=run_log_path)
    scheduler = McpScheduler(client, datastore=store)
    scheduler._dedup = DedupStore(path=dedup_path)

    key = scheduler._make_window_key("US", "QQQ", "intraday_brief", "2026-06-06", "regular")
    assert scheduler._make_window_key("US", "SGOV", "intraday_brief", "2026-06-06", "regular") != key


def test_provider_selection_precedence():
    """Verify provider resolution logic."""
    # This tests the _resolve_provider function directly
    import argparse
    from scripts.market_report_agent import _resolve_provider

    # With explicit provider
    args = argparse.Namespace(provider="mock")
    assert _resolve_provider(args) == "mock"

    # Without any config should fail (SystemExit)
    args2 = argparse.Namespace(provider=None)
    old_env = os.environ.pop("MARKET_DATA_PROVIDER", None)
    old_app_env = os.environ.pop("APP_ENV", None)
    try:
        try:
            _resolve_provider(args2)
            raise AssertionError("Should have raised SystemExit")
        except SystemExit:
            pass
    finally:
        if old_env:
            os.environ["MARKET_DATA_PROVIDER"] = old_env
        if old_app_env:
            os.environ["APP_ENV"] = old_app_env


def test_notifier_status_aggregation():
    """Test DISPATCHED / PARTIAL_FAILED / FAILED resolution."""
    from core.mcp_notifier import NotifierResult
    from scripts.market_report_agent import _resolve_dispatch_status

    # All success
    results = [NotifierResult(success=True, channel="console")]
    assert _resolve_dispatch_status(results) == "DISPATCHED"

    # Partial failure
    results = [
        NotifierResult(success=True, channel="console"),
        NotifierResult(success=False, channel="email", error_message="fail"),
    ]
    assert _resolve_dispatch_status(results) == "PARTIAL_FAILED"

    # All failure
    results = [
        NotifierResult(success=False, channel="email", error_message="fail"),
        NotifierResult(success=False, channel="webhook", error_message="fail"),
    ]
    assert _resolve_dispatch_status(results) == "FAILED"

    # None channels
    assert _resolve_dispatch_status([]) == "DISPATCHED"


# ── Main ──────────────────────────────────────────────────────────

def main():
    global PASS, FAIL

    parser = argparse.ArgumentParser(description="Market Report Agent Smoke Test")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Load env if present
    env_file = _PROJECT_ROOT / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)

    print("============================================")
    print("  Market Report Agent — Smoke Test")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print("============================================")
    print()

    # ── Environment ────────────────────────────────────────────
    print("--- Environment ---")
    run_test("Python version", lambda: print(sys.version.split()[0]))

    # ── Imports ────────────────────────────────────────────────
    print("\n--- Imports ---")
    run_test("Core imports", test_imports)

    # ── Mock Data Client ───────────────────────────────────────
    print("\n--- Mock Data Client ---")
    run_test("Mock health check", test_mock_health)
    run_test("Mock quotes", test_mock_quotes)
    run_test("Mock market status", test_mock_market_status)

    # ── Tool Policy ────────────────────────────────────────────
    print("\n--- Security Policy ---")
    run_test("Trading tools blocked", test_tool_policy_trading_blocked)
    run_test("Account-read blocked by default", test_tool_policy_account_read_disabled)
    run_test("Account-read allowed when enabled", test_tool_policy_account_read_enabled)
    run_test("Default-deny unknown tools", test_tool_policy_default_deny_unknown)
    run_test("Allowed market tools pass", test_tool_policy_allowed_market_tools)
    run_test("Trading tools raise PermissionError", test_tool_policy_permission_error)

    # ── Data Pipeline ──────────────────────────────────────────
    print("\n--- Data Pipeline ---")
    run_test("Collect + clean + validate", test_pipeline_collect_clean_validate)

    # ── Report Generation ──────────────────────────────────────
    print("\n--- Report Generation ---")
    run_test("Intraday brief", test_intraday_brief)
    run_test("Daily close report", test_daily_close_report)
    run_test("Event alert (no trigger)", test_event_alert_no_trigger)

    # ── Notification ───────────────────────────────────────────
    print("\n--- Notification ---")
    run_test("Console notifier", test_console_notifier)
    run_test("NotifierResult model", test_notifier_result)
    run_test("Composite partial failure", test_composite_notifier_partial_failure)
    run_test("Notification status aggregation", test_notifier_status_aggregation)

    # ── Run Logs ───────────────────────────────────────────────
    print("\n--- Run Log Storage ---")
    run_test("Run log write + read", test_run_log_write_read)

    # ── Dedup ─────────────────────────────────────────────────
    print("\n--- Dedup Persistence ---")
    run_test("Dedup survives restart", test_dedup_persistence)
    run_test("Dedup key includes symbol/window", test_scheduler_dedup_key)

    # ── Schema Validation ──────────────────────────────────────
    print("\n--- Schema Validation ---")
    run_test("Quote schema validation", test_schema_validation_quote)
    run_test("Market status schema", test_market_status_schema)

    # ── Longbridge MCP Client ──────────────────────────────────
    print("\n--- Longbridge MCP Client ---")
    run_test("Missing OAuth fails health", test_longbridge_client_no_oauth_fails)

    # ── Validation (report-type aware) ─────────────────────────
    print("\n--- Report-type Validation ---")
    run_test("Intraday blocked when closed", test_validation_intraday_blocked_when_closed)
    run_test("Daily close allowed after close", test_validation_daily_close_allowed_after_close)

    # ── Provider Selection ─────────────────────────────────────
    print("\n--- Provider Selection ---")
    run_test("Provider precedence / missing fails", test_provider_selection_precedence)

    # ── Summary ────────────────────────────────────────────────
    print()
    print("============================================")
    print(f"  RESULTS: {PASS} passed, {FAIL} failed")
    print("============================================")

    if FAIL > 0:
        print("SMOKE TEST FAILED")
        sys.exit(1)
    else:
        print("SMOKE TEST PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
