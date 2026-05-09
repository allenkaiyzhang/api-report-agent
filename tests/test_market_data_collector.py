from __future__ import annotations

import json
import shutil
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from clients.market_client import MarketClient
from core.market_data_store import DailyJsonlMarketDataStore
from core.reference_data_store import ReferenceDataStore
from core.trading_hours import filter_symbols_by_open_markets, open_markets
from scripts.market_data_collector import MarketDataCollector
from scripts.run_pipeline import build_reference_for_open_markets


class MarketDataCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.output_dir = PROJECT_ROOT / "tests" / "collector_output_test"
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)

    def tearDown(self) -> None:
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)

    def test_open_markets_detects_hk_and_us_sessions(self) -> None:
        hk_open = datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        us_open = datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        weekend = datetime(2026, 5, 9, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

        self.assertIn("HK", open_markets(hk_open))
        self.assertIn("US", open_markets(us_open))
        self.assertEqual(open_markets(weekend), [])

    def test_symbol_filter_uses_open_market_suffix(self) -> None:
        symbols = ["QQQ.US", "700.HK", "VIX"]

        self.assertEqual(filter_symbols_by_open_markets(symbols, ["HK"]), ["700.HK"])
        self.assertEqual(filter_symbols_by_open_markets(symbols, ["US"]), ["QQQ.US", "VIX"])

    def test_run_once_writes_daily_jsonl_snapshot(self) -> None:
        store = DailyJsonlMarketDataStore(self.output_dir)
        collector = MarketDataCollector(
            market_client=MarketClient(provider="mock"),
            store=store,
            interval_seconds=120,
        )
        us_open = datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))

        output_paths = collector.run_once(["QQQ", "700.HK"], now=us_open)

        self.assertEqual(len(output_paths), 1)
        output_path = output_paths[0]
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.name, "2026-05-07.jsonl")
        self.assertEqual(output_path.parent.name, "US")
        lines = output_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["provider"], "mock")
        self.assertEqual(record["symbol"], "QQQ")
        self.assertEqual(record["session"], "regular")
        self.assertEqual(record["trading_date"], "2026-05-07")
        self.assertEqual(record["session_window_id"], "US_REGULAR_2026-05-07")
        self.assertEqual(record["market_timezone"], "America/New_York")
        self.assertTrue(record["collected_at_utc"].endswith("Z"))
        self.assertTrue(record["source_timestamp_utc"].endswith("Z"))
        self.assertNotIn("daily_candlesticks", record)
        self.assertNotIn("static_info", record)
        self.assertNotIn("avg_volume_20d", record)

    def test_reference_data_contains_daily_candlesticks_by_symbol(self) -> None:
        client = MarketClient(provider="mock")
        reference = client.fetch_reference_data(["QQQ.US"])

        self.assertIn("daily_candlesticks_by_symbol", reference)
        self.assertIn("QQQ.US", reference["daily_candlesticks_by_symbol"])
        self.assertTrue(reference["daily_candlesticks_by_symbol"]["QQQ.US"])

    def test_reference_file_skips_when_existing_and_force_overwrites(self) -> None:
        class CountingClient(MarketClient):
            def __init__(self) -> None:
                super().__init__(provider="mock")
                self.calls = 0

            def fetch_reference_data(self, symbols: list[str]) -> dict:
                self.calls += 1
                return {
                    "static_info_by_symbol": {"QQQ.US": {"currency": "USD", "calls": self.calls}},
                    "calc_indexes_by_symbol": {},
                    "daily_candlesticks_by_symbol": {"QQQ.US": [{"close": self.calls}]},
                }

        client = CountingClient()
        store = ReferenceDataStore(PROJECT_ROOT / "tests" / "reference_output_test")
        if store.base_dir.exists():
            shutil.rmtree(store.base_dir)
        us_open = datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))

        build_reference_for_open_markets(client, store, ["QQQ.US"], ["US"], us_open, logger=_NullLogger())
        path = store.reference_path("US", "2026-05-07")
        first = json.loads(path.read_text(encoding="utf-8"))
        build_reference_for_open_markets(client, store, ["QQQ.US"], ["US"], us_open, logger=_NullLogger())
        second = json.loads(path.read_text(encoding="utf-8"))
        build_reference_for_open_markets(client, store, ["QQQ.US"], ["US"], us_open, logger=_NullLogger(), force=True)
        third = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(client.calls, 2)
        self.assertEqual(first["static_info_by_symbol"]["QQQ.US"]["calls"], 1)
        self.assertEqual(second["static_info_by_symbol"]["QQQ.US"]["calls"], 1)
        self.assertEqual(third["static_info_by_symbol"]["QQQ.US"]["calls"], 2)
        shutil.rmtree(store.base_dir)


class _NullLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def exception(self, *args, **kwargs) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
