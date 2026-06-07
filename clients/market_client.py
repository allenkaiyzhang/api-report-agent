"""LEGACY MarketClient — uses longbridge.openapi SDK (DEPRECATED).

This module is part of the legacy SDK pipeline. The current production path
uses MarketDataClient abstraction with LongbridgeMcpClient (MCP adapter).
See clients/longbridge_mcp_client.py for the MCP implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from random import Random
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketData:
    symbol: str
    latest_price: float
    previous_close: float
    change_percent: float
    volume: int
    avg_volume_20d: int
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        market = "HK" if self.symbol.upper().endswith(".HK") else "US"
        return {
            "symbol": self.symbol,
            "market": market,
            "latest_price": self.latest_price,
            "last_price": self.latest_price,
            "previous_close": self.previous_close,
            "change_percent": self.change_percent,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "event_time": self.timestamp,
            "currency": "HKD" if market == "HK" else "USD",
        }


class MarketClient:
    """Market data client.

    Defaults to deterministic mock data so the app can run without any API key.
    Set provider="longbridge" to pull quote data through Longbridge OpenAPI.
    """

    _BASE_QUOTES: dict[str, tuple[float, float, int, int]] = {
        "QQQ": (445.20, 436.10, 63_000_000, 45_000_000),
        "SGOV": (100.48, 100.46, 3_200_000, 3_100_000),
        "HSBC.US": (43.35, 44.52, 2_600_000, 1_500_000),
        "VIX": (24.80, 21.60, 0, 0),
    }

    def __init__(self, provider: str = "mock") -> None:
        self.provider = provider.lower()
        self._longbridge_quote_context: Any | None = None

    def fetch_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        return self.fetch_realtime_quotes(symbols)

    def fetch_realtime_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        if self.provider == "longbridge":
            return self._fetch_longbridge_realtime_quotes(symbols)
        return [self.fetch_quote(symbol).to_dict() for symbol in symbols]

    def fetch_reference_data(self, symbols: list[str]) -> dict[str, Any]:
        if self.provider == "longbridge":
            return self._fetch_longbridge_reference_data(symbols)

        static_info_by_symbol = {
            symbol: {
                "currency": "HKD" if symbol.upper().endswith(".HK") else "USD",
            }
            for symbol in symbols
        }
        calc_indexes_by_symbol = {}
        daily_candlesticks_by_symbol = {}
        for symbol in symbols:
            _, _, volume, avg_volume_20d = self._mock_values(symbol)
            daily_candlesticks_by_symbol[symbol] = [
                {
                    "close": self.fetch_quote(symbol).latest_price,
                    "open": self.fetch_quote(symbol).previous_close,
                    "low": min(self.fetch_quote(symbol).latest_price, self.fetch_quote(symbol).previous_close),
                    "high": max(self.fetch_quote(symbol).latest_price, self.fetch_quote(symbol).previous_close),
                    "volume": avg_volume_20d or volume,
                    "turnover": 0.0,
                    "timestamp": datetime.now(UTC).date().isoformat(),
                    "trade_session": "",
                }
            ]
        return {
            "static_info_by_symbol": static_info_by_symbol,
            "calc_indexes_by_symbol": calc_indexes_by_symbol,
            "daily_candlesticks_by_symbol": daily_candlesticks_by_symbol,
        }

    def fetch_quote(self, symbol: str) -> MarketData:
        if self.provider != "mock":
            raise NotImplementedError("Only mock market data is implemented in v1.")

        latest_price, previous_close, volume, avg_volume_20d = self._mock_values(symbol)
        change_percent = ((latest_price - previous_close) / previous_close) * 100

        return MarketData(
            symbol=symbol,
            latest_price=round(latest_price, 2),
            previous_close=round(previous_close, 2),
            change_percent=round(change_percent, 2),
            volume=volume,
            avg_volume_20d=avg_volume_20d,
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def _mock_values(self, symbol: str) -> tuple[float, float, int, int]:
        known_quote = self._BASE_QUOTES.get(symbol)
        if known_quote:
            return known_quote

        rng = Random(symbol)
        previous_close = round(rng.uniform(20, 250), 2)
        move_percent = rng.uniform(-3.0, 3.0)
        latest_price = previous_close * (1 + move_percent / 100)
        avg_volume_20d = rng.randint(500_000, 8_000_000)
        volume = int(avg_volume_20d * rng.uniform(0.6, 1.8))
        return latest_price, previous_close, volume, avg_volume_20d

    def _fetch_longbridge_realtime_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        ctx = self._get_longbridge_quote_context()
        try:
            quotes = ctx.quote(symbols)
        except Exception as exc:
            logger.error("Longbridge realtime quote failed: %s", exc)
            return []

        records = []
        for quote in quotes:
            symbol = str(getattr(quote, "symbol"))
            latest_price = self._to_float(getattr(quote, "last_done", 0))
            previous_close = self._to_float(getattr(quote, "prev_close", 0))
            change_percent = self._calculate_change_percent(latest_price, previous_close)
            volume = self._to_int(getattr(quote, "volume", 0))

            record = {
                "symbol": symbol,
                "market": self._infer_market(symbol),
                "latest_price": round(latest_price, 4),
                "last_price": round(latest_price, 4),
                "previous_close": round(previous_close, 4),
                "change_percent": round(change_percent, 2),
                "volume": volume,
                "timestamp": self._stringify(getattr(quote, "timestamp", datetime.now(UTC))),
                "event_time": self._stringify(getattr(quote, "timestamp", datetime.now(UTC))),
                "open": self._to_float(getattr(quote, "open", 0)),
                "high": self._to_float(getattr(quote, "high", 0)),
                "low": self._to_float(getattr(quote, "low", 0)),
                "turnover": self._to_float(getattr(quote, "turnover", 0)),
                "bid": self._to_float(getattr(quote, "bid", 0)),
                "ask": self._to_float(getattr(quote, "ask", 0)),
                "trade_status": self._stringify(getattr(quote, "trade_status", "")),
                "currency": self._stringify(getattr(quote, "currency", "")),
                "market_data_provider": "longbridge",
            }
            records.append(record)

        return records

    def _fetch_longbridge_reference_data(self, symbols: list[str]) -> dict[str, Any]:
        ctx = self._get_longbridge_quote_context()
        static_info_by_symbol = self._safe_static_info(ctx, symbols)
        calc_indexes_by_symbol = self._safe_calc_indexes(ctx, symbols)
        daily_candlesticks_by_symbol = {
            symbol: self._safe_daily_candlesticks(ctx, symbol, count=20)
            for symbol in symbols
        }
        return {
            "static_info_by_symbol": static_info_by_symbol,
            "calc_indexes_by_symbol": calc_indexes_by_symbol,
            "daily_candlesticks_by_symbol": daily_candlesticks_by_symbol,
        }

    def _get_longbridge_quote_context(self) -> Any:
        if self._longbridge_quote_context is not None:
            return self._longbridge_quote_context

        try:
            from longbridge.openapi import Config, QuoteContext
        except ImportError as exc:
            raise RuntimeError(
                "Longbridge SDK is not installed. Run: pip install longbridge"
            ) from exc

        config = Config.from_apikey_env()
        self._longbridge_quote_context = QuoteContext(config)
        return self._longbridge_quote_context

    def _safe_static_info(self, ctx: Any, symbols: list[str]) -> dict[str, dict[str, Any]]:
        try:
            rows = ctx.static_info(symbols)
        except Exception as exc:
            logger.warning("Longbridge static_info failed: %s", exc)
            return {}

        return {
            str(getattr(item, "symbol", "")): {
                "name_cn": self._stringify(getattr(item, "name_cn", "")),
                "name_en": self._stringify(getattr(item, "name_en", "")),
                "name_hk": self._stringify(getattr(item, "name_hk", "")),
                "exchange": self._stringify(getattr(item, "exchange", "")),
                "currency": self._stringify(getattr(item, "currency", "")),
                "lot_size": self._to_int(getattr(item, "lot_size", 0)),
                "eps": self._to_float(getattr(item, "eps", 0)),
                "eps_ttm": self._to_float(getattr(item, "eps_ttm", 0)),
                "bps": self._to_float(getattr(item, "bps", 0)),
                "dividend_yield": self._to_float(getattr(item, "dividend_yield", 0)),
                "board": self._stringify(getattr(item, "board", "")),
            }
            for item in rows
        }

    def _safe_calc_indexes(self, ctx: Any, symbols: list[str]) -> dict[str, dict[str, Any]]:
        try:
            from longbridge.openapi import CalcIndex

            indexes = [
                CalcIndex.LastDone,
                CalcIndex.ChangeValue,
                CalcIndex.ChangeRate,
                CalcIndex.Volume,
                CalcIndex.Turnover,
                CalcIndex.VolumeRatio,
                CalcIndex.FiveDayChangeRate,
                CalcIndex.TenDayChangeRate,
                CalcIndex.HalfYearChangeRate,
            ]
            rows = ctx.calc_indexes(symbols, indexes)
        except Exception as exc:
            logger.warning("Longbridge calc_indexes failed: %s", exc)
            return {}

        return {
            str(getattr(item, "symbol", "")): {
                "last_done": self._to_float(getattr(item, "last_done", 0)),
                "change_value": self._to_float(getattr(item, "change_value", 0)),
                "change_rate": self._to_float(getattr(item, "change_rate", 0)),
                "volume": self._to_int(getattr(item, "volume", 0)),
                "turnover": self._to_float(getattr(item, "turnover", 0)),
                "volume_ratio": self._to_float(getattr(item, "volume_ratio", 0)),
                "five_day_change_rate": self._to_float(getattr(item, "five_day_change_rate", 0)),
                "ten_day_change_rate": self._to_float(getattr(item, "ten_day_change_rate", 0)),
                "half_year_change_rate": self._to_float(getattr(item, "half_year_change_rate", 0)),
            }
            for item in rows
        }

    def _safe_daily_candlesticks(self, ctx: Any, symbol: str, count: int) -> list[dict[str, Any]]:
        try:
            from longbridge.openapi import AdjustType, Period

            rows = ctx.candlesticks(symbol, Period.Day, count, AdjustType.NoAdjust)
        except Exception as exc:
            logger.warning("Longbridge candlesticks failed for %s: %s", symbol, exc)
            return []

        return [
            {
                "close": self._to_float(getattr(item, "close", 0)),
                "open": self._to_float(getattr(item, "open", 0)),
                "low": self._to_float(getattr(item, "low", 0)),
                "high": self._to_float(getattr(item, "high", 0)),
                "volume": self._to_int(getattr(item, "volume", 0)),
                "turnover": self._to_float(getattr(item, "turnover", 0)),
                "timestamp": self._stringify(getattr(item, "timestamp", "")),
                "trade_session": self._stringify(getattr(item, "trade_session", "")),
            }
            for item in rows
        ]

    def _serialize_optional_quote(self, quote: Any | None) -> dict[str, Any] | None:
        if quote is None:
            return None
        return {
            "last_done": self._to_float(getattr(quote, "last_done", 0)),
            "timestamp": self._stringify(getattr(quote, "timestamp", "")),
            "volume": self._to_int(getattr(quote, "volume", 0)),
            "turnover": self._to_float(getattr(quote, "turnover", 0)),
            "high": self._to_float(getattr(quote, "high", 0)),
            "low": self._to_float(getattr(quote, "low", 0)),
            "prev_close": self._to_float(getattr(quote, "prev_close", 0)),
        }

    def _average_volume(self, daily_candlesticks: list[dict[str, Any]]) -> int:
        volumes = [int(item["volume"]) for item in daily_candlesticks if int(item.get("volume", 0)) > 0]
        if not volumes:
            return 0
        return int(sum(volumes) / len(volumes))

    def _infer_market(self, symbol: str) -> str:
        return "HK" if symbol.upper().endswith(".HK") else "US"

    def _calculate_change_percent(self, latest_price: float, previous_close: float) -> float:
        if previous_close == 0:
            return 0.0
        return ((latest_price - previous_close) / previous_close) * 100

    def _to_float(self, value: Any) -> float:
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _to_int(self, value: Any) -> int:
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value)
