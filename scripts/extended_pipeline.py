from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from clients.market_client import MarketClient
from core.extended_session import get_us_extended_window, should_collect_us_extended
from core.loader import load_symbols
from core.runtime_support import setup_logger
from core.time_model import iso_utc, market_timezone_name, normalize_source_timestamp


DEFAULT_EXTENDED_SYMBOLS = ["QQQ.US", "SPY.US", "AAPL.US", "NVDA.US", "TSLA.US", "GOOG.US"]
DEFAULT_INTERVAL_SECONDS = 1800


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect US extended-session quote data.")
    parser.add_argument("--once", action="store_true", help="Run one collection cycle and exit.")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    return parser.parse_args()


def extended_symbols() -> list[str]:
    path = PROJECT_ROOT / "config" / "symbols.json"
    if not path.exists():
        return DEFAULT_EXTENDED_SYMBOLS

    rows = load_symbols(path)
    configured = []
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        sessions = {str(item).lower() for item in row.get("sessions", ["regular"])}
        if (
            symbol in DEFAULT_EXTENDED_SYMBOLS
            and row.get("market", "US") == "US"
            and row.get("liquidity_class", "high") in {"", "high"}
            and "extended" in sessions
        ):
            configured.append(symbol)
    return configured or DEFAULT_EXTENDED_SYMBOLS


def append_extended_records(
    records: list[dict[str, Any]],
    provider: str,
    collected_at: datetime,
    output_dir: Path,
) -> Path | None:
    window = get_us_extended_window(collected_at)
    output_path = output_dir / "data" / "raw" / "US" / "extended" / f"{window.session_window_id}.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    collected_at_text = iso_utc(collected_at)

    count = 0
    with output_path.open("a", encoding="utf-8") as file:
        for record in records:
            source_raw, timezone_name, source_utc = normalize_source_timestamp(
                record.get("source_timestamp_raw") or record.get("timestamp") or record.get("event_time"),
                "US",
            )
            raw_line = {
                "market": "US",
                "market_timezone": timezone_name or market_timezone_name("US"),
                "session": "extended",
                "trading_date": window.trading_date,
                "collected_at": collected_at_text,
                "collected_at_utc": collected_at_text,
                "session_window_id": window.session_window_id,
                "source_timestamp_raw": source_raw,
                "source_timestamp_utc": source_utc,
                "provider": provider,
                **record,
            }
            raw_line["market"] = "US"
            raw_line["session"] = "extended"
            raw_line["trading_date"] = window.trading_date
            raw_line["session_window_id"] = window.session_window_id
            raw_line["market_timezone"] = timezone_name or market_timezone_name("US")
            raw_line["collected_at_utc"] = collected_at_text
            raw_line["source_timestamp_raw"] = source_raw
            raw_line["source_timestamp_utc"] = source_utc
            file.write(json.dumps(raw_line, ensure_ascii=False, default=str))
            file.write("\n")
            count += 1
    return output_path if count else None


def collect_once(logger) -> Path | None:
    now = datetime.now(UTC)
    if not should_collect_us_extended(now):
        logger.info("skip extended collect because US regular session is open")
        return None

    provider = os.getenv("MARKET_DATA_PROVIDER", "mock")
    client = MarketClient(provider=provider)
    symbols = extended_symbols()
    records = client.fetch_realtime_quotes(symbols)
    output_path = append_extended_records(records, client.provider, now, PROJECT_ROOT)
    if output_path:
        logger.info("collected extended quotes: symbols=%s path=%s", len(records), output_path)
    else:
        logger.info("extended collect produced no records")
    return output_path


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    logger = setup_logger("extended_pipeline", "extended_pipeline.log")

    while True:
        try:
            collect_once(logger)
        except Exception as exc:
            logger.exception("extended collection failed: %s", exc)
        if args.once:
            return
        time.sleep(max(args.interval_seconds, 60))


if __name__ == "__main__":
    main()
