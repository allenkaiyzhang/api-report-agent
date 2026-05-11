from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.email_reporter import EmailConfig, send_email


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    config = EmailConfig.from_env(os.environ)
    if not config.enabled or not config.is_ready():
        print("SMTP config is not ready: check EMAIL_ENABLED, SMTP_HOST, EMAIL_FROM, EMAIL_TO", file=sys.stderr)
        return 2

    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    message = EmailMessage()
    message["Subject"] = f"{config.subject_prefix} SMTP delivery test"
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(
        "\n".join(
            [
                "SMTP delivery test from api-report-agent.",
                f"sent_at_utc: {now}",
                f"smtp_host: {config.smtp_host}",
                f"smtp_port: {config.smtp_port}",
                f"force_ipv4: {config.smtp_force_ipv4}",
                f"retries: {config.smtp_retries}",
            ]
        )
    )

    try:
        send_email(config, message)
    except Exception as exc:
        print(f"SMTP delivery failed: {exc}", file=sys.stderr)
        return 1

    print("SMTP accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
