from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


US_TZ = ZoneInfo("America/New_York")
US_REGULAR_OPEN = time(9, 30)
US_REGULAR_CLOSE = time(16, 0)


@dataclass(frozen=True)
class ExtendedWindow:
    market: str
    start: datetime
    end: datetime
    trading_date: str
    session_window_id: str


def is_us_regular_session(now_utc: datetime | None = None) -> bool:
    now = (now_utc or datetime.now(UTC)).astimezone(US_TZ)
    if now.weekday() >= 5:
        return False
    return US_REGULAR_OPEN <= now.time() < US_REGULAR_CLOSE


def should_collect_us_extended(now_utc: datetime | None = None) -> bool:
    return not is_us_regular_session(now_utc)


def get_us_extended_window(now_utc: datetime | None = None) -> ExtendedWindow:
    now = (now_utc or datetime.now(UTC)).astimezone(US_TZ)
    close_at = previous_regular_close(now)
    open_at = next_regular_open(now)
    trading_date = open_at.date().isoformat()
    return ExtendedWindow(
        market="US",
        start=close_at,
        end=open_at,
        trading_date=trading_date,
        session_window_id=f"US_EXT_{close_at.date().isoformat()}_TO_{open_at.date().isoformat()}",
    )


def previous_regular_close(local_now: datetime) -> datetime:
    current = local_now
    if current.weekday() < 5 and current.time() >= US_REGULAR_CLOSE:
        return current.replace(hour=16, minute=0, second=0, microsecond=0)

    current = current - timedelta(days=1)
    while current.weekday() >= 5:
        current = current - timedelta(days=1)
    return current.replace(hour=16, minute=0, second=0, microsecond=0)


def next_regular_open(local_now: datetime) -> datetime:
    current = local_now
    if current.weekday() < 5 and current.time() < US_REGULAR_OPEN:
        return current.replace(hour=9, minute=30, second=0, microsecond=0)

    current = current + timedelta(days=1)
    while current.weekday() >= 5:
        current = current + timedelta(days=1)
    return current.replace(hour=9, minute=30, second=0, microsecond=0)
