from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from core.data_pipeline import load_jsonl, metrics_dir, normalized_file_path, quality_file_path, raw_file_path


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool
    sender: str
    recipients: tuple[str, ...]
    subject_prefix: str = "[api-report-agent]"

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "EmailConfig":
        recipients = tuple(
            item.strip()
            for item in env.get("EMAIL_TO", "").split(",")
            if item.strip()
        )
        return cls(
            enabled=env.get("EMAIL_ENABLED", "false").lower() == "true",
            smtp_host=env.get("SMTP_HOST", ""),
            smtp_port=int(env.get("SMTP_PORT", "587") or "587"),
            smtp_username=env.get("SMTP_USERNAME", ""),
            smtp_password=env.get("SMTP_PASSWORD", ""),
            smtp_use_tls=env.get("SMTP_USE_TLS", "true").lower() == "true",
            sender=env.get("EMAIL_FROM", env.get("SMTP_USERNAME", "")),
            recipients=recipients,
            subject_prefix=env.get("EMAIL_SUBJECT_PREFIX", "[api-report-agent]"),
        )

    def is_ready(self) -> bool:
        return bool(self.enabled and self.smtp_host and self.sender and self.recipients)


def build_daily_report_payload(base_dir: Path, market: str, trading_date: str) -> dict[str, Any]:
    raw_result = load_jsonl(raw_file_path(base_dir, market, trading_date))
    normalized_result = load_jsonl(normalized_file_path(base_dir, market, trading_date))
    daily = load_json(metrics_dir(base_dir, market, trading_date) / "daily.json")
    windows = load_json(metrics_dir(base_dir, market, trading_date) / "windows.json")
    quality = load_json(quality_file_path(base_dir, market, trading_date))

    return {
        "market": market,
        "trading_date": trading_date,
        "raw_lines": raw_result.raw_lines,
        "raw_json_parse_errors": len(raw_result.json_parse_errors),
        "normalized_lines": len(normalized_result.records),
        "window_count": daily.get("window_count", 0),
        "configured_windows": len(windows.get("windows", [])),
        "symbol_count": len(daily.get("symbols", [])),
        "quality": quality,
        "daily": daily,
    }


def compose_daily_report_email(config: EmailConfig, payload: dict[str, Any]) -> EmailMessage:
    market = payload["market"]
    trading_date = payload["trading_date"]
    subject = f"{config.subject_prefix} {market} daily data report {trading_date}"

    quality = payload.get("quality", {})
    normalized_quality = quality.get("normalized_quality", {})
    window_quality = quality.get("window_quality", {})
    daily = payload.get("daily", {})

    lines = [
        f"Market: {market}",
        f"Trading date: {trading_date}",
        "",
        "Data counts:",
        f"- raw lines: {payload.get('raw_lines', 0)}",
        f"- raw JSON parse errors: {payload.get('raw_json_parse_errors', 0)}",
        f"- normalized lines: {payload.get('normalized_lines', 0)}",
        f"- daily symbols: {payload.get('symbol_count', 0)}",
        f"- generated windows: {payload.get('window_count', 0)}",
        f"- configured windows: {payload.get('configured_windows', 0)}",
        "",
        "Quality:",
        f"- valid lines: {normalized_quality.get('valid_lines', 0)}",
        f"- invalid lines: {normalized_quality.get('invalid_lines', 0)}",
        f"- duplicate records: {normalized_quality.get('duplicate_records', 0)}",
        f"- missing windows: {', '.join(window_quality.get('missing_windows', [])) or 'none'}",
        "",
        "Top movers:",
    ]

    market_summary = daily.get("market_summary", {})
    for label, key in (
        ("top gainers", "top_gainers"),
        ("top losers", "top_losers"),
        ("highest volatility", "highest_volatility"),
        ("largest drawdown", "largest_drawdown"),
    ):
        items = market_summary.get(key, [])
        rendered = ", ".join(_format_summary_item(item) for item in items[:5]) or "none"
        lines.append(f"- {label}: {rendered}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content("\n".join(lines))
    return message


def send_email(config: EmailConfig, message: EmailMessage) -> None:
    if config.smtp_use_tls:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if config.smtp_username or config.smtp_password:
                smtp.login(config.smtp_username, config.smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        if config.smtp_username or config.smtp_password:
            smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


def send_daily_report(config: EmailConfig, base_dir: Path, market: str, trading_date: str) -> None:
    payload = build_daily_report_payload(base_dir, market, trading_date)
    message = compose_daily_report_email(config, payload)
    send_email(config, message)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _format_summary_item(item: dict[str, Any]) -> str:
    symbol = str(item.get("symbol", ""))
    values = [
        f"{key}={value}"
        for key, value in item.items()
        if key != "symbol" and value is not None
    ]
    if not values:
        return symbol
    return f"{symbol} ({', '.join(values)})"
