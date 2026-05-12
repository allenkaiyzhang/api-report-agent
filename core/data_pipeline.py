from __future__ import annotations

import json
import logging
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, time, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.market_calendar import (
    MarketWindow,
    get_market_windows,
)
from core.reference_data_store import ReferenceDataStore
from core.time_series_quality import check_time_series_quality
from core.time_model import (
    datetime_value_has_timezone,
    iso_utc,
    market_timezone_name,
    normalize_source_timestamp,
    parse_datetime as parse_datetime_aware,
    provider_timestamp_timezone,
)


BASE_DIR = Path(__file__).resolve().parents[1]
NORMALIZED_SCHEMA_VERSION = 1
PIPELINE_VERSION = "2026-05-08"
METRICS_VERSION = 1
ABNORMAL_SPREAD_PCT = 0.01
ABNORMAL_VOLATILITY = 0.05
INTERVAL_MINUTES = 2
INVALID_FLAGS = {
    "invalid_price",
    "missing_symbol",
    "missing_event_time",
    "missing_price",
    "duplicate_record",
}
MARKET_CURRENCIES = {
    "HK": "HKD",
    "US": "USD",
}
logger = logging.getLogger("data_pipeline")


@dataclass(frozen=True)
class JsonlLoadResult:
    records: list[dict[str, Any]]
    raw_lines: int
    json_parse_errors: list[dict[str, Any]]


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_jsonl(path: Path) -> JsonlLoadResult:
    """Load JSONL without failing the whole file on bad lines."""
    if not path.exists():
        raise FileNotFoundError(f"Input file does not exist: {path}")

    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    raw_lines = 0
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            raw_lines += 1
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append({"line_number": line_number, "error": str(exc)})
                logger.warning("JSON parse error in %s line %s: %s", path, line_number, exc)
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                errors.append({"line_number": line_number, "error": "line is not a JSON object"})
    return JsonlLoadResult(records=records, raw_lines=raw_lines, json_parse_errors=errors)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def write_jsonl_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    """Write JSONL through a temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, default=str))
            file.write("\n")
    os.replace(temp_path, path)


def normalize_record(
    raw_record: dict[str, Any],
    market: str,
    collected_at: str | None = None,
    reference_static_info_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize one raw quote record and attach quality flags."""
    normalized_market = normalize_market(raw_record.get("market") or market)
    symbol = normalize_symbol(raw_record.get("symbol"), normalized_market)
    raw_static_info = raw_record.get("static_info")
    reference_static_info = (reference_static_info_by_symbol or {}).get(symbol, {})
    static_info = raw_static_info if isinstance(raw_static_info, dict) else reference_static_info

    event_time = normalize_event_timestamp(raw_record, normalized_market)
    collected = normalize_timestamp(
        raw_record.get("collected_at_utc")
        or collected_at
        or raw_record.get("collected_at")
        or raw_record.get("collected_at_local")
    )
    source_timestamp_raw = str(
        raw_record.get("source_timestamp_raw")
        or raw_record.get("timestamp")
        or raw_record.get("event_time")
        or raw_record.get("quote_time")
        or ""
    )
    _, timezone_name, source_timestamp_utc = normalize_source_timestamp(source_timestamp_raw, normalized_market)
    if raw_record.get("source_timestamp_utc"):
        source_timestamp_utc = normalize_timestamp(raw_record.get("source_timestamp_utc"))

    last_price = optional_float(
        raw_record.get("last_price")
        if "last_price" in raw_record
        else raw_record.get("latest_price", raw_record.get("last_done"))
    )
    bid = optional_float(raw_record.get("bid"))
    ask = optional_float(raw_record.get("ask"))
    volume_cumulative = optional_int(
        raw_record.get("volume_cumulative")
        if "volume_cumulative" in raw_record
        else raw_record.get("volume")
    )
    turnover_cumulative = optional_float(
        raw_record.get("turnover_cumulative")
        if "turnover_cumulative" in raw_record
        else raw_record.get("turnover")
    )
    currency = normalize_currency(raw_record.get("currency") or static_info.get("currency"), normalized_market)

    flags: list[str] = []
    if not symbol:
        flags.append("missing_symbol")
    if not event_time:
        flags.append("missing_event_time")
    if last_price is None:
        flags.append("missing_price")
    elif last_price <= 0:
        flags.append("invalid_price")
    if (bid is not None and bid < 0) or (ask is not None and ask < 0):
        flags.append("invalid_bid_ask")
    if bid is not None and ask is not None and ask < bid:
        flags.append("ask_less_than_bid")
    if volume_cumulative is not None and volume_cumulative < 0:
        flags.append("invalid_volume")

    spread = None
    spread_pct = None
    if bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid:
        spread = ask - bid
        if last_price and last_price > 0:
            spread_pct = spread / last_price
            if spread_pct > ABNORMAL_SPREAD_PCT:
                flags.append("abnormal_spread")

    record_id = build_record_id(symbol, event_time)
    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "record_id": record_id,
        "event_time": event_time,
        "collected_at": collected,
        "collected_at_utc": collected,
        "source_timestamp_raw": source_timestamp_raw,
        "source_timestamp_utc": source_timestamp_utc,
        "market_timezone": str(raw_record.get("market_timezone") or timezone_name or market_timezone_name(normalized_market)),
        "session": str(raw_record.get("session") or "regular"),
        "trading_date": str(raw_record.get("trading_date") or ""),
        "session_window_id": str(raw_record.get("session_window_id") or ""),
        "provider": str(raw_record.get("provider") or raw_record.get("market_data_provider") or ""),
        "market": normalized_market,
        "symbol": symbol,
        "currency": currency,
        "last_price": last_price,
        "bid": bid,
        "ask": ask,
        "spread": round(spread, 8) if spread is not None else None,
        "spread_pct": round(spread_pct, 8) if spread_pct is not None else None,
        "volume_cumulative": volume_cumulative,
        "turnover_cumulative": turnover_cumulative,
        "is_valid": not any(flag in INVALID_FLAGS for flag in flags),
        "flags": sorted(set(flags)),
    }


