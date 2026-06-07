"""MCP scheduler — manages periodic intraday and post-market report generation.

On each tick:
  1. Check market status / trading session via MarketDataClient.
  2. Generate intraday reports during valid market windows at the configured interval.
  3. Generate post-market daily summary after market close + post_market_delay.
  4. Avoid duplicate report generation via persistent dedup state with status tracking.
  5. Record every skipped run with an auditable reason.

Timezone-aware: trading_date, window keys, and delay calculations use market-local
timezone (America/New_York for US, Asia/Hong_Kong for HK, etc.).
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
from zoneinfo import ZoneInfo

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


class DedupState(str, Enum):
    """State of a dedup window record."""
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# ── Market timezone config ───────────────────────────────────────
_MARKET_TIMEZONES: dict[str, str] = {
    "US": "America/New_York",
    "HK": "Asia/Hong_Kong",
    "CN": "Asia/Shanghai",
    "JP": "Asia/Tokyo",
}
_IN_PROGRESS_TIMEOUT_SECONDS = 3600  # 1 hour before retrying stale IN_PROGRESS


# ── Dedup state persistence ─────────────────────────────────────
_DEFAULT_DEDUP_PATH = "data/dedup_state.jsonl"


class DedupStore:
    """Persistent deduplication store using JSONL with state tracking.

    Each record: {window_key, run_id, timestamp, state, report_type, market, symbol}

    States: IN_PROGRESS, SUCCESS, FAILED, SKIPPED
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

    def get_state(self, window_key: str) -> DedupState | None:
        """Get the current state of a dedup window."""
        record = self._in_memory.get(window_key)
        if not record:
            return None
        state_raw = record.get("state", "")
        try:
            return DedupState(state_raw)
        except ValueError:
            return None

    def has_run(self, window_key: str) -> bool:
        """Check if a window has been completed (SUCCESS)."""
        return self.get_state(window_key) == DedupState.SUCCESS

    def is_in_progress(self, window_key: str) -> bool:
        """Check if a window is currently IN_PROGRESS."""
        return self.get_state(window_key) == DedupState.IN_PROGRESS

    def is_completed(self, window_key: str) -> bool:
        """Check if a window has a final state (SUCCESS, SKIPPED)."""
        state = self.get_state(window_key)
        return state in (DedupState.SUCCESS, DedupState.SKIPPED)

    def is_failed(self, window_key: str) -> bool:
        """Check if a window previously FAILED."""
        return self.get_state(window_key) == DedupState.FAILED

    def can_retry(self, window_key: str) -> bool:
        """Check if a window can be retried.

        Returns True if:
        - No record exists
        - Previous state was FAILED
        - IN_PROGRESS is stale (beyond timeout)
        """
        state = self.get_state(window_key)
        if state is None:
            return True
        if state == DedupState.FAILED:
            return True
        if state == DedupState.IN_PROGRESS:
            record = self._in_memory.get(window_key, {})
            ts = record.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
                if elapsed > _IN_PROGRESS_TIMEOUT_SECONDS:
                    return True
            except (ValueError, TypeError):
                return True  # Can't parse timestamp, allow retry
        return False

    def mark_state(
        self,
        window_key: str,
        run_id: str,
        state: DedupState,
        report_type: str,
        market: str,
        symbol: str,
    ) -> None:
        """Set the state of a dedup window."""
        record = {
            "window_key": window_key,
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state.value,
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
            raise RuntimeError(f"Dedup store write failed: {exc}") from exc

    def mark_run(self, window_key: str, run_id: str, report_type: str, market: str, symbol: str) -> None:
        """Legacy: marks as SUCCESS. Use mark_state for explicit state control."""
        self.mark_state(window_key, run_id, DedupState.SUCCESS, report_type, market, symbol)

    def mark_in_progress(self, window_key: str, run_id: str, report_type: str, market: str, symbol: str) -> None:
        self.mark_state(window_key, run_id, DedupState.IN_PROGRESS, report_type, market, symbol)

    def mark_failed(self, window_key: str, run_id: str, report_type: str, market: str, symbol: str) -> None:
        self.mark_state(window_key, run_id, DedupState.FAILED, report_type, market, symbol)


class McpScheduler:
    """Controls when report generation runs.

    Checks market sessions before running. Avoids duplicates.
    Records skipped reasons for auditability.
    Uses market-local timezone for trading_date and window keys.
    """

    # ── Market timezone helpers ──────────────────────────────────

    @staticmethod
    def get_market_timezone(market: str) -> ZoneInfo:
        """Return ZoneInfo for a market code."""
        tz_name = _MARKET_TIMEZONES.get(market.upper(), "UTC")
        return ZoneInfo(tz_name)

    @staticmethod
    def get_local_now(market: str) -> datetime:
        """Return current datetime in market-local timezone."""
        tz = McpScheduler.get_market_timezone(market)
        return datetime.now(timezone.utc).astimezone(tz)

    @staticmethod
    def get_trading_date(market: str) -> str:
        """Return trading date string in market-local timezone (YYYY-MM-DD)."""
        local = McpScheduler.get_local_now(market)
        return local.strftime("%Y-%m-%d")

    # ── Constructor ──────────────────────────────────────────────

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

        # Track last intraday run times (memory dedup)
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
        """Create a unique dedup key for a report window.

        Window key includes: market, symbol, report_type, market-local trading_date, time_window.
        """
        return f"{market}:{symbol}:{report_type}:{trading_date}:{time_window}"

    def _intraday_time_window(self, local_now: datetime, session: str) -> str:
        """Return a stable market-local interval bucket for persistent dedup."""
        interval = max(self._intraday_interval_hours, 1)
        bucket_hour = (local_now.hour // interval) * interval
        return f"{session}-{bucket_hour:02d}00"

    def _completed_trading_date(self, market: str, status: MarketStatusInfo) -> str | None:
        close_time = self._resolve_close_time(status, market)
        if close_time is None:
            return None
        return close_time.astimezone(self.get_market_timezone(market)).strftime("%Y-%m-%d")

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
        Uses market-local time for dedup keys and interval checks.
        """
        if not status.is_open:
            return False, "market_closed"

        session = status.session
        if session not in ("regular", "extended_pre", "extended_post"):
            return False, f"session_not_active ({session})"

        local_now = self.get_local_now(market)
        trading_date = local_now.strftime("%Y-%m-%d")
        time_window = self._intraday_time_window(local_now, session)

        # Check interval since last intraday run for this market+session
        window_mem_key = f"{market}:{session}"
        last = self._last_intraday.get(window_mem_key)

        if last:
            last_dt = datetime.fromisoformat(last)
            hours_since = (local_now - last_dt).total_seconds() / 3600
            if hours_since < self._intraday_interval_hours:
                return False, f"before_interval ({hours_since:.1f}h since last, need {self._intraday_interval_hours}h)"

        # Check persistent dedup (market-local trading date)
        for sym in symbols:
            dedup_key = self._make_window_key(market, sym, "intraday_brief", trading_date, time_window)
            if self._dedup.is_completed(dedup_key):
                return False, "duplicate_window"

        return True, ""

    # ── Post-market window ───────────────────────────────────────

    def _resolve_close_time(
        self, status: MarketStatusInfo, market: str | None = None
    ) -> datetime | None:
        """Resolve the relevant session close time for post-market delay.

        Priority:
        1. current_session_close — the close time of the session that just ended
        2. last_close — most recent known close

        Does NOT use next_close (points to next trading day, not today's close).
        """
        for field in [status.current_session_close, status.last_close]:
            if field:
                try:
                    parsed = datetime.fromisoformat(field.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(
                            tzinfo=self.get_market_timezone(market or status.market)
                        )
                    return parsed
                except (ValueError, TypeError):
                    continue
        return None

    def _is_post_market_window(
        self, market: str, status: MarketStatusInfo, symbols: list[str]
    ) -> tuple[bool, str]:
        """Check if daily close report should be generated.

        Returns (should_run, reason_if_skipped).
        Uses market-local timezone for trading_date and delay calculations.

        Uses current_session_close or last_close (NOT next_close) for delay.
        """
        local_now = self.get_local_now(market)
        # Skip if market is still open
        if status.is_open:
            return False, "market_still_open"

        session = status.session

        # Skip holidays
        if session == "holiday":
            return False, "not_trading_day (holiday)"

        # Skip unknown state
        if session == "unknown" or not session:
            return False, "market_status_unknown"

        # Check if post-market delay has elapsed against resolved close time
        close_time = self._resolve_close_time(status, market)
        if close_time is None:
            return False, "missing_session_close"
        close_local = close_time.astimezone(self.get_market_timezone(market))
        trading_date = close_local.strftime("%Y-%m-%d")
        if close_local.weekday() >= 5:
            return False, "not_trading_day (weekend)"

        delay_seconds = self._post_market_delay_minutes * 60
        if (local_now - close_time).total_seconds() < delay_seconds:
            minutes_since = (local_now - close_time).total_seconds() / 60
            return False, f"before_post_market_delay ({minutes_since:.0f}m since close, need {self._post_market_delay_minutes}m)"

        # Check memory dedup
        window_mem_key = f"{market}:daily:{trading_date}"
        if window_mem_key in self._last_daily:
            return False, "duplicate_window (in-memory)"

        # Check persistent dedup (market-local trading date)
        for sym in symbols:
            dedup_key = self._make_window_key(market, sym, "daily_close_report", trading_date, "close")
            if self._dedup.is_completed(dedup_key):
                return False, "duplicate_window"

        return True, ""

    @staticmethod
    def _is_weekend(market: str, status: MarketStatusInfo | None = None) -> bool:
        """Check if it's currently a weekend in market-local timezone.

        Uses status timestamp if available, falls back to current market-local time.
        """
        if status is not None:
            ts = status.timestamp
            if ts:
                try:
                    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    return d.weekday() >= 5
                except (ValueError, TypeError):
                    pass
        local = McpScheduler.get_local_now(market)
        return local.weekday() >= 5

    # ── Run loop ─────────────────────────────────────────────────

    def run_forever(
        self,
        markets: list[str],
        symbols_by_market: dict[str, list[str]],
        on_intraday: Callable[[str, str, list[str]], bool] | None = None,
        on_daily_close: Callable[[str, str, list[str]], bool] | None = None,
    ) -> None:
        """Blocking run loop. Callbacks are invoked when reports should be generated.

        Callbacks return True on success, False on failure.
        Dedup state is updated based on callback result:
        - True → SUCCESS
        - False → FAILED

        Args:
            markets: List of market codes to monitor (e.g. ["US", "HK"]).
            symbols_by_market: Dict mapping market -> list of symbols.
            on_intraday: Called for intraday report: (run_id, market, symbols) -> bool.
            on_daily_close: Called for daily close: (run_id, market, symbols) -> bool.
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

                    local_now = self.get_local_now(market)
                    trading_date = local_now.strftime("%Y-%m-%d")
                    intraday_window = self._intraday_time_window(local_now, status.session)
                    completed_trading_date = self._completed_trading_date(market, status)

                    # ── Intraday check ──────────────────────────
                    should_run, reason = self._is_intraday_window(market, status, symbols)
                    if should_run and on_intraday:
                        run_id = str(uuid.uuid4())

                        # Check if any symbol can be retried
                        can_run = True
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "intraday_brief", trading_date, intraday_window
                            )
                            if not self._dedup.can_retry(dedup_key):
                                can_run = False
                                break

                        if not can_run:
                            self._record_skip(
                                run_id, "intraday_brief", market, symbols,
                                "duplicate_window"
                            )
                            continue

                        # Mark IN_PROGRESS before calling handler
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "intraday_brief", trading_date, intraday_window
                            )
                            self._dedup.mark_in_progress(dedup_key, run_id, "intraday_brief", market, sym)

                        logger.info("Intraday window triggered: %s %s", market, status.session)
                        try:
                            success = on_intraday(run_id, market, symbols)
                            final_state = DedupState.SUCCESS if success else DedupState.FAILED
                        except Exception as exc:
                            logger.error("Intraday handler failed: %s", exc)
                            success = False
                            final_state = DedupState.FAILED

                        # Update dedup state based on result
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "intraday_brief", trading_date, intraday_window
                            )
                            self._dedup.mark_state(dedup_key, run_id, final_state, "intraday_brief", market, sym)
                        if success:
                            self._last_intraday[f"{market}:{status.session}"] = local_now.isoformat()
                    elif reason and reason != "market_closed":
                        run_id = str(uuid.uuid4())
                        self._record_skip(run_id, "intraday_brief", market, symbols, reason)

                    # ── Post-market check ────────────────────────
                    should_run, reason = self._is_post_market_window(market, status, symbols)
                    if should_run and on_daily_close:
                        run_id = str(uuid.uuid4())
                        if completed_trading_date is None:
                            self._record_skip(
                                run_id, "daily_close_report", market, symbols,
                                "missing_session_close"
                            )
                            continue

                        # Check if any symbol can be retried
                        can_run = True
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "daily_close_report", completed_trading_date, "close"
                            )
                            if not self._dedup.can_retry(dedup_key):
                                can_run = False
                                break

                        if not can_run:
                            self._record_skip(
                                run_id, "daily_close_report", market, symbols,
                                "duplicate_window"
                            )
                            continue

                        # Mark IN_PROGRESS before calling handler
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "daily_close_report", completed_trading_date, "close"
                            )
                            self._dedup.mark_in_progress(dedup_key, run_id, "daily_close_report", market, sym)

                        logger.info("Post-market window triggered: %s", market)
                        try:
                            success = on_daily_close(run_id, market, symbols)
                            final_state = DedupState.SUCCESS if success else DedupState.FAILED
                        except Exception as exc:
                            logger.error("Daily close handler failed: %s", exc)
                            success = False
                            final_state = DedupState.FAILED

                        # Update dedup state based on result
                        for sym in symbols:
                            dedup_key = self._make_window_key(
                                market, sym, "daily_close_report", completed_trading_date, "close"
                            )
                            self._dedup.mark_state(dedup_key, run_id, final_state, "daily_close_report", market, sym)
                        if success:
                            self._last_daily[f"{market}:daily:{completed_trading_date}"] = local_now.isoformat()
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
