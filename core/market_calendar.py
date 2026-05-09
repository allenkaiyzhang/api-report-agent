from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.time_model import MARKET_TIMEZONE_NAMES


DAILY_BUILD_DELAY_MINUTES = 10


@dataclass(frozen=True)
class TradingSession:
    start: time
    end: time

    def contains(self, value: time) -> bool:
        return self.start <= value < self.end


@dataclass(frozen=True)
class MarketWindow:
    window_id: str
    start: datetime
    end: datetime
    expected_points: int


MARKET_TIMEZONES = {
    "HK": ZoneInfo(MARKET_TIMEZONE_NAMES["HK"]),
    "US": ZoneInfo(MARKET_TIMEZONE_NAMES["US"]),
}

MARKET_SESSIONS = {
    "HK": (
        TradingSession(time(9, 30), time(12, 0)),
        TradingSession(time(13, 0), time(16, 0)),
    ),
    "US": (
        TradingSession(time(9, 30), time(16, 0)),
    ),
}

WINDOW_CONFIG = {
    "HK": [
        ("09:30", "10:30"),
        ("10:30", "11:30"),
        ("11:30", "12:00"),
        ("13:00", "14:00"),
        ("14:00", "15:00"),
        ("15:00", "16:00"),
    ],
    "US": [
        ("09:30", "10:30"),
        ("10:30", "11:30"),
        ("11:30", "12:30"),
        ("12:30", "13:30"),
        ("13:30", "14:30"),
        ("14:30", "15:30"),
        ("15:30", "16:00"),
    ],
}


def normalize_market(value: str) -> str:
    market = str(value or "").strip().upper()
    if market not in MARKET_TIMEZONES:
        raise ValueError(f"Unsupported market: {value}")
    return market


def get_market_timezone(market: str) -> ZoneInfo:
    return MARKET_TIMEZONES[normalize_market(market)]


def get_market_local_now(market: str, now_utc: datetime | None = None) -> datetime:
    now = now_utc or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(get_market_timezone(market))


def get_trading_date(market: str, now_utc: datetime | None = None) -> str:
    return get_market_local_now(market, now_utc).date().isoformat()


def is_market_open(market: str, now_utc: datetime | None = None) -> bool:
    market = normalize_market(market)
    local_now = get_market_local_now(market, now_utc)
    if local_now.weekday() >= 5:
        return False
    return any(session.contains(local_now.time()) for session in MARKET_SESSIONS[market])


def is_market_recently_closed(market: str, now_utc: datetime | None = None) -> bool:
    market = normalize_market(market)
    local_now = get_market_local_now(market, now_utc)
    if local_now.weekday() >= 5:
        return False
    close_time = MARKET_SESSIONS[market][-1].end
    close_dt = datetime.combine(local_now.date(), close_time, tzinfo=get_market_timezone(market))
    return close_dt <= local_now < close_dt + timedelta(minutes=DAILY_BUILD_DELAY_MINUTES)


def get_market_windows(market: str, trading_date: str, interval_minutes: int = 2) -> list[MarketWindow]:
    market = normalize_market(market)
    timezone = get_market_timezone(market)
    current_date = date.fromisoformat(trading_date)
    windows: list[MarketWindow] = []
    for start_text, end_text in WINDOW_CONFIG[market]:
        start_time = parse_time(start_text)
        end_time = parse_time(end_text)
        start_dt = datetime.combine(current_date, start_time, tzinfo=timezone)
        end_dt = datetime.combine(current_date, end_time, tzinfo=timezone)
        minutes = (end_dt - start_dt).total_seconds() / 60
        windows.append(
            MarketWindow(
                window_id=f"{start_time:%H%M}_{end_time:%H%M}",
                start=start_dt,
                end=end_dt,
                expected_points=int(minutes // interval_minutes),
            )
        )
    return windows


def should_collect_market(market: str, now_utc: datetime | None = None) -> bool:
    return is_market_open(market, now_utc)


def should_build_daily(
    market: str,
    now_utc: datetime | None = None,
    base_dir: Path | None = None,
    force_rebuild: bool = False,
) -> bool:
    market = normalize_market(market)
    trading_date = get_trading_date(market, now_utc)
    windows = get_market_windows(market, trading_date)
    if not windows:
        return False

    local_now = get_market_local_now(market, now_utc)
    build_after = windows[-1].end + timedelta(minutes=DAILY_BUILD_DELAY_MINUTES)
    if local_now < build_after:
        return False

    if base_dir is None:
        return True

    regular_raw_path = base_dir / "data" / "raw" / market / "regular" / f"{trading_date}.jsonl"
    legacy_raw_path = base_dir / "data" / "raw" / market / f"{trading_date}.jsonl"
    metrics_path = base_dir / "data" / "metrics" / market / trading_date
    daily_path = metrics_path / "daily.json"
    has_data = regular_raw_path.exists() or legacy_raw_path.exists() or metrics_path.exists()
    needs_build = force_rebuild or not daily_path.exists()
    return has_data and needs_build


def parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))
