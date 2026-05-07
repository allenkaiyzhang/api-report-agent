from __future__ import annotations

from datetime import datetime

from core.market_calendar import is_market_open


def open_markets(now: datetime) -> list[str]:
    return [
        market
        for market in ("HK", "US")
        if is_market_open(market, now)
    ]


def infer_symbol_market(symbol: str) -> str:
    upper_symbol = symbol.upper()
    if upper_symbol.endswith(".HK"):
        return "HK"
    return "US"


def filter_symbols_by_open_markets(symbols: list[str], markets: list[str]) -> list[str]:
    open_market_set = set(markets)
    return [
        symbol
        for symbol in symbols
        if infer_symbol_market(symbol) in open_market_set
    ]
