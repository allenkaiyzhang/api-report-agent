from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.market_data_cleaner import clean_market_records


class MarketDataCleanerTest(unittest.TestCase):
    def test_clean_market_records_normalizes_types_and_derives_fields(self) -> None:
        records = [
            {
                "symbol": " qqq.us ",
                "latest_price": "102",
                "previous_close": "100",
                "change_percent": "",
                "volume": "1500",
                "avg_volume_20d": "1000",
                "timestamp": "2026-05-07T10:00:00-04:00",
                "market_data_provider": "longbridge",
                "static_info": {"name_en": "Invesco QQQ", "currency": "USD"},
                "calc_indexes": {"five_day_change_rate": "1.2"},
                "daily_candlesticks": [{"close": "101", "volume": "900"}],
            }
        ]

        cleaned, issues = clean_market_records(records)

        self.assertEqual(issues, [])
        self.assertEqual(cleaned[0]["symbol"], "QQQ.US")
        self.assertEqual(cleaned[0]["market"], "US")
        self.assertEqual(cleaned[0]["latest_price"], 102.0)
        self.assertEqual(cleaned[0]["change_percent"], 2.0)
        self.assertEqual(cleaned[0]["volume"], 1500)
        self.assertEqual(cleaned[0]["volume_ratio"], 1.5)
        self.assertEqual(cleaned[0]["provider"], "longbridge")
        self.assertEqual(cleaned[0]["latest_candlestick"]["close"], 101.0)

    def test_clean_market_records_reports_quality_issues(self) -> None:
        records = [
            {
                "symbol": "QQQ.US",
                "latest_price": 0,
                "previous_close": 100,
                "volume": -10,
            },
            {
                "symbol": "QQQ.US",
                "latest_price": 101,
                "previous_close": 100,
                "volume": 10,
            },
            {
                "latest_price": 99,
                "previous_close": 100,
                "volume": 10,
            },
        ]

        cleaned, issues = clean_market_records(records)

        self.assertEqual(len(cleaned), 2)
        issue_names = [name for item in issues for name in item["issues"]]
        self.assertIn("invalid_latest_price", issue_names)
        self.assertIn("invalid_volume", issue_names)
        self.assertIn("duplicate_symbol", issue_names)
        self.assertIn("missing_symbol", issue_names)


if __name__ == "__main__":
    unittest.main()
