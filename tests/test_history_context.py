from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.history_context import build_history_context


class HistoryContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base_dir = PROJECT_ROOT / "tests" / "history_context_output_test"
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    def tearDown(self) -> None:
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    def test_no_history_data(self) -> None:
        context = build_history_context(self.base_dir, "US", "2026-05-12", lookback_days=5)

        self.assertFalse(context["history_available"])
        self.assertEqual(context["available_dates"], [])
        self.assertEqual(context["symbols"], {})

    def test_single_day_history(self) -> None:
        write_daily(self.base_dir, "US", "2026-05-11", [symbol_row("QQQ.US", 1.5, 0.02, 1000)])

        context = build_history_context(self.base_dir, "US", "2026-05-12", lookback_days=5)
        qqq = context["symbols"]["QQQ.US"]

        self.assertEqual(context["available_dates"], ["2026-05-11"])
        self.assertEqual(qqq["latest_return_pct"], 1.5)
        self.assertEqual(qqq["avg_daily_return_pct"], 1.5)
        self.assertEqual(qqq["cumulative_return_pct"], 1.5)
        self.assertIsNone(qqq["volume_vs_history"])

    def test_multi_day_history(self) -> None:
        write_daily(self.base_dir, "US", "2026-05-08", [symbol_row("QQQ.US", 1.0, 0.01, 1000)])
        write_daily(self.base_dir, "US", "2026-05-11", [symbol_row("QQQ.US", 2.0, 0.03, 3000)])

        context = build_history_context(self.base_dir, "US", "2026-05-12", lookback_days=5)
        qqq = context["symbols"]["QQQ.US"]

        self.assertEqual(context["available_dates"], ["2026-05-08", "2026-05-11"])
        self.assertEqual(qqq["latest_return_pct"], 2.0)
        self.assertEqual(qqq["avg_daily_return_pct"], 1.5)
        self.assertEqual(qqq["latest_volume_delta"], 3000.0)
        self.assertEqual(qqq["avg_volume_delta"], 2000.0)
        self.assertEqual(qqq["volume_vs_history"], 3.0)
        self.assertEqual(qqq["volatility_vs_history"], 3.0)
        self.assertEqual(qqq["trend_label"], "uptrend")

    def test_missing_daily_json_is_reported_and_skipped(self) -> None:
        missing_dir = self.base_dir / "data" / "metrics" / "US" / "2026-05-08"
        missing_dir.mkdir(parents=True)
        write_daily(self.base_dir, "US", "2026-05-11", [symbol_row("QQQ.US", 1.0, 0.01, 1000)])

        context = build_history_context(self.base_dir, "US", "2026-05-12", lookback_days=5)

        self.assertEqual(context["available_dates"], ["2026-05-11"])
        self.assertEqual(context["data_quality_summary"]["missing_daily_files"], ["2026-05-08"])

    def test_symbol_partial_dates_missing(self) -> None:
        write_daily(
            self.base_dir,
            "US",
            "2026-05-08",
            [
                symbol_row("QQQ.US", 1.0, 0.01, 1000),
                symbol_row("AAPL.US", -1.0, 0.02, 800),
            ],
        )
        write_daily(self.base_dir, "US", "2026-05-11", [symbol_row("QQQ.US", 2.0, 0.02, 1200)])

        context = build_history_context(self.base_dir, "US", "2026-05-12", lookback_days=5)
        aapl = context["symbols"]["AAPL.US"]

        self.assertEqual(aapl["available_dates"], ["2026-05-08"])
        self.assertEqual(aapl["data_quality_summary"]["missing_dates"], ["2026-05-11"])
        self.assertIn("partial_history", aapl["risk_flags"])


def write_daily(base_dir: Path, market: str, trading_date: str, symbols: list[dict]) -> None:
    path = base_dir / "data" / "metrics" / market / trading_date / "daily.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "market": market,
        "trading_date": trading_date,
        "window_count": 7,
        "window_status_summary": {"finalized": 7},
        "symbols": symbols,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def symbol_row(symbol: str, return_pct: float, volatility: float, volume: int) -> dict:
    return {
        "symbol": symbol,
        "daily_return_pct": return_pct,
        "daily_volatility": volatility,
        "daily_volume_delta": volume,
        "quality_summary": {
            "good_windows": 7,
            "minor_missing_windows": 0,
            "poor_windows": 0,
            "unusable_windows": 0,
        },
        "flags": [],
    }


if __name__ == "__main__":
    unittest.main()
