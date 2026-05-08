from __future__ import annotations

from typing import Any


def calculate_market_weather(daily: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    symbols = daily.get("symbols", [])
    returns = [item.get("daily_return_pct") for item in symbols if item.get("daily_return_pct") is not None]
    volatilities = [item.get("daily_volatility") for item in symbols if item.get("daily_volatility") is not None]
    drawdowns = [abs(item.get("daily_max_drawdown_pct") or 0) for item in symbols]
    usable = bool(quality.get("usable_for_analysis", True))

    avg_return = sum(returns) / len(returns) if returns else 0.0
    avg_volatility = sum(volatilities) / len(volatilities) if volatilities else 0.0
    avg_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
    trend_strength = min(abs(avg_return) / 5, 1.0)

    if not usable:
        weather = "storm"
    elif avg_return > 1 and avg_drawdown < 3:
        weather = "sunny"
    elif avg_drawdown > 5 or avg_volatility > 0.03:
        weather = "rainy"
    else:
        weather = "cloudy"

    return {
        "market_weather": weather,
        "risk_sentiment": "positive" if avg_return > 0.5 else ("negative" if avg_return < -0.5 else "neutral"),
        "volatility_level": "high" if avg_volatility > 0.03 else ("medium" if avg_volatility > 0.01 else "low"),
        "liquidity_state": "healthy" if symbols else "unknown",
        "trend_strength": round(trend_strength, 4),
    }
