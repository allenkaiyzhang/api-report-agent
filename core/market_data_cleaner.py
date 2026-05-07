from __future__ import annotations

from datetime import datetime
from typing import Any

from core.trading_hours import infer_symbol_market


ESSENTIAL_FIELDS = (
    "symbol",
    "latest_price",
    "previous_close",
    "change_percent",
    "volume",
    "avg_volume_20d",
    "timestamp",
)


def clean_market_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cleaned_records: list[dict[str, Any]] = []
    quality_issues: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()

    for index, record in enumerate(records):
        cleaned, issues = clean_market_record(record)
        symbol = cleaned.get("symbol", "")

        if not symbol:
            issues.append("missing_symbol")
        elif symbol in seen_symbols:
            issues.append("duplicate_symbol")
        else:
            seen_symbols.add(symbol)

        if issues:
            quality_issues.append(
                {
                    "record_index": index,
                    "symbol": symbol or None,
                    "issues": issues,
                }
            )

        if symbol:
            cleaned_records.append(cleaned)

    cleaned_records.sort(key=lambda item: item["symbol"])
    return cleaned_records, quality_issues


def clean_market_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    symbol = str(record.get("symbol", "")).strip().upper()

    latest_price = _to_float(record.get("latest_price"))
    previous_close = _to_float(record.get("previous_close"))
    change_percent = _to_float(record.get("change_percent"))
    volume = _to_int(record.get("volume"))
    avg_volume_20d = _to_int(record.get("avg_volume_20d"))

    if change_percent == 0 and latest_price > 0 and previous_close > 0:
        change_percent = ((latest_price - previous_close) / previous_close) * 100

    volume_ratio = _calculate_volume_ratio(volume, avg_volume_20d)
    timestamp = _normalize_timestamp(record.get("timestamp"))

    if latest_price <= 0:
        issues.append("invalid_latest_price")
    if previous_close < 0:
        issues.append("invalid_previous_close")
    if volume < 0:
        issues.append("invalid_volume")
    if avg_volume_20d < 0:
        issues.append("invalid_avg_volume_20d")

    cleaned = {
        "symbol": symbol,
        "market": infer_symbol_market(symbol) if symbol else "",
        "latest_price": round(latest_price, 4),
        "previous_close": round(previous_close, 4),
        "change_percent": round(change_percent, 2),
        "volume": max(volume, 0),
        "avg_volume_20d": max(avg_volume_20d, 0),
        "volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
        "timestamp": timestamp,
        "open": _to_float(record.get("open")),
        "high": _to_float(record.get("high")),
        "low": _to_float(record.get("low")),
        "turnover": _to_float(record.get("turnover")),
        "trade_status": _stringify(record.get("trade_status")),
        "provider": _stringify(record.get("market_data_provider")),
        "static_info": _clean_static_info(record.get("static_info")),
        "calc_indexes": _clean_calc_indexes(record.get("calc_indexes")),
        "latest_candlestick": _latest_candlestick(record.get("daily_candlesticks")),
    }

    for field in ESSENTIAL_FIELDS:
        if field not in record:
            issues.append(f"missing_{field}")

    return cleaned, issues


def _clean_static_info(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "name_cn": _stringify(value.get("name_cn")),
        "name_en": _stringify(value.get("name_en")),
        "exchange": _stringify(value.get("exchange")),
        "currency": _stringify(value.get("currency")),
        "lot_size": _to_int(value.get("lot_size")),
        "dividend_yield": _to_float(value.get("dividend_yield")),
        "board": _stringify(value.get("board")),
    }


def _clean_calc_indexes(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "change_value": _to_float(value.get("change_value")),
        "change_rate": _to_float(value.get("change_rate")),
        "volume_ratio": _to_float(value.get("volume_ratio")),
        "five_day_change_rate": _to_float(value.get("five_day_change_rate")),
        "ten_day_change_rate": _to_float(value.get("ten_day_change_rate")),
        "half_year_change_rate": _to_float(value.get("half_year_change_rate")),
    }


def _latest_candlestick(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list) or not value:
        return None
    latest = value[-1]
    if not isinstance(latest, dict):
        return None
    return {
        "close": _to_float(latest.get("close")),
        "open": _to_float(latest.get("open")),
        "low": _to_float(latest.get("low")),
        "high": _to_float(latest.get("high")),
        "volume": _to_int(latest.get("volume")),
        "turnover": _to_float(latest.get("turnover")),
        "timestamp": _normalize_timestamp(latest.get("timestamp")),
    }


def _calculate_volume_ratio(volume: int, avg_volume_20d: int) -> float | None:
    if avg_volume_20d <= 0:
        return None
    return volume / avg_volume_20d


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return _stringify(value)


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
