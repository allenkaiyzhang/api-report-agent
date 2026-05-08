from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.data_pipeline import BASE_DIR


def market_dir(base_dir: Path, root: str, market: str) -> Path:
    return base_dir / "data" / root / market


def report_path(base_dir: Path, market: str, trading_date: str, suffix: str) -> Path:
    return market_dir(base_dir, "reports", market) / f"{trading_date}_{suffix}"


def feature_path(base_dir: Path, market: str, trading_date: str) -> Path:
    return market_dir(base_dir, "features", market) / f"{trading_date}.json"


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default if default is not None else {}


def write_json_atomic(path: Path, payload: dict[str, Any] | list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temp_path, path)
    return path


def metrics_day_dir(base_dir: Path, market: str, trading_date: str) -> Path:
    return base_dir / "data" / "metrics" / market / trading_date


def reference_file(base_dir: Path, market: str, trading_date: str) -> Path:
    return base_dir / "data" / "reference" / market / f"{trading_date}.json"


def quality_file(base_dir: Path, market: str, trading_date: str) -> Path:
    return base_dir / "data" / "quality" / market / f"{trading_date}.json"


def raw_file(base_dir: Path, market: str, trading_date: str) -> Path:
    return base_dir / "data" / "raw" / market / f"{trading_date}.jsonl"
