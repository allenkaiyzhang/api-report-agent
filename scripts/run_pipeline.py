from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from clients.market_client import MarketClient
from core.ai_analyzer import AIAnalysisConfig
from core.email_reporter import EmailConfig, build_daily_report_notification, build_intraday_report_notification
from core.loader import load_symbols
from core.market_calendar import (
    get_market_local_now,
    get_trading_date,
    should_build_daily,
    should_collect_market,
)
from core.market_data_store import DailyJsonlMarketDataStore
from core.reference_data_store import ReferenceDataStore, filter_symbols_by_market
from core.trading_hours import filter_symbols_by_open_markets
from core.data_pipeline import (
    BASE_DIR,
    daily_day,
    get_market_windows,
    load_jsonl,
    metrics_dir,
    normalize_day,
    normalized_file_path,
    quality_day,
    raw_file_path,
    windows_day,
    write_json_atomic,
    build_window_metrics,
)
from core.runtime_support import RuntimeState, setup_logger
from core.notification import notify
from scripts.market_data_collector import MarketDataCollector


COLLECT_RETRY_ATTEMPTS = 3
COLLECT_RETRY_SECONDS = 5
DAILY_BUILD_DELAY_MINUTES = 10
DEFAULT_INTRADAY_EMAIL_INTERVAL_HOURS = 2


def market_date(market: str, now: datetime) -> str:
    return get_trading_date(market, now)


def collect_job(
    collector: MarketDataCollector,
    symbols: list[str],
    markets: list[str],
    now: datetime,
    state: RuntimeState,
    logger,
) -> None:
    for market in markets:
        collect_market(collector, symbols, market, now, state, logger)


def build_reference_for_open_markets(
    market_client: MarketClient,
    store: ReferenceDataStore,
    symbols: list[str],
    markets: list[str],
    now: datetime,
    logger,
    force: bool = False,
    build_on_market_open: bool = True,
) -> None:
    if not build_on_market_open:
        return

    for market in markets:
        trading_date = market_date(market, now)
        if not should_collect_market(market, now):
            continue
        path = store.reference_path(market, trading_date)
        if path.exists() and not force:
            logger.info("skip reference because file exists: %s %s", market, trading_date)
            continue

        market_symbols = filter_symbols_by_market(symbols, market)
        if not market_symbols:
            logger.info("skip reference because no symbols configured: %s %s", market, trading_date)
            continue

        try:
            reference_data = market_client.fetch_reference_data(market_symbols)
            output_path = store.write_reference(
                market=market,
                trading_date=trading_date,
                provider=market_client.provider,
                symbols=market_symbols,
                reference_data=reference_data,
            )
            logger.info("built reference data: %s", output_path)
        except Exception as exc:
            logger.exception("reference build failed for %s %s", market, trading_date)


def collect_market(
    collector: MarketDataCollector,
    symbols: list[str],
    market: str,
    now: datetime,
    state: RuntimeState,
    logger,
) -> None:
    local_time = get_market_local_now(market, now).isoformat(timespec="seconds")
    trading_date = market_date(market, now)
    if not should_collect_market(market, now):
        logger.info("skip collect because market closed: %s local_time=%s", market, local_time)
        return

    logger.info("collect market: %s local_time=%s", market, local_time)
    target_symbols = filter_symbols_by_open_markets(symbols, [market])
    if not target_symbols:
        logger.info("skip collect because no symbols configured: %s local_time=%s", market, local_time)
        return

    for attempt in range(1, COLLECT_RETRY_ATTEMPTS + 1):
        try:
            output_paths = collector.run_once(target_symbols, now=now)
            if output_paths:
                for output_path in output_paths:
                    output_market = output_path.parent.parent.name if output_path.parent.name in {"regular", "extended"} else output_path.parent.name
                    output_date = output_path.stem
                    state.record_collect_success(output_market, output_date, str(output_path))
            return
        except Exception as exc:
            logger.exception("collect attempt %s failed for %s", attempt, market)
            state.record_collect_failure(market, trading_date, exc)
            time.sleep(COLLECT_RETRY_SECONDS)


def normalize_existing_raw(markets: list[str], now: datetime, state: RuntimeState, logger) -> None:
    for market in markets:
        trading_date = market_date(market, now)
        if not raw_file_path(BASE_DIR, market, trading_date).exists():
            logger.info("skip normalize because raw file missing: %s %s", market, trading_date)
            continue
        try:
            normalize_day(market, trading_date)
            state.mark_success("last_normalize_time")
        except Exception as exc:
            logger.exception("normalize failed for %s %s", market, trading_date)
            state.record_error("normalize", exc)


