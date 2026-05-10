from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from core.email_reporter import EmailConfig, send_email


PROJECT = "api-report-agent"
BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CHANNELS = ("email", "archive")


def notify(
    title: str,
    body: str,
    level: str = "info",
    channels: list[str] | None = None,
    attachments: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    selected_channels = normalize_channels(channels)
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    event = {
        "id": build_notification_id(now, title, body, metadata or {}),
        "project": PROJECT,
        "time": now,
        "level": normalize_level(level),
        "title": title,
        "body": body,
        "attachments": attachments or [],
        "metadata": metadata or {},
        "results": {},
    }

    ordered_channels = ["archive"] + [channel for channel in selected_channels if channel != "archive"]
    for channel in ordered_channels:
        try:
            if channel == "archive":
                event["results"][channel] = write_archive(event)
            elif channel == "email":
                event["results"][channel] = send_email_notification(event)
            elif channel == "telegram":
                event["results"][channel] = {"status": "ignored", "reason": "telegram is not supported in api-report-agent"}
            else:
                event["results"][channel] = {"status": "ignored", "reason": f"unsupported channel: {channel}"}
        except Exception as exc:
            event["results"][channel] = {"status": "error", "error": str(exc)}

    if "archive" in selected_channels:
        update_archive_results(event)
    return event


def normalize_channels(channels: list[str] | None) -> list[str]:
    if channels is None:
        text = os.getenv("NOTIFY_CHANNELS", ",".join(DEFAULT_CHANNELS))
        channels = [item.strip() for item in text.split(",")]
    normalized = []
    for channel in channels:
        name = str(channel).strip().lower()
        if name and name not in normalized:
            normalized.append(name)
    return normalized or list(DEFAULT_CHANNELS)


def normalize_level(level: str) -> str:
    value = str(level or "info").lower()
    return value if value in {"info", "warning", "error"} else "info"


def archive_dir() -> Path:
    configured = os.getenv("NOTIFICATION_ARCHIVE_DIR", "")
    if configured:
        return Path(configured)
    return BASE_DIR / "data" / "notifications"


def archive_path_for(time_text: str) -> Path:
    day = time_text[:10]
    return archive_dir() / f"{day}.jsonl"


def write_archive(event: dict[str, Any]) -> dict[str, Any]:
    path = archive_path_for(str(event["time"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, default=str))
        file.write("\n")
    return {"status": "ok", "path": str(path)}


def update_archive_results(event: dict[str, Any]) -> None:
    path = archive_path_for(str(event["time"]))
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index in range(len(lines) - 1, -1, -1):
            try:
                row = json.loads(lines[index])
            except json.JSONDecodeError:
                continue
            if row.get("id") == event.get("id"):
                row["results"] = event.get("results", {})
                lines[index] = json.dumps(row, ensure_ascii=False, default=str)
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
    except OSError:
        return


def send_email_notification(event: dict[str, Any]) -> dict[str, Any]:
    config = EmailConfig.from_env(os.environ)
    if not config.enabled or not config.is_ready():
        return {"status": "skipped", "reason": "email config is not ready"}

    message = EmailMessage()
    message["Subject"] = f"{config.subject_prefix} {event['title']}"
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    attachment_text = ""
    attachments = event.get("attachments") or []
    if attachments:
        attachment_text = "\n\nAttachments:\n" + "\n".join(f"- {item}" for item in attachments)
    message.set_content(str(event.get("body", "")) + attachment_text)
    send_email(config, message)
    return {"status": "ok", "recipients": list(config.recipients)}


def build_notification_id(time_text: str, title: str, body: str, metadata: dict[str, Any]) -> str:
    source = json.dumps(
        {
            "project": PROJECT,
            "time": time_text,
            "title": title,
            "body": body,
            "metadata": metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, source))
