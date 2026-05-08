from __future__ import annotations

from pathlib import Path
from typing import Any

from core.data_pipeline import BASE_DIR
from core.post_market.common import feature_path, load_json, metrics_day_dir, write_json_atomic
from core.post_market.market_weather import calculate_market_weather


def generate_features(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    daily = load_json(metrics_day_dir(base_dir, market, trading_date) / "daily.json")
    quality = load_json(base_dir / "data" / "quality" / market / f"{trading_date}.json")
    symbols = daily.get("symbols", [])
    returns = [item.get("daily_return_pct") for item in symbols if item.get("daily_return_pct") is not None]
    vols = [item.get("daily_volatility") for item in symbols if item.get("daily_volatility") is not None]
    drawdowns = [item.get("daily_max_drawdown_pct") for item in symbols if item.get("daily_max_drawdown_pct") is not None]
    weather = calculate_market_weather(daily, quality)
    dominant_window = max(
        (item.get("best_window") for item in symbols if item.get("best_window")),
        key=lambda window: sum(1 for item in symbols if item.get("best_window") == window),
        default="",
    )
    payload: dict[str, Any] = {
        "market": market,
        "trading_date": trading_date,
        "rolling_volatility": round(sum(vols) / len(vols), 8) if vols else None,
        "rolling_return": round(sum(returns) / len(returns), 6) if returns else None,
        "liquidity_score": round(sum((item.get("daily_volume_delta") or 0) for item in symbols) / max(len(symbols), 1), 4),
        "volatility_percentile": percentile_rank(sum(vols) / len(vols), vols) if vols else None,
        "dominant_trading_window": dominant_window,
        "trend_strength": weather["trend_strength"],
        "average_drawdown": round(sum(drawdowns) / len(drawdowns), 6) if drawdowns else None,
        "window_win_rate": win_rate(returns),
        "symbol_relative_strength": sorted(
            [{"symbol": item.get("symbol"), "daily_return_pct": item.get("daily_return_pct")} for item in symbols],
            key=lambda item: item.get("daily_return_pct") if item.get("daily_return_pct") is not None else -10**9,
            reverse=True,
        ),
    }
    return write_json_atomic(feature_path(base_dir, market, trading_date), payload)


def percentile_rank(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(1 for item in values if item <= value) / len(values), 4)


def win_rate(returns: list[float]) -> float | None:
    if not returns:
        return None
    return round(sum(1 for item in returns if item > 0) / len(returns), 4)
