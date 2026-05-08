from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.data_pipeline import all_day
from datetime import datetime
from zoneinfo import ZoneInfo

from core.email_reporter import (
    EmailConfig,
    build_daily_report_payload,
    build_intraday_report_payload,
    compose_daily_report_email,
    compose_intraday_report_email,
)
from core.runtime_support import RuntimeState


class EmailReporterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base_dir = PROJECT_ROOT / "tests" / "email_report_output_test"
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        raw_dir = self.base_dir / "data" / "raw" / "US"
        raw_dir.mkdir(parents=True)
        raw_path = raw_dir / "2026-05-07.jsonl"
        rows = [
            {
                "collected_at": "2026-05-07T09:30:00-04:00",
                "symbol": "QQQ.US",
                "timestamp": "2026-05-07T09:30:00-04:00",
                "latest_price": 100,
                "volume": 1000,
                "turnover": 100000,
                "currency": "USD",
            },
            {
                "collected_at": "2026-05-07T09:32:00-04:00",
                "symbol": "QQQ.US",
                "timestamp": "2026-05-07T09:32:00-04:00",
                "latest_price": 101,
                "volume": 1300,
                "turnover": 131300,
                "currency": "USD",
            },
        ]
        raw_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        all_day("US", "2026-05-07", base_dir=self.base_dir)

    def tearDown(self) -> None:
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    def test_compose_daily_report_email_contains_data_summary(self) -> None:
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
        payload = build_daily_report_payload(self.base_dir, "US", "2026-05-07")
        message = compose_daily_report_email(config, payload)
        body = message.get_content()

        self.assertIn("US daily data report 2026-05-07", message["Subject"])
        self.assertIn("raw lines: 2", body)
        self.assertIn("normalized lines: 2", body)
        self.assertIn("daily symbols:", body)

    def test_compose_intraday_report_email_contains_two_hour_data(self) -> None:
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
        payload = build_intraday_report_payload(
            self.base_dir,
            "US",
            "2026-05-07",
            datetime(2026, 5, 7, 9, 30, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 5, 7, 11, 30, tzinfo=ZoneInfo("America/New_York")),
        )
        message = compose_intraday_report_email(config, payload, ai_analysis="AI says ok")
        body = message.get_content()

        self.assertIn("US intraday data report", message["Subject"])
        self.assertEqual(payload["raw_lines"], 2)
        self.assertEqual(payload["normalized_lines"], 2)
        self.assertIn("AI says ok", body)

    def test_runtime_state_tracks_email_report_sent(self) -> None:
        state_path = self.base_dir / "runtime" / "pipeline_status.json"
        state = RuntimeState(path=state_path)

        self.assertFalse(state.email_report_sent("US", "2026-05-07"))
        state.mark_email_report_sent("US", "2026-05-07")
        self.assertTrue(state.email_report_sent("US", "2026-05-07"))

    def test_runtime_state_tracks_email_report_failure_without_error_count(self) -> None:
        state_path = self.base_dir / "runtime" / "pipeline_status.json"
        state = RuntimeState(path=state_path)

        self.assertFalse(state.email_report_failed("US", "2026-05-07"))
        state.mark_email_report_failed("US", "2026-05-07", "SMTP host not found")

        self.assertTrue(state.email_report_failed("US", "2026-05-07"))
        self.assertEqual(state.data.get("error_count", 0), 0)

    def test_runtime_state_tracks_intraday_email_key(self) -> None:
        state_path = self.base_dir / "runtime" / "pipeline_status.json"
        state = RuntimeState(path=state_path)
        key = "US:2026-05-07:0930_1130"

        self.assertFalse(state.intraday_email_report_sent(key))
        state.mark_intraday_email_report_sent(key)
        self.assertTrue(state.intraday_email_report_sent(key))


if __name__ == "__main__":
    unittest.main()
