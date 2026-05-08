from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BASE_DIR / "runtime"
LOG_DIR = BASE_DIR / "logs"


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def load_json_file(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default or {}
    return data if isinstance(data, dict) else (default or {})


def setup_logger(name: str, filename: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler = TimedRotatingFileHandler(
        LOG_DIR / filename,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addHandler(console)
    return logger


class RuntimeState:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (RUNTIME_DIR / "pipeline_status.json")
        self.data = load_json_file(
            self.path,
            {
                "pipeline_status": "starting",
                "last_collect_time": None,
                "last_metrics_window": {},
                "last_daily_build": {},
                "last_email_report": {},
                "last_email_report_failure": {},
                "last_intraday_email_report": {},
                "last_intraday_email_report_failure": {},
                "collect_stats": {},
                "last_successful_run": None,
                "error_count": 0,
                "recent_errors": [],
            },
        )

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    def set_status(self, status: str) -> None:
        self.data["pipeline_status"] = status
        self.save()

    def mark_success(self, key: str, value: Any | None = None) -> None:
        now = utc_now_text()
        self.data[key] = value if value is not None else now
        self.data["last_successful_run"] = now
        self.data["pipeline_status"] = "running"
        self.save()

    def mark_window_done(self, market: str, trading_date: str, window_id: str) -> None:
        key = f"{market}:{trading_date}"
        self.data.setdefault("last_metrics_window", {})[key] = window_id
        self.mark_success("last_metrics_time")

    def mark_daily_done(self, market: str, trading_date: str) -> None:
        key = f"{market}:{trading_date}"
        self.data.setdefault("last_daily_build", {})[key] = utc_now_text()
        self.mark_success("last_quality_time")

    def email_report_sent(self, market: str, trading_date: str) -> bool:
        key = f"{market}:{trading_date}"
        return key in self.data.setdefault("last_email_report", {})

    def mark_email_report_sent(self, market: str, trading_date: str) -> None:
        key = f"{market}:{trading_date}"
        self.data.setdefault("last_email_report", {})[key] = utc_now_text()
        self.data.setdefault("last_email_report_failure", {}).pop(key, None)
        self.mark_success("last_email_report_time")

    def email_report_failed(self, market: str, trading_date: str) -> bool:
        key = f"{market}:{trading_date}"
        return key in self.data.setdefault("last_email_report_failure", {})

    def mark_email_report_failed(self, market: str, trading_date: str, error: str) -> None:
        key = f"{market}:{trading_date}"
        self.data.setdefault("last_email_report_failure", {})[key] = {
            "time": utc_now_text(),
            "error": error,
        }
        self.save()

    def intraday_email_report_sent(self, key: str) -> bool:
        return key in self.data.setdefault("last_intraday_email_report", {})

    def mark_intraday_email_report_sent(self, key: str) -> None:
        self.data.setdefault("last_intraday_email_report", {})[key] = utc_now_text()
        self.data.setdefault("last_intraday_email_report_failure", {}).pop(key, None)
        self.mark_success("last_intraday_email_report_time")

    def intraday_email_report_failed(self, key: str) -> bool:
        return key in self.data.setdefault("last_intraday_email_report_failure", {})

    def mark_intraday_email_report_failed(self, key: str, error: str) -> None:
        self.data.setdefault("last_intraday_email_report_failure", {})[key] = {
            "time": utc_now_text(),
            "error": error,
        }
        self.save()

    def record_collect_success(self, market: str, trading_date: str, output_path: str) -> None:
        stats = self._collect_stats(market, trading_date)
        stats["success_count"] = int(stats.get("success_count") or 0) + 1
        stats["last_success_time"] = utc_now_text()
        stats["last_output_path"] = output_path
        self.mark_success("last_collect_time")

    def record_collect_failure(self, market: str, trading_date: str, exc: BaseException) -> None:
        stats = self._collect_stats(market, trading_date)
        failures = list(stats.get("failures") or [])
        failures.append(
            {
                "time": utc_now_text(),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        stats["failures"] = failures[-100:]
        stats["failure_count"] = len(stats["failures"])
        self.record_error("collect", exc)

    def record_error(self, step: str, exc: BaseException) -> None:
        self.data["error_count"] = int(self.data.get("error_count") or 0) + 1
        errors = list(self.data.get("recent_errors") or [])
        errors.append(
            {
                "time": utc_now_text(),
                "step": step,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        self.data["recent_errors"] = errors[-20:]
        self.data["pipeline_status"] = "degraded"
        self.save()

    def _collect_stats(self, market: str, trading_date: str) -> dict[str, Any]:
        key = f"{market}:{trading_date}"
        stats = self.data.setdefault("collect_stats", {}).setdefault(
            key,
            {
                "market": market,
                "trading_date": trading_date,
                "success_count": 0,
                "failure_count": 0,
                "failures": [],
                "last_success_time": None,
                "last_output_path": None,
            },
        )
        return stats