def build_finished_windows(
    markets: list[str],
    now: datetime,
    state: RuntimeState,
    logger,
    force: bool = False,
    collector_started_at: datetime | None = None,
) -> None:
    for market in markets:
        trading_date = market_date(market, now)
        normalized_path = normalized_file_path(BASE_DIR, market, trading_date)
        if not normalized_path.exists():
            logger.info("skip metrics because normalized file missing: %s %s", market, trading_date)
            continue

        try:
            normalized = load_jsonl(normalized_path)
            windows_day(market, trading_date)
            for window in get_market_windows(market, trading_date):
                if now.astimezone(window.end.tzinfo) < window.end:
                    continue
                output_path = metrics_dir(BASE_DIR, market, trading_date) / f"window_{window.window_id}.json"
                if output_path.exists() and not force:
                    continue
                metric = build_window_metrics(
                    normalized.records,
                    market,
                    trading_date,
                    window,
                    source_normalized_file=str(normalized_path),
                    session_metadata=build_session_metadata(collector_started_at),
                )
                write_json_atomic(output_path, metric)
                state.mark_window_done(market, trading_date, window.window_id)
                logger.info("built window metrics: %s", output_path)
        except Exception as exc:
            logger.exception("metrics failed for %s %s", market, trading_date)
            state.record_error("metrics", exc)


def build_daily_after_close(
    markets: list[str],
    now: datetime,
    state: RuntimeState,
    logger,
    force: bool = False,
    email_config: EmailConfig | None = None,
    ai_config: AIAnalysisConfig | None = None,
) -> None:
    for market in markets:
        trading_date = market_date(market, now)
        windows = get_market_windows(market, trading_date)
        if not windows:
            continue
        last_end = windows[-1].end + timedelta(minutes=DAILY_BUILD_DELAY_MINUTES)
        if now.astimezone(last_end.tzinfo) < last_end:
            logger.info("skip daily because not after close: %s %s", market, trading_date)
            continue
        output_dir = metrics_dir(BASE_DIR, market, trading_date)
        if not output_dir.exists():
            logger.info("skip daily because metrics directory missing: %s %s", market, trading_date)
            continue
        daily_path = output_dir / "daily.json"
        quality_path = BASE_DIR / "data" / "quality" / market / f"{trading_date}.json"
        try:
            if not (daily_path.exists() and quality_path.exists() and not force):
                if not should_build_daily(market, now, BASE_DIR, force_rebuild=force):
                    logger.info("skip daily because no build needed or input missing: %s %s", market, trading_date)
                    continue
                daily_day(market, trading_date)
                quality_day(market, trading_date)
                state.mark_daily_done(market, trading_date)
                logger.info("built daily and quality: %s %s", daily_path, quality_path)
            send_daily_report_after_close(email_config, ai_config, market, trading_date, daily_path, quality_path, state, logger)
        except FileNotFoundError as exc:
            logger.info("daily skipped because input missing: %s", exc)
        except Exception as exc:
            logger.exception("daily/quality failed for %s %s", market, trading_date)
            state.record_error("daily_quality", exc)


def send_daily_report_after_close(
    email_config: EmailConfig | None,
    ai_config: AIAnalysisConfig | None,
    market: str,
    trading_date: str,
    daily_path: Path,
    quality_path: Path,
    state: RuntimeState,
    logger,
) -> None:
    if state.email_report_sent(market, trading_date):
        logger.info("skip notification because report already sent: %s %s", market, trading_date)
        return
    if not (daily_path.exists() and quality_path.exists()):
        logger.info("skip notification because daily or quality missing: %s %s", market, trading_date)
        return

    try:
        config = email_config or EmailConfig.from_env(os.environ)
        title, body, payload = build_daily_report_notification(config, BASE_DIR, market, trading_date, ai_config=ai_config)
        result = notify(
            title=title,
            body=body,
            level="info",
            metadata={
                "type": "daily_report",
                "market": market,
                "trading_date": trading_date,
                "payload": payload,
            },
        )
        state.mark_email_report_sent(market, trading_date)
        logger.info("sent daily notification: %s %s results=%s", market, trading_date, result.get("results", {}))
    except Exception as exc:
        logger.warning("daily notification failed for %s %s: %s", market, trading_date, exc)
        state.mark_email_report_failed(market, trading_date, str(exc))


def send_intraday_reports(
    markets: list[str],
    now: datetime,
    state: RuntimeState,
    logger,
    email_config: EmailConfig | None,
    ai_config: AIAnalysisConfig | None,
    interval_hours: int = DEFAULT_INTRADAY_EMAIL_INTERVAL_HOURS,
) -> None:
    for market in markets:
        window = intraday_email_window(market, now, interval_hours)
        if window is None:
            continue
        trading_date, period_start, period_end = window
        key = intraday_email_key(market, trading_date, period_start, period_end)
        if state.intraday_email_report_sent(key):
            continue
        if state.intraday_email_report_failed(key):
            logger.info("skip intraday notification because previous send failed: %s", key)
            continue
        if not (raw_file_path(BASE_DIR, market, trading_date).exists() and normalized_file_path(BASE_DIR, market, trading_date).exists()):
            logger.info("skip intraday notification because raw or normalized missing: %s", key)
            continue

        try:
            config = email_config or EmailConfig.from_env(os.environ)
            title, body, payload = build_intraday_report_notification(
                config,
                BASE_DIR,
                market,
                trading_date,
                period_start,
                period_end,
                ai_config=ai_config,
            )
            result = notify(
                title=title,
                body=body,
                level="info",
                metadata={
                    "type": "intraday_report",
                    "market": market,
                    "trading_date": trading_date,
                    "period_start": period_start.isoformat(timespec="seconds"),
                    "period_end": period_end.isoformat(timespec="seconds"),
                    "payload": payload,
                },
            )
            state.mark_intraday_email_report_sent(key)
            logger.info("sent intraday notification: %s results=%s", key, result.get("results", {}))
        except Exception as exc:
            logger.warning("intraday notification failed for %s: %s", key, exc)
            state.mark_intraday_email_report_failed(key, str(exc))


