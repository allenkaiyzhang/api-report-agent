from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.time_series_quality import check_time_series_quality


class TimeSeriesQualityTest(unittest.TestCase):
    def test_detects_gaps_duplicates_decreases_and_price_jumps(self) -> None:
        report = check_time_series_quality(
            [
                {
                    "event_time": "2026-05-07T09:30:00-04:00",
                    "last_price": 100,
                    "volume_cumulative": 100,
                    "turnover_cumulative": 1000,
                },
                {
                    "event_time": "2026-05-07T09:30:00-04:00",
                    "last_price": 100,
                    "volume_cumulative": 90,
                    "turnover_cumulative": 900,
                },
                {
                    "event_time": "2026-05-07T09:32:00-04:00",
                    "last_price": 112,
                    "volume_cumulative": 120,
                    "turnover_cumulative": 1200,
                },
                {
                    "event_time": "2026-05-07T09:40:00-04:00",
                    "last_price": 113,
                    "volume_cumulative": 140,
                    "turnover_cumulative": 1400,
                },
            ]
        )

        self.assertEqual(report["duplicate_timestamps"], 1)
        self.assertEqual(report["volume_decrease_count"], 1)
        self.assertEqual(report["turnover_decrease_count"], 1)
        self.assertEqual(report["abnormal_price_jump_count"], 1)
        self.assertEqual(report["timestamp_gap_count"], 1)
        self.assertEqual(report["max_gap_seconds"], 480)


if __name__ == "__main__":
    unittest.main()
