from __future__ import annotations

import json
import shutil
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.time_model import normalize_source_timestamp
from core.extended_session import extended_collect_decision, should_collect_us_extended
from scripts.extended_pipeline import append_extended_records


class TimeModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.output_dir = PROJECT_ROOT / "tests" / "time_model_output_test"
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)

    def tearDown(self) -> None:
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)

    def test_longbridge_us_market_local_timestamp_normalizes_to_utc(self) -> None:
        raw, timezone_name, source_utc = normalize_source_timestamp("2026-05-12 09:30:00", "US")

        self.assertEqual(raw, "2026-05-12 09:30:00")
        self.assertEqual(timezone_name, "America/New_York")
        self.assertEqual(source_utc, "2026-05-12T13:30:00Z")

    def test_extended_raw_uses_session_window_id_filename(self) -> None:
        path = append_extended_records(
            records=[
                {
                    "symbol": "QQQ.US",
                    "timestamp": "2026-05-09 10:00:00",
                    "last_price": 100,
                    "volume": 0,
                }
            ],
            provider="mock",
            collected_at=datetime(2026, 5, 9, 14, 0, tzinfo=UTC),
            output_dir=self.output_dir,
        )

        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(path.name, "US_EXT_2026-05-08_TO_2026-05-11.jsonl")
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["session"], "extended")
        self.assertEqual(row["session_window_id"], "US_EXT_2026-05-08_TO_2026-05-11")
        self.assertTrue(row["collected_at_utc"].endswith("Z"))
        self.assertTrue(row["source_timestamp_utc"].endswith("Z"))

    def test_extended_collection_skips_weekend_but_keeps_friday_afterhours_and_monday_premarket(self) -> None:
        friday_after_close = datetime(2026, 5, 8, 21, 30, tzinfo=UTC)
        saturday_midday = datetime(2026, 5, 9, 16, 0, tzinfo=UTC)
        sunday_midday = datetime(2026, 5, 10, 16, 0, tzinfo=UTC)
        monday_premarket = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)

        self.assertTrue(should_collect_us_extended(friday_after_close))
        self.assertFalse(should_collect_us_extended(saturday_midday))
        self.assertFalse(should_collect_us_extended(sunday_midday))
        self.assertTrue(should_collect_us_extended(monday_premarket))

    def test_extended_collect_decision_has_explicit_reasons(self) -> None:
        et = ZoneInfo("America/New_York")
        cases = [
            (datetime(2026, 5, 11, 4, 30, tzinfo=et), True, "premarket", "premarket"),
            (datetime(2026, 5, 11, 9, 45, tzinfo=et), False, "regular_session", "regular"),
            (datetime(2026, 5, 11, 17, 0, tzinfo=et), True, "afterhours", "afterhours"),
            (datetime(2026, 5, 11, 21, 0, tzinfo=et), False, "outside_extended_session", "closed"),
            (datetime(2026, 5, 10, 23, 0, tzinfo=et), False, "weekend", "closed"),
            (datetime(2026, 5, 11, 0, 30, tzinfo=et), False, "outside_extended_session", "closed"),
        ]
        for ny_time, should_collect, reason, session in cases:
            decision = extended_collect_decision(ny_time)
            self.assertEqual(decision["should_collect"], should_collect, decision)
            self.assertEqual(decision["reason"], reason, decision)
            self.assertEqual(decision["session"], session, decision)


if __name__ == "__main__":
    unittest.main()
