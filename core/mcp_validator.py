"""Validate cleaned MCP datasets before report generation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from clients.market_data_client import MarketReportDataset

logger = logging.getLogger(__name__)

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "config" / "schemas"
_MARKET_TIMEZONES = {
    "US": "America/New_York",
    "HK": "Asia/Hong_Kong",
    "CN": "Asia/Shanghai",
    "JP": "Asia/Tokyo",
}


class McpDataValidator:
    """Apply schemas and report-specific market/session rules."""

    def __init__(
        self,
        schema_dir: Path | None = None,
        post_market_delay_minutes: int = 15,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._schema_dir = schema_dir or _SCHEMA_DIR
        self._post_market_delay_minutes = post_market_delay_minutes
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._schemas: dict[str, dict[str, Any]] = {}
        self._load_schemas()

    def _load_schemas(self) -> None:
        for filename in (
            "quote.schema.json",
            "candle.schema.json",
            "intraday.schema.json",
            "market_status.schema.json",
            "market_report_dataset.schema.json",
        ):
            path = self._schema_dir / filename
            if path.exists():
                try:
                    self._schemas[filename] = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    logger.error("Invalid schema file %s: %s", filename, exc)

    def validate(self, dataset: MarketReportDataset) -> MarketReportDataset:
        errors: list[str] = []
        if not dataset.quotes:
            errors.append("No quote data available")
        if not dataset.market_status:
            errors.append("No market status data available")
        if not dataset.symbols:
            errors.append("No symbols specified")

        try:
            self._validate_schemas(dataset, errors)
        except ImportError:
            errors.append("jsonschema package not installed; schema validation cannot run")

        if dataset.market_status:
            self._validate_market_session(dataset, errors)

        symbols_with_quotes = {quote.symbol for quote in dataset.quotes}
        missing = set(dataset.symbols) - symbols_with_quotes
        if missing:
            errors.append(f"Missing quote data for symbols: {', '.join(sorted(missing))}")

        dataset.validated = not errors
        dataset.validation_errors = errors
        if errors:
            logger.warning(
                "Dataset %s [%s] validation failed: %s",
                dataset.run_id,
                dataset.report_type,
                "; ".join(errors),
            )
        return dataset

    def _validate_schemas(self, dataset: MarketReportDataset, errors: list[str]) -> None:
        import jsonschema

        for label, records, filename in (
            ("Quote", dataset.quotes, "quote.schema.json"),
            ("Candle", dataset.candles, "candle.schema.json"),
            ("Intraday", dataset.intraday, "intraday.schema.json"),
        ):
            schema = self._schemas.get(filename)
            if not schema:
                continue
            for index, record in enumerate(records):
                try:
                    jsonschema.validate(record.to_dict(), schema)
                except jsonschema.ValidationError as exc:
                    errors.append(f"{label}[{index}] {record.symbol}: {exc.message}")

    def _validate_market_session(
        self, dataset: MarketReportDataset, errors: list[str]
    ) -> None:
        status = dataset.market_status
        if status is None:
            return

        if dataset.report_type == "intraday_brief":
            if not status.is_open or status.session not in (
                "regular",
                "extended_pre",
                "extended_post",
            ):
                errors.append(
                    f"Intraday report requires open/active trading session. "
                    f"Market {dataset.market} is {status.session or 'unknown'}."
                )
            return

        if dataset.report_type == "daily_close_report":
            if status.session == "holiday":
                errors.append(f"Daily close report blocked: {dataset.market} market is on holiday")
            elif status.is_open:
                errors.append(
                    f"Daily close report requires market to be closed. "
                    f"Market {dataset.market} is open ({status.session})."
                )
            elif status.session not in ("closed", "extended_post"):
                errors.append(
                    f"Daily close report cannot verify market state: "
                    f"{dataset.market} status is {status.session or 'unknown'}"
                )
            else:
                self._validate_daily_close_data(dataset, errors)
            return

        if dataset.report_type == "event_alert" and status.session in ("holiday", "unknown", ""):
            errors.append("Event alert requires a known monitored market window")

    def _validate_daily_close_data(
        self, dataset: MarketReportDataset, errors: list[str]
    ) -> None:
        status = dataset.market_status
        if status is None:
            return

        market_tz = ZoneInfo(_MARKET_TIMEZONES.get(dataset.market.upper(), "UTC"))
        close_time = self._resolve_completed_close(status, market_tz)
        if close_time is None:
            errors.append("Daily close report blocked: missing_session_close")
            return

        close_local = close_time.astimezone(market_tz)
        trading_date = close_local.strftime("%Y-%m-%d")
        now_utc = self._now_provider().astimezone(timezone.utc)
        elapsed = (now_utc - close_time.astimezone(timezone.utc)).total_seconds()

        if close_local.weekday() >= 5:
            errors.append(f"Daily close report blocked: invalid trading day {trading_date}")
        if elapsed < 0:
            errors.append("Daily close report blocked: completed session close is in the future")
        elif elapsed < self._post_market_delay_minutes * 60:
            errors.append("Daily close report blocked: post-market delay not elapsed")

        status_time = self._parse_timestamp(status.timestamp, market_tz)
        if status_time is None:
            errors.append("Daily close report cannot verify market status timestamp")
        elif status_time < close_time:
            errors.append("Daily close report market status predates completed session close")

        if not dataset.candles:
            errors.append("Daily close report requires candle data (missing daily OHLCV)")
        if not dataset.intraday:
            errors.append("Daily close report requires intraday data")

        self._require_symbol_dates("quote", dataset.symbols, dataset.quotes, trading_date, market_tz, errors)
        self._require_symbol_dates("candle", dataset.symbols, dataset.candles, trading_date, market_tz, errors)
        self._require_symbol_dates("intraday", dataset.symbols, dataset.intraday, trading_date, market_tz, errors)

        for quote in dataset.quotes:
            timestamp = self._parse_timestamp(quote.timestamp, market_tz)
            if timestamp is None:
                errors.append(f"Daily close report: invalid quote timestamp for {quote.symbol}")
                continue
            age = (now_utc - timestamp.astimezone(timezone.utc)).total_seconds()
            if age < 0 or age > 3600:
                errors.append(
                    f"Daily close report: stale quote for {quote.symbol} "
                    f"(timestamp {quote.timestamp}, age {age:.0f}s)"
                )

    @staticmethod
    def _resolve_completed_close(status: Any, market_tz: ZoneInfo) -> datetime | None:
        for value in (status.current_session_close, status.last_close):
            parsed = McpDataValidator._parse_timestamp(value or "", market_tz)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_timestamp(value: str, default_tz: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=default_tz)
        except (ValueError, TypeError):
            return None

    @classmethod
    def _require_symbol_dates(
        cls,
        label: str,
        symbols: list[str],
        records: list[Any],
        trading_date: str,
        market_tz: ZoneInfo,
        errors: list[str],
    ) -> None:
        aligned = {
            record.symbol
            for record in records
            if (parsed := cls._parse_timestamp(record.timestamp, market_tz))
            and parsed.astimezone(market_tz).strftime("%Y-%m-%d") == trading_date
        }
        missing = set(symbols) - aligned
        if missing:
            errors.append(
                f"Daily close report: {label} timestamps do not align with trading date "
                f"{trading_date} for symbols: {', '.join(sorted(missing))}"
            )
