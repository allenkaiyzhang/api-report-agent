from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from clients.market_client import MarketClient
from core.loader import load_symbols
from core.market_data_store import DailyJsonlMarketDataStore
from core.trading_hours import filter_symbols_by_open_markets, open_markets
from core.data_pipeline import (
    BASE_DIR,
    daily_day,
    get_market_windows,
    load_jsonl,
    metrics_dir,
    normalize_day,
    normalized_file_path,
    quality_day,
    windows_day,
    write_json_atomic,
    build_window_metrics,
)
from core.runtime_support import RuntimeState, setup_logger
from scripts.market_data_agent import MarketDataCollectorAgent


COLLECT_RETRY_ATTEMPTS = 3
COLLECT_RETRY_SECONDS = 5
DAILY_BUILD_DELAY_MINUTES = 10


def market_date(market: str, now: datetime) -> str:
    timezone = {"HK": "Asia/Hong_Kong", "US": "America/New_York"}[market]
    from zoneinfo import ZoneInfo

    return now.astimezone(ZoneInfo(timezone)).date().isoformat()


def collect_job(agent: MarketDataCollectorAgent, symbols: list[str], state: RuntimeState, logger) -> None:
    for attempt in range(1, COLLECT_RETRY_ATTEMPTS + 1):
        try:
            output_paths = agent.run_once(symbols)
            if output_paths:
                for output_path in output_paths:
                    market = output_path.parent.name
                    trading_date = output_path.stem
                    state.record_collect_success(market, trading_date, str(output_path))
            return
        except Exception as exc:
            logger.exception("collect attempt %s failed", attempt)
            active_markets = open_markets(datetime.now(UTC))
            if active_markets:
                for market in active_markets:
                    state.record_collect_failure(market, market_date(market, datetime.now(UTC)), exc)
            else:
                state.record_error("collect", exc)
            time.sleep(COLLECT_RETRY_SECONDS)


def normalize_existing_raw(markets: list[str], now: datetime, state: RuntimeState, logger) -> None:
    for market in markets:
        trading_date = market_date(market, now)
        try:
            normalize_day(market, trading_date)
            state.mark_success("last_normalize_time")
        except FileNotFoundError:
            logger.info("raw file not found for normalize: %s %s", market, trading_date)
        except Exception as exc:
            logger.exception("normalize failed for %s %s", market, trading_date)
            state.record_error("normalize", exc)


def build_finished_windows(markets: list[str], now: datetime, state: RuntimeState, logger, force: bool = False) -> None:
    for market in markets:
        trading_date = market_date(market, now)
        try:
            normalized = load_jsonl(normalized_file_path(BASE_DIR, market, trading_date))
        except FileNotFoundError:
            logger.info("normalized file not found for metrics: %s %s", market, trading_date)
            continue

        try:
            windows_day(market, trading_date)
            for window in get_market_windows(market, trading_date):
                if now.astimezone(window.end.tzinfo) < window.end:
                    continue
                output_path = metrics_dir(BASE_DIR, market, trading_date) / f"window_{window.window_id}.json"
                if output_path.exists() and not force:
                    continue
                metric = build_window_metrics(normalized.records, market, trading_date, window)
                write_json_atomic(output_path, metric)
                state.mark_window_done(market, trading_date, window.window_id)
                logger.info("built window metrics: %s", output_path)
        except Exception as exc:
            logger.exception("metrics failed for %s %s", market, trading_date)
            state.record_error("metrics", exc)


def build_daily_after_close(markets: list[str], now: datetime, state: RuntimeState, logger, force: bool = False) -> None:
    for market in markets:
        trading_date = market_date(market, now)
        windows = get_market_windows(market, trading_date)
        if not windows:
            continue
        last_end = windows[-1].end + timedelta(minutes=DAILY_BUILD_DELAY_MINUTES)
        if now.astimezone(last_end.tzinfo) < last_end:
            continue
        daily_path = metrics_dir(BASE_DIR, market, trading_date) / "daily.json"
        quality_path = BASE_DIR / "data" / "quality" / market / f"{trading_date}.json"
        try:
            if not (daily_path.exists() and quality_path.exists() and not force):
                daily_day(market, trading_date)
                quality_day(market, trading_date)
                state.mark_daily_done(market, trading_date)
                logger.info("built daily and quality: %s %s", daily_path, quality_path)
        except FileNotFoundError as exc:
            logger.info("daily skipped because input missing: %s", exc)
        except Exception as exc:
            logger.exception("daily/quality failed for %s %s", market, trading_date)
            state.record_error("daily_quality", exc)


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    logger = setup_logger("pipeline", "pipeline.log")
    setup_logger("collect", "collect.log")
    setup_logger("normalize", "normalize.log")
    setup_logger("metrics", "metrics.log")
    setup_logger("quality", "quality.log")

    state = RuntimeState()
    state.set_status("running")

    symbols = [row["symbol"] for row in load_symbols(BASE_DIR / "config" / "symbols.csv")]
    provider = os.getenv("MARKET_DATA_PROVIDER", "longbridge")
    interval_seconds = int(os.getenv("DATA_COLLECTION_INTERVAL_SECONDS", "120"))
    force_rebuild = os.getenv("PIPELINE_FORCE_REBUILD", "false").lower() == "true"
    loop_sleep = int(os.getenv("PIPELINE_LOOP_SLEEP_SECONDS", "10"))
    output_dir = Path(os.getenv("DATA_COLLECTION_OUTPUT_DIR", "data/raw"))
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    agent = MarketDataCollectorAgent(
        market_client=MarketClient(provider=provider),
        store=DailyJsonlMarketDataStore(output_dir=output_dir),
        interval_seconds=interval_seconds,
    )

    last_collect = datetime.min.replace(tzinfo=UTC)
    markets = ["HK", "US"]
    logger.info("pipeline started provider=%s interval=%ss", provider, interval_seconds)

    while True:
        now = datetime.now(UTC)
        try:
            if (now - last_collect).total_seconds() >= interval_seconds:
                collect_job(agent, symbols, state, logger)
                active_markets = open_markets(now)
                if active_markets:
                    normalize_existing_raw(active_markets, now, state, logger)
                last_collect = now

            build_finished_windows(markets, now, state, logger, force=force_rebuild)
            build_daily_after_close(markets, now, state, logger, force=force_rebuild)
        except Exception as exc:
            logger.exception("pipeline loop failed")
            state.record_error("pipeline_loop", exc)
        time.sleep(loop_sleep)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        RuntimeState().set_status("stopped")
        raise SystemExit("pipeline stopped by user")
