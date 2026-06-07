"""Release-blocker regression tests for the production MCP workflow."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from clients.longbridge_mcp_client import LongbridgeMcpClient
from clients.market_data_client import (
    Candle,
    IntradayPoint,
    MarketReportDataset,
    MarketStatusInfo,
    Quote,
)
from core.mcp_report_generator import ReportGenerator
from core.mcp_validator import McpDataValidator
from scripts.market_report_agent import _create_client

ROOT = Path(__file__).resolve().parents[1]


def _discovered_tools() -> dict:
    return {
        "tools": [
            {"name": "get_stock_quote"},
            {"name": "candlesticks"},
            {"name": "intraday"},
            {"name": "trading_session"},
            {"name": "submit_order"},
            {"name": "unknown_read_tool"},
        ]
    }


def _daily_dataset(
    *,
    close: str = "2026-06-05T16:00:00-04:00",
    data_timestamp: str = "2026-06-05T16:20:00-04:00",
    session: str = "closed",
) -> MarketReportDataset:
    quote = Quote(
        symbol="QQQ", market="US", latest_price=100, previous_close=99,
        change_percent=1, open=99, high=101, low=98, volume=100,
        turnover=10000, bid=99.9, ask=100.1, trade_status="normal",
        currency="USD", timestamp=data_timestamp,
    )
    candle = Candle(
        symbol="QQQ", market="US", close=100, open=99, low=98, high=101,
        volume=100, turnover=10000, timestamp=data_timestamp,
        trade_session="regular",
    )
    point = IntradayPoint(
        symbol="QQQ", market="US", price=100, volume=10, turnover=1000,
        timestamp=data_timestamp,
    )
    status = MarketStatusInfo(
        market="US", is_open=False, session=session,
        current_session_close=close,
        timestamp="2026-06-05T16:30:00-04:00",
    )
    return MarketReportDataset(
        run_id="daily", report_type="daily_close_report", market="US",
        symbols=["QQQ"], quotes=[quote], candles=[candle], intraday=[point],
        market_status=status, collected_at=data_timestamp,
    )


def test_create_client_never_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(SystemExit, match="No market data provider configured"):
        _create_client(argparse.Namespace(provider=None))


def test_environment_mock_is_blocked_outside_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SMOKE_TEST_MODE", raising=False)
    with pytest.raises(SystemExit, match="Mock provider is not allowed"):
        _create_client(argparse.Namespace(provider=None))


def test_longbridge_discovery_failure_blocks_provider() -> None:
    client = LongbridgeMcpClient(auth_header="Bearer test")
    client._list_tools_raw = MagicMock(side_effect=RuntimeError("discovery unavailable"))
    client._call_mcp_raw = MagicMock()

    with pytest.raises(RuntimeError, match="Tool discovery failed"):
        client.get_quotes(["QQQ"])
    client._call_mcp_raw.assert_not_called()


def test_discovered_tools_map_and_policy_is_enforced_before_call() -> None:
    client = LongbridgeMcpClient(auth_header="Bearer test")
    client._list_tools_raw = MagicMock(return_value=_discovered_tools())
    client._call_mcp_raw = MagicMock(return_value=[])

    client.discover_tools()

    assert client.policy.get_mapped_tool("quote") == "get_stock_quote"
    assert client.policy.get_mapped_tool("candles") == "candlesticks"
    assert client.policy.get_mapped_tool("market_status") == "trading_session"
    assert not client.policy.is_allowed("unknown_read_tool")
    assert not client.policy.is_allowed("submit_order")

    client.get_quotes(["QQQ"])
    client._call_mcp_raw.assert_called_with("get_stock_quote", {"symbols": ["QQQ"]})


def test_health_discovery_failure_is_not_healthy() -> None:
    client = LongbridgeMcpClient(auth_header="Bearer test")
    client._list_tools_raw = MagicMock(side_effect=RuntimeError("discovery unavailable"))
    client._call_mcp_raw = MagicMock()
    result = client.health_check()
    assert result["ok"] is False
    assert result["status"] == "discovery_failed"
    client._call_mcp_raw.assert_not_called()


def test_daily_close_valid_and_invalid_cases() -> None:
    now = datetime(2026, 6, 5, 16, 30, tzinfo=ZoneInfo("America/New_York"))
    validator = McpDataValidator(now_provider=lambda: now)

    assert validator.validate(_daily_dataset()).validated
    assert not validator.validate(_daily_dataset(close="")).validated
    assert not validator.validate(
        _daily_dataset(data_timestamp="2026-06-04T16:20:00-04:00")
    ).validated

    early = McpDataValidator(
        now_provider=lambda: datetime(
            2026, 6, 5, 16, 5, tzinfo=ZoneInfo("America/New_York")
        )
    )
    assert not early.validate(_daily_dataset()).validated


def test_report_generator_rejects_unvalidated_dataset() -> None:
    with pytest.raises(ValueError, match="must be validated"):
        ReportGenerator().generate_daily_close_report(_daily_dataset())


def test_health_clis_obey_provider_and_exit_codes() -> None:
    env = os.environ.copy()
    env.pop("LONGBRIDGE_MCP_AUTH_HEADER", None)
    env.pop("MARKET_DATA_PROVIDER", None)

    mock = subprocess.run(
        [sys.executable, "scripts/healthcheck_mcp.py", "--json", "--provider", "mock"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert mock.returncode == 0, mock.stdout + mock.stderr

    real = subprocess.run(
        [sys.executable, "scripts/market_report_agent.py", "--health", "--provider", "longbridge_mcp"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert real.returncode != 0
    assert "LONGBRIDGE_MCP_AUTH_HEADER not set" in real.stdout


def test_production_entrypoint_and_deploy_smoke_fallback_are_structural() -> None:
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    redeploy = (ROOT / "redeploy.sh").read_text(encoding="utf-8")
    service = (ROOT / "systemd" / "market-report-agent.service").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert '"$PYTHON" scripts/smoke_test.py' in deploy
    assert "bash scripts/smoke_test.sh" in deploy
    assert '"$PYTHON" scripts/smoke_test.sh' not in deploy
    assert "scripts/market_report_agent.py" in service
    assert "scripts/run_pipeline.py" not in service
    assert "scripts/deploy.sh" in redeploy
    assert "scripts/run_pipeline.py" not in redeploy
    assert "only production workflow entrypoint is `scripts/market_report_agent.py`" in readme
