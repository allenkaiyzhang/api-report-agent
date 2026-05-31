from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def load_symbols(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Symbols file not found: {path}")

    if path.suffix.lower() in {".yaml", ".yml"}:
        return load_symbols_yaml(path)
    if path.suffix.lower() != ".json":
        raise ValueError(f"Symbols file must be JSON or YAML: {path}")
    return load_symbols_json(path)


def load_symbols_json(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Symbols JSON is invalid: {path}") from exc

    return normalize_symbol_rows(data, "Symbols JSON must contain a list or an object with a symbols list")


def load_symbols_yaml(path: Path) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Symbols YAML is invalid: {path}") from exc
    return normalize_symbol_rows(data, "Symbols YAML must contain a list or an object with a symbols list")


def normalize_symbol_rows(data: Any, error_message: str) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        rows = data.get("symbols")
    else:
        rows = data

    if not isinstance(rows, list):
        raise ValueError(error_message)

    symbols: list[dict[str, Any]] = []
    for item in rows:
        row = normalize_symbol_item(item)
        if row.get("symbol") and row.get("enabled", "true").lower() != "false":
            symbols.append(row)
    return symbols


def normalize_symbol_item(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        symbol = item.strip()
        return {
            "symbol": symbol,
            "market": infer_market_from_symbol(symbol),
            "asset_type": "",
            "liquidity_class": "",
            "include_in_movers": True,
            "sessions": ["regular"],
            "enabled": "true",
        }
    if not isinstance(item, dict):
        return {}
    symbol = str(item.get("symbol", "")).strip()
    sessions = item.get("sessions", ["regular"])
    if isinstance(sessions, str):
        sessions = [part.strip() for part in sessions.split(",") if part.strip()]
    if not isinstance(sessions, list):
        sessions = ["regular"]
    return {
        "symbol": symbol,
        "market": str(item.get("market") or infer_market_from_symbol(symbol)).strip().upper(),
        "asset_type": str(item.get("asset_type", "")).strip(),
        "liquidity_class": str(item.get("liquidity_class", "")).strip().lower(),
        "include_in_movers": parse_bool(item.get("include_in_movers", True)),
        "sessions": [str(session).strip().lower() for session in sessions if str(session).strip()],
        "enabled": str(item.get("enabled", True)).strip().lower(),
    }


def infer_market_from_symbol(symbol: str) -> str:
    return "HK" if symbol.upper().endswith(".HK") else "US"


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}
