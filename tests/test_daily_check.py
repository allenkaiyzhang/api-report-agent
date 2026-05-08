from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.daily_check import exit_code, run_daily_check
from scripts.post_market_pipeline import run_post_market_pipeline


class DailyCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base_dir = PROJECT_ROOT / "tests" / "daily_check_output_test"
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        raw_dir = self.base_dir / "data" / "raw" / "US"
        raw_dir.mkdir(parents=True)
        rows = [
            {
                "collected_at": "2026-05-07T09:30:00-04:00",
                "provider": "mock",
                "market": "US",
                "symbol": "QQQ.US",
                "timestamp": "2026-05-07T09:30:00-04:00",
                "last_price": 100,
                "volume": 1000,
                "turnover": 100000,
                "currency": "USD",
            },
            {
                "collected_at": "2026-05-07T09:32:00-04:00",
                "provider": "mock",
                "market": "US",
                "symbol": "QQQ.US",
                "timestamp": "2026-05-07T09:32:00-04:00",
                "last_price": 101,
                "volume": 1200,
                "turnover": 121200,
                "currency": "USD",
            },
        ]
        (raw_dir / "2026-05-07.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        reference_dir = self.base_dir / "data" / "reference" / "US"
        reference_dir.mkdir(parents=True)
        (reference_dir / "2026-05-07.json").write_text(json.dumps({"symbols": ["QQQ.US"]}), encoding="utf-8")
        run_post_market_pipeline("US", "2026-05-07", base_dir=self.base_dir)

    def tearDown(self) -> None:
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    @patch("scripts.daily_check.check_systemd")
    def test_daily_check_generates_json_report_and_warning_exit(self, mock_systemd) -> None:
        mock_systemd.return_value = {"service": "api-report-agent.service", "active": True, "status": "active"}

        report = run_daily_check("2026-05-07", ["US"], base_dir=self.base_dir)

        self.assertIn(report["summary"]["status"], {"ok", "warning", "critical"})
        self.assertEqual(exit_code("ok"), 0)
        self.assertEqual(exit_code("warning"), 1)
        self.assertEqual(exit_code("critical"), 2)
        self.assertTrue(report["markets"]["US"]["raw"]["exists"])
        self.assertTrue(report["markets"]["US"]["metrics"]["daily_exists"])
        self.assertTrue(report["markets"]["US"]["quality"]["exists"])
        self.assertGreaterEqual(report["markets"]["US"]["reports"]["report_file_count"], 1)


if __name__ == "__main__":
    unittest.main()
