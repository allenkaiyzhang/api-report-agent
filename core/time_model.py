from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


MARKET_TIMEZONE_NAMES = {
    "US": "America/New_York",
    "HK": "Asia/Hong_Kong",
    "JP": "Asia/Tokyo",
    "EU": "Europe/London",
}
DEFAULT_PROVIDER_TIMESTAMP_TIMEZONE_NAME = "Asia/Shanghai"


def normalize_market(value: Any) -> str:
    market = str(value or "").strip().upper()
    if market in MARKET_TIMEZONE_NAMES:
        return market
    return "HK" if market.endswith(".HK") else "US"


def market_timezone_name(market: str) -> str:
    return MARKET_TIMEZONE_NAMES[normalize_market(market)]


def market_timezone(market: str) -> ZoneInfo:
    return ZoneInfo(market_timezone_name(market))


def provider_timestamp_timezone() -> ZoneInfo:
    return ZoneInfo(DEFAULT_PROVIDER_TIMESTAMP_TIMEZONE_NAME)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    aware = ensure_aware(value).astimezone(UTC)
    return aware.isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_aware(value: datetime, default_timezone: ZoneInfo | None = None) -> datetime:
    if value.tzinfo is not None:
        return value
    timezone = default_timezone or UTC
    return value.replace(tzinfo=timezone)


def parse_datetime(value: Any, default_timezone: ZoneInfo | None = None) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_aware(value, default_timezone)
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ensure_aware(parsed, default_timezone)


def datetime_value_has_timezone(value: Any) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is not None
    text = str(value or "").strip()
    if not text:
        return False
    if text.endswith("Z"):
        return True
    if "T" in text:
        time_part = text.rsplit("T", 1)[1]
    elif " " in text:
        time_part = text.rsplit(" ", 1)[1]
    else:
        return False
    return "+" in time_part or "-" in time_part


def normalize_source_timestamp(value: Any, market: str) -> tuple[str, str, str | None]:
    raw = "" if value is None else str(value)
    timezone_name = market_timezone_name(market)
    parsed = parse_datetime(value, default_timezone=provider_timestamp_timezone())
    return raw, timezone_name, iso_utc(parsed) if parsed else None


def trading_date_from_utc(market: str, now_utc: datetime) -> str:
    return ensure_aware(now_utc).astimezone(market_timezone(market)).date().isoformat()


def regular_session_window_id(market: str, trading_date: str) -> str:
    return f"{normalize_market(market)}_REGULAR_{trading_date}"
