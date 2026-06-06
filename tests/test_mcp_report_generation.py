"""Tests for report generation: intraday_brief, daily_close_report, event_alert."""

from __future__ import annotations

import unittest

from clients.market_data_client import (
    MarketReportDataset,
    MarketStatusInfo,
    Quote,
)
from core.mcp_report_generator import ReportGenerator


class TestReportGenerator(unittest.TestCase):
    """Verify report generation produces valid Markdown."""

    def setUp(self):
        self.generator = ReportGenerator(
            price_change_threshold_pct=3.0,
            volume_spike_ratio=3.0,
        )

    def _make_quotes(self, *pairs):
        """Create quotes from (symbol, change%) pairs."""
        quotes = []
        for symbol, change in pairs:
            prev = 100.0
            latest = prev * (1 + change / 100)
            quotes.append(Quote(
                symbol=symbol, market="US", latest_price=round(latest, 2),
                previous_close=prev, change_percent=round(change, 2),
                open=prev, high=round(latest * 1.01, 2), low=round(prev * 0.99, 2),
                volume=1000000, turnover=latest * 1000000,
                bid=round(latest * 0.9999, 2), ask=round(latest * 1.0001, 2),
                trade_status="normal", currency="USD",
                timestamp="2026-06-06T10:00:00Z", source="mock",
            ))
        return quotes

    def _make_dataset(self, quotes, report_type="intraday_brief"):
        return MarketReportDataset(
            run_id="rpt-test",
            report_type=report_type,
            market="US",
            symbols=[q.symbol for q in quotes],
            quotes=quotes,
            market_status=MarketStatusInfo(
                market="US", is_open=True, session="regular",
                timestamp="2026-06-06T10:00:00Z", source="mock",
            ),
            collected_at="2026-06-06T10:00:00Z",
            validated=True,
        )

    def test_intraday_brief_structure(self):
        quotes = self._make_quotes(("QQQ", 2.0), ("SGOV", -0.5))
        dataset = self._make_dataset(quotes)
        report = self.generator.generate_intraday_brief(dataset)
        self.assertIn("Intraday Brief", report)
        self.assertIn("QQQ", report)
        self.assertIn("SGOV", report)
        self.assertIn("Market Snapshot", report)

    def test_intraday_brief_no_quotes(self):
        dataset = self._make_dataset([], report_type="intraday_brief")
        report = self.generator.generate_intraday_brief(dataset)
        self.assertIn("Intraday Brief", report)

    def test_intraday_brief_detects_movers(self):
        quotes = self._make_quotes(("QQQ", 5.0), ("SGOV", 0.1))
        dataset = self._make_dataset(quotes)
        report = self.generator.generate_intraday_brief(dataset)
        self.assertIn("Notable Movers", report)
        self.assertIn("UP", report)

    def test_daily_close_report_structure(self):
        quotes = self._make_quotes(("QQQ", 1.5), ("HSBC.US", -2.0))
        dataset = self._make_dataset(quotes, report_type="daily_close_report")
        report = self.generator.generate_daily_close_report(dataset)
        self.assertIn("Daily Close Report", report)
        self.assertIn("Market Summary", report)
        self.assertIn("Advancers", report)
        self.assertIn("Decliners", report)
        self.assertIn("Top Movers", report)

    def test_daily_close_report_no_quotes(self):
        dataset = self._make_dataset([], report_type="daily_close_report")
        report = self.generator.generate_daily_close_report(dataset)
        self.assertIn("No quote data available", report)

    def test_event_alert_triggers(self):
        quotes = self._make_quotes(("QQQ", 5.0))
        dataset = self._make_dataset(quotes, report_type="event_alert")
        report = self.generator.generate_event_alert(dataset)
        self.assertIsNotNone(report)
        self.assertIn("Event Alert", report)
        self.assertIn("QQQ", report)

    def test_event_alert_no_triggers(self):
        quotes = self._make_quotes(("SGOV", 0.1))
        dataset = self._make_dataset(quotes, report_type="event_alert")
        report = self.generator.generate_event_alert(dataset)
        self.assertIsNone(report)

    def test_event_alert_unvalidated_no_trigger(self):
        quotes = self._make_quotes(("QQQ", 5.0))
        dataset = self._make_dataset(quotes, report_type="event_alert")
        dataset.validated = False
        report = self.generator.generate_event_alert(dataset)
        self.assertIsNone(report)


if __name__ == "__main__":
    unittest.main()
