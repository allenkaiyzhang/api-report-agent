from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from clients.market_client import MarketClient


@unittest.skipUnless(
    os.getenv("RUN_LONGBRIDGE_API_TESTS") == "1",
    "Set RUN_LONGBRIDGE_API_TESTS=1 to run real Longbridge API connectivity tests.",
)
class LongbridgeApiConnectivityTest(unittest.TestCase):
    """Real API connectivity test.

    This test intentionally does not use mock data. It requires valid Longbridge
    credentials in `.env` or the process environment.
    """

    def test_fetch_real_market_data(self) -> None:
        symbols = [
            item.strip()
            for item in os.getenv("LONGBRIDGE_TEST_SYMBOLS", "QQQ.US,SGOV.US").split(",")
            if item.strip()
        ]

        client = MarketClient(provider="longbridge")
        rows = client.fetch_quotes(symbols)

        self.assertEqual(len(rows), len(symbols))
        for row in rows:
            self.assertIn(row["symbol"], symbols)
            self.assertGreater(row["latest_price"], 0)
            self.assertGreaterEqual(row["previous_close"], 0)
            self.assertIn("static_info", row)
            self.assertIn("calc_indexes", row)
            self.assertIn("daily_candlesticks", row)
            self.assertEqual(row["market_data_provider"], "longbridge")


if __name__ == "__main__":
    unittest.main()
