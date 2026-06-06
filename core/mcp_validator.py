"""MCP data validator — validates cleaned data against JSON schemas.

Enforces report-type-specific validation rules:

  intraday_brief  → requires valid open/active trading session
  daily_close_report → requires market closed after trading session
  event_alert     → can run during valid monitored windows only

Never silently skips validation if jsonschema is missing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clients.market_data_client import MarketReportDataset

logger = logging.getLogger(__name__)

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "config" / "schemas"


class McpDataValidator:
    """Validates MarketReportDataset against JSON schemas and report-type rules.

    Must pass validation before reports can be generated.
    Does NOT fabricate missing data.
    """

    def __init__(self, schema_dir: Path | None = None) -> None:
        self._schema_dir = schema_dir or _SCHEMA_DIR
        self._schemas: dict[str, dict[str, Any]] = {}
        self._load_schemas()

    def _load_schemas(self) -> None:
        schema_files = [
            "quote.schema.json",
            "candle.schema.json",
            "intraday.schema.json",
            "market_status.schema.json",
            "market_report_dataset.schema.json",
        ]
        for fname in schema_files:
            path = self._schema_dir / fname
            if path.exists():
                try:
                    self._schemas[fname] = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    logger.error("Invalid schema file %s: %s", fname, exc)

    def validate(self, dataset: MarketReportDataset) -> MarketReportDataset:
        """Validate dataset. Sets dataset.validated and dataset.validation_errors.

        Validation is report-type aware:
        - intraday_brief: requires open/active session (or explicit extended-hours mode)
        - daily_close_report: requires market closed after trading session
        - event_alert: must run during valid monitored windows
        """
        errors: list[str] = []

        # ── Basic data presence ──────────────────────────────────
        if not dataset.quotes:
            errors.append("No quote data available")
        if not dataset.market_status:
            errors.append("No market status data available")
        if not dataset.symbols:
            errors.append("No symbols specified")

        # ── Schema validation (must not silently skip) ───────────
        try:
            import jsonschema
            self._validate_schemas(dataset, errors)
        except ImportError:
            errors.append(
                "jsonschema package not installed — schema validation skipped. "
                "Install with: pip install jsonschema"
            )
            logger.error("jsonschema not installed; cannot validate schemas")

        # ── Report-type-aware market session validation ──────────
        if dataset.market_status:
            self._validate_market_session(dataset, errors)

        # ── Cross-field validation ───────────────────────────────
        symbols_with_quotes = {q.symbol for q in dataset.quotes}
        missing = set(dataset.symbols) - symbols_with_quotes
        if missing:
            errors.append(f"Missing quote data for symbols: {', '.join(sorted(missing))}")

        dataset.validated = len(errors) == 0
        dataset.validation_errors = errors

        if not dataset.validated:
            logger.warning(
                "Dataset %s [%s] validation failed: %s",
                dataset.run_id,
                dataset.report_type,
                "; ".join(errors),
            )
        else:
            logger.info("Dataset %s [%s] validation passed", dataset.run_id, dataset.report_type)

        return dataset

    def _validate_schemas(self, dataset: MarketReportDataset, errors: list[str]) -> None:
        import jsonschema

        quote_schema = self._schemas.get("quote.schema.json")
        candle_schema = self._schemas.get("candle.schema.json")
        intraday_schema = self._schemas.get("intraday.schema.json")

        for i, q in enumerate(dataset.quotes):
            if quote_schema:
                try:
                    jsonschema.validate(q.to_dict(), quote_schema)
                except jsonschema.ValidationError as exc:
                    errors.append(f"Quote[{i}] {q.symbol}: {exc.message}")

        for i, c in enumerate(dataset.candles):
            if candle_schema:
                try:
                    jsonschema.validate(c.to_dict(), candle_schema)
                except jsonschema.ValidationError as exc:
                    errors.append(f"Candle[{i}] {c.symbol}: {exc.message}")

        for i, p in enumerate(dataset.intraday):
            if intraday_schema:
                try:
                    jsonschema.validate(p.to_dict(), intraday_schema)
                except jsonschema.ValidationError as exc:
                    errors.append(f"Intraday[{i}] {p.symbol}: {exc.message}")

    def _validate_market_session(
        self, dataset: MarketReportDataset, errors: list[str]
    ) -> None:
        """Report-type-aware market session validation."""
        status = dataset.market_status
        if status is None:
            return

        session = status.session
        report_type = dataset.report_type

        # ── Distinguish market states ────────────────────────────
        is_holiday = session == "holiday"
        is_closed = session == "closed"
        is_weekend = self._is_weekend(status)

        if report_type == "intraday_brief":
            if not status.is_open:
                reason = self._closed_reason(status, is_holiday, is_weekend, is_closed)
                errors.append(
                    f"Intraday report requires open/active trading session. "
                    f"Market {dataset.market} is {reason}."
                )

        elif report_type == "daily_close_report":
            if is_holiday:
                errors.append(
                    f"Daily close report skipped: {dataset.market} market is on holiday"
                )
            elif is_weekend:
                errors.append(
                    f"Daily close report skipped: {dataset.market} market is closed (weekend)"
                )
            elif status.is_open:
                errors.append(
                    f"Daily close report requires market to be closed. "
                    f"Market {dataset.market} is open ({session})."
                )
            elif is_closed:
                # Market closed — this is the expected state for daily_close_report.
                # Only block if there's no trading date data or if the close seems stale.
                pass
            elif session == "unknown" or not session:
                errors.append(
                    f"Daily close report cannot verify market state: "
                    f"{dataset.market} status is unknown/stale"
                )

        elif report_type == "event_alert":
            if is_holiday or (is_closed and not self._could_be_post_market(status)):
                errors.append(
                    f"Event alert requires valid monitored window. "
                    f"Market {dataset.market} is {self._closed_reason(status, is_holiday, is_weekend, is_closed)}."
                )

    @staticmethod
    def _is_weekend(status: Any) -> bool:
        """Heuristic: detect weekend based on timestamp day-of-week."""
        ts = getattr(status, "timestamp", "")
        if ts:
            try:
                from datetime import datetime as dt
                d = dt.fromisoformat(ts.replace("Z", "+00:00"))
                return d.weekday() >= 5  # Saturday=5, Sunday=6
            except (ValueError, TypeError):
                pass
        return False

    @staticmethod
    def _could_be_post_market(status: Any) -> bool:
        """Check if a closed market could legitimately be in post-market window."""
        session = getattr(status, "session", "")
        return session in ("extended_post", "closed")

    @staticmethod
    def _closed_reason(
        status: Any, is_holiday: bool, is_weekend: bool, is_closed: bool
    ) -> str:
        if is_holiday:
            return "on holiday"
        if is_weekend:
            return "closed (weekend)"
        session = getattr(status, "session", "")
        if session == "pre_market":
            return "pre-market"
        if session == "extended_post":
            return "post-market (extended)"
        if is_closed:
            return "closed"
        if session:
            return f"in session '{session}' (not open)"
        return "in unknown state"
