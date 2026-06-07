"""MockMarketDataClient — deterministic mock for smoke tests and local dev.

Produces predictable data so smoke tests do not require real Longbridge OAuth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from random import Random
from typing import Any

from clients.market_data_client import (
    Candle,
    FundamentalData,
    IntradayPoint,
    MarketDataClient,
    MarketStatusInfo,
    Quote,
)

_MOCK_QUOTES: dict[str, tuple[float, float, int]] = {
    "QQQ": (445.20, 436.10, 63_000_000),
    "SGOV": (100.48, 100.46, 3_200_000),
    "HSBC.US": (43.35, 44.52, 2_600_000),
    "VIX": (24.80, 21.60, 0),
}

_MOCK_MARKETS: dict[str, dict[str, Any]] = {
    "US": {
        "is_open": True,
        "session": "regular",
        "current_session_open": "2026-06-05T09:30:00-04:00",
        "current_session_close": "2026-06-05T16:00:00-04:00",
        "last_close": "2026-06-04T16:00:00-04:00",
        "next_open": "2026-06-08T09:30:00-04:00",
        "next_close": "2026-06-08T16:00:00-04:00",
    },
    "HK": {
        "is_open": True,
        "session": "regular",
        "current_session_open": "2026-06-05T09:30:00+08:00",
        "current_session_close": "2026-06-05T16:00:00+08:00",
        "last_close": "2026-06-04T16:00:00+08:00",
        "next_open": "2026-06-08T09:30:00+08:00",
        "next_close": "2026-06-08T16:00:00+08:00",
    },
}


class MockMarketDataClient(MarketDataClient):
    """Deterministic mock market data provider for smoke tests.

    No network calls. No credentials needed.
    Uses seeded Random for consistent output per symbol.
    """

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed

    @property
    def provider_name(self) -> str:
        return "mock"

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider": "mock", "detail": "mock client always healthy"}

    def _infer_market(self, symbol: str) -> str:
        return "HK" if symbol.upper().endswith(".HK") else "US"

    def _mock_values(self, symbol: str) -> tuple[float, float, int]:
        known = _MOCK_QUOTES.get(symbol)
        if known:
            return known
        rng = Random(f"{self._seed}:{symbol}")
        prev = round(rng.uniform(20, 250), 2)
        move = rng.uniform(-3.0, 3.0)
        latest = prev * (1 + move / 100)
        vol = rng.randint(500_000, 8_000_000)
        return latest, prev, vol

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        results = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for symbol in symbols:
            market = self._infer_market(symbol)
            latest, prev, vol = self._mock_values(symbol)
            change = ((latest - prev) / prev * 100) if prev else 0.0
            results.append(Quote(
                symbol=symbol,
                market=market,
                latest_price=round(latest, 4),
                previous_close=round(prev, 4),
                change_percent=round(change, 2),
                open=prev,
                high=max(latest, prev),
                low=min(latest, prev),
                volume=vol,
                turnover=vol * latest,
                bid=round(latest * 0.9999, 4),
                ask=round(latest * 1.0001, 4),
                trade_status="normal",
                currency="HKD" if market == "HK" else "USD",
                timestamp=now,
                source="mock",
            ))
        return results

    def get_candles(self, symbols: list[str], period: str = "day", count: int = 20) -> list[Candle]:
        results = []
        rng = Random(self._seed)
        for symbol in symbols:
            market = self._infer_market(symbol)
            base_price = self._mock_values(symbol)[0]
            for i in range(count):
                day_offset = count - i
                open_p = round(base_price * (1 + rng.uniform(-2, 2) / 100), 4)
                close_p = round(open_p * (1 + rng.uniform(-1.5, 1.5) / 100), 4)
                results.append(Candle(
                    symbol=symbol,
                    market=market,
                    close=close_p,
                    open=open_p,
                    low=min(open_p, close_p) * 0.99,
                    high=max(open_p, close_p) * 1.01,
                    volume=rng.randint(500_000, 80_000_000),
                    turnover=close_p * rng.randint(500_000, 80_000_000),
                    timestamp=f"2026-06-{day_offset:02d}",
                    trade_session="regular",
                    source="mock",
                ))
        return results

    def get_intraday(self, symbols: list[str]) -> list[IntradayPoint]:
        results = []
        now = datetime.now(timezone.utc)
        rng = Random(self._seed)
        for symbol in symbols:
            market = self._infer_market(symbol)
            base_price = self._mock_values(symbol)[0]
            for i in range(24):
                point_time = now.replace(hour=i, minute=0, second=0, microsecond=0)
                results.append(IntradayPoint(
                    symbol=symbol,
                    market=market,
                    price=round(base_price * (1 + rng.uniform(-1, 1) / 100), 4),
                    volume=rng.randint(100_000, 5_000_000),
                    turnover=base_price * rng.randint(100_000, 5_000_000),
                    timestamp=point_time.isoformat(timespec="seconds"),
                    source="mock",
                ))
        return results

    def get_market_status(self, markets: list[str]) -> list[MarketStatusInfo]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results = []
        for market in markets:
            info = _MOCK_MARKETS.get(market.upper(), {"is_open": False, "session": "closed"})
            results.append(MarketStatusInfo(
                market=market.upper(),
                is_open=info.get("is_open", False),
                session=str(info.get("session", "closed")),
                current_session_open=info.get("current_session_open", ""),
                current_session_close=info.get("current_session_close", ""),
                last_close=info.get("last_close", ""),
                next_open=info.get("next_open", ""),
                next_close=info.get("next_close", ""),
                timestamp=now,
                source="mock",
            ))
        return results

    def get_fundamentals(self, symbols: list[str]) -> list[FundamentalData]:
        results = []
        for symbol in symbols:
            market = self._infer_market(symbol)
            results.append(FundamentalData(
                symbol=symbol,
                market=market,
                name_en=f"{symbol} Mock Corp",
                name_cn=f"{symbol} 模拟公司",
                exchange="NASDAQ" if market == "US" else "HKEX",
                currency="USD" if market == "US" else "HKD",
                lot_size=100,
                eps=round(self._mock_values(symbol)[0] / 20, 2),
                eps_ttm=round(self._mock_values(symbol)[0] / 20, 2),
                bps=round(self._mock_values(symbol)[0] / 5, 2),
                dividend_yield=1.5,
                board="main",
                source="mock",
            ))
        return results
