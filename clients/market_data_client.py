"""MarketDataClient abstract interface.

All report workflows MUST call this interface, never raw MCP tools directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Quote:
    symbol: str
    market: str
    latest_price: float
    previous_close: float
    change_percent: float
    open: float
    high: float
    low: float
    volume: int
    turnover: float
    bid: float
    ask: float
    trade_status: str
    currency: str
    timestamp: str
    source: str = "mock"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "latest_price": self.latest_price,
            "previous_close": self.previous_close,
            "change_percent": self.change_percent,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "turnover": self.turnover,
            "bid": self.bid,
            "ask": self.ask,
            "trade_status": self.trade_status,
            "currency": self.currency,
            "timestamp": self.timestamp,
            "event_time": self.timestamp,
            "source": self.source,
        }


@dataclass
class Candle:
    symbol: str
    market: str
    close: float
    open: float
    low: float
    high: float
    volume: int
    turnover: float
    timestamp: str
    trade_session: str
    source: str = "mock"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "close": self.close,
            "open": self.open,
            "low": self.low,
            "high": self.high,
            "volume": self.volume,
            "turnover": self.turnover,
            "timestamp": self.timestamp,
            "trade_session": self.trade_session,
            "source": self.source,
        }


@dataclass
class IntradayPoint:
    symbol: str
    market: str
    price: float
    volume: int
    turnover: float
    timestamp: str
    source: str = "mock"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "price": self.price,
            "volume": self.volume,
            "turnover": self.turnover,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class MarketStatusInfo:
    market: str
    is_open: bool
    session: str
    next_open: str | None = None
    next_close: str | None = None
    timestamp: str = ""
    source: str = "mock"

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "is_open": self.is_open,
            "session": self.session,
            "next_open": self.next_open,
            "next_close": self.next_close,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class FundamentalData:
    symbol: str
    market: str
    name_cn: str = ""
    name_en: str = ""
    exchange: str = ""
    currency: str = ""
    lot_size: int = 0
    eps: float = 0.0
    eps_ttm: float = 0.0
    bps: float = 0.0
    dividend_yield: float = 0.0
    board: str = ""
    source: str = "mock"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "name_cn": self.name_cn,
            "name_en": self.name_en,
            "exchange": self.exchange,
            "currency": self.currency,
            "lot_size": self.lot_size,
            "eps": self.eps,
            "eps_ttm": self.eps_ttm,
            "bps": self.bps,
            "dividend_yield": self.dividend_yield,
            "board": self.board,
            "source": self.source,
        }


@dataclass
class MarketReportDataset:
    """Aggregated dataset for report generation.

    Contains all collected, cleaned, and validated data for a single run.
    """

    run_id: str
    report_type: str
    market: str
    symbols: list[str]
    quotes: list[Quote] = field(default_factory=list)
    candles: list[Candle] = field(default_factory=list)
    intraday: list[IntradayPoint] = field(default_factory=list)
    market_status: MarketStatusInfo | None = None
    fundamentals: list[FundamentalData] = field(default_factory=list)
    collected_at: str = ""
    validated: bool = False
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "report_type": self.report_type,
            "market": self.market,
            "symbols": self.symbols,
            "quotes": [q.to_dict() for q in self.quotes],
            "candles": [c.to_dict() for c in self.candles],
            "intraday": [p.to_dict() for p in self.intraday],
            "market_status": self.market_status.to_dict() if self.market_status else None,
            "fundamentals": [f.to_dict() for f in self.fundamentals],
            "collected_at": self.collected_at,
            "validated": self.validated,
            "validation_errors": self.validation_errors,
        }


class MarketDataClient(ABC):
    """Abstract interface for market data providers.

    All report workflows call methods on this interface.
    Concrete implementations: LongbridgeMcpClient, MockMarketDataClient.
    """

    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Fetch latest quotes for given symbols."""

    @abstractmethod
    def get_candles(self, symbols: list[str], period: str = "day", count: int = 20) -> list[Candle]:
        """Fetch historical candles."""

    @abstractmethod
    def get_intraday(self, symbols: list[str]) -> list[IntradayPoint]:
        """Fetch intraday price/volume points."""

    @abstractmethod
    def get_market_status(self, markets: list[str]) -> list[MarketStatusInfo]:
        """Check if markets are currently open and their session state."""

    @abstractmethod
    def get_fundamentals(self, symbols: list[str]) -> list[FundamentalData]:
        """Fetch fundamental data for symbols (optional)."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Check provider connectivity. Returns {"ok": bool, "provider": str, ...}."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""