def normalize_day(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    """Normalize one market/date raw JSONL file."""
    market = normalize_market(market)
    raw_path = raw_file_path(base_dir, market, trading_date)
    output_path = normalized_file_path(base_dir, market, trading_date)
    if not raw_path.exists():
        logger.info("skip normalize because raw file missing: %s %s", market, trading_date)
        return output_path
    load_result = load_jsonl(raw_path)
    reference = ReferenceDataStore(base_dir).load_reference(market, trading_date)
    reference_static_info_by_symbol = reference.get("static_info_by_symbol", {})
    if not isinstance(reference_static_info_by_symbol, dict):
        reference_static_info_by_symbol = {}

    normalized_records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw_entry in load_result.records:
        for raw_record, collected_at in expand_raw_entry(raw_entry):
            normalized = normalize_record(
                raw_record,
                market=market,
                collected_at=collected_at,
                reference_static_info_by_symbol=reference_static_info_by_symbol,
            )
            key = normalized.get("record_id") or ""
            if key and key in seen_keys:
                normalized["flags"] = sorted(set(normalized["flags"] + ["duplicate_record"]))
                normalized["is_valid"] = False
            elif key:
                seen_keys.add(key)
            normalized_records.append(normalized)

    normalized_records.sort(key=lambda item: (item.get("event_time") or "", item.get("symbol") or ""))
    write_jsonl_atomic(output_path, normalized_records)
    logger.info(
        "Normalized %s %s: raw_lines=%s records=%s output=%s",
        market,
        trading_date,
        load_result.raw_lines,
        len(normalized_records),
        output_path,
    )
    return output_path


def expand_raw_entry(raw_entry: dict[str, Any]) -> list[tuple[dict[str, Any], str | None]]:
    """Support both one-record raw lines and legacy snapshot lines with records."""
    collected_at = (
        raw_entry.get("collected_at")
        or raw_entry.get("collected_at_utc")
        or raw_entry.get("collected_at_local")
    )
    records = raw_entry.get("records")
    if isinstance(records, list):
        return [
            (record, collected_at)
            for record in records
            if isinstance(record, dict)
        ]
    return [(raw_entry, collected_at)]


def build_window_metrics(
    records: list[dict[str, Any]],
    market: str,
    trading_date: str,
    window: MarketWindow,
    interval_minutes: int = INTERVAL_MINUTES,
    source_normalized_file: str | None = None,
    session_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build metrics for all symbols in one configured window."""
    records_in_window = [
        record
        for record in records
        if record_belongs_to_window(record, window)
    ]
    if records and not records_in_window:
        log_empty_window_match(market, trading_date, window, records)
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records_in_window:
        symbol = record.get("symbol")
        if symbol:
            by_symbol[str(symbol)].append(record)

    symbol_metrics = []
    for symbol, symbol_records in sorted(by_symbol.items()):
        try:
            symbol_metrics.append(build_symbol_window_metrics(symbol, symbol_records, window.expected_points))
        except Exception as exc:
            logger.exception("Failed to build metrics for %s %s %s: %s", market, window.window_id, symbol, exc)
            symbol_metrics.append(
                {
                    "symbol": symbol,
                    "expected_points": window.expected_points,
                    "actual_points": 0,
                    "missing_points": window.expected_points,
                    "missing_ratio": 1.0 if window.expected_points else None,
                    "flags": ["metrics_error"],
                }
            )

    window_status = classify_window_status(records_in_window, symbol_metrics, window.expected_points)
    return {
        "metrics_version": METRICS_VERSION,
        "market": normalize_market(market),
        "trading_date": trading_date,
        "window_id": window.window_id,
        "window_status": window_status,
        "window_start": window.start.isoformat(timespec="seconds"),
        "window_end": window.end.isoformat(timespec="seconds"),
        "interval_minutes": interval_minutes,
        "expected_points": window.expected_points,
        "source_normalized_file": source_normalized_file or "",
        "generated_from_window": window.window_id,
        "session_metadata": session_metadata or {},
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "symbols": symbol_metrics,
        "cross_symbol": build_cross_symbol_metrics(symbol_metrics),
    }


def build_symbol_window_metrics(
    symbol: str,
    records: list[dict[str, Any]],
    expected_points: int,
) -> dict[str, Any]:
    """Build metrics for one symbol in one window; never mutates input records."""
    ordered = sorted(records, key=lambda item: item.get("event_time") or "")
    valid_records = [
        record
        for record in ordered
        if record.get("is_valid") and optional_float(record.get("last_price")) is not None
    ]
    prices = [float(record["last_price"]) for record in valid_records if float(record["last_price"]) > 0]
    flags: list[str] = []
    integrity_report = check_time_series_integrity(records)

    actual_points = len(valid_records)
    missing_points = max(expected_points - actual_points, 0)
    missing_ratio = (missing_points / expected_points) if expected_points else None
    if expected_points and actual_points < expected_points * 0.6:
        flags.append("excessive_missing_points")
    if missing_ratio is not None and missing_ratio > 0.2:
        flags.append("poor_data_quality")

    first_price = prices[0] if prices else None
    last_price = prices[-1] if prices else None
    high = max(prices) if prices else None
    low = min(prices) if prices else None

    if not first_price or not last_price:
        return_pct = None
        flags.append("insufficient_price_data")
    else:
        return_pct = (last_price / first_price - 1) * 100
    intraday_range_pct = ((high - low) / first_price * 100) if first_price and high is not None and low is not None else None

    volume_values = [
        optional_int(record.get("volume_cumulative"))
        for record in valid_records
        if optional_int(record.get("volume_cumulative")) is not None
    ]
    turnover_values = [
        optional_float(record.get("turnover_cumulative"))
        for record in valid_records
        if optional_float(record.get("turnover_cumulative")) is not None
    ]
    volume_delta = None
    turnover_delta = None
    if len(volume_values) >= 2:
        volume_delta = volume_values[-1] - volume_values[0]
    if len(turnover_values) >= 2:
        turnover_delta = turnover_values[-1] - turnover_values[0]

    if volume_delta is None or turnover_delta is None or volume_delta <= 0:
        vwap = None
        flags.append("invalid_volume_delta")
    else:
        vwap = turnover_delta / volume_delta
    if integrity_report["volume_reset_detected"]:
        flags.append("volume_reset_detected")

    returns = [
        (prices[index] / prices[index - 1] - 1)
        for index in range(1, len(prices))
        if prices[index - 1] > 0
    ]
    if len(returns) < 2:
        volatility = None
        if "insufficient_price_data" not in flags:
            flags.append("insufficient_price_data")
    else:
        volatility = statistics.stdev(returns)
        if volatility > ABNORMAL_VOLATILITY:
            flags.append("abnormal_volatility")

    spread_values = [
        optional_float(record.get("spread_pct"))
        for record in valid_records
        if optional_float(record.get("spread_pct")) is not None
    ]
    spread_metrics_available = bool(spread_values)
    max_spread = max(spread_values) if spread_values else None
    if max_spread is not None and max_spread > ABNORMAL_SPREAD_PCT:
        flags.append("abnormal_spread")

    stale_price_periods = count_stale_price_periods(prices)
    if integrity_report["max_stale_run"] >= 5:
        flags.append("stale_price")

    quality_grade = classify_quality_grade(missing_ratio, flags)

    return {
        "symbol": symbol,
        "expected_points": expected_points,
        "actual_points": actual_points,
        "missing_points": missing_points,
        "missing_ratio": round(missing_ratio, 4) if missing_ratio is not None else None,
        "first_price": first_price,
        "last_price": last_price,
        "high": high,
        "low": low,
        "return_pct": round(return_pct, 6) if return_pct is not None else None,
        "intraday_range_pct": round(intraday_range_pct, 6) if intraday_range_pct is not None else None,
        "volume_delta": volume_delta,
        "turnover_delta": round(turnover_delta, 4) if turnover_delta is not None else None,
        "vwap": round(vwap, 6) if vwap is not None else None,
        "volatility": round(volatility, 8) if volatility is not None else None,
        "max_drawdown_pct": round(max_drawdown_pct(prices), 6) if prices else None,
        "avg_spread_pct": round(sum(spread_values) / len(spread_values), 8) if spread_values else None,
        "max_spread_pct": round(max_spread, 8) if max_spread is not None else None,
        "spread_metrics_available": spread_metrics_available,
        "stale_price_periods": stale_price_periods,
        "quality_grade": quality_grade,
        "integrity_report": integrity_report,
        "flags": sorted(set(flags)),
    }


def build_daily_metrics(market: str, trading_date: str, window_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate window metric files into a stable daily metrics summary."""
    symbol_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "windows": [],
            "flags": set(),
            "quality_summary": Counter(),
        }
    )
    for window_metric in window_metrics:
        for item in window_metric.get("symbols", []):
            totals = symbol_totals[item["symbol"]]
            totals["windows"].append({"window_id": window_metric["window_id"], **item})
            totals["flags"].update(item.get("flags", []))
            totals["quality_summary"][item.get("quality_grade", "unusable")] += 1

    symbols = []
    for symbol, totals in sorted(symbol_totals.items()):
        windows = totals["windows"]
        usable = [
            item
            for item in windows
            if item.get("quality_grade") != "unusable"
        ]
        first_window = next((item for item in usable if item.get("first_price")), None)
        last_window = next((item for item in reversed(usable) if item.get("last_price")), None)
        daily_return_pct = None
        if first_window and last_window and first_window["first_price"]:
            daily_return_pct = (last_window["last_price"] / first_window["first_price"] - 1) * 100

        highs = [item["high"] for item in usable if item.get("high") is not None]
        lows = [item["low"] for item in usable if item.get("low") is not None]
        volume_deltas = [item["volume_delta"] for item in usable if item.get("volume_delta") is not None and item["volume_delta"] > 0]
        turnover_deltas = [
            item["turnover_delta"]
            for item in usable
            if item.get("turnover_delta") is not None and item["turnover_delta"] >= 0
        ]
        returns = [item["return_pct"] for item in usable if item.get("return_pct") is not None]
        drawdowns = [item["max_drawdown_pct"] for item in usable if item.get("max_drawdown_pct") is not None]
        daily_volume_delta = sum(volume_deltas) if volume_deltas else None
        daily_turnover_delta = sum(turnover_deltas) if turnover_deltas else None
        daily_vwap = (
            daily_turnover_delta / daily_volume_delta
            if daily_volume_delta and daily_turnover_delta is not None
            else None
        )
        best_window = max(usable, key=lambda item: item.get("return_pct") if item.get("return_pct") is not None else -math.inf, default=None)
        worst_window = min(usable, key=lambda item: item.get("return_pct") if item.get("return_pct") is not None else math.inf, default=None)
        quality_summary = {
            "good_windows": totals["quality_summary"].get("good", 0),
            "minor_missing_windows": totals["quality_summary"].get("minor_missing", 0),
            "poor_windows": totals["quality_summary"].get("poor", 0),
            "unusable_windows": totals["quality_summary"].get("unusable", 0),
        }
        symbols.append(
            {
                "symbol": symbol,
                "daily_return_pct": round(daily_return_pct, 6) if daily_return_pct is not None else None,
                "daily_high": max(highs) if highs else None,
                "daily_low": min(lows) if lows else None,
                "daily_volume_delta": daily_volume_delta,
                "daily_turnover_delta": round(daily_turnover_delta, 4) if daily_turnover_delta is not None else None,
                "daily_vwap": round(daily_vwap, 6) if daily_vwap is not None else None,
                "daily_volatility": round(statistics.stdev(returns), 8) if len(returns) >= 2 else None,
                "daily_max_drawdown_pct": round(min(drawdowns), 6) if drawdowns else None,
                "best_window": best_window["window_id"] if best_window else "",
                "worst_window": worst_window["window_id"] if worst_window else "",
                "quality_summary": quality_summary,
                "flags": sorted(totals["flags"]),
            }
        )

    return {
        "metrics_version": METRICS_VERSION,
        "market": normalize_market(market),
        "trading_date": trading_date,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "window_count": len(window_metrics),
        "window_status_summary": dict(Counter(item.get("window_status", "missing") for item in window_metrics)),
        "symbols": symbols,
        "market_summary": build_daily_market_summary(symbols),
    }


