"""MCP data cleaner — normalizes and cleans raw market data.

Transforms raw MCP data into a consistent, clean format.
Handles missing fields, type coercion, and outlier filtering.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from clients.market_data_client import (
    Candle,
    IntradayPoint,
    MarketReportDataset,
    Quote,
)

logger = logging.getLogger(__name__)

# ── Outlier thresholds ───────────────────────────────────────────

MAX_PRICE_CHANGE_PCT = 50.0  # Ignore price changes > 50% (likely bad data)
MIN_PRICE = 0.001
MAX_PRICE = 1_000_000.0
MAX_VOLUME = 10_000_000_000  # 10B shares — cap at suspicious levels


class McpDataCleaner:
    """Cleans raw MarketReportDataset: normalizes fields, removes outliers."""

    def clean(self, dataset: MarketReportDataset) -> MarketReportDataset:
        """Clean in-place and return the same dataset."""
        dataset.quotes = [self._clean_quote(q) for q in dataset.quotes if self._is_valid_quote(q)]
        dataset.candles = [self._clean_candle(c) for c in dataset.candles if self._is_valid_candle(c)]
        dataset.intraday = [self._clean_intraday(p) for p in dataset.intraday if self._is_valid_intraday(p)]
        logger.info(
            "Cleaned dataset %s: %d quotes, %d candles, %d intraday",
            dataset.run_id,
            len(dataset.quotes),
            len(dataset.candles),
            len(dataset.intraday),
        )
        return dataset

    def _is_valid_quote(self, q: Quote) -> bool:
        if q.latest_price <= MIN_PRICE or q.latest_price > MAX_PRICE:
            logger.debug("Quote %s filtered: invalid price %s", q.symbol, q.latest_price)
            return False
        if q.volume < 0 or q.volume > MAX_VOLUME:
            logger.debug("Quote %s filtered: invalid volume %s", q.symbol, q.volume)
            return False
        if abs(q.change_percent) > MAX_PRICE_CHANGE_PCT:
            logger.debug("Quote %s filtered: excessive change %s%%", q.symbol, q.change_percent)
            return False
        if not q.timestamp:
            logger.debug("Quote %s filtered: missing timestamp", q.symbol)
            return False
        return True

    def _is_valid_candle(self, c: Candle) -> bool:
        if c.close <= MIN_PRICE or c.close > MAX_PRICE:
            return False
        if c.volume < 0 or c.volume > MAX_VOLUME:
            return False
        if not c.timestamp:
            return False
        return True

    def _is_valid_intraday(self, p: IntradayPoint) -> bool:
        if p.price <= MIN_PRICE or p.price > MAX_PRICE:
            return False
        if not p.timestamp:
            return False
        return True

    def _clean_quote(self, q: Quote) -> Quote:
        return Quote(
            symbol=q.symbol.strip().upper(),
            market=q.market,
            latest_price=round(q.latest_price, 4),
            previous_close=round(q.previous_close, 4),
            change_percent=round(q.change_percent, 2),
            open=round(q.open, 4) if q.open else 0.0,
            high=round(q.high, 4) if q.high else 0.0,
            low=round(q.low, 4) if q.low else 0.0,
            volume=max(0, int(q.volume)),
            turnover=round(q.turnover, 4) if q.turnover else 0.0,
            bid=round(q.bid, 4) if q.bid else 0.0,
            ask=round(q.ask, 4) if q.ask else 0.0,
            trade_status=q.trade_status or "unknown",
            currency=q.currency or ("HKD" if q.market == "HK" else "USD"),
            timestamp=q.timestamp or datetime.now(timezone.utc).isoformat(),
            source=q.source,
        )

    def _clean_candle(self, c: Candle) -> Candle:
        return Candle(
            symbol=c.symbol.strip().upper(),
            market=c.market,
            close=round(c.close, 4),
            open=round(c.open, 4),
            low=round(c.low, 4),
            high=round(c.high, 4),
            volume=max(0, int(c.volume)),
            turnover=round(c.turnover, 4) if c.turnover else 0.0,
            timestamp=c.timestamp,
            trade_session=c.trade_session or "unknown",
            source=c.source,
        )

    def _clean_intraday(self, p: IntradayPoint) -> IntradayPoint:
        return IntradayPoint(
            symbol=p.symbol.strip().upper(),
            market=p.market,
            price=round(p.price, 4),
            volume=max(0, int(p.volume)),
            turnover=round(p.turnover, 4) if p.turnover else 0.0,
            timestamp=p.timestamp,
            source=p.source,
        )
