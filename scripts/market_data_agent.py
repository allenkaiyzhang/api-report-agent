from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from clients.market_client import MarketClient
from core.loader import load_symbols
from core.market_data_store import DailyJsonlMarketDataStore
from core.trading_hours import filter_symbols_by_open_markets, open_markets


BASE_DIR = Path(__file__).resolve().parents[1]


class MarketDataCollectorAgent:
    def __init__(
        self,
        market_client: MarketClient,
        store: DailyJsonlMarketDataStore,
        interval_seconds: int = 120,
        clock_timezone: str = "Asia/Shanghai",
    ) -> None:
        self.market_client = market_client
        self.store = store
        self.interval_seconds = interval_seconds
        self.clock_timezone = ZoneInfo(clock_timezone)

    def run_forever(self, symbols: list[str]) -> None:
        print(
            f"Market data collector started. interval={self.interval_seconds}s, symbols={len(symbols)}"
        )
        while True:
            self.run_once(symbols)
            time.sleep(self.interval_seconds)

    def run_once(self, symbols: list[str], now: datetime | None = None) -> list[Path]:
        collected_at = now or datetime.now(UTC)
        markets = open_markets(collected_at)
        target_symbols = filter_symbols_by_open_markets(symbols, markets)

        if not target_symbols:
            local_time = collected_at.astimezone(self.clock_timezone).isoformat(timespec="seconds")
            print(f"No HK/US market is open at {local_time}; skipped API collection.")
            return []

        raw_records = self.market_client.fetch_quotes(target_symbols)
        output_paths = self.store.append_raw_records(
            records=raw_records,
            collected_at=collected_at,
            provider=self.market_client.provider,
        )
        print(
            f"Collected {len(raw_records)} raw records for {', '.join(markets)} market(s): "
            f"{', '.join(str(path) for path in output_paths)}"
        )
        return output_paths

    def _build_snapshot(
        self,
        collected_at: datetime,
        open_market_names: list[str],
        records: list[dict[str, Any]],
        raw_count: int,
        quality_issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "collected_at_utc": collected_at.astimezone(UTC).isoformat(timespec="seconds"),
            "collected_at_local": collected_at.astimezone(self.clock_timezone).isoformat(timespec="seconds"),
            "open_markets": open_market_names,
            "provider": self.market_client.provider,
            "raw_count": raw_count,
            "cleaned_count": len(records),
            "quality_issues": quality_issues,
            "records": records,
        }


def main() -> None:
    load_dotenv(BASE_DIR / ".env")

    symbols = load_symbols(BASE_DIR / "config" / "symbols.csv")
    symbol_codes = [row["symbol"] for row in symbols]

    provider = os.getenv("MARKET_DATA_PROVIDER", "mock")
    interval_seconds = int(os.getenv("DATA_COLLECTION_INTERVAL_SECONDS", "120"))
    output_dir = Path(os.getenv("DATA_COLLECTION_OUTPUT_DIR", "data/raw"))
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    file_timezone = os.getenv("DATA_COLLECTION_FILE_TIMEZONE", "Asia/Shanghai")
    run_once = os.getenv("DATA_COLLECTION_RUN_ONCE", "false").lower() == "true"

    agent = MarketDataCollectorAgent(
        market_client=MarketClient(provider=provider),
        store=DailyJsonlMarketDataStore(output_dir=output_dir, file_timezone=file_timezone),
        interval_seconds=interval_seconds,
        clock_timezone=file_timezone,
    )

    if run_once:
        agent.run_once(symbol_codes)
        return

    agent.run_forever(symbol_codes)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("Market data collector stopped by user.")
    except Exception as exc:
        raise SystemExit(f"Market data collector failed: {exc}") from exc
