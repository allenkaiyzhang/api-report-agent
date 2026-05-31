from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from clients.market_client import MarketClient
from core.config_registry import DEFAULT_REGISTRY_PATH, apply_registry_to_env
from core.data_pipeline import daily_day, metrics_day, normalize_day, quality_day
from core.loader import load_symbols
from core.market_data_store import DailyJsonlMarketDataStore
from core.runtime_support import BASE_DIR, RUNTIME_DIR, RuntimeState, load_json_file
from scripts.healthcheck import healthcheck
from scripts.market_data_collector import MarketDataCollector
from scripts.post_market_pipeline import run_post_market_pipeline


load_dotenv(BASE_DIR / ".env")
apply_registry_to_env(override=True)


app = FastAPI(title="API Report Agent")
bearer_scheme = HTTPBearer(auto_error=False)

REPORT_FILES = {
    "market_summary": ("data/reports/{market}/{date}_market_summary.json", "json"),
    "timeline": ("data/reports/{market}/{date}_timeline.json", "json"),
    "ai_summary": ("data/reports/{market}/{date}_ai_summary.md", "text"),
    "health": ("data/reports/{market}/{date}_health.json", "json"),
    "features": ("data/features/{market}/{date}.json", "json"),
    "daily_metrics": ("data/metrics/{market}/{date}/daily.json", "json"),
    "windows_metrics": ("data/metrics/{market}/{date}/windows.json", "json"),
    "quality": ("data/quality/{market}/{date}.json", "json"),
}


class CollectRunRequest(BaseModel):
    provider: str | None = Field(default=None, description="Optional provider override for this run.")
    symbols: list[str] | None = Field(default=None, description="Optional symbol override for this run.")
    output_dir: str | None = Field(default=None, description="Optional raw output directory override.")


class PostMarketRunRequest(BaseModel):
    market: Literal["HK", "US"]
    trading_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class DailyBuildRequest(BaseModel):
    market: Literal["HK", "US"]
    trading_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def require_api_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> None:
    expected = os.getenv("API_TOKEN") or os.getenv("API_KEY")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API token is not configured",
        )
    if credentials is None or credentials.scheme.lower() != "bearer" or credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api-report-agent"}


@app.get("/status", dependencies=[Depends(require_api_token)])
def status_report() -> dict[str, Any]:
    return {
        "service": "api-report-agent",
        "health": healthcheck(),
        "runtime": load_json_file(RUNTIME_DIR / "pipeline_status.json"),
    }


@app.get("/symbols", dependencies=[Depends(require_api_token)])
def symbols() -> dict[str, Any]:
    path = _symbols_path()
    rows = load_symbols(path)
    return {"source": str(path), "count": len(rows), "symbols": rows}


@app.post("/collect/run", dependencies=[Depends(require_api_token)])
def run_collection_once(payload: CollectRunRequest) -> dict[str, Any]:
    provider = payload.provider or os.getenv("MARKET_DATA_PROVIDER", "mock")
    output_dir = _resolve_project_path(payload.output_dir or os.getenv("DATA_COLLECTION_OUTPUT_DIR", "data/raw"))
    interval_seconds = int(os.getenv("DATA_COLLECTION_INTERVAL_SECONDS", "120") or "120")
    selected_symbols = payload.symbols or [row["symbol"] for row in load_symbols(_symbols_path())]

    collector = MarketDataCollector(
        market_client=MarketClient(provider=provider),
        store=DailyJsonlMarketDataStore(output_dir=output_dir),
        interval_seconds=interval_seconds,
    )
    now = datetime.now(UTC)
    output_paths = collector.run_once(selected_symbols, now=now)
    state = RuntimeState()
    for output_path in output_paths:
        output_market = output_path.parent.parent.name if output_path.parent.name in {"regular", "extended"} else output_path.parent.name
        state.record_collect_success(output_market, output_path.stem, str(output_path))

    return {
        "status": "ok",
        "provider": provider,
        "symbol_count": len(selected_symbols),
        "output_paths": [str(path) for path in output_paths],
        "collected_at": now.isoformat(timespec="seconds"),
    }


