from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.ai_analyzer import AIAnalysisConfig, analyze_market_report
from core.config_registry import apply_registry_to_env
from core.extended_session import get_us_extended_window
from core.runtime_support import setup_logger
from core.time_model import iso_utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate US extended-session report.")
    parser.add_argument("--market", default="US", choices=["US"])
    parser.add_argument("--date", default="", help="Extended trading date. Defaults to next regular open date.")
    parser.add_argument("--force", action="store_true", help="Generate even before the extended window has ended.")
    return parser.parse_args()


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = []
    errors = []
    if not path.exists():
        return records, [{"line": 0, "error": "file missing"}]
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append({"line": line_number, "error": str(exc)})
    return records, errors


def find_extended_raw_path(market: str, trading_date: str) -> Path:
    base = PROJECT_ROOT / "data" / "raw" / market / "extended"
    if not base.exists():
        return base / f"{trading_date}.jsonl"
    candidates = sorted(base.glob(f"*TO_{trading_date}.jsonl"))
    if candidates:
        return candidates[-1]
    legacy = base / f"{trading_date}.jsonl"
    if legacy.exists():
        return legacy
    return base / f"{trading_date}.jsonl"


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        symbol = str(record.get("symbol", ""))
        if symbol:
            by_symbol[symbol].append(record)

    summary = []
    for symbol, rows in sorted(by_symbol.items()):
        ordered = sorted(rows, key=lambda item: str(item.get("collected_at_utc") or item.get("collected_at") or ""))
        prices = [to_float(row.get("last_price", row.get("latest_price"))) for row in ordered]
        prices = [price for price in prices if price > 0]
        volumes = [to_int(row.get("volume")) for row in ordered]
        first_price = prices[0] if prices else None
        last_price = prices[-1] if prices else None
        return_pct = None
        if first_price and last_price:
            return_pct = round((last_price / first_price - 1) * 100, 4)
        summary.append(
            {
                "symbol": symbol,
                "records": len(rows),
                "first_price": first_price,
                "last_price": last_price,
                "return_pct": return_pct,
                "volume_latest": volumes[-1] if volumes else 0,
                "stale_quote": len({str(row.get("source_timestamp_utc") or row.get("timestamp") or row.get("event_time")) for row in rows}) <= 1 and len(rows) > 1,
                "low_liquidity": (volumes[-1] if volumes else 0) == 0,
                "extended_reference": True,
            }
        )
    return summary


def build_payload(market: str, trading_date: str, records: list[dict[str, Any]], parse_errors: list[dict[str, Any]]) -> dict[str, Any]:
    symbol_summary = summarize(records)
    stale_symbols = [item["symbol"] for item in symbol_summary if item.get("stale_quote")]
    low_liquidity_symbols = [item["symbol"] for item in symbol_summary if item.get("low_liquidity")]
    window_id = records[0].get("session_window_id", "") if records else ""
    return {
        "report_type": "extended",
        "market": market,
        "session": "extended",
        "trading_date": trading_date,
        "session_window_id": window_id,
        "generated_at": iso_utc(datetime.now(UTC)),
        "raw_lines": len(records),
        "raw_json_parse_errors": len(parse_errors),
        "symbol_count": len(symbol_summary),
        "symbol_summary": symbol_summary,
        "quality": {
            "session": "extended",
            "parse_errors": parse_errors,
            "warnings": {
                "stale_quote": stale_symbols,
                "low_liquidity": low_liquidity_symbols,
            },
            "rules": [
                "duplicate timestamps allowed",
                "low volume is warning only",
                "wide spread is warning only",
            ],
        },
        "analysis_focus": [
            "gap risk",
            "overnight sentiment",
            "earnings reaction",
            "premarket momentum",
            "afterhours volatility",
            "market direction",
        ],
    }


def report_paths(payload: dict[str, Any]) -> tuple[Path, Path]:
    output_dir = PROJECT_ROOT / "data" / "reports" / "extended"
    report_id = f"{payload['market']}_extended_{payload['session_window_id'] or payload['trading_date']}"
    return output_dir / f"{report_id}.json", output_dir / f"{report_id}_ai_summary.md"


def write_reports(payload: dict[str, Any], ai_text: str) -> tuple[Path, Path]:
    json_path, md_path = report_paths(payload)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(ai_text or "AI analysis not enabled.\n", encoding="utf-8")
    return json_path, md_path


def extended_window_has_ended(trading_date: str) -> bool:
    end = datetime.combine(datetime.fromisoformat(trading_date).date(), time(9, 30), tzinfo=ZoneInfo("America/New_York"))
    return datetime.now(UTC) >= end.astimezone(UTC)


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    apply_registry_to_env(override=True)
    logger = setup_logger("extended_report", "extended_report.log")
    trading_date = args.date or get_us_extended_window(datetime.now(UTC)).trading_date
    if not args.force and not extended_window_has_ended(trading_date):
        logger.info("skip extended report because window has not ended: US %s", trading_date)
        return
    raw_path = find_extended_raw_path(args.market, trading_date)
    records, parse_errors = load_jsonl(raw_path)
    payload = build_payload(args.market, trading_date, records, parse_errors)
    json_path, _ = report_paths(payload)
    if json_path.exists() and not args.force:
        logger.info("skip extended report because report already exists: %s", json_path)
        return
    ai_text = analyze_market_report(AIAnalysisConfig.from_env(os.environ), payload)
    json_path, md_path = write_reports(payload, ai_text)
    logger.info("built extended report: %s %s", json_path, md_path)


if __name__ == "__main__":
    main()
