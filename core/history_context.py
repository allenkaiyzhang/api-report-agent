from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any


def build_history_context(
    base_dir: Path,
    market: str,
    trading_date: str,
    lookback_days: int = 5,
) -> dict[str, Any]:
    """Build compact historical context from metrics daily.json files only."""
    market = str(market or "").strip().upper()
    metrics_root = base_dir / "data" / "metrics" / market
    selected_dates, missing_daily_files = select_history_dates(metrics_root, trading_date, lookback_days)
    daily_payloads = [(day, load_daily(metrics_root / day / "daily.json")) for day in selected_dates]
    daily_payloads = [(day, payload) for day, payload in daily_payloads if payload]

    symbols_by_name: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for day, payload in daily_payloads:
        for symbol_row in payload.get("symbols", []):
            if not isinstance(symbol_row, dict):
                continue
            symbol = str(symbol_row.get("symbol") or "")
            if symbol:
                symbols_by_name.setdefault(symbol, []).append((day, symbol_row))

    symbols = {
        symbol: build_symbol_history(symbol, rows, selected_dates)
        for symbol, rows in sorted(symbols_by_name.items())
    }

    window_status_counts: dict[str, int] = {}
    for _, payload in daily_payloads:
        summary = payload.get("window_status_summary", {})
        if isinstance(summary, dict):
            for status, count in summary.items():
                window_status_counts[str(status)] = window_status_counts.get(str(status), 0) + int(count or 0)

    return {
        "market": market,
        "trading_date": trading_date,
        "lookback_days": lookback_days,
        "available_dates": [day for day, _ in daily_payloads],
        "history_available": bool(daily_payloads),
        "symbols": symbols,
        "data_quality_summary": {
            "available_day_count": len(daily_payloads),
            "missing_daily_files": missing_daily_files,
            "window_status_summary": window_status_counts,
            "symbol_count": len(symbols),
        },
    }


def select_history_dates(metrics_root: Path, trading_date: str, lookback_days: int) -> tuple[list[str], list[str]]:
    if lookback_days <= 0 or not metrics_root.exists():
        return [], []

    target = date.fromisoformat(trading_date)
    candidates = []
    for path in metrics_root.iterdir():
        if not path.is_dir():
            continue
        try:
            day = date.fromisoformat(path.name)
        except ValueError:
            continue
        if day < target:
            candidates.append(path.name)

    selected = sorted(candidates, reverse=True)[:lookback_days]
    selected = sorted(selected)
    missing = [day for day in selected if not (metrics_root / day / "daily.json").exists()]
    available = [day for day in selected if day not in missing]
    return available, missing


def load_daily(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def build_symbol_history(
    symbol: str,
    rows: list[tuple[str, dict[str, Any]]],
    selected_dates: list[str],
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda item: item[0])
    dates = [day for day, _ in rows]
    latest = rows[-1][1] if rows else {}
    returns = [to_float(row.get("daily_return_pct")) for _, row in rows]
    returns = [value for value in returns if value is not None]
    volatilities = [to_float(row.get("daily_volatility")) for _, row in rows]
    volatilities = [value for value in volatilities if value is not None]
    volumes = [to_float(row.get("daily_volume_delta")) for _, row in rows]
    volumes = [value for value in volumes if value is not None]

    latest_return = to_float(latest.get("daily_return_pct"))
    latest_volatility = to_float(latest.get("daily_volatility"))
    latest_volume = to_float(latest.get("daily_volume_delta"))
    previous_volatilities = volatilities[:-1]
    previous_volumes = volumes[:-1]
    avg_volatility = average(volatilities)
    avg_volume = average(volumes)
    volume_vs_history = ratio(latest_volume, average(previous_volumes))
    volatility_vs_history = ratio(latest_volatility, average(previous_volatilities))
    cumulative_return = cumulative_return_pct(returns)
    missing_dates = [day for day in selected_dates if day not in set(dates)]

    return {
        "symbol": symbol,
        "available_dates": dates,
        "latest_return_pct": round_value(latest_return),
        "avg_daily_return_pct": round_value(average(returns)),
        "cumulative_return_pct": round_value(cumulative_return),
        "avg_volatility": round_value(avg_volatility, digits=8),
        "latest_volatility": round_value(latest_volatility, digits=8),
        "latest_volume_delta": round_value(latest_volume),
        "avg_volume_delta": round_value(avg_volume),
        "volume_vs_history": round_value(volume_vs_history),
        "volatility_vs_history": round_value(volatility_vs_history),
        "trend_label": trend_label(cumulative_return, average(returns), len(returns)),
        "risk_flags": risk_flags(
            latest_return=latest_return,
            cumulative_return=cumulative_return,
            latest_volatility=latest_volatility,
            volatility_vs_history=volatility_vs_history,
            volume_vs_history=volume_vs_history,
            missing_dates=missing_dates,
            rows=rows,
        ),
        "data_quality_summary": symbol_quality_summary(rows, missing_dates),
    }


def symbol_quality_summary(rows: list[tuple[str, dict[str, Any]]], missing_dates: list[str]) -> dict[str, Any]:
    poor_windows = 0
    unusable_windows = 0
    flags: set[str] = set()
    for _, row in rows:
        quality = row.get("quality_summary", {})
        if isinstance(quality, dict):
            poor_windows += int(quality.get("poor_windows") or 0)
            unusable_windows += int(quality.get("unusable_windows") or 0)
        for flag in row.get("flags", []) if isinstance(row.get("flags", []), list) else []:
            flags.add(str(flag))
    return {
        "observed_days": len(rows),
        "missing_dates": missing_dates,
        "poor_windows": poor_windows,
        "unusable_windows": unusable_windows,
        "flags": sorted(flags),
    }


def risk_flags(
    latest_return: float | None,
    cumulative_return: float | None,
    latest_volatility: float | None,
    volatility_vs_history: float | None,
    volume_vs_history: float | None,
    missing_dates: list[str],
    rows: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    flags = []
    if missing_dates:
        flags.append("partial_history")
    if latest_return is not None and latest_return <= -3:
        flags.append("large_latest_decline")
    if cumulative_return is not None and cumulative_return <= -5:
        flags.append("negative_history_trend")
    if volatility_vs_history is not None and volatility_vs_history >= 1.5:
        flags.append("volatility_above_history")
    if volume_vs_history is not None and volume_vs_history >= 1.5:
        flags.append("volume_above_history")
    if latest_volatility is not None and latest_volatility <= 0:
        flags.append("low_or_zero_volatility")
    for _, row in rows:
        quality = row.get("quality_summary", {})
        if isinstance(quality, dict) and int(quality.get("unusable_windows") or 0) > 0:
            flags.append("unusable_windows_present")
            break
    return sorted(set(flags))


def trend_label(cumulative_return: float | None, avg_return: float | None, count: int) -> str:
    if count == 0:
        return "insufficient_history"
    cumulative = cumulative_return or 0.0
    average_return = avg_return or 0.0
    if cumulative >= 2 and average_return > 0:
        return "uptrend"
    if cumulative <= -2 and average_return < 0:
        return "downtrend"
    if abs(cumulative) < 1:
        return "flat"
    return "mixed"


def cumulative_return_pct(values: list[float]) -> float | None:
    if not values:
        return None
    multiplier = 1.0
    for value in values:
        multiplier *= 1 + value / 100
    return (multiplier - 1) * 100


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def round_value(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)
