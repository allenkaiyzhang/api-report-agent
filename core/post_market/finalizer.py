from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.data_pipeline import BASE_DIR
from core.post_market.common import load_json, metrics_day_dir, write_json_atomic


def finalize_market_day(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> dict[str, Any]:
    metrics_dir = metrics_day_dir(base_dir, market, trading_date)
    daily_path = metrics_dir / "daily.json"
    daily = load_json(daily_path)
    if not daily:
        return {"finalized": False, "reason": "daily_missing", "path": str(daily_path)}
    if daily.get("finalized"):
        return {"finalized": True, "already_finalized": True, "path": str(daily_path)}

    finalized_at = datetime.now(UTC).isoformat(timespec="seconds")
    status_summary: dict[str, int] = {}
    for path in sorted(metrics_dir.glob("window_*.json")):
        metric = load_json(path)
        if not isinstance(metric, dict):
            continue
        metric["window_status"] = "finalized"
        metric["finalized_at"] = finalized_at
        write_json_atomic(path, metric)
        status_summary["finalized"] = status_summary.get("finalized", 0) + 1

    daily["finalized"] = True
    daily["finalized_at"] = finalized_at
    daily["window_status_summary"] = status_summary or daily.get("window_status_summary", {})
    write_json_atomic(daily_path, daily)
    return {"finalized": True, "already_finalized": False, "path": str(daily_path), "finalized_at": finalized_at}
