from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.email_reporter import EmailConfig, send_email


BASE_DIR = PROJECT_ROOT
SERVICE_NAME = "api-report-agent.service"


def run_daily_check(trading_date: str, markets: list[str], base_dir: Path = BASE_DIR) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "date": trading_date,
        "markets": {},
        "systemd": check_systemd(),
        "disk": check_disk(base_dir),
        "summary": {
            "status": "ok",
            "critical": [],
            "warnings": [],
        },
    }

    for market in markets:
        report["markets"][market] = check_market(base_dir, market, trading_date)

    evaluate_summary(report)
    return report


def send_daily_check_email(report: dict[str, Any], config: EmailConfig) -> bool:
    if not config.enabled or not config.is_ready():
        return False

    summary = report.get("summary", {})
    status = summary.get("status", "unknown")
    subject = f"{config.subject_prefix} daily check {report.get('date')} status={status}"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(build_daily_check_email_body(report))
    send_email(config, message)
    return True


def build_daily_check_email_body(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        f"Daily Check: {report.get('date')}",
        f"Status: {summary.get('status')}",
        "",
        "Critical:",
        *[f"- {item}" for item in summary.get("critical", [])],
        "",
        "Warnings:",
        *[f"- {item}" for item in summary.get("warnings", [])],
        "",
        "Systemd:",
        f"- service: {report.get('systemd', {}).get('service')}",
        f"- status: {report.get('systemd', {}).get('status')}",
        "",
        "Disk:",
        f"- used percent: {report.get('disk', {}).get('disk_used_percent')}",
        f"- data size bytes: {report.get('disk', {}).get('data_size_bytes')}",
        "",
        "Full JSON:",
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
    ]
    return "\n".join(lines)


def check_systemd() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"service": SERVICE_NAME, "active": False, "status": "unknown", "error": str(exc)}

    status = result.stdout.strip() or result.stderr.strip()
    return {
        "service": SERVICE_NAME,
        "active": status == "active",
        "status": status,
    }


def check_market(base_dir: Path, market: str, trading_date: str) -> dict[str, Any]:
    raw_path = base_dir / "data" / "raw" / market / f"{trading_date}.jsonl"
    normalized_path = base_dir / "data" / "normalized" / market / f"{trading_date}.jsonl"
    reference_path = base_dir / "data" / "reference" / market / f"{trading_date}.json"
    metrics_path = base_dir / "data" / "metrics" / market / trading_date
    quality_path = base_dir / "data" / "quality" / market / f"{trading_date}.json"
    reports_path = base_dir / "data" / "reports" / market

    return {
        "raw": check_jsonl(raw_path),
        "normalized": check_normalized(normalized_path),
        "reference": check_reference(reference_path),
        "metrics": check_metrics(metrics_path),
        "quality": check_quality(quality_path),
        "reports": check_reports(reports_path, trading_date),
    }


def check_jsonl(path: Path) -> dict[str, Any]:
    result = {
        "path": str(path),
        "exists": path.exists(),
        "line_count": 0,
        "json_parse_errors": 0,
    }
    if not path.exists():
        return result

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            result["line_count"] += 1
            text = line.strip()
            if not text:
                continue
            try:
                json.loads(text)
            except json.JSONDecodeError:
                result["json_parse_errors"] += 1
    return result


def check_normalized(path: Path) -> dict[str, Any]:
    result = check_jsonl(path)
    invalid_count = 0
    duplicate_count = 0
    parsed_count = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed_count += 1
                if not row.get("is_valid", True):
                    invalid_count += 1
                if "duplicate_record" in row.get("flags", []):
                    duplicate_count += 1
    result["invalid_count"] = invalid_count
    result["invalid_ratio"] = round(invalid_count / parsed_count, 4) if parsed_count else 0.0
    result["duplicate_records"] = duplicate_count
    return result


def check_reference(path: Path) -> dict[str, Any]:
    data = load_json(path)
    return {
        "path": str(path),
        "exists": path.exists(),
        "symbols_count": len(data.get("symbols", [])) if isinstance(data, dict) else 0,
        "symbols_non_empty": bool(data.get("symbols")) if isinstance(data, dict) else False,
    }


def check_metrics(path: Path) -> dict[str, Any]:
    window_files = sorted(path.glob("window_*.json")) if path.exists() else []
    empty_windows = []
    for file_path in window_files:
        data = load_json(file_path)
        if not data.get("symbols"):
            empty_windows.append(file_path.stem.replace("window_", ""))
    daily_path = path / "daily.json"
    daily = load_json(daily_path)
    return {
        "path": str(path),
        "exists": path.exists(),
        "window_file_count": len(window_files),
        "empty_windows": empty_windows,
        "daily_exists": daily_path.exists(),
        "finalized": bool(daily.get("finalized", False)) if isinstance(daily, dict) else False,
    }


