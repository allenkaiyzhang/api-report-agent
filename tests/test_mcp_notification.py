"""Tests for notification dispatch: ConsoleNotifier, audit logs, composite, NotifierResult.

Tests:
  - ConsoleNotifier prints and returns success
  - Audit logging
  - Composite dispatch with partial failure
  - DISPATCHED / PARTIAL_FAILED / FAILED status resolution
  - Config-driven channel enablement
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.mcp_notifier import (
    CompositeNotifier,
    ConsoleNotifier,
    EmailNotifier,
    NotificationAuditLogger,
    NotifierResult,
    WebhookNotifier,
    create_notifiers,
)


class TestNotifierResult(unittest.TestCase):
    """Verify NotifierResult dataclass."""

    def test_success_result(self):
        r = NotifierResult(success=True, channel="console")
        self.assertTrue(r.success)
        self.assertIsNone(r.error_message)

    def test_failure_result(self):
        r = NotifierResult(success=False, channel="email", error_message="SMTP timeout")
        self.assertFalse(r.success)
        self.assertEqual(r.error_message, "SMTP timeout")

    def test_with_metadata(self):
        r = NotifierResult(
            success=True, channel="webhook",
            message_id="msg-123", metadata={"status": 200},
        )
        self.assertEqual(r.message_id, "msg-123")
        self.assertEqual(r.metadata["status"], 200)


class TestConsoleNotifier(unittest.TestCase):
    """Verify ConsoleNotifier prints and returns success."""

    def test_console_send(self):
        notifier = ConsoleNotifier()
        result = notifier.send("Test Subject", "Test body content", "report")
        self.assertTrue(result)

    def test_console_handles_unicode(self):
        notifier = ConsoleNotifier()
        result = notifier.send("测试主题", "包含中文和emoji 📊 的内容", "report")
        self.assertTrue(result)

    def test_console_health(self):
        notifier = ConsoleNotifier()
        health = notifier.health_check()
        self.assertTrue(health["ok"])
        self.assertEqual(health["type"], "console")


class TestNotificationAuditLogger(unittest.TestCase):
    """Verify audit logging."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.audit = NotificationAuditLogger(audit_dir=self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_success(self):
        self.audit.log("console", "Test subject", True)
        log_files = list(Path(self.temp_dir).glob("*.jsonl"))
        self.assertEqual(len(log_files), 1)
        content = log_files[0].read_text(encoding="utf-8")
        record = json.loads(content.strip())
        self.assertTrue(record["success"])
        self.assertEqual(record["channel"], "console")

    def test_log_failure(self):
        self.audit.log("email", "Failed send", False, "SMTP timeout")
        log_files = list(Path(self.temp_dir).glob("*.jsonl"))
        self.assertEqual(len(log_files), 1)
        content = log_files[0].read_text(encoding="utf-8")
        record = json.loads(content.strip())
        self.assertFalse(record["success"])
        self.assertIn("SMTP timeout", record["error"])


class TestCompositeNotifier(unittest.TestCase):
    """Verify composite dispatches to all channels with proper results."""

    def test_send_to_all(self):
        console = ConsoleNotifier()
        composite = CompositeNotifier([console])
        results = composite.send("Subj", "Body")
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].channel, "ConsoleNotifier")

    def test_send_does_not_crash_on_failure(self):
        class FailingNotifier(ConsoleNotifier):
            def send(self, subject, body, report_type="report"):
                raise RuntimeError("simulated failure")

        composite = CompositeNotifier([
            FailingNotifier(),
            ConsoleNotifier(),
        ])
        results = composite.send("Subj", "Body")
        self.assertEqual(len(results), 2)
        success_count = sum(1 for r in results if r.success)
        self.assertEqual(success_count, 1)

        fail_count = sum(1 for r in results if not r.success)
        self.assertEqual(fail_count, 1)

    def test_all_fail(self):
        class AlwaysFail(ConsoleNotifier):
            def send(self, subject, body, report_type="report"):
                return False

        composite = CompositeNotifier([AlwaysFail(), AlwaysFail()])
        results = composite.send("Subj", "Body")
        self.assertTrue(all(not r.success for r in results))

    def test_factory_creates_console_only(self):
        composite = create_notifiers(
            enable_email=False,
            enable_webhook=False,
            enable_console=True,
        )
        health = composite.health_check()
        self.assertEqual(len(health["channels"]), 1)

    def test_factory_creates_all_channels(self):
        composite = create_notifiers(
            enable_email=True,
            enable_webhook=True,
            enable_console=True,
        )
        health = composite.health_check()
        self.assertEqual(len(health["channels"]), 3)


class TestStatusResolution(unittest.TestCase):
    """Test DISPATCHED / PARTIAL_FAILED / FAILED resolution."""

    def test_all_success_dispatched(self):
        from scripts.market_report_agent import _resolve_dispatch_status
        results = [NotifierResult(success=True, channel="console")]
        self.assertEqual(_resolve_dispatch_status(results), "DISPATCHED")

    def test_partial_failure(self):
        from scripts.market_report_agent import _resolve_dispatch_status
        results = [
            NotifierResult(success=True, channel="console"),
            NotifierResult(success=False, channel="email", error_message="fail"),
        ]
        self.assertEqual(_resolve_dispatch_status(results), "PARTIAL_FAILED")

    def test_all_failed(self):
        from scripts.market_report_agent import _resolve_dispatch_status
        results = [
            NotifierResult(success=False, channel="email", error_message="fail"),
            NotifierResult(success=False, channel="webhook", error_message="fail"),
        ]
        self.assertEqual(_resolve_dispatch_status(results), "FAILED")

    def test_empty(self):
        from scripts.market_report_agent import _resolve_dispatch_status
        self.assertEqual(_resolve_dispatch_status([]), "DISPATCHED")


class TestEmailNotifier(unittest.TestCase):
    """Verify EmailNotifier config reading."""

    def test_not_configured(self):
        notifier = EmailNotifier()
        self.assertFalse(notifier._host)
        result = notifier.send("Test", "Body")
        self.assertFalse(result)

    def test_health_check_unconfigured(self):
        notifier = EmailNotifier()
        health = notifier.health_check()
        self.assertFalse(health["ok"])
        self.assertFalse(health["configured"])


class TestWebhookNotifier(unittest.TestCase):
    """Verify WebhookNotifier config reading."""

    def test_not_configured(self):
        notifier = WebhookNotifier()
        result = notifier.send("Test", "Body")
        self.assertFalse(result)

    def test_health_check_unconfigured(self):
        notifier = WebhookNotifier()
        health = notifier.health_check()
        self.assertFalse(health["ok"])


if __name__ == "__main__":
    unittest.main()
