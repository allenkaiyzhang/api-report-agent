from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping

import yaml


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = BASE_DIR / "config" / "registry.yaml"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or DEFAULT_REGISTRY_PATH
    if not registry_path.exists():
        return {}
    try:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Config registry YAML is invalid: {registry_path}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Config registry YAML must contain a mapping: {registry_path}")
    return data


def apply_registry_to_env(
    path: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> dict[str, str]:
    target = environ if environ is not None else os.environ
    values = registry_to_env(load_registry(path))
    for key, value in values.items():
        if override or key not in target:
            target[key] = value
    return values


def registry_to_env(registry: Mapping[str, Any]) -> dict[str, str]:
    service = _mapping(registry.get("service"))
    market_data = _mapping(registry.get("market_data"))
    collection = _mapping(market_data.get("collection"))
    extended_collection = _mapping(market_data.get("extended_collection"))
    pipeline = _mapping(registry.get("pipeline"))
    notifications = _mapping(registry.get("notifications"))
    email = _mapping(registry.get("email"))
    smtp = _mapping(email.get("smtp"))
    ai = _mapping(registry.get("ai"))
    deepseek = _mapping(ai.get("deepseek"))
    gemini = _mapping(ai.get("gemini"))

    values = {
        "HOST": service.get("host"),
        "PORT": service.get("port"),
        "LOG_LEVEL": service.get("log_level"),
        "DATABASE_URL": service.get("database_url"),
        "DATA_DIR": service.get("data_dir"),
        "PYTHONUNBUFFERED": service.get("python_unbuffered"),
        "MARKET_DATA_PROVIDER": market_data.get("provider"),
        "DATA_COLLECTION_INTERVAL_SECONDS": collection.get("interval_seconds"),
        "DATA_COLLECTION_OUTPUT_DIR": collection.get("output_dir"),
        "DATA_COLLECTION_FILE_TIMEZONE": collection.get("file_timezone"),
        "EXTENDED_COLLECTION_INTERVAL_SECONDS": extended_collection.get("interval_seconds"),
        "PIPELINE_LOOP_SLEEP_SECONDS": pipeline.get("loop_sleep_seconds"),
        "PIPELINE_FORCE_REBUILD": pipeline.get("force_rebuild"),
        "REFERENCE_FORCE_REBUILD": pipeline.get("reference_force_rebuild"),
        "REFERENCE_BUILD_ON_MARKET_OPEN": pipeline.get("reference_build_on_market_open"),
        "NOTIFY_CHANNELS": _join_list(notifications.get("channels")),
        "NOTIFICATION_ARCHIVE_DIR": notifications.get("archive_dir"),
        "EMAIL_ENABLED": email.get("enabled"),
        "EMAIL_INTRADAY_ENABLED": email.get("intraday_enabled"),
        "EMAIL_INTRADAY_INTERVAL_HOURS": email.get("intraday_interval_hours"),
        "SMTP_HOST": smtp.get("host"),
        "SMTP_PORT": smtp.get("port"),
        "SMTP_USERNAME": smtp.get("username"),
        "SMTP_USE_TLS": smtp.get("use_tls"),
        "SMTP_FORCE_IPV4": smtp.get("force_ipv4"),
        "SMTP_RETRIES": smtp.get("retries"),
        "SMTP_RETRY_SECONDS": smtp.get("retry_seconds"),
        "EMAIL_FROM": email.get("from"),
        "EMAIL_TO": _join_list(email.get("to")),
        "EMAIL_SUBJECT_PREFIX": email.get("subject_prefix"),
        "DAILY_CHECK_EMAIL_ENABLED": email.get("daily_check_enabled"),
        "AI_ANALYSIS_ENABLED": ai.get("analysis_enabled"),
        "AI_PROVIDER": ai.get("provider"),
        "AI_FALLBACK_PROVIDER": ai.get("fallback_provider"),
        "AI_TIMEOUT_SECONDS": ai.get("timeout_seconds"),
        "DEEPSEEK_BASE_URL": deepseek.get("base_url"),
        "DEEPSEEK_MODEL": deepseek.get("model"),
        "GEMINI_MODEL": gemini.get("model"),
    }
    return {key: _stringify(value) for key, value in values.items() if value is not None}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _join_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value)


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
