from __future__ import annotations

import json
import smtplib
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from core.ai_analyzer import AIAnalysisConfig, analyze_market_report
from core.data_pipeline import load_jsonl, metrics_dir, normalized_file_path, parse_datetime, quality_file_path, raw_file_path
from core.history_context import build_history_context


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
    smtp_force_ipv4: bool = True
    smtp_retries: int = 3
    smtp_retry_seconds: int = 5
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
            smtp_force_ipv4=env.get("SMTP_FORCE_IPV4", "true").lower() == "true",
            smtp_retries=max(int(env.get("SMTP_RETRIES", "3") or "3"), 1),
            smtp_retry_seconds=max(int(env.get("SMTP_RETRY_SECONDS", "5") or "5"), 0),
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
    history_context = build_history_context(base_dir, market, trading_date)

    return {
        "report_type": "daily",
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
        "history_context": history_context,
    }


def build_intraday_report_payload(
    base_dir: Path,
    market: str,
    trading_date: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    raw_result = load_jsonl(raw_file_path(base_dir, market, trading_date))
    normalized_result = load_jsonl(normalized_file_path(base_dir, market, trading_date))
    raw_records = [
        record
        for record in raw_result.records
        if datetime_in_period(record.get("collected_at"), period_start, period_end)
    ]
    normalized_records = [
        record
        for record in normalized_result.records
        if datetime_in_period(record.get("collected_at"), period_start, period_end)
    ]
    return {
        "report_type": "intraday",
        "market": market,
        "trading_date": trading_date,
        "period_start": period_start.isoformat(timespec="seconds"),
        "period_end": period_end.isoformat(timespec="seconds"),
        "raw_lines": len(raw_records),
        "normalized_lines": len(normalized_records),
        "raw_json_parse_errors": len(raw_result.json_parse_errors),
        "symbol_count": len({record.get("symbol") for record in normalized_records if record.get("symbol")}),
        "symbol_summary": build_symbol_summary(normalized_records),
    }


def compose_daily_report_email(config: EmailConfig, payload: dict[str, Any], ai_analysis: str = "") -> EmailMessage:
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
        "AI analysis:",
        ai_analysis or "not enabled",
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


def compose_intraday_report_email(config: EmailConfig, payload: dict[str, Any], ai_analysis: str = "") -> EmailMessage:
    market = payload["market"]
    trading_date = payload["trading_date"]
    subject = f"{config.subject_prefix} {market} intraday data report {trading_date} {payload['period_start']} to {payload['period_end']}"
    lines = [
        f"Market: {market}",
        f"Trading date: {trading_date}",
        f"Period: {payload['period_start']} -> {payload['period_end']}",
        "",
        "Data counts:",
        f"- raw lines: {payload.get('raw_lines', 0)}",
        f"- normalized lines: {payload.get('normalized_lines', 0)}",
        f"- raw JSON parse errors: {payload.get('raw_json_parse_errors', 0)}",
        f"- symbols: {payload.get('symbol_count', 0)}",
        "",
        "AI analysis:",
        ai_analysis or "not enabled",
        "",
        "Symbol summary:",
    ]
    for item in payload.get("symbol_summary", []):
        lines.append(
            f"- {item['symbol']}: first={item.get('first_price')} last={item.get('last_price')} "
            f"return_pct={item.get('return_pct')} volume_delta={item.get('volume_delta')} records={item.get('records')}"
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content("\n".join(lines))
    return message


def send_email(config: EmailConfig, message: EmailMessage) -> None:
    last_error: BaseException | None = None
    attempts = max(config.smtp_retries, 1)
    for attempt in range(1, attempts + 1):
        try:
            targets = resolve_smtp_targets(config)
        except Exception as exc:
            last_error = exc
            if attempt < attempts and config.smtp_retry_seconds:
                time.sleep(config.smtp_retry_seconds)
            continue
        for target_index, target in enumerate(targets, start=1):
            try:
                send_email_once(config, message, target)
                return
            except Exception as exc:
                last_error = exc
                if target_index < len(targets):
                    continue
        if attempt < attempts and config.smtp_retry_seconds:
            time.sleep(config.smtp_retry_seconds)

    detail = str(last_error) if last_error else "unknown SMTP error"
    raise RuntimeError(
        "SMTP delivery failed "
        f"host={config.smtp_host} port={config.smtp_port} "
        f"force_ipv4={config.smtp_force_ipv4} attempts={attempts}: {detail}"
    ) from last_error


def resolve_smtp_targets(config: EmailConfig) -> list[str]:
    if not config.smtp_force_ipv4:
        return [config.smtp_host]

    infos = socket.getaddrinfo(
        config.smtp_host,
        config.smtp_port,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
    )
    targets = []
    for info in infos:
        address = info[4][0]
        if address not in targets:
            targets.append(address)
    return targets or [config.smtp_host]


def send_email_once(config: EmailConfig, message: EmailMessage, smtp_host: str) -> None:
    if config.smtp_use_tls:
        with smtplib.SMTP(smtp_host, config.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if config.smtp_username or config.smtp_password:
                smtp.login(config.smtp_username, config.smtp_password)
            refused = smtp.send_message(message)
            if refused:
                raise RuntimeError(f"SMTP refused recipients: {refused}")
        return

    with smtplib.SMTP(smtp_host, config.smtp_port, timeout=30) as smtp:
        if config.smtp_username or config.smtp_password:
            smtp.login(config.smtp_username, config.smtp_password)
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError(f"SMTP refused recipients: {refused}")


def build_daily_report_notification(
    config: EmailConfig,
    base_dir: Path,
    market: str,
    trading_date: str,
    ai_config: AIAnalysisConfig | None = None,
) -> tuple[str, str, dict[str, Any]]:
    payload = build_daily_report_payload(base_dir, market, trading_date)
    ai_analysis = analyze_market_report(ai_config, payload)
    message = compose_daily_report_email(config, payload, ai_analysis=ai_analysis)
    return str(message["Subject"]), message.get_content(), payload


def build_intraday_report_notification(
    config: EmailConfig,
    base_dir: Path,
    market: str,
    trading_date: str,
    period_start: datetime,
    period_end: datetime,
    ai_config: AIAnalysisConfig | None = None,
) -> tuple[str, str, dict[str, Any]]:
    payload = build_intraday_report_payload(base_dir, market, trading_date, period_start, period_end)
    ai_analysis = analyze_market_report(ai_config, payload)
    message = compose_intraday_report_email(config, payload, ai_analysis=ai_analysis)
    return str(message["Subject"]), message.get_content(), payload


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


def datetime_in_period(value: Any, period_start: datetime, period_end: datetime) -> bool:
    parsed = parse_datetime(value)
    if parsed is None:
        return False
    local_value = parsed.astimezone(period_start.tzinfo)
    return period_start <= local_value < period_end


def build_symbol_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        symbol = str(record.get("symbol") or "")
        if symbol:
            by_symbol.setdefault(symbol, []).append(record)

    summaries = []
    for symbol, rows in sorted(by_symbol.items()):
        ordered = sorted(rows, key=lambda row: row.get("event_time") or "")
        prices = [float(row["last_price"]) for row in ordered if row.get("last_price") is not None]
        volumes = [int(row["volume_cumulative"]) for row in ordered if row.get("volume_cumulative") is not None]
        first_price = prices[0] if prices else None
        last_price = prices[-1] if prices else None
        return_pct = None
        if first_price and last_price:
            return_pct = round((last_price / first_price - 1) * 100, 6)
        volume_delta = None
        if len(volumes) >= 2:
            volume_delta = volumes[-1] - volumes[0]
        summaries.append(
            {
                "symbol": symbol,
                "records": len(rows),
                "first_price": first_price,
                "last_price": last_price,
                "return_pct": return_pct,
                "volume_delta": volume_delta,
            }
        )
    return summaries
