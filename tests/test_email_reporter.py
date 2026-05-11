from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from email.message import EmailMessage
from unittest.mock import patch

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
    send_email,
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

    def test_email_config_reads_ipv4_retry_defaults(self) -> None:
        config = EmailConfig.from_env(
            {
                "EMAIL_ENABLED": "true",
                "SMTP_HOST": "smtp.example.com",
                "EMAIL_FROM": "from@example.com",
                "EMAIL_TO": "to@example.com",
            }
        )

        self.assertTrue(config.smtp_force_ipv4)
        self.assertEqual(config.smtp_retries, 3)
        self.assertEqual(config.smtp_retry_seconds, 5)

    @patch("core.email_reporter.socket.getaddrinfo")
    @patch("core.email_reporter.smtplib.SMTP")
    def test_send_email_refused_recipients_raises(self, mock_smtp, mock_getaddrinfo) -> None:
        mock_getaddrinfo.return_value = [(None, None, None, None, ("127.0.0.1", 587))]
        mock_smtp.return_value = _FakeSMTP(refused={"to@example.com": (550, b"no")})
        config = test_config(smtp_retries=1, smtp_retry_seconds=0)

        with self.assertRaisesRegex(RuntimeError, "SMTP refused recipients"):
            send_email(config, test_message())

    @patch("core.email_reporter.socket.getaddrinfo")
    @patch("core.email_reporter.smtplib.SMTP")
    def test_send_email_retries_temporary_failure(self, mock_smtp, mock_getaddrinfo) -> None:
        mock_getaddrinfo.return_value = [(None, None, None, None, ("127.0.0.1", 587))]
        mock_smtp.side_effect = [OSError("network down"), _FakeSMTP(refused={})]
        config = test_config(smtp_retries=2, smtp_retry_seconds=0)

        send_email(config, test_message())

        self.assertEqual(mock_smtp.call_count, 2)


def test_config(smtp_retries: int = 1, smtp_retry_seconds: int = 0) -> EmailConfig:
    return EmailConfig(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="password",
        smtp_use_tls=True,
        sender="from@example.com",
        recipients=("to@example.com",),
        smtp_force_ipv4=True,
        smtp_retries=smtp_retries,
        smtp_retry_seconds=smtp_retry_seconds,
    )


def test_message() -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "test"
    message["From"] = "from@example.com"
    message["To"] = "to@example.com"
    message.set_content("body")
    return message


class _FakeSMTP:
    def __init__(self, refused: dict) -> None:
        self.refused = refused

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, message: EmailMessage) -> dict:
        return self.refused


if __name__ == "__main__":
    unittest.main()
