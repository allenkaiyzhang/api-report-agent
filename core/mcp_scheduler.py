"""MCP scheduler — manages periodic intraday and post-market report generation.

On each tick:
  1. Check market status / trading session via MarketDataClient.
  2. Generate intraday reports during valid market windows at the configured interval.
  3. Generate post-market daily summary after market close + post_market_delay.
  4. Avoid duplicate report generation via persistent dedup state.
  5. Record every skipped run with an auditable reason.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from clients.market_data_client import MarketDataClient, MarketStatusInfo
from core.mcp_datastore import McpDataStore

logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DATA_COLLECTED = "DATA_COLLECTED"
    DATA_VALIDATED = "DATA_VALIDATED"
    ANALYZED = "ANALYZED"
    REPORT_GENERATED = "REPORT_GENERATED"
    DISPATCHED = "DISPATCHED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


# ── Dedup state persistence ─────────────────────────────────────
_DEFAULT_DEDUP_PATH = "data/dedup_state.jsonl"


class DedupStore:
    """Persistent deduplication store using JSONL.

    Each record: {window_key, run_id, timestamp, report_type, market, symbol}
    """

    def __init__(self, path: str = _DEFAULT_DEDUP_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._in_memory: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        key = record.get("window_key", "")
                        if key:
                            self._in_memory[key] = record
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning("Dedup store load error: %s", exc)

    def has_run(self, window_key: str) -> bool:
        return window_key in self._in_memory

    def mark_run(self, window_key: str, run_id: str, report_type: str, market: str, symbol: str) -> None:
        record = {
            "window_key": window_key,
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "report_type": report_type,
            "market": market,
            "symbol": symbol,
        }
        self._in_memory[window_key] = record
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Dedup store write error: %s", exc)


class McpScheduler:
    """Controls when report generation runs.

    Checks market sessions before running. Avoids duplicates.
    Records skipped reasons for auditability.
    """

    def __init__(
        self,
        client: MarketDataClient,
        tick_seconds: int = 60,
        intraday_interval_hours: int = 2,
        post_market_delay_minutes: int = 15,
        datastore: McpDataStore | None = None,
    ) -> None:
        self._client = client
        self._tick_seconds = tick_seconds
        self._intraday_interval_hours = intraday_interval_hours
        self._post_market_delay_minutes = post_market_delay_minutes
        self._datastore = datastore
        self._dedup = DedupStore()

        # Track last intraday run times
        self._last_intraday: dict[str, str] = {}
        self._last_daily: dict[str, str] = {}

        self._running = False

    # ── Market session check ─────────────────────────────────────

    def check_market_status(self, markets: list[str]) -> dict[str, MarketStatusInfo]:
        """Returns market status for each requested market."""
        statuses = self._client.get_market_status(markets)
        return {s.market: s for s in statuses}

    def _make_window_key(
        self,
        market: str,
        symbol: str,
        report_type: str,
        trading_date: str,
        time_window: str,
    ) -> str:
        """Create a unique dedup key for a report window."""
        return f"{market}:{symbol}:{report_type}:{trading_date}:{time_window}"

    def _record_skip(
        self,
        run_id: str,
        report_type: str,
        market: str,
        symbols: list[str],
        reason: str,
    ) -> None:
        """Record a skipped run in the audit log."""
        logger.info("SKIPPED [%s] %s %s: %s", report_type, market, symbols, reason)
        if self._datastore:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for sym in symbols:
                self._datastore.log_run(
                    run_id, report_type, market, sym, "SKIPPED", now,
                    error_message=f"SKIPPED: {reason}",
                )

    # ── Intraday window ──────────────────────────────────────────

    def _is_intraday_window(
        self, market: str, status: MarketStatusInfo, symbols: list[str]
    ) -> tuple[bool, str]:
        """Check if it's time for an intraday report.

        Returns (should_run, reason_if_skipped).
        """
        if not status.is_open:
            return False, "market_closed"

        session = status.session
        if session not in ("regular", "extended_pre", "extended_post"):
            return False, f"session_not_active ({session})"

        # Check interval since last intraday run for this market+session
        window_key = f"{market}:{session}"
        last = self._last_intraday.get(window_key)
        now = datetime.now(timezone.utc)

        if last:
            last_dt = datetime.fromisoformat(last)
            hours_since = (now - last_dt).total_seconds() / 3600
            if hours_since < self._intraday_interval_hours:
                return False, f"before_interval ({hours_since:.1f}h since last, need {self._intraday_interval_hours}h)"

        # Check persistent dedup
        trading_date = now.strftime("%Y-%m-%d")
        for sym in symbols:
            dedup_key = self._make_window_key(market, sym, "intraday_brief", trading_date, session)
            if self._dedup.has_run(dedup_key):
                return False, "duplicate_window"

        return True, ""

    # ── Post-market window ───────────────────────────────────────

    def _is_post_market_window(
        self, market: str, status: MarketStatusInfo, symbols: list[str]
    ) -> tuple[bool, str]:
        """Check if daily close report should be generated.

        Returns (should_run, reason_if_skipped).
        """
        now = datetime.now(timezone.utc)
        trading_date = now.strftime("%Y-%m-%d")

        # Skip if market is still open
        if status.is_open:
            return False, "market_still_open"

        session = status.session

        # Skip holidays
        if session == "holiday":
            return False, "not_trading_day (holiday)"

        # Skip weekends
        if self._is_weekend(status):
            return False, "not_trading_day (weekend)"

        # Skip unknown state
        if session == "unknown" or not session:
            return False, "market_status_unknown"

        # Check if post-market delay has elapsed
        if status.next_close:
            try:
                close_time = datetime.fromisoformat(status.next_close.replace("Z", "+00:00"))
                delay_seconds = self._post_market_delay_minutes * 60
                if (now - close_time).total_seconds() < delay_seconds:
                    minutes_since = (now - close_time).total_seconds() / 60
                    return False, f"before_post_market_delay ({minutes_since:.0f}m since close, need {self._post_market_delay_minutes}m)"
            except (ValueError, TypeError):
                pass

        # Check today's dedup
        window_key = f"{market}:daily:{trading_date}"
        if window_key in self._last_daily:
            return False, "duplicate_window (in-memory)"

        for sym in symbols:
            dedup_key = self._make_window_key(market, sym, "daily_close_report", trading_date, "close")
            if self._dedup.has_run(dedup_key):
                return False, "duplicate_window"

        return True, ""

    @staticmethod
    def _is_weekend(status: MarketStatusInfo) -> bool:
        """Heuristic: detect weekend from timestamp."""
        ts = status.timestamp
        if ts:
            try:
                d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return d.weekday() >= 5
            except (ValueError, TypeError):
                pass
        return False

    # ── Run loop ─────────────────────────────────────────────────

    def run_forever(
        self,
        markets: list[str],
        symbols_by_market: dict[str, list[str]],
        on_intraday: Callable[[str, str, list[str]], None] | None = None,
        on_daily_close: Callable[[str, str, list[str]], None] | None = None,
    ) -> None:
        """Blocking run loop. Callbacks are invoked when reports should be generated.

        Args:
            markets: List of market codes to monitor (e.g. ["US", "HK"]).
            symbols_by_market: Dict mapping market -> list of symbols.
            on_intraday: Called for intraday report: (run_id, market, symbols).
            on_daily_close: Called for daily close: (run_id, market, symbols).
        """
        self._running = True
        logger.info(
            "Scheduler started: markets=%s, tick=%ds, intraday_interval=%dh, post_market_delay=%dm",
            markets,
            self._tick_seconds,
            self._intraday_interval_hours,
            self._post_market_delay_minutes,
        )

        while self._running:
            try:
                statuses = self.check_market_status(markets)

                for market in markets:
                    status = statuses.get(market)
                    symbols = symbols_by_market.get(market, [])

                    if not status or not symbols:
                        continue

                    now = datetime.now(timezone.utc)
                    trading_date = now.strftime("%Y-%m-%d")

                    # ── Intraday check ──────────────────────────
                    should_run, reason = self._is_intraday_window(market, status, symbols)
                    if should_run and on_intraday:
                        run_id = str(uuid.uuid4())
                        window_key = f"{market}:{status.session}"
                        self._last_intraday[window_key] = now.isoformat()

                        # Mark dedup
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "intraday_brief", trading_date, status.session
                            )
                            self._dedup.mark_run(dedup_key, run_id, "intraday_brief", market, sym)

                        logger.info("Intraday window triggered: %s %s", market, status.session)
                        try:
                            on_intraday(run_id, market, symbols)
                        except Exception as exc:
                            logger.error("Intraday handler failed: %s", exc)
                    elif reason and reason != "market_closed":
                        run_id = str(uuid.uuid4())
                        self._record_skip(run_id, "intraday_brief", market, symbols, reason)

                    # ── Post-market check ────────────────────────
                    should_run, reason = self._is_post_market_window(market, status, symbols)
                    if should_run and on_daily_close:
                        run_id = str(uuid.uuid4())
                        window_key = f"{market}:daily:{trading_date}"
                        self._last_daily[window_key] = now.isoformat()

                        # Mark dedup
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "daily_close_report", trading_date, "close"
                            )
                            self._dedup.mark_run(dedup_key, run_id, "daily_close_report", market, sym)

                        logger.info("Post-market window triggered: %s", market)
                        try:
                            on_daily_close(run_id, market, symbols)
                        except Exception as exc:
                            logger.error("Daily close handler failed: %s", exc)
                    elif reason and reason != "market_still_open":
                        run_id = str(uuid.uuid4())
                        self._record_skip(run_id, "daily_close_report", market, symbols, reason)

                time.sleep(self._tick_seconds)

            except KeyboardInterrupt:
                logger.info("Scheduler stopped by user")
                self._running = False
            except Exception as exc:
                logger.error("Scheduler loop error: %s", exc)
                time.sleep(self._tick_seconds)

    def stop(self) -> None:
        """Signal the run loop to stop."""
        self._running = False
