from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.email_reporter import EmailConfig
from core.runtime_support import RuntimeState
from scripts.run_pipeline import intraday_email_key, intraday_email_window, send_daily_report_after_close, send_intraday_reports


class RunPipelineEmailTest(unittest.TestCase):
    def test_intraday_email_window_uses_two_hour_market_period(self) -> None:
        now = datetime(2026, 5, 7, 11, 35, tzinfo=ZoneInfo("America/New_York"))
        window = intraday_email_window("US", now)

        self.assertIsNotNone(window)
        assert window is not None
        trading_date, start, end = window
        self.assertEqual(trading_date, "2026-05-07")
        self.assertEqual(start.isoformat(timespec="minutes"), "2026-05-07T09:30-04:00")
        self.assertEqual(end.isoformat(timespec="minutes"), "2026-05-07T11:30-04:00")
        self.assertEqual(intraday_email_key("US", trading_date, start, end), "US:2026-05-07:0930_1130")

    def test_intraday_notify_email_error_does_not_mark_sent_and_can_retry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            raw_path = temp_path / "raw.jsonl"
            normalized_path = temp_path / "normalized.jsonl"
            raw_path.write_text("x", encoding="utf-8")
            normalized_path.write_text("x", encoding="utf-8")
            state = RuntimeState(path=temp_path / "runtime.json")
            key = "US:2026-05-07:0930_1130"
            state.mark_intraday_email_report_failed(key, "previous failure")

            with (
                patch("scripts.run_pipeline.intraday_email_window") as mock_window,
                patch("scripts.run_pipeline.raw_file_path", return_value=raw_path),
                patch("scripts.run_pipeline.normalized_file_path", return_value=normalized_path),
                patch("scripts.run_pipeline.build_intraday_report_notification", return_value=("title", "body", {})),
                patch("scripts.run_pipeline.notify", return_value={"results": {"email": {"status": "error", "error": "smtp down"}}}) as mock_notify,
            ):
                mock_window.return_value = (
                    "2026-05-07",
                    datetime(2026, 5, 7, 9, 30, tzinfo=ZoneInfo("America/New_York")),
                    datetime(2026, 5, 7, 11, 30, tzinfo=ZoneInfo("America/New_York")),
                )

                send_intraday_reports(
                    markets=["US"],
                    now=datetime(2026, 5, 7, 11, 35, tzinfo=ZoneInfo("America/New_York")),
                    state=state,
                    logger=_NullLogger(),
                    email_config=test_email_config(),
                    ai_config=None,
                )

            self.assertFalse(state.intraday_email_report_sent(key))
            self.assertTrue(state.intraday_email_report_failed(key))
            mock_notify.assert_called_once()

    def test_daily_notify_email_error_does_not_mark_sent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            daily_path = temp_path / "daily.json"
            quality_path = temp_path / "quality.json"
            daily_path.write_text("{}", encoding="utf-8")
            quality_path.write_text("{}", encoding="utf-8")
            state = RuntimeState(path=temp_path / "runtime.json")

            with (
                patch("scripts.run_pipeline.build_daily_report_notification", return_value=("title", "body", {})),
                patch("scripts.run_pipeline.notify", return_value={"results": {"email": {"status": "error", "error": "smtp down"}}}) as mock_notify,
            ):
                send_daily_report_after_close(
                    email_config=test_email_config(),
                    ai_config=None,
                    market="US",
                    trading_date="2026-05-07",
                    daily_path=daily_path,
                    quality_path=quality_path,
                    state=state,
                    logger=_NullLogger(),
                )

            self.assertFalse(state.email_report_sent("US", "2026-05-07"))
            self.assertTrue(state.email_report_failed("US", "2026-05-07"))
            mock_notify.assert_called_once()


def test_email_config() -> EmailConfig:
    return EmailConfig(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="password",
        smtp_use_tls=True,
        sender="from@example.com",
        recipients=("to@example.com",),
    )


class _NullLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