def build_quality_report(
    market: str,
    trading_date: str,
    raw_load_result: JsonlLoadResult,
    normalized_records: list[dict[str, Any]],
    window_metrics: list[dict[str, Any]],
    source_metrics_path: str = "",
    source_normalized_path: str = "",
) -> dict[str, Any]:
    """Build a daily data quality report from raw, normalized, and metrics layers."""
    flag_counts: Counter[str] = Counter()
    duplicate_records = 0
    invalid_lines = 0
    symbol_quality: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "expected_points_total": 0,
            "actual_points_total": 0,
            "invalid_points": 0,
            "duplicate_points": 0,
            "flags": set(),
        }
    )

    for record in normalized_records:
        flags = record.get("flags", [])
        flag_counts.update(flags)
        if not record.get("is_valid"):
            invalid_lines += 1
        if "duplicate_record" in flags:
            duplicate_records += 1
        symbol = record.get("symbol")
        if symbol:
            quality = symbol_quality[symbol]
            if not record.get("is_valid"):
                quality["invalid_points"] += 1
            if "duplicate_record" in flags:
                quality["duplicate_points"] += 1
            quality["flags"].update(flags)

    expected_windows = get_market_windows(market, trading_date)
    expected_window_ids = {window.window_id for window in expected_windows}
    actual_window_ids = {item.get("window_id") for item in window_metrics}
    normalized_symbols = sorted({record.get("symbol") for record in normalized_records if record.get("symbol")})
    metrics_by_window = {item.get("window_id"): item for item in window_metrics}
    for window in expected_windows:
        metric = metrics_by_window.get(window.window_id, {})
        metric_symbols = {item.get("symbol"): item for item in metric.get("symbols", [])}
        for symbol in normalized_symbols:
            quality = symbol_quality[symbol]
            quality["expected_points_total"] += window.expected_points
            item = metric_symbols.get(symbol)
            if item:
                quality["actual_points_total"] += int(item.get("actual_points") or 0)
                quality["flags"].update(item.get("flags", []))
    low_quality_windows = []
    for metric in window_metrics:
        weak_symbols = [
            item["symbol"]
            for item in metric.get("symbols", [])
            if (item.get("missing_ratio") or 0) > 0.2 or item.get("flags")
        ]
        if weak_symbols:
            low_quality_windows.append({"window_id": metric["window_id"], "symbols": weak_symbols})

    symbol_quality_output = {}
    for symbol, quality in sorted(symbol_quality.items()):
        expected = quality["expected_points_total"]
        actual = quality["actual_points_total"]
        symbol_quality_output[symbol] = {
            "expected_points_total": expected,
            "actual_points_total": actual,
            "missing_ratio": round(max(expected - actual, 0) / expected, 4) if expected else 0.0,
            "invalid_points": quality["invalid_points"],
            "duplicate_points": quality["duplicate_points"],
            "flags": sorted(quality["flags"]),
        }

    time_series_quality = check_time_series_quality(normalized_records)
    overall = classify_overall_quality(
        symbol_quality_output=symbol_quality_output,
        window_metrics=window_metrics,
        normalized_count=len(normalized_records),
        time_series_quality=time_series_quality,
    )
    return {
        "market": normalize_market(market),
        "trading_date": trading_date,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "overall_grade": overall["overall_grade"],
        "usable_for_analysis": overall["usable_for_analysis"],
        "quality_summary": overall["quality_summary"],
        "time_series_quality": time_series_quality,
        "raw_quality": {
            "raw_lines": raw_load_result.raw_lines,
            "json_parse_errors": len(raw_load_result.json_parse_errors),
        },
        "normalized_quality": {
            "normalized_lines": len(normalized_records),
            "valid_lines": len(normalized_records) - invalid_lines,
            "invalid_lines": invalid_lines,
            "duplicate_records": duplicate_records,
            "flag_counts": dict(sorted(flag_counts.items())),
        },
        "window_quality": {
            "expected_windows": len(expected_window_ids),
            "actual_windows": len(actual_window_ids),
            "missing_windows": sorted(expected_window_ids - actual_window_ids),
            "low_quality_windows": low_quality_windows,
        },
        "source_metrics_path": source_metrics_path,
        "source_normalized_path": source_normalized_path,
        "symbol_quality": symbol_quality_output,
    }


