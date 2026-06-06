"""Tests for MCP scheduler decision logic.

Covers:
  - Market session check
  - Intraday window (open market, interval, dedup)
  - Post-market window (closed market, delay, weekend/holiday skip)
  - Dedup key format
  - Dedup persistence across restarts
  - Skipped reason recording
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from clients.market_data_client import MarketStatusInfo
from core.mcp_datastore import McpDataStore
from core.mcp_scheduler import DedupStore, McpScheduler, RunStatus


class TestRunStatus(unittest.TestCase):
    """Verify RunStatus enum values."""

    def test_run_status_values(self):
        expected = {
            "PENDING", "RUNNING", "DATA_COLLECTED", "DATA_VALIDATED",
            "ANALYZED", "REPORT_GENERATED", "DISPATCHED",
            "PARTIAL_FAILED", "SKIPPED", "FAILED",
        }
        actual = set(RunStatus.__members__.keys())
        self.assertEqual(expected, actual)


class TestDedupStore(unittest.TestCase):
    """Verify dedup store persistence."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dedup_path = os.path.join(self.tmp, "dedup.jsonl")

    def test_basic_dedup(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:intraday_brief:2026-06-06:regular"
        self.assertFalse(store.has_run(key))
        store.mark_run(key, "run-1", "intraday_brief", "US", "QQQ")
        self.assertTrue(store.has_run(key))

    def test_dedup_survives_restart(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:daily_close_report:2026-06-06:close"
        store.mark_run(key, "run-1", "daily_close_report", "US", "QQQ")

        # New store instance (simulates restart)
        store2 = DedupStore(path=self.dedup_path)
        self.assertTrue(store2.has_run(key))

    def test_different_keys(self):
        store = DedupStore(path=self.dedup_path)
        key1 = "US:QQQ:intraday_brief:2026-06-06:regular"
        key2 = "US:SGOV:intraday_brief:2026-06-06:regular"
        key3 = "US:QQQ:daily_close_report:2026-06-06:close"
        store.mark_run(key1, "r1", "intraday_brief", "US", "QQQ")
        self.assertFalse(store.has_run(key2))
        self.assertFalse(store.has_run(key3))


class TestMcpSchedulerLogic(unittest.TestCase):
    """Verify scheduler decision logic: session check, duplicate avoidance."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.tmp = tempfile.mkdtemp()
        self.run_log_path = os.path.join(self.tmp, "run_logs.jsonl")
        self.datastore = McpDataStore(run_logs_path=self.run_log_path)
        self.scheduler = McpScheduler(
            client=self.mock_client,
            tick_seconds=60,
            intraday_interval_hours=2,
            post_market_delay_minutes=15,
            datastore=self.datastore,
        )
        # Use test dedup path
        self.dedup_path = os.path.join(self.tmp, "dedup.jsonl")
        self.scheduler._dedup = DedupStore(path=self.dedup_path)

    def test_is_intraday_window_open_market(self):
        status = MarketStatusInfo(
            market="US", is_open=True, session="regular",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        should_run, reason = self.scheduler._is_intraday_window("US", status, ["QQQ"])
        self.assertTrue(should_run, f"Should run, got reason: {reason}")

    def test_is_intraday_window_closed_market(self):
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        should_run, reason = self.scheduler._is_intraday_window("US", status, ["QQQ"])
        self.assertFalse(should_run)
        self.assertEqual(reason, "market_closed")

    def test_is_intraday_window_duplicate(self):
        """After marking dedup, window should be skipped."""
        status = MarketStatusInfo(
            market="US", is_open=True, session="regular",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        # First call should run
        should_run, _ = self.scheduler._is_intraday_window("US", status, ["QQQ"])
        self.assertTrue(should_run)

        # Mark dedup manually (simulating what the run loop does)
        key = self.scheduler._make_window_key("US", "QQQ", "intraday_brief", "2026-06-06", "regular")
        self.scheduler._dedup.mark_run(key, "r1", "intraday_brief", "US", "QQQ")

        # Second call should be blocked by dedup
        should_run2, reason2 = self.scheduler._is_intraday_window("US", status, ["QQQ"])
        self.assertFalse(should_run2)
        self.assertEqual(reason2, "duplicate_window")

    def test_is_post_market_window_closed_market(self):
        """Friday close should trigger daily close report."""
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            timestamp="2026-06-05T16:30:00Z", source="mock",
        )
        should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
        self.assertTrue(should_run, f"Should run: {reason}")

    def test_is_post_market_window_open_market(self):
        status = MarketStatusInfo(
            market="US", is_open=True, session="regular",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
        self.assertFalse(should_run)
        self.assertEqual(reason, "market_still_open")

    def test_is_post_market_window_holiday(self):
        status = MarketStatusInfo(
            market="US", is_open=False, session="holiday",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
        self.assertFalse(should_run)
        self.assertEqual(reason, "not_trading_day (holiday)")

    def test_is_post_market_window_duplicate(self):
        """After marking in-memory daily cache, second call is blocked."""
        from datetime import datetime, timezone as tz
        now = datetime.now(tz.utc)
        trading_date = now.strftime("%Y-%m-%d")

        # Create status with a weekday timestamp
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            timestamp="2026-06-05T16:30:00Z", source="mock",
        )

        # Manually set the in-memory cache so first call thinks it already ran
        window_key = f"US:daily:{trading_date}"
        self.scheduler._last_daily[window_key] = now.isoformat()

        # The call should be blocked by in-memory dedup
        should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
        self.assertFalse(should_run)
        self.assertIn("duplicate", reason)

    def test_make_window_key_format(self):
        key = self.scheduler._make_window_key("US", "QQQ", "intraday_brief", "2026-06-06", "regular")
        self.assertEqual(key, "US:QQQ:intraday_brief:2026-06-06:regular")

        key2 = self.scheduler._make_window_key("HK", "0700.HK", "daily_close_report", "2026-06-06", "close")
        self.assertEqual(key2, "HK:0700.HK:daily_close_report:2026-06-06:close")

    def test_market_status_check_calls_client(self):
        self.mock_client.get_market_status.return_value = [
            MarketStatusInfo(
                market="US", is_open=True, session="regular",
                timestamp="", source="mock",
            ),
        ]
        statuses = self.scheduler.check_market_status(["US"])
        self.assertIn("US", statuses)
        self.mock_client.get_market_status.assert_called_once_with(["US"])

    def test_record_skip_writes_run_log(self):
        self.scheduler._record_skip(
            "skip-001", "intraday_brief", "US", ["QQQ"], "market_closed"
        )
        runs = self.datastore.get_recent_runs(market="US", limit=10)
        skipped = [r for r in runs if r["status"] == "SKIPPED"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["run_id"], "skip-001")
        self.assertIn("market_closed", skipped[0]["error_message"])


if __name__ == "__main__":
    unittest.main()
