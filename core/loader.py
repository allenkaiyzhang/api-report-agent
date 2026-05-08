from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_symbols(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Symbols file not found: {path}")

    if path.suffix.lower() != ".json":
        raise ValueError(f"Symbols file must be JSON: {path}")
    return load_symbols_json(path)


def load_symbols_json(path: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Symbols JSON is invalid: {path}") from exc

    if isinstance(data, dict):
        rows = data.get("symbols")
    else:
        rows = data

    if not isinstance(rows, list):
        raise ValueError("Symbols JSON must contain a list or an object with a symbols list")

    symbols: list[dict[str, str]] = []
    for item in rows:
        row = normalize_symbol_item(item)
        if row.get("symbol") and row.get("enabled", "true").lower() != "false":
            symbols.append(row)
    return symbols


def normalize_symbol_item(item: Any) -> dict[str, str]:
    if isinstance(item, str):
        return {
            "symbol": item.strip(),
            "enabled": "true",
        }
    if not isinstance(item, dict):
        return {}
    return {
        "symbol": str(item.get("symbol", "")).strip(),
        "enabled": str(item.get("enabled", True)).strip().lower(),
    }