def intraday_email_window(
    market: str,
    now: datetime,
    interval_hours: int = DEFAULT_INTRADAY_EMAIL_INTERVAL_HOURS,
) -> tuple[str, datetime, datetime] | None:
    if interval_hours <= 0 or not should_collect_market(market, now):
        return None
    trading_date = market_date(market, now)
    windows = get_market_windows(market, trading_date)
    if not windows:
        return None
    local_now = get_market_local_now(market, now)
    first_start = windows[0].start
    interval = timedelta(hours=interval_hours)
    if local_now < first_start + interval:
        return None
    elapsed_intervals = int((local_now - first_start).total_seconds() // interval.total_seconds())
    period_end = first_start + elapsed_intervals * interval
    period_start = period_end - interval
    return trading_date, period_start, period_end


def intraday_email_key(market: str, trading_date: str, period_start: datetime, period_end: datetime) -> str:
    return f"{market}:{trading_date}:{period_start:%H%M}_{period_end:%H%M}"


def build_session_metadata(collector_started_at: datetime | None) -> dict[str, object]:
    if collector_started_at is None:
        return {
            "collector_started_at": "",
            "collector_uptime_seconds": None,
            "pipeline_restart_detected": False,
        }
    return {
        "collector_started_at": collector_started_at.isoformat(timespec="seconds"),
        "collector_uptime_seconds": int((datetime.now(UTC) - collector_started_at).total_seconds()),
        "pipeline_restart_detected": False,
    }


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    logger = setup_logger("pipeline", "pipeline.log")
    setup_logger("collect", "collect.log")
    setup_logger("normalize", "normalize.log")
    setup_logger("metrics", "metrics.log")
    setup_logger("quality", "quality.log")

    state = RuntimeState()
    state.set_status("running")

    symbols = [row["symbol"] for row in load_symbols(BASE_DIR / "config" / "symbols.json")]
    provider = os.getenv("MARKET_DATA_PROVIDER", "longbridge")
    interval_seconds = int(os.getenv("DATA_COLLECTION_INTERVAL_SECONDS", "120"))
    force_rebuild = os.getenv("PIPELINE_FORCE_REBUILD", "false").lower() == "true"
    reference_force_rebuild = os.getenv("REFERENCE_FORCE_REBUILD", "false").lower() == "true"
    reference_build_on_market_open = os.getenv("REFERENCE_BUILD_ON_MARKET_OPEN", "true").lower() == "true"
    email_config = EmailConfig.from_env(os.environ)
    ai_config = AIAnalysisConfig.from_env(os.environ)
    intraday_email_enabled = os.getenv("EMAIL_INTRADAY_ENABLED", "true").lower() == "true"
    intraday_email_interval_hours = int(os.getenv("EMAIL_INTRADAY_INTERVAL_HOURS", "2") or "2")
    loop_sleep = int(os.getenv("PIPELINE_LOOP_SLEEP_SECONDS", "10"))
    output_dir = Path(os.getenv("DATA_COLLECTION_OUTPUT_DIR", "data/raw"))
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    market_client = MarketClient(provider=provider)
    collector = MarketDataCollector(
        market_client=market_client,
        store=DailyJsonlMarketDataStore(output_dir=output_dir),
        interval_seconds=interval_seconds,
    )
    reference_store = ReferenceDataStore(BASE_DIR)

    collector_started_at = datetime.now(UTC)
    last_collect = datetime.min.replace(tzinfo=UTC)
    markets = ["HK", "US"]
    logger.info("pipeline started provider=%s interval=%ss", provider, interval_seconds)

    while True:
        now = datetime.now(UTC)
        try:
            if (now - last_collect).total_seconds() >= interval_seconds:
                build_reference_for_open_markets(
                    market_client=market_client,
                    store=reference_store,
                    symbols=symbols,
                    markets=markets,
                    now=now,
                    logger=logger,
                    force=reference_force_rebuild,
                    build_on_market_open=reference_build_on_market_open,
                )
                collect_job(collector, symbols, markets, now, state, logger)
                normalize_existing_raw(markets, now, state, logger)
                if intraday_email_enabled:
                    send_intraday_reports(
                        markets=markets,
                        now=now,
                        state=state,
                        logger=logger,
                        email_config=email_config,
                        ai_config=ai_config,
                        interval_hours=intraday_email_interval_hours,
                    )
                last_collect = now

            build_finished_windows(markets, now, state, logger, force=force_rebuild, collector_started_at=collector_started_at)
            build_daily_after_close(markets, now, state, logger, force=force_rebuild, email_config=email_config, ai_config=ai_config)
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
