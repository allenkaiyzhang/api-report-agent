from __future__ import annotations

import json
import shutil
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.time_model import normalize_source_timestamp
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


if __name__ == "__main__":
    unittest.main()
