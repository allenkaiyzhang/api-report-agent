from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from core.data_pipeline import (
    BASE_DIR,
    load_jsonl,
    metrics_dir,
    normalized_file_path,
    quality_file_path,
    raw_file_path,
)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def replay_summary(market: str, trading_date: str, window: str | None = None) -> dict[str, Any]:
    raw_result = load_jsonl(raw_file_path(BASE_DIR, market, trading_date))
    normalized_result = load_jsonl(normalized_file_path(BASE_DIR, market, trading_date))
    flags = Counter(flag for row in normalized_result.records for flag in row.get("flags", []))
    duplicate_count = flags.get("duplicate_record", 0)
    invalid_count = sum(1 for row in normalized_result.records if not row.get("is_valid"))

    metric_path = metrics_dir(BASE_DIR, market, trading_date) / (
        f"window_{window}.json" if window else "daily.json"
    )
    metric = load_json(metric_path)
    quality = load_json(quality_file_path(BASE_DIR, market, trading_date))

    if window:
        symbols = metric.get("symbols", [])
        missing_ratio = max((item.get("missing_ratio") or 0 for item in symbols), default=0)
        quality_grade = Counter(item.get("quality_grade", "unknown") for item in symbols)
        volume_reset = [
            item["symbol"]
            for item in symbols
            if item.get("integrity_report", {}).get("volume_reset_detected")
        ]
        stale_periods = sum(int(item.get("stale_price_periods") or 0) for item in symbols)
        top_volatility = metric.get("cross_symbol", {}).get("highest_volatility", [])
        top_drawdown = metric.get("cross_symbol", {}).get("largest_drawdown", [])
    else:
        symbols = metric.get("symbols", [])
        missing_ratio = max(
            (
                item.get("quality_summary", {}).get("poor_windows", 0)
                + item.get("quality_summary", {}).get("unusable_windows", 0)
                for item in symbols
            ),
            default=0,
        )
        quality_grade = quality.get("window_quality", {})
        volume_reset = [
            item["symbol"]
            for item in symbols
            if "volume_reset_detected" in item.get("flags", [])
        ]
        stale_periods = sum(1 for item in symbols if "stale_price" in item.get("flags", []))
        top_volatility = metric.get("market_summary", {}).get("highest_volatility", [])
        top_drawdown = metric.get("market_summary", {}).get("largest_drawdown", [])

    return {
        "market": market,
        "trading_date": trading_date,
        "window": window,
        "raw_count": raw_result.raw_lines,
        "raw_json_parse_errors": len(raw_result.json_parse_errors),
        "normalized_count": len(normalized_result.records),
        "duplicate_count": duplicate_count,
        "invalid_count": invalid_count,
        "flag_counts": dict(sorted(flags.items())),
        "missing_ratio_or_bad_window_count": missing_ratio,
        "quality_grade": quality_grade,
        "volume_reset_detection": volume_reset,
        "stale_periods": stale_periods,
        "top_volatility_symbols": top_volatility,
        "top_drawdown_symbols": top_drawdown,
    }

