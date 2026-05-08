from __future__ import annotations

from pathlib import Path
from typing import Any

from core.data_pipeline import BASE_DIR
from core.post_market.common import load_json, metrics_day_dir, report_path, write_json_atomic
from core.post_market.market_weather import calculate_market_weather


def generate_market_summary(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    daily = load_json(metrics_day_dir(base_dir, market, trading_date) / "daily.json")
    quality = load_json(base_dir / "data" / "quality" / market / f"{trading_date}.json")
    symbols = daily.get("symbols", [])

    strongest = max(symbols, key=lambda item: item.get("daily_return_pct") if item.get("daily_return_pct") is not None else -10**9, default={})
    weakest = min(symbols, key=lambda item: item.get("daily_return_pct") if item.get("daily_return_pct") is not None else 10**9, default={})
    highest_vol = max(symbols, key=lambda item: item.get("daily_volatility") or 0, default={})
    highest_volume = max(symbols, key=lambda item: item.get("daily_volume_delta") or 0, default={})
    dominant_window = strongest.get("best_window") or ""
    weather = calculate_market_weather(daily, quality)

    summary_points = []
    if strongest:
        summary_points.append(f"Strongest symbol: {strongest.get('symbol')}")
    if weakest:
        summary_points.append(f"Weakest symbol: {weakest.get('symbol')}")
    if quality.get("overall_grade"):
        summary_points.append(f"Quality grade: {quality.get('overall_grade')}")

    payload: dict[str, Any] = {
        "market": market,
        "trading_date": trading_date,
        "market_regime": weather["market_weather"],
        "dominant_window": dominant_window,
        "strongest_symbol": strongest.get("symbol", ""),
        "weakest_symbol": weakest.get("symbol", ""),
        "highest_volatility_symbol": highest_vol.get("symbol", ""),
        "highest_volume_symbol": highest_volume.get("symbol", ""),
        "summary_points": summary_points,
        **weather,
    }
    return write_json_atomic(report_path(base_dir, market, trading_date, "market_summary.json"), payload)
