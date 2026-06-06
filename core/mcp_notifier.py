"""Notifier interface and implementations.

Notifications:
  - EmailNotifier — SMTP email delivery
  - WebhookNotifier — HTTP POST to webhook URL
  - ConsoleNotifier — prints to stdout for local tests

Notification failures are logged but must not corrupt report files.
Audit logs go to logs/notifications/.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NotifierResult:
    """Result of a single notification channel dispatch."""
    success: bool
    channel: str
    error_message: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Notifier(ABC):
    """Abstract notification interface."""

    @abstractmethod
    def send(self, subject: str, body: str, report_type: str = "report") -> bool:
        """Send a notification. Returns True on success, False on failure."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Check notifier health."""


# ── Audit logger ─────────────────────────────────────────────────

class NotificationAuditLogger:
    """Logs notification attempts for audit trail."""

    def __init__(self, audit_dir: str = "logs/notifications") -> None:
        self._audit_dir = Path(audit_dir)
        self._audit_dir.mkdir(parents=True, exist_ok=True)

    def log(self, channel: str, subject: str, success: bool, error: str | None = None) -> None:
        """Append an audit record to the daily log file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = self._audit_dir / f"{today}.jsonl"

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "channel": channel,
            "subject": subject[:200],
            "success": success,
            "error": error,
        }
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Failed to write audit log: %s", exc)


# ── EmailNotifier ────────────────────────────────────────────────

class EmailNotifier(Notifier):
    """Sends email notifications via SMTP.

    Configuration from environment variables:
      SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
      SMTP_USE_TLS, SMTP_FORCE_IPV4, SMTP_RETRIES, SMTP_RETRY_SECONDS,
      EMAIL_FROM, EMAIL_TO, EMAIL_SUBJECT_PREFIX
    """

    def __init__(self) -> None:
        self._host = os.getenv("SMTP_HOST", "")
        self._port = int(os.getenv("SMTP_PORT", "587"))
        self._username = os.getenv("SMTP_USERNAME", "")
        self._password = os.getenv("SMTP_PASSWORD", "")
        self._use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        self._force_ipv4 = os.getenv("SMTP_FORCE_IPV4", "true").lower() == "true"
        self._retries = int(os.getenv("SMTP_RETRIES", "3"))
        self._retry_seconds = int(os.getenv("SMTP_RETRY_SECONDS", "5"))
        self._from = os.getenv("EMAIL_FROM", "")
        self._to_raw = os.getenv("EMAIL_TO", "")
        self._subject_prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "[Market Report]")
        self._to = [a.strip() for a in self._to_raw.split(",") if a.strip()]

        self._audit = NotificationAuditLogger()

    def send(self, subject: str, body: str, report_type: str = "report") -> bool:
        if not self._host or not self._to:
            logger.warning("Email not configured; skipping send")
            self._audit.log("email", subject, False, "Email not configured")
            return False

        full_subject = f"{self._subject_prefix} {subject}"

        for attempt in range(self._retries):
            try:
                self._send_email(full_subject, body)
                self._audit.log("email", subject, True)
                return True
            except Exception as exc:
                logger.warning(
                    "Email attempt %d/%d failed: %s",
                    attempt + 1,
                    self._retries,
                    exc,
                )
                if attempt < self._retries - 1:
                    import time
                    time.sleep(self._retry_seconds)

        self._audit.log("email", subject, False, f"Failed after {self._retries} retries")
        return False

    def _send_email(self, subject: str, body: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if self._force_ipv4:
            import socket
            socket.setdefaulttimeout(30)

        if self._use_tls:
            server = smtplib.SMTP(self._host, self._port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(self._host, self._port, timeout=30)

        if self._username:
            server.login(self._username, self._password)

        server.sendmail(self._from, self._to, msg.as_string())
        server.quit()
        logger.info("Email sent: %s", subject)

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": bool(self._host and self._to),
            "type": "email",
            "configured": bool(self._host),
        }


# ── WebhookNotifier ──────────────────────────────────────────────

class WebhookNotifier(Notifier):
    """Sends notifications via HTTP POST webhook."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self._url = webhook_url or os.getenv("WEBHOOK_URL", "")
        self._secret = os.getenv("WEBHOOK_SECRET", "")
        self._audit = NotificationAuditLogger()

    def send(self, subject: str, body: str, report_type: str = "report") -> bool:
        if not self._url:
            logger.warning("Webhook not configured; skipping send")
            self._audit.log("webhook", subject, False, "Webhook not configured")
            return False

        payload = json.dumps({
            "subject": subject,
            "body": body,
            "report_type": report_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }).encode("utf-8")

        req = urllib.request.Request(
            self._url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Secret": self._secret,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    self._audit.log("webhook", subject, True)
                    logger.info("Webhook sent: %s (status %d)", subject, resp.status)
                    return True
                else:
                    self._audit.log("webhook", subject, False, f"HTTP {resp.status}")
                    return False
        except Exception as exc:
            logger.error("Webhook failed: %s", exc)
            self._audit.log("webhook", subject, False, str(exc)[:200])
            return False

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": bool(self._url),
            "type": "webhook",
            "configured": bool(self._url),
        }