@app.post("/reports/post-market/run", dependencies=[Depends(require_api_token)])
def run_post_market_report(payload: PostMarketRunRequest) -> dict[str, Any]:
    outputs = run_post_market_pipeline(payload.market, payload.trading_date, base_dir=BASE_DIR)
    RuntimeState().mark_daily_done(payload.market, payload.trading_date)
    return {
        "status": "ok",
        "market": payload.market,
        "trading_date": payload.trading_date,
        "outputs": outputs,
    }


@app.post("/pipeline/daily/run", dependencies=[Depends(require_api_token)])
def run_daily_build(payload: DailyBuildRequest) -> dict[str, Any]:
    normalized = normalize_day(payload.market, payload.trading_date, base_dir=BASE_DIR)
    metrics = metrics_day(payload.market, payload.trading_date, base_dir=BASE_DIR)
    daily = daily_day(payload.market, payload.trading_date, base_dir=BASE_DIR)
    quality = quality_day(payload.market, payload.trading_date, base_dir=BASE_DIR)
    RuntimeState().mark_daily_done(payload.market, payload.trading_date)
    return {
        "status": "ok",
        "market": payload.market,
        "trading_date": payload.trading_date,
        "outputs": {
            "normalized": str(normalized),
            "metrics": str(metrics),
            "daily": str(daily),
            "quality": str(quality),
        },
    }


@app.get("/reports", dependencies=[Depends(require_api_token)])
def list_reports(
    market: Literal["HK", "US"] | None = None,
    trading_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    items = []
    for report_type, (template, _) in REPORT_FILES.items():
        markets = [market] if market else ["HK", "US"]
        for current_market in markets:
            if trading_date:
                path = _report_path(report_type, current_market, trading_date)
                if path.exists():
                    items.append(_artifact_item(report_type, current_market, trading_date, path))
                continue
            items.extend(_discover_report_items(report_type, current_market, template))
    return {"count": len(items), "reports": sorted(items, key=lambda item: (item["trading_date"], item["market"], item["type"]))}


@app.get("/reports/{market}/{trading_date}/{report_type}", dependencies=[Depends(require_api_token)])
def get_report(
    market: Literal["HK", "US"],
    trading_date: str,
    report_type: str,
) -> dict[str, Any]:
    _validate_trading_date(trading_date)
    if report_type not in REPORT_FILES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown report type: {report_type}")
    path = _report_path(report_type, market, trading_date)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"report not found: {report_type}")

    _, payload_type = REPORT_FILES[report_type]
    content: Any
    if payload_type == "json":
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="report JSON is invalid") from exc
    else:
        content = path.read_text(encoding="utf-8")
    return {
        "type": report_type,
        "market": market,
        "trading_date": trading_date,
        "path": str(path),
        "content": content,
    }


def _symbols_path() -> Path:
    legacy_path = BASE_DIR / "config" / "symbols.json"
    return legacy_path if legacy_path.exists() else DEFAULT_REGISTRY_PATH


def _validate_trading_date(value: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="trading_date must use YYYY-MM-DD")


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


def _report_path(report_type: str, market: str, trading_date: str) -> Path:
    template, _ = REPORT_FILES[report_type]
    return BASE_DIR / template.format(market=market, date=trading_date)


def _artifact_item(report_type: str, market: str, trading_date: str, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "type": report_type,
        "market": market,
        "trading_date": trading_date,
        "path": str(path),
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(timespec="seconds"),
    }


def _discover_report_items(report_type: str, market: str, template: str) -> list[dict[str, Any]]:
    pattern = template.format(market=market, date="*")
    paths = BASE_DIR.glob(pattern)
    items = []
    for path in paths:
        trading_date = _date_from_report_path(report_type, path)
        if trading_date:
            items.append(_artifact_item(report_type, market, trading_date, path))
    return items


def _date_from_report_path(report_type: str, path: Path) -> str:
    if report_type in {"daily_metrics", "windows_metrics"}:
        return path.parent.name
    if report_type in {"quality", "features"}:
        return path.stem
    for suffix in ("_market_summary", "_timeline", "_ai_summary", "_health"):
        if path.stem.endswith(suffix):
            return path.stem[: -len(suffix)]
    return ""
