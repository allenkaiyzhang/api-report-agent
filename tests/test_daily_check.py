from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.email_reporter import EmailConfig
from scripts.daily_check import build_daily_check_email_body, exit_code, run_daily_check, send_daily_check_email
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

    def test_daily_check_email_body_contains_summary(self) -> None:
        report = {
            "date": "2026-05-07",
            "summary": {"status": "warning", "critical": [], "warnings": ["US:empty_windows"]},
            "systemd": {"service": "api-report-agent.service", "status": "active"},
            "disk": {"disk_used_percent": 12.3, "data_size_bytes": 100},
        }

        body = build_daily_check_email_body(report)

        self.assertIn("Status: warning", body)
        self.assertIn("US:empty_windows", body)

    @patch("scripts.daily_check.send_email")
    def test_send_daily_check_email_uses_existing_email_config(self, mock_send_email) -> None:
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="password",
            smtp_use_tls=True,
            sender="from@example.com",
            recipients=("to@example.com",),
        )
        report = {
            "date": "2026-05-07",
            "summary": {"status": "ok", "critical": [], "warnings": []},
            "systemd": {"service": "api-report-agent.service", "status": "active"},
            "disk": {"disk_used_percent": 12.3, "data_size_bytes": 100},
        }

        sent = send_daily_check_email(report, config)

        self.assertTrue(sent)
        mock_send_email.assert_called_once()


if __name__ == "__main__":
    unittest.main()
