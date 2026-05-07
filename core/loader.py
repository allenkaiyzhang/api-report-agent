from __future__ import annotations

import csv
from pathlib import Path


def load_symbols(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Symbols file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required_fields = {"symbol", "name", "type", "watch_reason"}
        if not reader.fieldnames or not required_fields.issubset(reader.fieldnames):
            raise ValueError(f"Symbols CSV must contain fields: {sorted(required_fields)}")
        return [dict(row) for row in reader if row.get("symbol")]
