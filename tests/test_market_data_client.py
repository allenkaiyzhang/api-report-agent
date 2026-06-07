"""Tests for MarketDataClient interface, MockMarketDataClient, and LongbridgeMcpClient."""

from __future__ import annotations

import os
import unittest

from clients.market_data_client import (
    Candle,
    IntradayPoint,
    MarketDataClient,
    MarketReportDataset,
    MarketStatusInfo,
    Quote,
)
from clients.longbridge_mcp_client import LongbridgeMcpClient
from clients.mock_market_data_client import MockMarketDataClient


class TestMarketDataClientInterface(unittest.TestCase):
    """Verify MarketDataClient abstract interface."""

    def test_abstract_class_cannot_instantiate(self):
        with self.assertRaises(TypeError):
            MarketDataClient()  # type: ignore[abstract]

    def test_mock_client_implements_interface(self):
        client = MockMarketDataClient()
        self.assertIsInstance(client, MarketDataClient)
        self.assertEqual(client.provider_name, "mock")

    def test_longbridge_client_implements_interface(self):
        client = LongbridgeMcpClient(
            mcp_url="https://mcp.longbridge.com",
            auth_header="test-token",
        )
        self.assertIsInstance(client, MarketDataClient)
        self.assertEqual(client.provider_name, "longbridge_mcp")


class TestMockMarketDataClient(unittest.TestCase):
    """Verify MockMarketDataClient produces valid data."""

    def setUp(self):
        self.client = MockMarketDataClient(seed=42)

    def test_health_check(self):
        result = self.client.health_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "mock")

    def test_get_quotes_known_symbols(self):
        quotes = self.client.get_quotes(["QQQ", "SGOV"])
        self.assertEqual(len(quotes), 2)
        for q in quotes:
            self.assertIsInstance(q, Quote)
            self.assertGreater(q.latest_price, 0)
            self.assertGreaterEqual(q.volume, 0)
            self.assertTrue(q.timestamp)

    def test_get_quotes_unknown_symbol(self):
        quotes = self.client.get_quotes(["MYSTERY.US"])
        self.assertEqual(len(quotes), 1)
        self.assertGreater(quotes[0].latest_price, 0)

    def test_get_quotes_hk_market(self):
        quotes = self.client.get_quotes(["0700.HK"])
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].market, "HK")

    def test_get_candles(self):
        candles = self.client.get_candles(["QQQ"], count=20)
        self.assertEqual(len(candles), 20)
        for c in candles:
            self.assertIsInstance(c, Candle)
            self.assertGreater(c.close, 0)
            self.assertEqual(c.market, "US")

    def test_get_intraday(self):
        points = self.client.get_intraday(["QQQ"])
        self.assertEqual(len(points), 24)
        for p in points:
            self.assertIsInstance(p, IntradayPoint)
            self.assertGreater(p.price, 0)
            self.assertTrue(p.timestamp)

    def test_get_market_status(self):
        statuses = self.client.get_market_status(["US", "HK"])
        self.assertEqual(len(statuses), 2)
        for s in statuses:
            self.assertIsInstance(s, MarketStatusInfo)
            self.assertIn(s.market, ["US", "HK"])

    def test_get_fundamentals(self):
        funds = self.client.get_fundamentals(["QQQ"])
        self.assertEqual(len(funds), 1)
        self.assertEqual(funds[0].symbol, "QQQ")
        self.assertGreater(funds[0].eps, 0)


class TestLongbridgeMcpClient(unittest.TestCase):
    """Verify LongbridgeMcpClient behavior."""

    def test_no_auth_fails_health_check(self):
        """Health check should fail without auth header."""
        old_auth = os.environ.pop("LONGBRIDGE_MCP_AUTH_HEADER", None)
        try:
            client = LongbridgeMcpClient(
                mcp_url="https://mcp.longbridge.com",
                auth_header="",
            )
            h = client.health_check()
            self.assertFalse(h["ok"])
            self.assertEqual(h["status"], "not_configured")
        finally:
            if old_auth:
                os.environ["LONGBRIDGE_MCP_AUTH_HEADER"] = old_auth

    def test_trading_always_disabled(self):
        client = LongbridgeMcpClient(
            mcp_url="https://mcp.longbridge.com",
            auth_header="test",
        )
        self.assertFalse(client.is_trading_enabled())

    def test_account_read_disabled_by_default(self):
        client = LongbridgeMcpClient(
            mcp_url="https://mcp.longbridge.com",
            auth_header="test",
        )
        self.assertFalse(client.is_account_read_enabled())

    def test_policy_blocks_trading_tools(self):
        client = LongbridgeMcpClient(
            mcp_url="https://mcp.longbridge.com",
            auth_header="test",
        )
        for tool in client.policy.trading_tools:
            with self.assertRaises(PermissionError):
                client.policy.assert_allowed(tool)

    def test_policy_blocks_unknown_tools(self):
        client = LongbridgeMcpClient(
            mcp_url="https://mcp.longbridge.com",
            auth_header="test",
        )
        self.assertFalse(client.policy.is_allowed("invented_tool"))

    def test_tool_discovery_has_no_defaults(self):
        client = LongbridgeMcpClient(
            mcp_url="https://mcp.longbridge.com",
            auth_header="test",
        )
        self.assertIsNone(client.policy.get_mapped_tool("candles"))
        self.assertIsNone(client.policy.get_mapped_tool("market_status"))
        self.assertIsNone(client.policy.get_mapped_tool("quote"))
        self.assertIsNone(client.policy.get_mapped_tool("intraday"))


class TestMarketReportDataset(unittest.TestCase):
    """Verify MarketReportDataset data class."""

    def test_to_dict(self):
        dataset = MarketReportDataset(
            run_id="test-001",
            report_type="intraday_brief",
            market="US",
            symbols=["QQQ"],
            collected_at="2026-06-06T10:00:00Z",
        )
        d = dataset.to_dict()
        self.assertEqual(d["run_id"], "test-001")
        self.assertEqual(d["report_type"], "intraday_brief")
        self.assertEqual(d["market"], "US")
        self.assertFalse(d["validated"])

    def test_with_quotes_converts(self):
        client = MockMarketDataClient()
        quotes = client.get_quotes(["QQQ"])
        dataset = MarketReportDataset(
            run_id="test-002",
            report_type="daily_close_report",
            market="US",
            symbols=["QQQ"],
            quotes=quotes,
            collected_at="2026-06-06T10:00:00Z",
            validated=True,
        )
        d = dataset.to_dict()
        self.assertTrue(d["validated"])
        self.assertEqual(len(d["quotes"]), 1)
        self.assertEqual(d["quotes"][0]["symbol"], "QQQ")


if __name__ == "__main__":
    unittest.main()
