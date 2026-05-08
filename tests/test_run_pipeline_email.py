from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_pipeline import intraday_email_key, intraday_email_window


class RunPipelineEmailTest(unittest.TestCase):
    def test_intraday_email_window_uses_two_hour_market_period(self) -> None:
        now = datetime(2026, 5, 7, 11, 35, tzinfo=ZoneInfo("America/New_York"))
        window = intraday_email_window("US", now)

        self.assertIsNotNone(window)
        assert window is not None
        trading_date, start, end = window
        self.assertEqual(trading_date, "2026-05-07")
        self.assertEqual(start.isoformat(timespec="minutes"), "2026-05-07T09:30-04:00")
        self.assertEqual(end.isoformat(timespec="minutes"), "2026-05-07T11:30-04:00")
        self.assertEqual(intraday_email_key("US", trading_date, start, end), "US:2026-05-07:0930_1130")


if __name__ == "__main__":
    unittest.main()
