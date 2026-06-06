#!/usr/bin/env python
"""Market Report Agent — main workflow entry point.

Runs the MCP-based market report workflow:
  Longbridge MCP data collection → data cleaning →
  periodic intraday analysis → post-market daily summary →
  report generation → notification dispatch.

Usage:
  python scripts/market_report_agent.py                     # Run scheduler loop
  python scripts/market_report_agent.py --once              # Single collection + report
  python scripts/market_report_agent.py --health            # Health check
  python scripts/market_report_agent.py --provider mock     # Explicit mock mode
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from clients.market_data_client import MarketDataClient
from clients.mock_market_data_client import MockMarketDataClient
from core.mcp_cleaner import McpDataCleaner
from core.mcp_collector import McpDataCollector
from core.mcp_datastore import McpDataStore
from core.mcp_notifier import (
    CompositeNotifier,
    create_notifiers,
    NotifierResult,
)
from core.mcp_report_generator import ReportGenerator
from core.mcp_scheduler import McpScheduler
from core.mcp_validator import McpDataValidator


def _setup_logging(log_level: str = "INFO") -> None:
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "market_report_agent.log", encoding="utf-8"),
        ],
    )


def _load_env() -> None:
    """Load .env file if present."""
    env_file = _PROJECT_ROOT / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)


def _resolve_provider(args: argparse.Namespace) -> str:
    """Resolve provider with precedence: CLI > env > config file > fail.

    Returns 'mock' or 'longbridge_mcp'.
    Does NOT default to mock in production mode.
    """
    # 1. Explicit CLI argument
    if args.provider:
        return args.provider

    # 2. Environment variable
    env_provider = os.getenv("MARKET_DATA_PROVIDER", "")
    if env_provider:
        return env_provider

    # 3. Config file
    config_paths = [
        _PROJECT_ROOT / "config" / "config.yaml",
        _PROJECT_ROOT / "config" / "registry.yaml",
    ]
    for config_path in config_paths:
        if config_path.exists():
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            provider = cfg.get("provider") or cfg.get("market_data_provider")
            if provider:
                return provider

    # 4. Safe default: only mock if explicitly in test mode
    app_env = os.getenv("APP_ENV", "")
    if app_env == "test":
        return "mock"

    # 5. In production/normal mode, fail clearly
    raise SystemExit(
        "ERROR: No market data provider configured.\n"
        "  Set one of:\n"
        "    - --provider longbridge_mcp\n"
        "    - MARKET_DATA_PROVIDER=longbridge_mcp\n"
        "    - provider field in config/config.yaml\n"
        "  Or use --provider mock for smoke testing only."
    )


def _create_client(args: argparse.Namespace) -> MarketDataClient:
    """Factory: create MarketDataClient based on resolved provider.

    Raises SystemExit if provider is missing in production mode.
    """
    provider = _resolve_provider(args)

    if provider == "mock":
        if not args.provider and os.getenv("APP_ENV", "") != "test":
            logger = logging.getLogger(__name__)
            logger.warning("Using mock provider — only for test/smoke mode")
        return MockMarketDataClient()

    if provider in ("longbridge_mcp", "longbridge"):
        from clients.longbridge_mcp_client import LongbridgeMcpClient
        return LongbridgeMcpClient()

    raise SystemExit(f"ERROR: Unknown provider '{provider}'. Use 'mock' or 'longbridge_mcp'.")


def _get_symbols(market: str | None = None) -> dict[str, list[str]]:
    """Get symbols grouped by market from config."""
    import yaml

    config_paths = [
        _PROJECT_ROOT / "config" / "config.yaml",
        _PROJECT_ROOT / "config" / "registry.yaml",
        _PROJECT_ROOT / "config" / "config.example.yaml",
    ]

    symbols_by_market: dict[str, list[str]] = {}

    for config_path in config_paths:
        if not config_path.exists():
            continue
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        for sym in cfg.get("symbols", []):
            if not isinstance(sym, dict):
                continue
            if not sym.get("enabled", True):
                continue
            m = sym.get("market", "US")
            if market and m != market:
                continue
            symbol_name = sym.get("symbol", "")
            if symbol_name:
                symbols_by_market.setdefault(m, []).append(symbol_name)

        if symbols_by_market:
            break

    # Default fallback only when no config at all
    if not symbols_by_market:
        symbols_by_market = {
            "US": ["QQQ", "SGOV", "HSBC.US", "VIX"],
            "HK": [],
        }

    return symbols_by_market


def _load_config() -> dict:
    """Load runtime configuration from config files."""
    import yaml

    config: dict = {}

    config_paths = [
        _PROJECT_ROOT / "config" / "config.yaml",
        _PROJECT_ROOT / "config" / "config.example.yaml",
    ]
    for path in config_paths:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            config.update(data)

    return config


def _run_report_workflow(
    run_id: str,
    market: str,
    symbols: list[str],
    report_type: str,
    client: MarketDataClient,
    collector: McpDataCollector,
    cleaner: McpDataCleaner,
    validator: McpDataValidator,
    generator: ReportGenerator,
    store: McpDataStore,
    notifier: CompositeNotifier,
) -> bool:
    """Execute a single report workflow cycle.

    Returns True if the workflow completed successfully (including SKIPPED events).
    Returns False only if the workflow failed completely.
    """
    logger = logging.getLogger(__name__)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for symbol in symbols:
        store.log_run(run_id, report_type, market, symbol, "PENDING", started_at)

    try:
        # ── Collect ──────────────────────────────────────────────
        store.log_run(run_id, report_type, market, "*", "RUNNING", started_at)
        dataset = collector.collect(
            symbols=symbols,
            market=market,
            report_type=report_type,
        )
        store.log_run(run_id, report_type, market, "*", "DATA_COLLECTED", started_at)

        # ── Clean ────────────────────────────────────────────────
        dataset = cleaner.clean(dataset)

        # ── Validate ─────────────────────────────────────────────
        dataset = validator.validate(dataset)
        if not dataset.validated:
            logger.warning("Dataset %s validation failed, skipping report", run_id)
            skip_reason = "; ".join(dataset.validation_errors)
            for symbol in symbols:
                store.log_run(
                    run_id, report_type, market, symbol, "SKIPPED",
                    started_at,
                    error_message=skip_reason,
                )
            # SKIPPED is not a failure
            return True
        store.log_run(run_id, report_type, market, "*", "DATA_VALIDATED", started_at)

        # ── Save clean snapshot ──────────────────────────────────
        store.save_clean_snapshot(dataset.to_dict(), market)

        # ── Generate report ──────────────────────────────────────
        if report_type == "intraday_brief":
            content = generator.generate_intraday_brief(dataset)
        elif report_type == "daily_close_report":
            content = generator.generate_daily_close_report(dataset)
        elif report_type == "event_alert":
            content = generator.generate_event_alert(dataset)
            if content is None:
                logger.info("No event alert triggered for %s", run_id)
                for symbol in symbols:
                    store.log_run(run_id, report_type, market, symbol, "SKIPPED", started_at)
                return True
        else:
            content = generator.generate_intraday_brief(dataset)

        store.log_run(run_id, report_type, market, "*", "ANALYZED", started_at)

        # ── Save report ──────────────────────────────────────────
        report_path = store.save_report(report_type, market, content)
        store.log_run(run_id, report_type, market, "*", "REPORT_GENERATED", started_at)

        # ── Notify ───────────────────────────────────────────────
        subject = f"{report_type.upper()} — {market} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        notifier_results = notifier.send(subject, content, report_type)

        # ── Determine final status based on notification results ─
        status = _resolve_dispatch_status(notifier_results)
        store.log_run(run_id, report_type, market, "*", status, started_at)

        logger.info("Workflow completed: %s %s %s → %s (%s)", run_id, report_type, market, report_path, status)
        return status != "FAILED"

    except Exception as exc:
        logger.error("Workflow %s failed: %s", exc)
        for symbol in symbols:
            store.log_run(
                run_id, report_type, market, symbol, "FAILED",
                started_at,
                error_message=str(exc)[:500],
            )
        return False


def _resolve_dispatch_status(notifier_results: list[NotifierResult]) -> str:
    """Map notifier results to a workflow status.

    Rules:
      - all enabled channels succeeded → DISPATCHED
      - some succeeded, some failed → PARTIAL_FAILED
      - all failed → FAILED
      - no enabled channels → DISPATCHED (report still generated)
    """
    if not notifier_results:
        return "DISPATCHED"

    success_count = sum(1 for r in notifier_results if r.success)
    fail_count = len(notifier_results) - success_count

    if fail_count == 0:
        return "DISPATCHED"
    if success_count == 0:
        return "FAILED"
    return "PARTIAL_FAILED"


def run_once(market: str | None = None, provider: str | None = None) -> bool:
    """Run a single collection + intraday report cycle.

    Returns True if all workflows completed successfully.
    """
    _load_env()
    _setup_logging()
    logger = logging.getLogger(__name__)

    args = argparse.Namespace(provider=provider)
    try:
        client = _create_client(args)
    except SystemExit:
        logger.error("Provider not configured; cannot run")
        return False

    config = _load_config()
    symbols_by_market = _get_symbols(market)

    notif_cfg = config.get("notifications", {})
    email_enabled = notif_cfg.get("email", {}).get("enabled", False)
    webhook_enabled = notif_cfg.get("webhook", {}).get("enabled", False)

    collector = McpDataCollector(client)
    cleaner = McpDataCleaner()
    validator = McpDataValidator()
    generator = ReportGenerator()
    store = McpDataStore()
    notifier = create_notifiers(
        enable_email=email_enabled,
        enable_webhook=webhook_enabled,
        enable_console=True,
    )

    import uuid
    all_ok = True
    for m, symbols in symbols_by_market.items():
        run_id = str(uuid.uuid4())
        logger.info("Running once for %s: %s", m, symbols)
        ok = _run_report_workflow(
            run_id, m, symbols, "intraday_brief",
            client, collector, cleaner, validator, generator, store, notifier,
        )
        if not ok:
            all_ok = False

    return all_ok


def run_scheduler(market: str | None = None, provider: str | None = None) -> None:
    """Run the scheduler loop."""
    _load_env()
    _setup_logging()
    logger = logging.getLogger(__name__)

    args = argparse.Namespace(provider=provider)
    client = _create_client(args)

    config = _load_config()
    symbols_by_market = _get_symbols(market)

    sched_cfg = config.get("schedule", {})
    tick_seconds = int(sched_cfg.get("tick_seconds", 60))
    intraday_interval = int(sched_cfg.get("intraday_interval_hours", 2))
    post_market_delay = int(sched_cfg.get("post_market_delay_minutes", 15))

    notif_cfg = config.get("notifications", {})
    email_enabled = notif_cfg.get("email", {}).get("enabled", False)
    webhook_enabled = notif_cfg.get("webhook", {}).get("enabled", False)

    collector = McpDataCollector(client)
    cleaner = McpDataCleaner()
    validator = McpDataValidator()
    generator = ReportGenerator()
    store = McpDataStore()
    notifier = create_notifiers(
        enable_email=email_enabled,
        enable_webhook=webhook_enabled,
        enable_console=True,
    )

    markets = list(symbols_by_market.keys())

    def on_intraday(run_id: str, m: str, symbols: list[str]) -> None:
        _run_report_workflow(
            run_id, m, symbols, "intraday_brief",
            client, collector, cleaner, validator, generator, store, notifier,
        )

    def on_daily_close(run_id: str, m: str, symbols: list[str]) -> None:
        _run_report_workflow(
            run_id, m, symbols, "daily_close_report",
            client, collector, cleaner, validator, generator, store, notifier,
        )

    scheduler = McpScheduler(
        client,
        tick_seconds=tick_seconds,
        intraday_interval_hours=intraday_interval,
        post_market_delay_minutes=post_market_delay,
        datastore=store,
    )
    scheduler.run_forever(
        markets=markets,
        symbols_by_market=symbols_by_market,
        on_intraday=on_intraday,
        on_daily_close=on_daily_close,
    )


def run_health(provider: str | None = None) -> dict:
    """Run health check and return status."""
    _load_env()
    _setup_logging(log_level="WARNING")

    try:
        args = argparse.Namespace(provider=provider or os.getenv("MARKET_DATA_PROVIDER", "mock"))
        client = _create_client(args)
    except SystemExit:
        client = MockMarketDataClient()

    notifier = create_notifiers(enable_console=False)
    store = McpDataStore()

    client_health = client.health_check()
    notifier_health = notifier.health_check()
    recent_runs = store.get_recent_runs(limit=5)

    return {
        "status": "ok" if client_health.get("ok") else "degraded",
        "service": "market-report-agent",
        "client": client_health,
        "notifier": notifier_health,
        "recent_runs": len(recent_runs),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Market Report Agent")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--market", type=str, default=None, help="Filter to specific market (US, HK)")
    parser.add_argument("--provider", type=str, default=None, help="Data provider (mock, longbridge_mcp)")
    args = parser.parse_args()

    if args.health:
        import json
        result = run_health(provider=args.provider)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.once:
        ok = run_once(market=args.market, provider=args.provider)
        if not ok:
            sys.exit(1)
    else:
        run_scheduler(market=args.market, provider=args.provider)


if __name__ == "__main__":
    main()
