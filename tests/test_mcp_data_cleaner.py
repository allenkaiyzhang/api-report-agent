"""Tests for MCP data cleaning pipeline."""

from __future__ import annotations

import unittest

from clients.market_data_client import (
    Candle,
    IntradayPoint,
    MarketReportDataset,
    MarketStatusInfo,
    Quote,
)
from core.mcp_cleaner import McpDataCleaner


class TestMcpDataCleaner(unittest.TestCase):
    """Verify data cleaning: normalization, outlier removal, type coercion."""

    def setUp(self):
        self.cleaner = McpDataCleaner()

    def _make_dataset(self, quotes=None, candles=None, intraday=None):
        return MarketReportDataset(
            run_id="clean-test",
            report_type="intraday_brief",
            market="US",
            symbols=["QQQ"],
            quotes=quotes or [],
            candles=candles or [],
            intraday=intraday or [],
            market_status=MarketStatusInfo(
                market="US", is_open=True, session="regular"
            ),
            collected_at="2026-06-06T10:00:00Z",
        )

    def test_clean_valid_quote(self):
        q = Quote(
            symbol=" QQQ ", market="US", latest_price=445.20,
            previous_close=436.10, change_percent=2.09, open=436.50,
            high=446.00, low=435.00, volume=63000000, turnover=28035000000.0,
            bid=445.15, ask=445.25, trade_status="normal", currency="USD",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        dataset = self._make_dataset(quotes=[q])
        cleaned = self.cleaner.clean(dataset)
        self.assertEqual(len(cleaned.quotes), 1)
        self.assertEqual(cleaned.quotes[0].symbol, "QQQ")

    def test_filter_negative_price(self):
        q = Quote(
            symbol="BAD", market="US", latest_price=-5.0,
            previous_close=10.0, change_percent=0.0, open=10.0,
            high=10.0, low=10.0, volume=1000, turnover=10000.0,
            bid=10.0, ask=10.0, trade_status="normal", currency="USD",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        dataset = self._make_dataset(quotes=[q])
        cleaned = self.cleaner.clean(dataset)
        self.assertEqual(len(cleaned.quotes), 0)

    def test_filter_zero_price(self):
        q = Quote(
            symbol="ZERO", market="US", latest_price=0.0,
            previous_close=10.0, change_percent=0.0, open=10.0,
            high=10.0, low=10.0, volume=1000, turnover=10000.0,
            bid=10.0, ask=10.0, trade_status="normal", currency="USD",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        dataset = self._make_dataset(quotes=[q])
        cleaned = self.cleaner.clean(dataset)
        self.assertEqual(len(cleaned.quotes), 0)

    def test_filter_excessive_change(self):
        q = Quote(
            symbol="CRAZY", market="US", latest_price=200.0,
            previous_close=10.0, change_percent=1900.0, open=10.0,
            high=200.0, low=10.0, volume=1000, turnover=200000.0,
            bid=199.0, ask=201.0, trade_status="normal", currency="USD",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        dataset = self._make_dataset(quotes=[q])
        cleaned = self.cleaner.clean(dataset)
        self.assertEqual(len(cleaned.quotes), 0)

    def test_filter_missing_timestamp(self):
        q = Quote(
            symbol="NOTIME", market="US", latest_price=100.0,
            previous_close=100.0, change_percent=0.0, open=100.0,
            high=100.0, low=100.0, volume=1000, turnover=100000.0,
            bid=100.0, ask=100.0, trade_status="normal", currency="USD",
            timestamp="", source="mock",
        )
        dataset = self._make_dataset(quotes=[q])
        cleaned = self.cleaner.clean(dataset)
        self.assertEqual(len(cleaned.quotes), 0)

    def test_clean_candles_removes_invalid(self):
        bad_candle = Candle(
            symbol="QQQ", market="US", close=0.0, open=100.0,
            low=90.0, high=110.0, volume=1000, turnover=100000.0,
            timestamp="", trade_session="regular", source="mock",
        )
        good_candle = Candle(
            symbol="QQQ", market="US", close=445.0, open=440.0,
            low=438.0, high=446.0, volume=50000000, turnover=22250000000.0,
            timestamp="2026-06-06", trade_session="regular", source="mock",
        )
        dataset = self._make_dataset(candles=[bad_candle, good_candle])
        cleaned = self.cleaner.clean(dataset)
        self.assertEqual(len(cleaned.candles), 1)

    def test_clean_intraday_removes_invalid(self):
        bad = IntradayPoint(
            symbol="QQQ", market="US", price=0.0, volume=100,
            turnover=10000.0, timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        good = IntradayPoint(
            symbol="QQQ", market="US", price=445.0, volume=1000000,
            turnover=445000000.0, timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        dataset = self._make_dataset(intraday=[bad, good])
        cleaned = self.cleaner.clean(dataset)
        self.assertEqual(len(cleaned.intraday), 1)


if __name__ == "__main__":
    unittest.main()
