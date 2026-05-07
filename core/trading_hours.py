from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TradingSession:
    start: time
    end: time

    def contains(self, value: time) -> bool:
        return self.start <= value < self.end


@dataclass(frozen=True)
class MarketHours:
    market: str
    timezone: ZoneInfo
    sessions: tuple[TradingSession, ...]

    def is_open(self, now: datetime) -> bool:
        local_now = now.astimezone(self.timezone)
        if local_now.weekday() >= 5:
            return False
        return any(session.contains(local_now.time()) for session in self.sessions)


HK_MARKET_HOURS = MarketHours(
    market="HK",
    timezone=ZoneInfo("Asia/Hong_Kong"),
    sessions=(
        TradingSession(time(9, 30), time(12, 0)),
        TradingSession(time(13, 0), time(16, 0)),
    ),
)

US_MARKET_HOURS = MarketHours(
    market="US",
    timezone=ZoneInfo("America/New_York"),
    sessions=(
        TradingSession(time(9, 30), time(16, 0)),
    ),
)

MARKET_HOURS = {
    "HK": HK_MARKET_HOURS,
    "US": US_MARKET_HOURS,
}


def open_markets(now: datetime) -> list[str]:
    return [
        market
        for market, hours in MARKET_HOURS.items()
        if hours.is_open(now)
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
