from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.market_calendar import get_trading_date
from core.trading_hours import infer_symbol_market


class DailyJsonlMarketDataStore:
    def __init__(self, output_dir: Path, file_timezone: str = "Asia/Shanghai") -> None:
        self.output_dir = output_dir
        self.file_timezone = ZoneInfo(file_timezone)

    def append_snapshot(self, snapshot: dict[str, Any], collected_at: datetime) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        file_date = collected_at.astimezone(self.file_timezone).strftime("%Y%m%d")
        output_path = self.output_dir / f"market_data_{file_date}.jsonl"
        with output_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(snapshot, ensure_ascii=False, default=str))
            file.write("\n")
        return output_path

    def append_raw_records(
        self,
        records: list[dict[str, Any]],
        collected_at: datetime,
        provider: str,
    ) -> list[Path]:
        """Append raw records into data/raw/{market}/YYYY-MM-DD.jsonl without mutation."""
        output_paths: dict[str, Path] = {}
        collected_at_text = collected_at.astimezone(self.file_timezone).isoformat(timespec="seconds")

        for record in records:
            symbol = str(record.get("symbol", ""))
            market = infer_symbol_market(symbol)
            file_date = get_trading_date(market, collected_at)
            output_path = self.output_dir / market / f"{file_date}.jsonl"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            raw_line = {
                "collected_at": collected_at_text,
                "provider": provider,
                **record,
            }
            with output_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(raw_line, ensure_ascii=False, default=str))
                file.write("\n")
            output_paths[market] = output_path

        return list(output_paths.values())
