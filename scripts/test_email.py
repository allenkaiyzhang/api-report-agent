from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.email_reporter import EmailConfig
from core.notification import notify


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a test email using the real project .env settings.")
    parser.add_argument(
        "--env-file",
        default=str(PROJECT_ROOT / ".env"),
        help="Path to env file. Defaults to project .env.",
    )
    parser.add_argument(
        "--subject",
        default=None,
        help="Optional subject suffix for the test email.",
    )
    parser.add_argument(
        "--body",
        default=None,
        help="Optional body text for the test email.",
    )
    parser.add_argument(
        "--ignore-enabled",
        action="store_true",
        help="Send even when EMAIL_ENABLED is not true. SMTP/recipient settings still come from env.",
    )
    return parser.parse_args()


def masked_config_summary(config: EmailConfig) -> str:
    return "\n".join(
        [
            f"EMAIL_ENABLED={config.enabled}",
            f"SMTP_HOST={mask_value(config.smtp_host)}",
            f"SMTP_PORT={config.smtp_port}",
            f"SMTP_USERNAME={mask_value(config.smtp_username)}",
            f"SMTP_USE_TLS={config.smtp_use_tls}",
            f"EMAIL_FROM={mask_value(config.sender)}",
            f"EMAIL_TO={', '.join(mask_value(item) for item in config.recipients) or '<empty>'}",
        ]
    )


def mask_value(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]} (len={len(value)})"


def validate_config(config: EmailConfig, ignore_enabled: bool = False) -> list[str]:
    errors = []
    if not config.enabled and not ignore_enabled:
        errors.append("EMAIL_ENABLED is not true")
    if not config.smtp_host:
        errors.append("SMTP_HOST is empty")
    if not config.sender:
        errors.append("EMAIL_FROM is empty and SMTP_USERNAME is empty")
    if not config.recipients:
        errors.append("EMAIL_TO is empty")
    return errors


def build_message_text(body: str | None) -> str:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return body or "\n".join(
        [
            "This is a test email from api-report-agent.",
            "",
            f"sent_at_utc: {now}",
            "config_source: project env",
            "",
            "If this arrives, SMTP delivery works through core.notification.notify().",
        ]
    )


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"env file not found: {env_path}", file=sys.stderr)
        return 2

    load_dotenv(env_path)
    config = EmailConfig.from_env(os.environ)
    print("Using email config:")
    print(masked_config_summary(config))

    errors = validate_config(config, ignore_enabled=args.ignore_enabled)
    if errors:
        print("Email config is not ready:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    try:
        if args.ignore_enabled:
            os.environ["EMAIL_ENABLED"] = "true"
        title = "test email"
        if args.subject:
            title = f"{title} {args.subject}"
        result = notify(title=title, body=build_message_text(args.body), channels=["email"])
    except Exception as exc:
        print(f"test email failed: {exc}", file=sys.stderr)
        return 1

    print(f"test email result: {result.get('results', {}).get('email')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
