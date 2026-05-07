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
from core.trading_hours import filter_symbols_by_open_markets, open_markets
from scripts.market_data_agent import MarketDataCollectorAgent


class MarketDataAgentTest(unittest.TestCase):
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
        agent = MarketDataCollectorAgent(
            market_client=MarketClient(provider="mock"),
            store=store,
            interval_seconds=120,
        )
        us_open = datetime(2026, 5, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))

        output_paths = agent.run_once(["QQQ", "700.HK"], now=us_open)

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


if __name__ == "__main__":
    unittest.main()
