from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.data_pipeline import (
    all_day,
    build_symbol_window_metrics,
    check_time_series_integrity,
    daily_day,
    metrics_day,
    get_market_windows,
    normalize_day,
    normalize_record,
)


class DataPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base_dir = PROJECT_ROOT / "tests" / "pipeline_output_test"
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        raw_dir = self.base_dir / "data" / "raw" / "US"
        raw_dir.mkdir(parents=True)
        raw_path = raw_dir / "2026-05-07.jsonl"
        raw_rows = [
            {
                "collected_at": "2026-05-07T09:30:00-04:00",
                "symbol": "aapl.us",
                "timestamp": "2026-05-07T09:30:00-04:00",
                "latest_price": 100,
                "bid": 99.9,
                "ask": 100.1,
                "volume": 1000,
                "turnover": 100000,
                "static_info": {"currency": "USD"},
            },
            {
                "collected_at": "2026-05-07T09:32:00-04:00",
                "symbol": "AAPL.US",
                "timestamp": "2026-05-07T09:32:00-04:00",
                "latest_price": 102,
                "bid": 101.9,
                "ask": 102.1,
                "volume": 1300,
                "turnover": 130600,
                "static_info": {"currency": "USD"},
            },
            "{bad json",
            {
                "collected_at": "2026-05-07T09:34:00-04:00",
                "symbol": "",
                "timestamp": "",
                "latest_price": 0,
                "volume": -1,
            },
        ]
        with raw_path.open("w", encoding="utf-8") as file:
            for row in raw_rows:
                if isinstance(row, str):
                    file.write(row + "\n")
                else:
                    file.write(json.dumps(row) + "\n")

    def tearDown(self) -> None:
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    def test_normalize_record_flags_invalid_data(self) -> None:
        record = normalize_record(
            {
                "symbol": "700",
                "timestamp": "",
                "latest_price": 0,
                "bid": 2,
                "ask": 1,
                "volume": -1,
            },
            market="HK",
        )

        self.assertEqual(record["symbol"], "0700.HK")
        self.assertFalse(record["is_valid"])
        self.assertIn("missing_event_time", record["flags"])
        self.assertIn("invalid_price", record["flags"])
        self.assertIn("ask_less_than_bid", record["flags"])
        self.assertIn("invalid_volume", record["flags"])

    def test_windows_include_hk_midday_break_without_missing_window(self) -> None:
        windows = get_market_windows("HK", "2026-05-07")

        self.assertEqual([window.window_id for window in windows], [
            "0930_1030",
            "1030_1130",
            "1130_1200",
            "1300_1400",
            "1400_1500",
            "1500_1600",
        ])
        self.assertEqual(windows[2].expected_points, 15)

    def test_symbol_metrics_handles_basic_window(self) -> None:
        metrics = build_symbol_window_metrics(
            "AAPL.US",
            [
                {
                    "event_time": "2026-05-07T09:30:00-04:00",
                    "is_valid": True,
                    "last_price": 100,
                    "volume_cumulative": 1000,
                    "turnover_cumulative": 100000,
                    "spread_pct": 0.001,
                },
                {
                    "event_time": "2026-05-07T09:32:00-04:00",
                    "is_valid": True,
                    "last_price": 102,
                    "volume_cumulative": 1300,
                    "turnover_cumulative": 130600,
                    "spread_pct": 0.002,
                },
            ],
            expected_points=30,
        )

        self.assertEqual(metrics["actual_points"], 2)
        self.assertEqual(metrics["missing_points"], 28)
        self.assertEqual(metrics["return_pct"], 2.0)
        self.assertEqual(metrics["intraday_range_pct"], 2.0)
        self.assertEqual(metrics["volume_delta"], 300)
        self.assertEqual(metrics["vwap"], 102.0)
        self.assertEqual(metrics["quality_grade"], "unusable")
        self.assertIn("excessive_missing_points", metrics["flags"])

    def test_symbol_metrics_flags_stale_price_and_volume_reset(self) -> None:
        rows = []
        for index in range(6):
            rows.append(
                {
                    "event_time": f"2026-05-07T09:{30 + index * 2:02d}:00-04:00",
                    "is_valid": True,
                    "last_price": 100,
                    "volume_cumulative": 1000 - index,
                    "turnover_cumulative": 100000,
                    "spread_pct": 0.02,
                }
            )

        metrics = build_symbol_window_metrics("AAPL.US", rows, expected_points=6)

        self.assertEqual(metrics["stale_price_periods"], 5)
        self.assertIn("stale_price", metrics["flags"])
        self.assertIn("volume_reset_detected", metrics["flags"])
        self.assertIn("abnormal_spread", metrics["flags"])
        self.assertEqual(metrics["quality_grade"], "poor")

    def test_time_series_integrity_detects_duplicates_gaps_and_non_monotonic_volume(self) -> None:
        report = check_time_series_integrity(
            [
                {"event_time": "2026-05-07T09:30:00-04:00", "last_price": 100, "volume_cumulative": 100},
                {"event_time": "2026-05-07T09:30:00-04:00", "last_price": 100, "volume_cumulative": 90},
                {"event_time": "2026-05-07T09:40:00-04:00", "last_price": 100, "volume_cumulative": 120},
            ]
        )

        self.assertEqual(report["duplicate_timestamps"], 1)
        self.assertEqual(report["timestamp_not_increasing"], 1)
        self.assertTrue(report["volume_reset_detected"])
        self.assertEqual(len(report["timestamp_gaps"]), 1)

    def test_all_day_generates_normalized_metrics_and_quality(self) -> None:
        all_day("US", "2026-05-07", base_dir=self.base_dir)

        normalized_path = self.base_dir / "data" / "normalized" / "US" / "2026-05-07.jsonl"
        windows_path = self.base_dir / "data" / "metrics" / "US" / "2026-05-07" / "windows.json"
        window_path = self.base_dir / "data" / "metrics" / "US" / "2026-05-07" / "window_0930_1030.json"
        daily_metrics_path = self.base_dir / "data" / "metrics" / "US" / "2026-05-07" / "daily.json"
        quality_path = self.base_dir / "data" / "quality" / "US" / "2026-05-07.json"

        self.assertTrue(normalized_path.exists())
        self.assertTrue(windows_path.exists())
        self.assertTrue(window_path.exists())
        self.assertTrue(daily_metrics_path.exists())
        self.assertTrue(quality_path.exists())

        window_metric = json.loads(window_path.read_text(encoding="utf-8"))
        self.assertIn("largest_drawdown", window_metric["cross_symbol"])
        self.assertIn("quality_grade", window_metric["symbols"][0])

        daily = json.loads(daily_metrics_path.read_text(encoding="utf-8"))
        self.assertIn("market_summary", daily)
        self.assertIn("daily_return_pct", daily["symbols"][0])

        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        self.assertEqual(quality["raw_quality"]["raw_lines"], 4)
        self.assertEqual(quality["raw_quality"]["json_parse_errors"], 1)
        self.assertEqual(quality["normalized_quality"]["normalized_lines"], 3)
        self.assertGreaterEqual(quality["window_quality"]["expected_windows"], 7)

    def test_missing_raw_skips_normalize_without_traceback(self) -> None:
        output_path = normalize_day("HK", "2026-05-07", base_dir=self.base_dir)

        self.assertEqual(output_path, self.base_dir / "data" / "normalized" / "HK" / "2026-05-07.jsonl")
        self.assertFalse(output_path.exists())

    def test_missing_normalized_skips_metrics_without_traceback(self) -> None:
        output_dir = metrics_day("HK", "2026-05-07", base_dir=self.base_dir)

        self.assertEqual(output_dir, self.base_dir / "data" / "metrics" / "HK" / "2026-05-07")
        self.assertFalse(output_dir.exists())

    def test_missing_metrics_directory_skips_daily_without_traceback(self) -> None:
        output_path = daily_day("HK", "2026-05-07", base_dir=self.base_dir)

        self.assertEqual(output_path, self.base_dir / "data" / "metrics" / "HK" / "2026-05-07" / "daily.json")
        self.assertFalse(output_path.exists())

    def test_normalize_handles_lightweight_raw_without_static_info(self) -> None:
        raw_dir = self.base_dir / "data" / "raw" / "HK"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / "2026-05-07.jsonl"
        raw_path.write_text(
            json.dumps(
                {
                    "collected_at": "2026-05-07T10:00:00+08:00",
                    "provider": "mock",
                    "market": "HK",
                    "symbol": "700.HK",
                    "last_price": 500,
                    "previous_close": 490,
                    "volume": 1000,
                    "timestamp": "2026-05-07T10:00:00+08:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        output_path = normalize_day("HK", "2026-05-07", base_dir=self.base_dir)
        row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(row["symbol"], "0700.HK")
        self.assertEqual(row["currency"], "HKD")

    def test_normalize_remains_compatible_with_legacy_raw_static_info(self) -> None:
        output_path = normalize_day("US", "2026-05-07", base_dir=self.base_dir)
        rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(rows[0]["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
