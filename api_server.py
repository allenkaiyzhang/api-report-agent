from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from core.extended_session import get_us_extended_window
from core.loader import load_symbols
from core.market_calendar import get_trading_date


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="api-report-agent", version="1.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
control_lock = Lock()
running_processes: dict[str, subprocess.Popen] = {}


def json_error(message: str, status_code: int = 404, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status_code)


def require_control_token(x_api_token: str = Header(default="")) -> None:
    expected = os.getenv("API_CONTROL_TOKEN", "")
    if not expected or x_api_token != expected:
        raise HTTPException(status_code=401, detail="invalid control token")


def read_jsonl(path: Path) -> dict[str, Any]:
    records = []
    parse_errors = []
    if not path.exists():
        return {"path": str(path), "exists": False, "records": [], "parse_errors": [{"line": 0, "error": "file missing"}]}
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                parse_errors.append({"line": line_number, "error": str(exc)})
    return {"path": str(path), "exists": True, "records": records, "parse_errors": parse_errors}


def latest_jsonl_file(market: str, session: str = "regular") -> Path | None:
    market = market.upper()
    if session == "extended":
        base = BASE_DIR / "data" / "raw" / market / "extended"
    else:
        legacy = BASE_DIR / "data" / "raw" / market
        layered = BASE_DIR / "data" / "raw" / market / "regular"
        base = legacy if legacy.exists() else layered
    if not base.exists():
        return None
    files = sorted(path for path in base.glob("*.jsonl") if path.is_file())
    return files[-1] if files else None


def latest_payload(market: str, session: str = "regular") -> dict[str, Any]:
    path = latest_jsonl_file(market, session)
    if path is None:
        return {"market": market.upper(), "session": session, "error": "raw file not found", "records": [], "parse_errors": []}
    result = read_jsonl(path)
    records = result["records"]
    return {
        "market": market.upper(),
        "session": session,
        "path": result["path"],
        "line_count": len(records),
        "parse_errors": result["parse_errors"],
        "latest": records[-1] if records else None,
    }


def latest_quote(symbol: str) -> dict[str, Any] | None:
    target = symbol.upper()
    candidates = [
        latest_jsonl_file("US", "regular"),
        latest_jsonl_file("US", "extended"),
        latest_jsonl_file("HK", "regular"),
    ]
    latest_record = None
    for path in candidates:
        if path is None:
            continue
        for record in read_jsonl(path)["records"]:
            if str(record.get("symbol", "")).upper() == target:
                latest_record = record
    return latest_record


def report_files() -> list[dict[str, Any]]:
    roots = [
        BASE_DIR / "data" / "reports",
        BASE_DIR / "data" / "quality",
        BASE_DIR / "data" / "metrics",
    ]
    reports = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".json", ".md"}:
                relative = path.relative_to(BASE_DIR)
                reports.append(
                    {
                        "report_id": encode_report_id(relative),
                        "path": str(relative).replace("\\", "/"),
                        "type": path.suffix.lower().lstrip("."),
                        "size_bytes": path.stat().st_size,
                        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds"),
                    }
                )
    return reports


def encode_report_id(path: Path) -> str:
    return str(path).replace("\\", "/").replace("/", "__")


def decode_report_id(report_id: str) -> Path:
    return BASE_DIR / Path(report_id.replace("__", "/"))


def start_process(name: str, args: list[str]) -> dict[str, Any]:
    with control_lock:
        existing = running_processes.get(name)
        if existing is not None and existing.poll() is None:
            return {"status": "started", "already_running": True, "name": name, "pid": existing.pid}
        process = subprocess.Popen(args, cwd=str(BASE_DIR))
        running_processes[name] = process
        return {"status": "started", "already_running": False, "name": name, "pid": process.pid}


