from __future__ import annotations

from pathlib import Path

from core.data_pipeline import BASE_DIR
from core.post_market.common import load_json, quality_file, report_path, write_json_atomic


def generate_health_report(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    quality = load_json(quality_file(base_dir, market, trading_date))
    window_quality = quality.get("window_quality", {})
    normalized_quality = quality.get("normalized_quality", {})
    payload = {
        "market": market,
        "trading_date": trading_date,
        "collector_health": "good" if quality.get("usable_for_analysis", False) else "poor",
        "usable_for_analysis": bool(quality.get("usable_for_analysis", False)),
        "missing_windows": window_quality.get("missing_windows", []),
        "duplicate_records": normalized_quality.get("duplicate_records", 0),
        "quality_grade": quality.get("overall_grade", "unknown"),
        "pipeline_restart_detected": False,
    }
    return write_json_atomic(report_path(base_dir, market, trading_date, "health.json"), payload)
