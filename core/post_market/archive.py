from __future__ import annotations

import gzip
import hashlib
import shutil
from pathlib import Path
from typing import Any

from core.data_pipeline import BASE_DIR
from core.post_market.common import raw_file, write_json_atomic


def archive_raw(market: str, trading_date: str, base_dir: Path = BASE_DIR) -> Path:
    source = raw_file(base_dir, market, trading_date)
    archive_dir = base_dir / "data" / "archive" / "raw" / market
    archive_dir.mkdir(parents=True, exist_ok=True)
    gz_path = archive_dir / f"{trading_date}.jsonl.gz"
    sha_path = archive_dir / f"{trading_date}.sha256"
    manifest_path = archive_dir / f"{trading_date}.manifest.json"
    if not source.exists():
        return write_json_atomic(manifest_path, {"market": market, "trading_date": trading_date, "compressed": False, "reason": "raw_missing"})

    with source.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    checksum = sha256_file(gz_path)
    sha_path.write_text(f"{checksum}  {gz_path.name}\n", encoding="utf-8")
    manifest: dict[str, Any] = {
        "market": market,
        "trading_date": trading_date,
        "compressed": True,
        "checksum": checksum,
        "original_size_bytes": source.stat().st_size,
        "compressed_size_bytes": gz_path.stat().st_size,
        "archive_path": str(gz_path),
    }
    return write_json_atomic(manifest_path, manifest)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
