"""MCP data collector — collects raw market data via MarketDataClient.

Saves raw snapshots to data/raw/{market}/{date}/.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clients.market_data_client import (
    MarketDataClient,
    MarketReportDataset,
)

logger = logging.getLogger(__name__)

DEFAULT_RAW_DIR = "data/raw"


class McpDataCollector:
    """Collects raw market data from any MarketDataClient implementation.

    Saves raw snapshots as JSONL under data/raw/{market}/{YYYY-MM-DD}/.
    """

    def __init__(
        self,
        client: MarketDataClient,
        raw_dir: str = DEFAULT_RAW_DIR,
    ) -> None:
        self._client = client
        self._raw_dir = Path(raw_dir)

    def collect(
        self,
        symbols: list[str],
        market: str,
        report_type: str = "intraday_brief",
        include_fundamentals: bool = False,
    ) -> MarketReportDataset:
        """Collect all data for a report run.

        Returns a MarketReportDataset with raw data.
        Does NOT validate — that happens separately.
        """
        run_id = str(uuid.uuid4())
        collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        dataset = MarketReportDataset(
            run_id=run_id,
            report_type=report_type,
            market=market,
            symbols=symbols,
            collected_at=collected_at,
        )

        # ── Quotes ───────────────────────────────────────────────
        try:
            dataset.quotes = self._client.get_quotes(symbols)
            logger.info("Collected %d quotes for %s", len(dataset.quotes), market)
        except Exception as exc:
            logger.error("Quote collection failed for %s: %s", market, exc)

        # ── Candles ──────────────────────────────────────────────
        try:
            dataset.candles = self._client.get_candles(symbols, count=20)
            logger.info("Collected %d candles for %s", len(dataset.candles), market)
        except Exception as exc:
            logger.error("Candle collection failed for %s: %s", market, exc)

        # ── Intraday ─────────────────────────────────────────────
        try:
            dataset.intraday = self._client.get_intraday(symbols)
            logger.info("Collected %d intraday points for %s", len(dataset.intraday), market)
        except Exception as exc:
            logger.error("Intraday collection failed for %s: %s", market, exc)

        # ── Market status ────────────────────────────────────────
        try:
            statuses = self._client.get_market_status([market])
            if statuses:
                dataset.market_status = statuses[0]
        except Exception as exc:
            logger.error("Market status collection failed for %s: %s", market, exc)

        # ── Fundamentals (optional) ──────────────────────────────
        if include_fundamentals:
            try:
                dataset.fundamentals = self._client.get_fundamentals(symbols)
            except Exception as exc:
                logger.error("Fundamentals collection failed: %s", exc)

        # ── Save raw snapshot ────────────────────────────────────
        self._save_raw(dataset)

        return dataset

    def _save_raw(self, dataset: MarketReportDataset) -> None:
        """Append raw dataset as JSONL to data/raw/{market}/{date}/collection.jsonl."""
        trading_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_dir = self._raw_dir / dataset.market / trading_date
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "collection.jsonl"

        record = dataset.to_dict()
        record["_saved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        try:
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info("Raw snapshot saved: %s", output_file)
        except Exception as exc:
            logger.error("Failed to save raw snapshot: %s", exc)
