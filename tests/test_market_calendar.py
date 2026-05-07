from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.market_calendar import (
    get_trading_date,
    should_build_daily,
    should_collect_market,
)


class MarketCalendarTest(unittest.TestCase):
    def test_singapore_evening_skips_hk_and_evaluates_us_in_new_york_time(self) -> None:
        now = datetime(2026, 5, 7, 22, 18, tzinfo=ZoneInfo("Asia/Singapore"))

        self.assertFalse(should_collect_market("HK", now))
        self.assertTrue(should_collect_market("US", now))
        self.assertEqual(get_trading_date("HK", now), "2026-05-07")
        self.assertEqual(get_trading_date("US", now), "2026-05-07")

    def test_hk_midday_break_is_closed(self) -> None:
        now = datetime(2026, 5, 7, 12, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))

        self.assertFalse(should_collect_market("HK", now))

    def test_hk_regular_session_is_open(self) -> None:
        now = datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

        self.assertTrue(should_collect_market("HK", now))

    def test_us_regular_session_is_open(self) -> None:
        now = datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))

        self.assertTrue(should_collect_market("US", now))

    def test_us_evening_is_closed(self) -> None:
        now = datetime(2026, 5, 7, 20, 0, tzinfo=ZoneInfo("America/New_York"))

        self.assertFalse(should_collect_market("US", now))

    def test_daily_requires_after_close(self) -> None:
        before_close_delay = datetime(2026, 5, 7, 16, 5, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        after_close_delay = datetime(2026, 5, 7, 16, 10, tzinfo=ZoneInfo("Asia/Hong_Kong"))

        self.assertFalse(should_build_daily("HK", before_close_delay))
        self.assertTrue(should_build_daily("HK", after_close_delay))


if __name__ == "__main__":
    unittest.main()
