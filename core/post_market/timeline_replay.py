from __future__ import annotations

from pathlib import Path
from typing import Any

from core.data_pipeline import BASE_DIR
from core.post_market.common import load_json, metrics_day_dir, report_path, write_json_atomic


def generate_timeline_replay(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    events: list[dict[str, Any]] = []
    for path in sorted(metrics_day_dir(base_dir, market, trading_date).glob("window_*.json")):
        metric = load_json(path)
        time_label = str(metric.get("window_start", ""))[11:16]
        for symbol in metric.get("symbols", []):
            name = symbol.get("symbol", "")
            if (symbol.get("volume_delta") or 0) > 0:
                events.append({"time": time_label, "event": f"{name} volume expansion", "window_id": metric.get("window_id")})
            if symbol.get("volatility") is not None and symbol.get("volatility") > 0.02:
                events.append({"time": time_label, "event": f"{name} volatility spike", "window_id": metric.get("window_id")})
            if symbol.get("max_drawdown_pct") is not None and symbol.get("max_drawdown_pct") < -3:
                events.append({"time": time_label, "event": f"{name} drawdown pressure", "window_id": metric.get("window_id")})
            for flag in symbol.get("flags", []):
                if flag in {"stale_price", "volume_reset_detected", "abnormal_spread"}:
                    events.append({"time": time_label, "event": f"{name} anomaly: {flag}", "window_id": metric.get("window_id")})
    return write_json_atomic(report_path(base_dir, market, trading_date, "timeline.json"), events)
