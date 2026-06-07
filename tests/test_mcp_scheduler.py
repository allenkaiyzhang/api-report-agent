"""Tests for MCP scheduler decision logic.

Covers:
  - Market session check
  - Intraday window (open market, interval, dedup)
  - Post-market window (closed market, delay, weekend/holiday skip)
  - Dedup key format (market-local timezone)
  - Dedup state tracking (IN_PROGRESS, SUCCESS, FAILED, SKIPPED)
  - Failure does not permanently dedup
  - Post-market delay semantics (current_session_close, not next_close)
  - Skipped reason recording
  - UTC boundary timezone behavior
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from clients.market_data_client import MarketStatusInfo
from core.mcp_datastore import McpDataStore
from core.mcp_scheduler import DedupState, DedupStore, McpScheduler, RunStatus


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
    """Verify dedup store persistence with state tracking."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dedup_path = os.path.join(self.tmp, "dedup.jsonl")

    def test_basic_dedup(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:intraday_brief:2026-06-06:regular"
        self.assertIsNone(store.get_state(key))
        self.assertFalse(store.has_run(key))
        store.mark_state(key, "run-1", DedupState.SUCCESS, "intraday_brief", "US", "QQQ")
        self.assertEqual(store.get_state(key), DedupState.SUCCESS)
        self.assertTrue(store.has_run(key))

    def test_dedup_survives_restart(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:daily_close_report:2026-06-06:close"
        store.mark_state(key, "run-1", DedupState.SUCCESS, "daily_close_report", "US", "QQQ")

        store2 = DedupStore(path=self.dedup_path)
        self.assertTrue(store2.has_run(key))
        self.assertEqual(store2.get_state(key), DedupState.SUCCESS)

    def test_different_keys(self):
        store = DedupStore(path=self.dedup_path)
        key1 = "US:QQQ:intraday_brief:2026-06-06:regular"
        key2 = "US:SGOV:intraday_brief:2026-06-06:regular"
        key3 = "US:QQQ:daily_close_report:2026-06-06:close"
        store.mark_state(key1, "r1", DedupState.SUCCESS, "intraday_brief", "US", "QQQ")
        self.assertIsNone(store.get_state(key2))
        self.assertIsNone(store.get_state(key3))

    def test_failed_state_can_retry(self):
        store = DedupStore(path=self.dedup_path)
        key = "HK:0700.HK:intraday_brief:2026-06-06:regular"
        store.mark_state(key, "r1", DedupState.FAILED, "intraday_brief", "HK", "0700.HK")

        self.assertTrue(store.can_retry(key))
        self.assertFalse(store.has_run(key))
        self.assertFalse(store.is_completed(key))

    def test_success_state_blocks_retry(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:intraday_brief:2026-06-06:regular"
        store.mark_state(key, "r1", DedupState.SUCCESS, "intraday_brief", "US", "QQQ")

        self.assertTrue(store.has_run(key))
        self.assertTrue(store.is_completed(key))
        self.assertFalse(store.can_retry(key))

    def test_skipped_state_is_completed(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:daily_close_report:2026-06-06:close"
        store.mark_state(key, "r1", DedupState.SKIPPED, "daily_close_report", "US", "QQQ")

        self.assertTrue(store.is_completed(key))

    def test_in_progress_not_completed(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:intraday_brief:2026-06-06:regular"
        store.mark_in_progress(key, "r1", "intraday_brief", "US", "QQQ")

        self.assertFalse(store.is_completed(key))
        self.assertFalse(store.has_run(key))
        self.assertTrue(store.is_in_progress(key))

    def test_legacy_mark_run_is_success(self):
        store = DedupStore(path=self.dedup_path)
        key = "US:QQQ:intraday_brief:2026-06-06:regular"
        store.mark_run(key, "r1", "intraday_brief", "US", "QQQ")
        self.assertEqual(store.get_state(key), DedupState.SUCCESS)


class TestMcpSchedulerLogic(unittest.TestCase):
    """Verify scheduler decision logic: session check, duplicate avoidance, timezone."""

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
        self.dedup_path = os.path.join(self.tmp, "dedup.jsonl")
        self.scheduler._dedup = DedupStore(path=self.dedup_path)

    # ── Timezone helpers ────────────────────────────────────────

    def test_us_market_timezone(self):
        tz = self.scheduler.get_market_timezone("US")
        self.assertEqual(str(tz), "America/New_York")

    def test_hk_market_timezone(self):
        tz = self.scheduler.get_market_timezone("HK")
        self.assertEqual(str(tz), "Asia/Hong_Kong")

    def test_trading_date_is_market_local(self):
        """Trading date should be based on market-local time, not UTC."""
        # Mock the timezone so we control the local date
        with patch.object(McpScheduler, "get_market_timezone") as mock_tz:
            mock_tz.return_value = ZoneInfo("America/New_York")
            with patch.object(McpScheduler, "get_local_now") as mock_local:
                # Simulate 1 AM UTC = 9 PM NY (previous day)
                mock_local.return_value = datetime(2026, 6, 6, 21, 0, 0, tzinfo=ZoneInfo("America/New_York"))
                date = self.scheduler.get_trading_date("US")
                self.assertEqual(date, "2026-06-06")

    # ── Intraday window ─────────────────────────────────────────

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

    def test_is_intraday_window_duplicate_by_completion(self):
        """After marking SUCCESS, window should be skipped."""
        status = MarketStatusInfo(
            market="US", is_open=True, session="regular",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        # First call should run
        should_run, _ = self.scheduler._is_intraday_window("US", status, ["QQQ"])
        self.assertTrue(should_run)

        # Mark dedup as SUCCESS
        with patch.object(McpScheduler, "get_local_now") as mock_local:
            mock_local.return_value = datetime(2026, 6, 6, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
            key = self.scheduler._make_window_key("US", "QQQ", "intraday_brief", "2026-06-06", "regular-1000")
            self.scheduler._dedup.mark_state(key, "r1", DedupState.SUCCESS, "intraday_brief", "US", "QQQ")

        # Second call should be blocked by completed dedup
        with patch.object(McpScheduler, "get_local_now") as mock_local:
            mock_local.return_value = datetime(2026, 6, 6, 10, 30, 0, tzinfo=ZoneInfo("America/New_York"))
            should_run2, reason2 = self.scheduler._is_intraday_window("US", status, ["QQQ"])
            self.assertFalse(should_run2)
            self.assertEqual(reason2, "duplicate_window")

    def test_later_intraday_bucket_is_not_duplicate(self):
        status = MarketStatusInfo(market="US", is_open=True, session="regular")
        key = self.scheduler._make_window_key(
            "US", "QQQ", "intraday_brief", "2026-06-06", "regular-1000"
        )
        self.scheduler._dedup.mark_state(
            key, "r1", DedupState.SUCCESS, "intraday_brief", "US", "QQQ"
        )
        with patch.object(McpScheduler, "get_local_now") as mock_local:
            mock_local.return_value = datetime(
                2026, 6, 6, 12, 0, tzinfo=ZoneInfo("America/New_York")
            )
            should_run, reason = self.scheduler._is_intraday_window("US", status, ["QQQ"])
        self.assertTrue(should_run, reason)

    def test_failed_dedup_does_not_block(self):
        """FAILED state should not block retry."""
        status = MarketStatusInfo(
            market="US", is_open=True, session="regular",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        with patch.object(McpScheduler, "get_local_now") as mock_local:
            mock_local.return_value = datetime(2026, 6, 6, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
            key = self.scheduler._make_window_key("US", "QQQ", "intraday_brief", "2026-06-06", "regular")
            self.scheduler._dedup.mark_state(key, "r1", DedupState.FAILED, "intraday_brief", "US", "QQQ")

        # Should still be allowed to run
        with patch.object(McpScheduler, "get_local_now") as mock_local:
            mock_local.return_value = datetime(2026, 6, 6, 12, 0, 0, tzinfo=ZoneInfo("America/New_York"))
            should_run, reason = self.scheduler._is_intraday_window("US", status, ["QQQ"])
            self.assertTrue(should_run, f"FAILED should not block retry: {reason}")

    # ── Post-market window ──────────────────────────────────────

    def test_is_post_market_window_closed_market(self):
        """After close with valid close time should trigger."""
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            current_session_close="2026-06-05T16:00:00-04:00",
            timestamp="2026-06-05T21:00:00Z", source="mock",
        )
        # Mock local time to be after close + delay
        with patch.object(McpScheduler, "get_local_now") as mock_local:
            mock_local.return_value = datetime(2026, 6, 5, 16, 30, 0, tzinfo=ZoneInfo("America/New_York"))
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

    def test_is_post_market_window_missing_close_time(self):
        """Missing session close time should be skipped with clear reason."""
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            current_session_close="", last_close="",
            timestamp="2026-06-05T21:00:00Z", source="mock",
        )
        # Even though day of week could be weekday, no close time → skip
        with patch.object(McpScheduler, "_is_weekend", return_value=False):
            should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
            self.assertFalse(should_run)
            self.assertEqual(reason, "missing_session_close")

    def test_is_post_market_window_before_delay(self):
        """Close time is known but delay hasn't elapsed."""
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            current_session_close="2026-06-05T16:00:00-04:00",
            timestamp="2026-06-05T20:05:00Z", source="mock",
        )
        with patch.object(McpScheduler, "_is_weekend", return_value=False):
            with patch.object(McpScheduler, "get_local_now") as mock_local:
                # 5 minutes after close, 15 min delay not yet elapsed
                mock_local.return_value = datetime(2026, 6, 5, 16, 5, 0, tzinfo=ZoneInfo("America/New_York"))
                should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
                self.assertFalse(should_run)
                self.assertIn("before_post_market_delay", reason)

    def test_next_close_not_used_as_today_close(self):
        """next_close points to next trading day, should NOT be used as today's close."""
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            current_session_close="", last_close="",
            next_close="2026-06-08T16:00:00-04:00",  # Monday, not today
            timestamp="2026-06-05T21:00:00Z", source="mock",
        )
        with patch.object(McpScheduler, "_is_weekend", return_value=False):
            should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
            self.assertFalse(should_run)
            self.assertEqual(reason, "missing_session_close")

    def test_post_market_uses_last_close_as_fallback(self):
        """If current_session_close is missing, last_close should be used."""
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            current_session_close="",
            last_close="2026-06-05T16:00:00-04:00",
            timestamp="2026-06-05T21:00:00Z", source="mock",
        )
        with patch.object(McpScheduler, "_is_weekend", return_value=False):
            with patch.object(McpScheduler, "get_local_now") as mock_local:
                mock_local.return_value = datetime(2026, 6, 5, 16, 30, 0, tzinfo=ZoneInfo("America/New_York"))
                should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
                self.assertTrue(should_run, f"Should use last_close: {reason}")

    def test_is_post_market_window_duplicate(self):
        """In-memory dedup should block repeat calls."""
        status = MarketStatusInfo(
            market="US", is_open=False, session="closed",
            current_session_close="2026-06-05T16:00:00-04:00",
            timestamp="2026-06-05T21:00:00Z", source="mock",
        )
        with patch.object(McpScheduler, "_is_weekend", return_value=False):
            with patch.object(McpScheduler, "get_local_now") as mock_local:
                mock_local.return_value = datetime(2026, 6, 5, 16, 30, 0, tzinfo=ZoneInfo("America/New_York"))
                trading_date = "2026-06-05"
                window_mem_key = f"US:daily:{trading_date}"
                self.scheduler._last_daily[window_mem_key] = mock_local.return_value.isoformat()

                should_run, reason = self.scheduler._is_post_market_window("US", status, ["QQQ"])
                self.assertFalse(should_run)
                self.assertIn("duplicate", reason)

    # ── Window key format ───────────────────────────────────────

    def test_make_window_key_format(self):
        key = self.scheduler._make_window_key("US", "QQQ", "intraday_brief", "2026-06-06", "regular")
        self.assertEqual(key, "US:QQQ:intraday_brief:2026-06-06:regular")

        key2 = self.scheduler._make_window_key("HK", "0700.HK", "daily_close_report", "2026-06-06", "close")
        self.assertEqual(key2, "HK:0700.HK:daily_close_report:2026-06-06:close")

    # ── Run loop dedup integration ───────────────────────────────

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

    def test_memory_intraday_dedup(self):
        """Memory dedup should prevent same session from running within interval."""
        status = MarketStatusInfo(
            market="US", is_open=True, session="regular",
            timestamp="2026-06-06T10:00:00Z", source="mock",
        )
        # First call runs
        with patch.object(McpScheduler, "get_local_now") as mock_local:
            mock_local.return_value = datetime(2026, 6, 6, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
            should_run, _ = self.scheduler._is_intraday_window("US", status, ["QQQ"])
            self.assertTrue(should_run)
            # Memory cache set
            self.scheduler._last_intraday["US:regular"] = mock_local.return_value.isoformat()

        # Second call within interval should be blocked
        with patch.object(McpScheduler, "get_local_now") as mock_local2:
            mock_local2.return_value = datetime(2026, 6, 6, 10, 30, 0, tzinfo=ZoneInfo("America/New_York"))
            should_run2, reason2 = self.scheduler._is_intraday_window("US", status, ["QQQ"])
            self.assertFalse(should_run2)
            self.assertIn("before_interval", reason2)

    def test_failed_handler_window_remains_retryable(self):
        """A failed workflow handler must not permanently suppress the window."""
        local_now = datetime(
            2026, 6, 5, 10, 0, tzinfo=ZoneInfo("America/New_York")
        )
        self.mock_client.get_market_status.return_value = [
            MarketStatusInfo(market="US", is_open=True, session="regular")
        ]

        def fail_handler(run_id, market, symbols):
            self.scheduler.stop()
            return False

        with (
            patch.object(McpScheduler, "get_local_now", return_value=local_now),
            patch("core.mcp_scheduler.time.sleep", return_value=None),
        ):
            self.scheduler.run_forever(
                markets=["US"],
                symbols_by_market={"US": ["QQQ"]},
                on_intraday=fail_handler,
            )

        key = self.scheduler._make_window_key(
            "US", "QQQ", "intraday_brief", "2026-06-05", "regular-1000"
        )
        self.assertEqual(self.scheduler._dedup.get_state(key), DedupState.FAILED)
        self.assertTrue(self.scheduler._dedup.can_retry(key))
        self.assertNotIn("US:regular", self.scheduler._last_intraday)


if __name__ == "__main__":
    unittest.main()