def metrics_day(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    """Generate per-window metrics from normalized JSONL."""
    market = normalize_market(market)
    normalized_path = normalized_file_path(base_dir, market, trading_date)
    if not normalized_path.exists():
        logger.info("skip metrics because normalized file missing: %s %s", market, trading_date)
        return metrics_dir(base_dir, market, trading_date)
    load_result = load_jsonl(normalized_path)
    output_dir = metrics_dir(base_dir, market, trading_date)
    output_dir.mkdir(parents=True, exist_ok=True)

    window_metrics = []
    for window in get_market_windows(market, trading_date):
        metric = build_window_metrics(load_result.records, market, trading_date, window, source_normalized_file=str(normalized_path))
        window_metrics.append(metric)
        write_json_atomic(output_dir / f"window_{window.window_id}.json", metric)

    logger.info("Generated metrics %s %s: windows=%s output=%s", market, trading_date, len(window_metrics), output_dir)
    return output_dir


def windows_day(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    """Write configured trading windows for one market/date."""
    market = normalize_market(market)
    windows = get_market_windows(market, trading_date)
    payload = {
        "market": market,
        "trading_date": trading_date,
        "interval_minutes": INTERVAL_MINUTES,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "windows": [
            {
                "window_id": window.window_id,
                "start": window.start.isoformat(timespec="seconds"),
                "end": window.end.isoformat(timespec="seconds"),
                "expected_points": window.expected_points,
            }
            for window in windows
        ],
    }
    output_path = metrics_dir(base_dir, market, trading_date) / "windows.json"
    write_json_atomic(output_path, payload)
    logger.info("Generated windows %s %s: %s", market, trading_date, output_path)
    return output_path


def daily_day(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    """Generate daily metrics from existing window metrics files."""
    market = normalize_market(market)
    output_dir = metrics_dir(base_dir, market, trading_date)
    if not output_dir.exists():
        logger.info("skip daily because metrics directory missing: %s %s", market, trading_date)
        return output_dir / "daily.json"
    window_metrics = []
    for path in sorted(output_dir.glob("window_*.json")):
        try:
            window_metrics.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            logger.exception("Cannot parse window metrics file %s: %s", path, exc)
    daily = build_daily_metrics(market, trading_date, window_metrics)
    daily_path = output_dir / "daily.json"
    write_json_atomic(daily_path, daily)
    logger.info("Generated daily metrics %s %s: %s", market, trading_date, daily_path)
    return daily_path


def quality_day(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    """Generate daily quality report."""
    market = normalize_market(market)
    raw_path = raw_file_path(base_dir, market, trading_date)
    normalized_path = normalized_file_path(base_dir, market, trading_date)
    output_dir = metrics_dir(base_dir, market, trading_date)
    output_path = quality_file_path(base_dir, market, trading_date)
    if not raw_path.exists():
        logger.info("skip quality because raw file missing: %s %s", market, trading_date)
        return output_path
    if not normalized_path.exists():
        logger.info("skip quality because normalized file missing: %s %s", market, trading_date)
        return output_path
    if not output_dir.exists():
        logger.info("skip quality because metrics directory missing: %s %s", market, trading_date)
        return output_path
    raw_load_result = load_jsonl(raw_path)
    normalized_load_result = load_jsonl(normalized_path)

    window_metrics = []
    for path in sorted(output_dir.glob("window_*.json")):
        try:
            window_metrics.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            logger.warning("Cannot parse metrics file %s: %s", path, exc)

    report = build_quality_report(
        market=market,
        trading_date=trading_date,
        raw_load_result=raw_load_result,
        normalized_records=normalized_load_result.records,
        window_metrics=window_metrics,
        source_metrics_path=str(output_dir),
        source_normalized_path=str(normalized_path),
    )
    write_json_atomic(output_path, report)
    logger.info("Generated quality report %s %s: %s", market, trading_date, output_path)
    return output_path


def all_day(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> None:
    """Run normalize, windows, metrics, daily, and quality for one market/date."""
    if not raw_file_path(base_dir, market, trading_date).exists():
        logger.info("skip normalize because raw file missing: %s %s", normalize_market(market), trading_date)
        return
    normalize_day(market, trading_date, base_dir)
    windows_day(market, trading_date, base_dir)
    metrics_day(market, trading_date, base_dir)
    daily_day(market, trading_date, base_dir)
    quality_day(market, trading_date, base_dir)


def build_cross_symbol_metrics(symbol_metrics: list[dict[str, Any]], top_n: int = 5) -> dict[str, list[dict[str, Any]]]:
    def top_by(field: str, reverse: bool = True) -> list[dict[str, Any]]:
        rows = [
            {"symbol": item["symbol"], field: item.get(field)}
            for item in symbol_metrics
            if item.get("quality_grade") != "unusable" and item.get(field) is not None
        ]
        return sorted(rows, key=lambda item: item[field], reverse=reverse)[:top_n]

    return {
        "top_gainers": top_by("return_pct", reverse=True),
        "top_losers": top_by("return_pct", reverse=False),
        "highest_volume_delta": top_by("volume_delta", reverse=True),
        "highest_volatility": top_by("volatility", reverse=True),
        "largest_drawdown": top_by("max_drawdown_pct", reverse=False),
        "weakest_data_quality": sorted(
            [
                {
                    "symbol": item["symbol"],
                    "missing_ratio": item.get("missing_ratio"),
                    "quality_grade": item.get("quality_grade"),
                    "flags": item.get("flags", []),
                }
                for item in symbol_metrics
            ],
            key=lambda item: (quality_rank(item.get("quality_grade")), (item["missing_ratio"] or 0), len(item["flags"])),
            reverse=True,
        )[:top_n],
    }


def build_daily_market_summary(symbols: list[dict[str, Any]], top_n: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Build cross-symbol rankings for daily metrics."""
    def top_by(field: str, reverse: bool = True) -> list[dict[str, Any]]:
        rows = [
            {"symbol": item["symbol"], field: item.get(field)}
            for item in symbols
            if item.get(field) is not None and item.get("quality_summary", {}).get("unusable_windows", 0) == 0
        ]
        return sorted(rows, key=lambda item: item[field], reverse=reverse)[:top_n]

    return {
        "top_gainers": top_by("daily_return_pct", reverse=True),
        "top_losers": top_by("daily_return_pct", reverse=False),
        "highest_volatility": top_by("daily_volatility", reverse=True),
        "largest_drawdown": top_by("daily_max_drawdown_pct", reverse=False),
        "highest_volume_delta": top_by("daily_volume_delta", reverse=True),
        "weakest_quality": sorted(
            [
                {
                    "symbol": item["symbol"],
                    "unusable_windows": item.get("quality_summary", {}).get("unusable_windows", 0),
                    "poor_windows": item.get("quality_summary", {}).get("poor_windows", 0),
                    "flags": item.get("flags", []),
                }
                for item in symbols
            ],
            key=lambda item: (item["unusable_windows"], item["poor_windows"], len(item["flags"])),
            reverse=True,
        )[:top_n],
    }


def check_time_series_integrity(records: list[dict[str, Any]], interval_minutes: int = INTERVAL_MINUTES) -> dict[str, Any]:
    """Check ordering, timestamp gaps, duplicates, volume monotonicity, and stale prices."""
    parsed_rows = [
        (parse_datetime(record.get("event_time")), record)
        for record in records
        if parse_datetime(record.get("event_time")) is not None
    ]
    duplicate_timestamps = 0
    timestamp_gaps = []
    timestamp_not_increasing = 0
    volume_reset_detected = False
    max_stale_run = 0
    current_stale_run = 0
    previous_time: datetime | None = None
    previous_volume: int | None = None
    previous_price: float | None = None
    seen_timestamps: set[str] = set()

    for event_time, record in parsed_rows:
        assert event_time is not None
        timestamp_key = event_time.isoformat()
        if timestamp_key in seen_timestamps:
            duplicate_timestamps += 1
        seen_timestamps.add(timestamp_key)

        if previous_time is not None:
            diff_minutes = (event_time - previous_time).total_seconds() / 60
            if diff_minutes <= 0:
                timestamp_not_increasing += 1
            elif diff_minutes > interval_minutes * 1.5:
                timestamp_gaps.append(
                    {
                        "from": previous_time.isoformat(timespec="seconds"),
                        "to": event_time.isoformat(timespec="seconds"),
                        "gap_minutes": round(diff_minutes, 2),
                    }
                )

        volume = optional_int(record.get("volume_cumulative"))
        if previous_volume is not None and volume is not None and volume < previous_volume:
            volume_reset_detected = True
        if volume is not None:
            previous_volume = volume

        price = optional_float(record.get("last_price"))
        if previous_price is not None and price is not None and price == previous_price:
            current_stale_run += 1
            max_stale_run = max(max_stale_run, current_stale_run)
        else:
            current_stale_run = 0
        if price is not None:
            previous_price = price
        previous_time = event_time

    return {
        "timestamp_not_increasing": timestamp_not_increasing,
        "timestamp_gaps": timestamp_gaps,
        "duplicate_timestamps": duplicate_timestamps,
        "volume_reset_detected": volume_reset_detected,
        "max_stale_run": max_stale_run,
        "long_stale_detected": max_stale_run >= 5,
    }


def count_stale_price_periods(prices: list[float]) -> int:
    """Count adjacent periods where price remains exactly unchanged."""
    return sum(1 for index in range(1, len(prices)) if prices[index] == prices[index - 1])


def classify_quality_grade(missing_ratio: float | None, flags: list[str]) -> str:
    """Classify window quality based on missing ratio and critical flags."""
    ratio = missing_ratio if missing_ratio is not None else 1.0
    if ratio <= 0.05:
        grade = "good"
    elif ratio <= 0.2:
        grade = "minor_missing"
    elif ratio <= 0.4:
        grade = "poor"
    else:
        grade = "unusable"

    if any(flag in flags for flag in ["insufficient_price_data", "invalid_volume_delta", "volume_reset_detected"]):
        if quality_rank(grade) < quality_rank("poor"):
            grade = "poor"
    return grade


def quality_rank(grade: str | None) -> int:
    return {"good": 0, "minor_missing": 1, "poor": 2, "unusable": 3}.get(str(grade), 3)


def record_belongs_to_window(record: dict[str, Any], window: MarketWindow) -> bool:
    event_time = parse_datetime(record.get("event_time"))
    if event_time is None:
        return False
    event_time_utc = event_time.astimezone(UTC)
    window_start_utc = window.start.astimezone(UTC)
    window_end_utc = window.end.astimezone(UTC)
    return window_start_utc <= event_time_utc < window_end_utc


def build_record_id(symbol: str, event_time: str | None) -> str:
    if not symbol or not event_time:
        return ""
    return f"{symbol}_{event_time}"


def classify_window_status(
    records_in_window: list[dict[str, Any]],
    symbol_metrics: list[dict[str, Any]],
    expected_points: int,
) -> str:
    if expected_points == 0:
        return "pending"
    if not records_in_window:
        return "missing"
    if not symbol_metrics:
        return "unusable"
    worst_missing_ratio = max((item.get("missing_ratio") or 0 for item in symbol_metrics), default=1)
    if worst_missing_ratio > 0.4:
        return "unusable"
    if worst_missing_ratio > 0.05:
        return "partial"
    return "finalized"


def classify_overall_quality(
    symbol_quality_output: dict[str, dict[str, Any]],
    window_metrics: list[dict[str, Any]],
    normalized_count: int,
    time_series_quality: dict[str, Any],
) -> dict[str, Any]:
    critical_issues = []
    warnings = []
    if normalized_count == 0:
        critical_issues.append("no_normalized_records")
    status_counts = Counter(item.get("window_status", "missing") for item in window_metrics)
    if status_counts.get("missing", 0):
        warnings.append("missing_windows")
    if status_counts.get("unusable", 0) >= 2:
        critical_issues.append("multiple_unusable_windows")
    max_missing_ratio = max((item.get("missing_ratio") or 0 for item in symbol_quality_output.values()), default=1.0)
    if max_missing_ratio > 0.7:
        critical_issues.append("high_missing_ratio")
    elif max_missing_ratio > 0.2:
        warnings.append("moderate_missing_ratio")
    if time_series_quality.get("duplicate_timestamps", 0):
        warnings.append("duplicate_timestamps")
    if time_series_quality.get("timestamp_gap_count", 0):
        warnings.append("timestamp_gaps")
    if time_series_quality.get("volume_decrease_count", 0) or time_series_quality.get("turnover_decrease_count", 0):
        warnings.append("decreasing_cumulative_values")

    if critical_issues and normalized_count == 0:
        overall_grade = "unusable"
    elif critical_issues:
        overall_grade = "poor"
    elif warnings:
        overall_grade = "minor_issue"
    else:
        overall_grade = "good"
    return {
        "overall_grade": overall_grade,
        "usable_for_analysis": overall_grade in {"good", "minor_issue"},
        "quality_summary": {
            "critical_issues": critical_issues,
            "warnings": warnings,
        },
    }


def log_empty_window_match(
    market: str,
    trading_date: str,
    window: MarketWindow,
    records: list[dict[str, Any]],
) -> None:
    event_times = [
        event_time
        for event_time in (parse_datetime(record.get("event_time")) for record in records)
        if event_time is not None
    ]
    if not event_times:
        logger.info(
            "window matched zero records with no parseable event_time: %s %s %s start=%s end=%s",
            market,
            trading_date,
            window.window_id,
            window.start.isoformat(timespec="seconds"),
            window.end.isoformat(timespec="seconds"),
        )
        return

    local_event_times = [event_time.astimezone(window.start.tzinfo) for event_time in event_times]
    logger.info(
        "window matched zero records: %s %s %s start=%s end=%s event_time_min=%s event_time_max=%s records=%s",
        market,
        trading_date,
        window.window_id,
        window.start.isoformat(timespec="seconds"),
        window.end.isoformat(timespec="seconds"),
        min(local_event_times).isoformat(timespec="seconds"),
        max(local_event_times).isoformat(timespec="seconds"),
        len(records),
    )


def max_drawdown_pct(prices: list[float]) -> float:
    peak = prices[0]
    max_drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak > 0:
            max_drawdown = min(max_drawdown, price / peak - 1)
    return max_drawdown * 100


def raw_file_path(base_dir: Path, market: str, trading_date: str) -> Path:
    market = normalize_market(market)
    regular_path = base_dir / "data" / "raw" / market / "regular" / f"{trading_date}.jsonl"
    legacy_path = base_dir / "data" / "raw" / market / f"{trading_date}.jsonl"
    if regular_path.exists():
        return regular_path
    return legacy_path


def normalized_file_path(base_dir: Path, market: str, trading_date: str) -> Path:
    return base_dir / "data" / "normalized" / normalize_market(market) / f"{trading_date}.jsonl"


def metrics_dir(base_dir: Path, market: str, trading_date: str) -> Path:
    return base_dir / "data" / "metrics" / normalize_market(market) / trading_date


def quality_file_path(base_dir: Path, market: str, trading_date: str) -> Path:
    return base_dir / "data" / "quality" / normalize_market(market) / f"{trading_date}.json"


def normalize_market(value: Any) -> str:
    market = str(value or "").strip().upper()
    if market not in {"HK", "US"}:
        return "HK" if market.endswith(".HK") else "US"
    return market


def normalize_symbol(value: Any, market: str) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        return ""
    if symbol.endswith(".HK"):
        ticker = symbol[:-3]
        return f"{ticker.zfill(4) if ticker.isdigit() else ticker}.HK"
    if symbol.endswith(".US"):
        return symbol
    if market == "HK" and symbol.isdigit():
        return f"{symbol.zfill(4)}.HK"
    if market == "US" and "." not in symbol:
        return f"{symbol}.US"
    return symbol


def normalize_currency(value: Any, market: str) -> str | None:
    text = str(value or "").strip().upper()
    return text or MARKET_CURRENCIES.get(market)


def normalize_timestamp(value: Any) -> str | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return iso_utc(parsed)


def normalize_event_timestamp(raw_record: dict[str, Any], market: str) -> str | None:
    source_utc = raw_record.get("source_timestamp_utc")
    if source_utc not in (None, ""):
        parsed = parse_datetime(source_utc)
        if parsed is not None:
            return iso_utc(parsed)

    event_time_value = raw_record.get("event_time")
    if event_time_value not in (None, ""):
        parsed = parse_datetime(event_time_value, default_timezone=provider_timestamp_timezone())
        if parsed is not None and datetime_value_has_timezone(event_time_value):
            return iso_utc(parsed)
        if parsed is not None and raw_record.get("timestamp") in (None, ""):
            return iso_utc(parsed)

    timestamp_value = raw_record.get("timestamp") or raw_record.get("quote_time")
    if timestamp_value not in (None, ""):
        _, _, source_timestamp_utc = normalize_source_timestamp(timestamp_value, market)
        if source_timestamp_utc:
            return source_timestamp_utc

    if event_time_value not in (None, ""):
        _, _, source_timestamp_utc = normalize_source_timestamp(event_time_value, market)
        if source_timestamp_utc:
            return source_timestamp_utc
    return None


def parse_datetime(value: Any, default_timezone: tzinfo = UTC) -> datetime | None:
    timezone = default_timezone if isinstance(default_timezone, ZoneInfo) else None
    parsed = parse_datetime_aware(value, default_timezone=timezone)
    if parsed is None:
        return None
    if timezone is None and parsed.tzinfo is None:
        return parsed.replace(tzinfo=default_timezone)
    return parsed


def parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