def check_quality(path: Path) -> dict[str, Any]:
    data = load_json(path)
    normalized_quality = data.get("normalized_quality", {}) if isinstance(data, dict) else {}
    return {
        "path": str(path),
        "exists": path.exists(),
        "overall_grade": data.get("overall_grade") if isinstance(data, dict) else None,
        "usable_for_analysis": data.get("usable_for_analysis") if isinstance(data, dict) else None,
        "invalid_lines": normalized_quality.get("invalid_lines", 0),
        "duplicate_records": normalized_quality.get("duplicate_records", 0),
    }


def check_reports(path: Path, trading_date: str) -> dict[str, Any]:
    files = sorted(path.glob(f"{trading_date}_*")) if path.exists() else []
    ai_summary = path / f"{trading_date}_ai_summary.md"
    return {
        "path": str(path),
        "exists": path.exists(),
        "report_file_count": len(files),
        "ai_summary_exists": ai_summary.exists(),
    }


def check_disk(base_dir: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(base_dir)
    data_dir = base_dir / "data"
    return {
        "disk_used_percent": round((usage.used / usage.total) * 100, 2) if usage.total else 0,
        "data_size_bytes": directory_size(data_dir),
    }


def evaluate_summary(report: dict[str, Any]) -> None:
    critical: list[str] = []
    warnings: list[str] = []

    if not report["systemd"].get("active"):
        critical.append("systemd_service_not_active")

    if report["disk"].get("disk_used_percent", 0) >= 90:
        critical.append("disk_used_over_90_percent")
    elif report["disk"].get("disk_used_percent", 0) >= 80:
        warnings.append("disk_used_over_80_percent")

    for market, checks in report["markets"].items():
        raw = checks["raw"]
        normalized = checks["normalized"]
        reference = checks["reference"]
        metrics = checks["metrics"]
        quality = checks["quality"]
        reports = checks["reports"]

        if not raw["exists"] or raw["line_count"] == 0:
            critical.append(f"{market}:raw_missing_or_empty")
        if raw["json_parse_errors"]:
            warnings.append(f"{market}:raw_json_parse_errors")
        if not normalized["exists"] or normalized["line_count"] == 0:
            critical.append(f"{market}:normalized_missing_or_empty")
        if normalized["json_parse_errors"]:
            warnings.append(f"{market}:normalized_json_parse_errors")
        if normalized["invalid_ratio"] > 0.2:
            warnings.append(f"{market}:high_invalid_ratio")
        if normalized["duplicate_records"]:
            warnings.append(f"{market}:duplicate_records")
        if not reference["exists"] or not reference["symbols_non_empty"]:
            warnings.append(f"{market}:reference_missing_or_empty")
        if not metrics["exists"] or metrics["window_file_count"] == 0:
            critical.append(f"{market}:metrics_missing")
        if metrics["empty_windows"]:
            warnings.append(f"{market}:empty_windows")
        if not metrics["daily_exists"]:
            critical.append(f"{market}:daily_missing")
        if not metrics["finalized"]:
            warnings.append(f"{market}:daily_not_finalized")
        if not quality["exists"]:
            critical.append(f"{market}:quality_missing")
        elif quality["usable_for_analysis"] is False:
            critical.append(f"{market}:quality_unusable_for_analysis")
        if reports["report_file_count"] == 0:
            warnings.append(f"{market}:reports_missing")
        if not reports["ai_summary_exists"]:
            warnings.append(f"{market}:ai_summary_missing")

    status = "critical" if critical else ("warning" if warnings else "ok")
    report["summary"] = {
        "status": status,
        "critical": critical,
        "warnings": warnings,
    }


def exit_code(status: str) -> int:
    return {"ok": 0, "warning": 1, "critical": 2}.get(status, 2)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only P0 production daily checks")
    parser.add_argument("--date", required=True)
    parser.add_argument("--markets", default="HK,US")
    parser.add_argument("--output")
    parser.add_argument("--email", action="store_true", help="send the daily check report by email")
    return parser.parse_args()


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    args = parse_args()
    markets = [market.strip().upper() for market in args.markets.split(",") if market.strip()]
    report = run_daily_check(args.date, markets)
    if args.email or os.getenv("DAILY_CHECK_EMAIL_ENABLED", "false").lower() == "true":
        try:
            sent = send_daily_check_email(report, EmailConfig.from_env(os.environ))
            report.setdefault("daily_check_email", {})["sent"] = sent
        except Exception as exc:
            report.setdefault("daily_check_email", {})["sent"] = False
            report.setdefault("daily_check_email", {})["error"] = str(exc)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(exit_code(report["summary"]["status"]))


if __name__ == "__main__":
    main()