def pipeline_status() -> dict[str, Any]:
    path = BASE_DIR / "runtime" / "pipeline_status.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": "pipeline_status parse error"}
    return data if isinstance(data, dict) else {}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": "internal server error", "detail": str(exc)}, status_code=500)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "api-report-agent",
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "pipeline_status": pipeline_status().get("pipeline_status", "unknown"),
    }


@app.get("/symbols")
def symbols() -> dict[str, Any]:
    path = BASE_DIR / "config" / "symbols.json"
    if not path.exists():
        return {"symbols": [], "warning": "config/symbols.json not found"}
    return {"symbols": load_symbols(path)}


@app.get("/markets/{market}/latest")
def market_latest(market: str) -> dict[str, Any]:
    return latest_payload(market, "regular")


@app.get("/sessions/{market}/regular/latest")
def regular_latest(market: str) -> dict[str, Any]:
    return latest_payload(market, "regular")


@app.get("/sessions/{market}/extended/latest")
def extended_latest(market: str) -> dict[str, Any]:
    return latest_payload(market, "extended")


@app.get("/quotes/{symbol}/latest")
def quote_latest(symbol: str) -> dict[str, Any]:
    record = latest_quote(symbol)
    if record is None:
        return {"symbol": symbol.upper(), "error": "quote not found"}
    return {"symbol": symbol.upper(), "latest": record}


@app.get("/reports")
def reports() -> dict[str, Any]:
    return {"reports": report_files()}


@app.get("/reports/{report_id}")
def report(report_id: str) -> dict[str, Any]:
    path = decode_report_id(report_id)
    if not path.exists() or not path.is_file() or BASE_DIR not in path.resolve().parents:
        return {"report_id": report_id, "error": "report not found"}
    if path.suffix.lower() == ".json":
        try:
            return {"report_id": report_id, "content": json.loads(path.read_text(encoding="utf-8"))}
        except json.JSONDecodeError as exc:
            return {"report_id": report_id, "error": "report parse error", "detail": str(exc)}
    return {"report_id": report_id, "content": path.read_text(encoding="utf-8")}


@app.post("/control/run-regular-pipeline", dependencies=[Depends(require_control_token)])
def run_regular_pipeline() -> dict[str, Any]:
    return start_process("regular-pipeline", [sys.executable, "-m", "scripts.pipeline_runner"])


@app.post("/control/run-extended-pipeline", dependencies=[Depends(require_control_token)])
def run_extended_pipeline() -> dict[str, Any]:
    return start_process("extended-pipeline", [sys.executable, "-m", "scripts.extended_pipeline", "--once"])


@app.post("/control/run-daily-report", dependencies=[Depends(require_control_token)])
def run_daily_report(market: str = "US", date: str = "") -> dict[str, Any]:
    if not date:
        date = get_trading_date(market.upper(), datetime.now(UTC))
    args = [sys.executable, "-m", "scripts.post_market_pipeline", "--market", market.upper()]
    args.extend(["--date", date])
    return start_process(f"daily-report-{market.upper()}", args)


@app.post("/control/run-extended-report", dependencies=[Depends(require_control_token)])
def run_extended_report(market: str = "US", date: str = "") -> dict[str, Any]:
    if not date:
        date = get_us_extended_window(datetime.now(UTC)).trading_date
    args = [sys.executable, "-m", "scripts.extended_report", "--market", market.upper()]
    args.extend(["--date", date])
    return start_process(f"extended-report-{market.upper()}", args)


@app.get("/ui/dashboard", response_class=HTMLResponse)
def ui_dashboard(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "us_regular": latest_payload("US", "regular"),
        "us_extended": latest_payload("US", "extended"),
        "hk_regular": latest_payload("HK", "regular"),
        "reports": report_files()[:10],
        "pipeline_status": pipeline_status(),
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/ui/reports", response_class=HTMLResponse)
def ui_reports(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("reports.html", {"request": request, "reports": report_files()})


@app.get("/ui/control", response_class=HTMLResponse)
def ui_control(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("control.html", {"request": request})