# ── ConsoleNotifier ──────────────────────────────────────────────

class ConsoleNotifier(Notifier):
    """Prints notifications to stdout for local development and tests.

    Handles Windows console encoding issues gracefully.
    """

    def __init__(self) -> None:
        self._audit = NotificationAuditLogger()

    def send(self, subject: str, body: str, report_type: str = "report") -> bool:
        try:
            # Try normal print with emoji
            self._safe_print(f"\n{'='*60}")
            self._safe_print(f"NOTIFICATION [{report_type}]: {subject}")
            self._safe_print(f"{'='*60}")
            self._safe_print(body)
            self._safe_print(f"{'='*60}\n")
        except UnicodeEncodeError:
            # Fall back to ASCII-safe output on broken consoles
            print(f"\n{'='*60}")
            print(f"NOTIFICATION [{report_type}]: {subject}")
            print(f"{'='*60}")
            # Strip non-ASCII characters
            ascii_body = body.encode("ascii", errors="replace").decode("ascii")
            print(ascii_body)
            print(f"{'='*60}\n")

        self._audit.log("console", subject, True)
        return True

    @staticmethod
    def _safe_print(text: str) -> None:
        """Print text, handling encoding errors on Windows consoles."""
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", errors="replace").decode("ascii"))

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "type": "console", "configured": True}


# ── Composite notifier ───────────────────────────────────────────

class CompositeNotifier:
    """Sends to multiple notifiers. Failures don't stop other channels.

    Returns per-channel NotifierResult for status aggregation.
    """

    def __init__(self, notifiers: list[Notifier] | None = None) -> None:
        self._notifiers = notifiers or []

    def add(self, notifier: Notifier) -> None:
        self._notifiers.append(notifier)

    def send(self, subject: str, body: str, report_type: str = "report") -> list[NotifierResult]:
        """Send to all channels. Returns list of NotifierResult."""
        results: list[NotifierResult] = []
        for n in self._notifiers:
            channel = n.__class__.__name__
            try:
                ok = n.send(subject, body, report_type)
                results.append(NotifierResult(
                    success=ok,
                    channel=channel,
                    error_message=None if ok else "dispatch failed",
                ))
            except Exception as exc:
                logger.error("Notifier %s exception: %s", channel, exc)
                results.append(NotifierResult(
                    success=False,
                    channel=channel,
                    error_message=str(exc)[:300],
                ))
        return results

    def health_check(self) -> dict[str, Any]:
        return {
            "channels": {
                n.__class__.__name__: n.health_check()
                for n in self._notifiers
            }
        }


# ── Factory ──────────────────────────────────────────────────────

def create_notifiers(
    enable_email: bool = False,
    enable_webhook: bool = False,
    enable_console: bool = True,
) -> CompositeNotifier:
    """Create configured notifiers from parameters and environment variables."""
    composite = CompositeNotifier()

    if enable_console:
        composite.add(ConsoleNotifier())

    if enable_email:
        email = EmailNotifier()
        composite.add(email)

    if enable_webhook:
        webhook = WebhookNotifier()
        composite.add(webhook)

    return composite
