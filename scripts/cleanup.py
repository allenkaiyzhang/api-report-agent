from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from core.runtime_support import BASE_DIR, setup_logger


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup transient charts and logs")
    parser.add_argument("--charts-days", type=int, default=30)
    parser.add_argument("--logs-days", type=int, default=14)
    return parser.parse_args()


def main() -> None:
    logger = setup_logger("cleanup", "pipeline.log")
    args = parse_args()
    removed_charts = cleanup_dir(BASE_DIR / "charts", args.charts_days)
    removed_logs = cleanup_dir(BASE_DIR / "logs", args.logs_days)
    logger.info("cleanup finished charts=%s logs=%s", len(removed_charts), len(removed_logs))
    print(f"removed charts={len(removed_charts)} logs={len(removed_logs)}")


if __name__ == "__main__":
    main()
