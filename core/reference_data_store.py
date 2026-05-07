from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.trading_hours import infer_symbol_market


class ReferenceDataStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def reference_path(self, market: str, trading_date: str) -> Path:
        return self.base_dir / "data" / "reference" / market / f"{trading_date}.json"

    def exists(self, market: str, trading_date: str) -> bool:
        return self.reference_path(market, trading_date).exists()

    def write_reference(
        self,
        market: str,
        trading_date: str,
        provider: str,
        symbols: list[str],
        reference_data: dict[str, Any],
    ) -> Path:
        path = self.reference_path(market, trading_date)
        payload = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "market": market,
            "trading_date": trading_date,
            "provider": provider,
            "symbols": symbols,
            "static_info_by_symbol": reference_data.get("static_info_by_symbol", {}),
            "calc_indexes_by_symbol": reference_data.get("calc_indexes_by_symbol", {}),
            "daily_candlesticks_by_symbol": reference_data.get("daily_candlesticks_by_symbol", {}),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temp_path, path)
        return path

    def load_reference(self, market: str, trading_date: str) -> dict[str, Any]:
        path = self.reference_path(market, trading_date)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


def filter_symbols_by_market(symbols: list[str], market: str) -> list[str]:
    return [symbol for symbol in symbols if infer_symbol_market(symbol) == market]
