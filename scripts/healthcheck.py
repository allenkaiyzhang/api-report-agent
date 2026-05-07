from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.runtime_support import BASE_DIR, RUNTIME_DIR, load_json_file


def latest_file_size(path: Path) -> int:
    files = [
        item for item in path.rglob("*")
        if item.is_file() and item.name != ".gitkeep"
    ] if path.exists() else []
    if not files:
        return 0
    latest = max(files, key=lambda item: item.stat().st_mtime)
    return latest.stat().st_size


def healthcheck() -> dict:
    state = load_json_file(RUNTIME_DIR / "pipeline_status.json")
    recent_errors = state.get("recent_errors") or []
    status = state.get("pipeline_status", "unknown")
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "pipeline_running": status == "running",
        "pipeline_status": status,
        "last_collect_time": state.get("last_collect_time"),
        "last_metrics_time": state.get("last_metrics_time"),
        "last_quality_time": state.get("last_quality_time"),
        "recent_error_count": len(recent_errors),
        "total_error_count": state.get("error_count", 0),
        "current_raw_file_size": latest_file_size(BASE_DIR / "data" / "raw"),
        "current_normalized_file_size": latest_file_size(BASE_DIR / "data" / "normalized"),
    }


def main() -> None:
    print(json.dumps(healthcheck(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
