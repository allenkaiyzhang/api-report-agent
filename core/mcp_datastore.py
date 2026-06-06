"""MCP data store — saves cleaned snapshots, run logs, and structured JSONL logs.

Storage format:
  - data/raw/{market}/{date}/collection.jsonl (raw snapshots)
  - data/clean/{market}/{date}/cleaned.jsonl (cleaned snapshots)
  - data/run_logs.jsonl (run state records)
  - logs/data_access.jsonl (structured data access log)
  - logs/report_generation.jsonl (report generation log)
  - logs/notification.jsonl (notification dispatch log)
  - reports/{YYYY-MM-DD}/ (generated report files)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RAW_DIR = "data/raw"
DEFAULT_CLEAN_DIR = "data/clean"
DEFAULT_RUN_LOGS_PATH = "data/run_logs.jsonl"
DEFAULT_REPORTS_DIR = "reports"
DEFAULT_LOG_DIR = "logs"


class McpDataStore:
    """Persistent storage for pipeline data, run logs, and reports.

    Supports structured JSONL logs for observability:
      - data_access.jsonl: records all provider data access
      - report_generation.jsonl: records report generation events
      - notification.jsonl: records notification dispatch events
    """

    def __init__(
        self,
        raw_dir: str = DEFAULT_RAW_DIR,
        clean_dir: str = DEFAULT_CLEAN_DIR,
        run_logs_path: str = DEFAULT_RUN_LOGS_PATH,
        reports_dir: str = DEFAULT_REPORTS_DIR,
        log_dir: str = DEFAULT_LOG_DIR,
    ) -> None:
        self._raw_dir = Path(raw_dir)
        self._clean_dir = Path(clean_dir)
        self._run_logs_path = Path(run_logs_path)
        self._reports_dir = Path(reports_dir)
        self._log_dir = Path(log_dir)

        # Ensure log directories exist
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── Raw snapshots ──────────────────────────────────────────

    def save_raw_snapshot(self, record: dict[str, Any], market: str) -> str:
        """Append raw provider data to data/raw/{market}/{date}/collection.jsonl."""
        trading_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_dir = self._raw_dir / market / trading_date
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "collection.jsonl"

        record["_saved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return str(output_file)

    # ── Clean snapshots ─────────────────────────────────────────

    def save_clean_snapshot(self, record: dict[str, Any], market: str) -> str:
        """Append a cleaned data record to data/clean/{market}/{date}/cleaned.jsonl."""
        trading_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_dir = self._clean_dir / market / trading_date
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "cleaned.jsonl"

        record["_saved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return str(output_file)

    # ── Run logs (JSONL) ─────────────────────────────────────────

    def log_run(
        self,
        run_id: str,
        report_type: str,
        market: str,
        symbol: str,
        status: str,
        started_at: str,
        ended_at: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Append a run state record to data/run_logs.jsonl.

        Status values: PENDING, RUNNING, DATA_COLLECTED, DATA_VALIDATED,
        ANALYZED, REPORT_GENERATED, DISPATCHED, PARTIAL_FAILED, SKIPPED, FAILED.
        """
        self._run_logs_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "run_id": run_id,
            "report_type": report_type,
            "market": market,
            "symbol": symbol,
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error_message": error_message,
        }
        with open(self._run_logs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_recent_runs(
        self, report_type: str | None = None, market: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Read recent run log entries, optionally filtered."""
        if not self._run_logs_path.exists():
            return []

        runs: list[dict[str, Any]] = []
        with open(self._run_logs_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    run = json.loads(line)
                    if report_type and run.get("report_type") != report_type:
                        continue
                    if market and run.get("market") != market:
                        continue
                    runs.append(run)
                except json.JSONDecodeError:
                    continue

        return runs[-limit:]

    # ── Structured JSONL logs ────────────────────────────────────

    def _write_jsonl_log(self, log_file: str, record: dict[str, Any]) -> None:
        """Append a record to a structured JSONL log file."""
        path = self._log_dir / log_file
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Failed to write JSONL log %s: %s", log_file, exc)

    def log_data_access(
        self,
        run_id: str,
        provider: str,
        operation: str,
        symbols: list[str],
        market: str,
        success: bool,
        error: str | None = None,
        record_count: int = 0,
    ) -> None:
        """Log a data access event to logs/data_access.jsonl."""
        self._write_jsonl_log("data_access.jsonl", {
            "run_id": run_id,
            "provider": provider,
            "operation": operation,
            "symbols": symbols,
            "market": market,
            "success": success,
            "error": error,
            "record_count": record_count,
        })

    def log_report_generation(
        self,
        run_id: str,
        report_type: str,
        market: str,
        symbols: list[str],
        success: bool,
        report_path: str = "",
        error: str | None = None,
    ) -> None:
        """Log a report generation event to logs/report_generation.jsonl."""
        self._write_jsonl_log("report_generation.jsonl", {
            "run_id": run_id,
            "report_type": report_type,
            "market": market,
            "symbols": symbols,
            "success": success,
            "report_path": report_path,
            "error": error,
        })

    def log_notification(
        self,
        run_id: str,
        channel: str,
        report_type: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log a notification dispatch event to logs/notification.jsonl."""
        self._write_jsonl_log("notification.jsonl", {
            "run_id": run_id,
            "channel": channel,
            "report_type": report_type,
            "success": success,
            "error": error,
        })

    # ── Reports ──────────────────────────────────────────────────

    def save_report(
        self,
        report_type: str,
        market: str,
        content: str,
        report_date: str | None = None,
    ) -> str:
        """Save a generated report to reports/{YYYY-MM-DD}/{type}_{market}.md."""
        report_date = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_dir = self._reports_dir / report_date
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{report_type}_{market}.md"
        output_file = output_dir / filename
        output_file.write_text(content, encoding="utf-8")
        logger.info("Report saved: %s", output_file)
        return str(output_file)

    def list_reports(self, report_date: str | None = None) -> list[str]:
        """List generated report file paths."""
        target = self._reports_dir / (report_date or "")
        if not target.exists():
            return []
        if report_date:
            return [str(p) for p in target.iterdir() if p.is_file()]
        results = []
        for d in sorted(target.iterdir(), reverse=True):
            if d.is_dir():
                results.extend(str(p) for p in d.iterdir() if p.is_file())
        return results

    # ── Duplicate check ─────────────────────────────────────────

    def has_run_for_window(
        self,
        report_type: str,
        market: str,
        symbol: str,
        window_start: str,
    ) -> bool:
        """Check if a report was already generated for this window."""
        recent = self.get_recent_runs(
            report_type=report_type, market=market, limit=200
        )
        for run in recent:
            if (
                run.get("symbol") == symbol
                and run.get("started_at", "") >= window_start
                and run.get("status") in (
                    "REPORT_GENERATED", "DISPATCHED", "PARTIAL_FAILED", "SKIPPED"
                )
            ):
                return True
        return False
