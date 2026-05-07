from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from core.runtime_support import BASE_DIR


def cleanup_dir(path: Path, retention_days: int) -> list[Path]:
    cutoff = datetime.now().timestamp() - timedelta(days=retention_days).total_seconds()
    removed: list[Path] = []
    if not path.exists():
        return removed
    for item in path.rglob("*"):
        if item.name == ".gitkeep":
            continue
        if item.is_file() and item.stat().st_mtime < cutoff:
            item.unlink()
            removed.append(item)
    return removed


def cleanup(charts_days: int = 30, logs_days: int = 14) -> dict[str, int]:
    removed_charts = cleanup_dir(BASE_DIR / "charts", charts_days)
    removed_logs = cleanup_dir(BASE_DIR / "logs", logs_days)
    return {"charts": len(removed_charts), "logs": len(removed_logs)}
