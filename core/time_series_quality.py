from __future__ import annotations

from datetime import datetime
from typing import Any


def check_time_series_quality(
    records: list[dict[str, Any]],
    expected_interval_seconds: int = 120,
    price_jump_threshold_pct: float = 10.0,
) -> dict[str, Any]:
    parsed_rows = sorted(
        [
            (parse_datetime(record.get("source_timestamp_utc") or record.get("event_time")), record)
            for record in records
            if parse_datetime(record.get("source_timestamp_utc") or record.get("event_time")) is not None
        ],
        key=lambda item: item[0] or datetime.min,
    )
    timestamp_not_increasing = 0
    timestamp_gap_count = 0
    max_gap_seconds = 0
    duplicate_timestamps = 0
    volume_decrease_count = 0
    turnover_decrease_count = 0
    abnormal_price_jump_count = 0

    seen_timestamps: set[str] = set()
    previous_time: datetime | None = None
    previous_volume: int | None = None
    previous_turnover: float | None = None
    previous_price: float | None = None

    for event_time, record in parsed_rows:
        if event_time is None:
            continue
        timestamp_key = event_time.isoformat()
        if timestamp_key in seen_timestamps:
            duplicate_timestamps += 1
        seen_timestamps.add(timestamp_key)

        if previous_time is not None:
            diff_seconds = (event_time - previous_time).total_seconds()
            if diff_seconds <= 0:
                timestamp_not_increasing += 1
            elif diff_seconds > expected_interval_seconds * 1.5:
                timestamp_gap_count += 1
                max_gap_seconds = max(max_gap_seconds, int(diff_seconds))

            price = optional_float(record.get("last_price"))
            if (
                price is not None
                and previous_price is not None
                and previous_price > 0
                and 0 < diff_seconds <= expected_interval_seconds
            ):
                jump_pct = abs(price / previous_price - 1) * 100
                if jump_pct > price_jump_threshold_pct:
                    abnormal_price_jump_count += 1
            if price is not None:
                previous_price = price
        else:
            price = optional_float(record.get("last_price"))
            if price is not None:
                previous_price = price

        volume = optional_int(record.get("volume_cumulative"))
        if previous_volume is not None and volume is not None and volume < previous_volume:
            volume_decrease_count += 1
        if volume is not None:
            previous_volume = volume

        turnover = optional_float(record.get("turnover_cumulative"))
        if previous_turnover is not None and turnover is not None and turnover < previous_turnover:
            turnover_decrease_count += 1
        if turnover is not None:
            previous_turnover = turnover

        previous_time = event_time

    return {
        "timestamp_not_increasing": timestamp_not_increasing,
        "timestamp_gap_count": timestamp_gap_count,
        "max_gap_seconds": max_gap_seconds,
        "duplicate_timestamps": duplicate_timestamps,
        "volume_decrease_count": volume_decrease_count,
        "turnover_decrease_count": turnover_decrease_count,
        "abnormal_price_jump_count": abnormal_price_jump_count,
    }


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
