from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


US_TZ = ZoneInfo("America/New_York")
US_PREMARKET_OPEN = time(4, 0)
US_REGULAR_OPEN = time(9, 30)
US_REGULAR_CLOSE = time(16, 0)
US_AFTERHOURS_CLOSE = time(20, 0)


@dataclass(frozen=True)
class ExtendedWindow:
    market: str
    start: datetime
    end: datetime
    trading_date: str
    session_window_id: str


def to_us_local(now_utc: datetime | None = None) -> datetime:
    now = now_utc or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(US_TZ)


def is_us_weekday(now_utc: datetime | None = None) -> bool:
    return to_us_local(now_utc).weekday() < 5


def is_us_regular_session(now_utc: datetime | None = None) -> bool:
    now = to_us_local(now_utc)
    if not is_us_weekday(now):
        return False
    return US_REGULAR_OPEN <= now.time() < US_REGULAR_CLOSE


def is_us_premarket(now_utc: datetime | None = None) -> bool:
    now = to_us_local(now_utc)
    if not is_us_weekday(now):
        return False
    return US_PREMARKET_OPEN <= now.time() < US_REGULAR_OPEN


def is_us_afterhours(now_utc: datetime | None = None) -> bool:
    now = to_us_local(now_utc)
    if not is_us_weekday(now):
        return False
    return US_REGULAR_CLOSE <= now.time() < US_AFTERHOURS_CLOSE


def should_collect_us_extended(now_utc: datetime | None = None) -> bool:
    return is_us_premarket(now_utc) or is_us_afterhours(now_utc)


def extended_collect_decision(now_utc: datetime | None = None) -> dict:
    utc_now = now_utc or datetime.now(UTC)
    if utc_now.tzinfo is None:
        utc_now = utc_now.replace(tzinfo=UTC)
    ny_now = to_us_local(utc_now)

    if ny_now.weekday() >= 5:
        reason = "weekend"
        session = "closed"
        should_collect = False
    elif US_PREMARKET_OPEN <= ny_now.time() < US_REGULAR_OPEN:
        reason = "premarket"
        session = "premarket"
        should_collect = True
    elif US_REGULAR_OPEN <= ny_now.time() < US_REGULAR_CLOSE:
        reason = "regular_session"
        session = "regular"
        should_collect = False
    elif US_REGULAR_CLOSE <= ny_now.time() < US_AFTERHOURS_CLOSE:
        reason = "afterhours"
        session = "afterhours"
        should_collect = True
    else:
        reason = "outside_extended_session"
        session = "closed"
        should_collect = False

    return {
        "should_collect": should_collect,
        "reason": reason,
        "utc_time": utc_now.astimezone(UTC).isoformat(timespec="seconds"),
        "ny_time": ny_now.isoformat(timespec="seconds"),
        "ny_weekday": ny_now.weekday(),
        "session": session,
    }


def get_us_extended_window(now_utc: datetime | None = None) -> ExtendedWindow:
    now = to_us_local(now_utc)
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
