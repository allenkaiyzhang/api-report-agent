from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AIAnalysisConfig:
    enabled: bool
    provider: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    gemini_api_key: str
    gemini_model: str
    fallback_provider: str
    timeout_seconds: int

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "AIAnalysisConfig":
        return cls(
            enabled=env.get("AI_ANALYSIS_ENABLED", "false").lower() == "true",
            provider=env.get("AI_PROVIDER", "deepseek").lower(),
            deepseek_api_key=env.get("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=env.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            gemini_api_key=env.get("GEMINI_API_KEY", ""),
            gemini_model=env.get("GEMINI_MODEL", "gemini-2.5-flash"),
            fallback_provider=env.get("AI_FALLBACK_PROVIDER", "gemini").lower(),
            timeout_seconds=int(env.get("AI_TIMEOUT_SECONDS", "30") or "30"),
        )

    def provider_ready(self, provider: str) -> bool:
        if provider == "mock":
            return True
        if provider == "deepseek":
            return bool(self.deepseek_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        return False


def analyze_market_report(config: AIAnalysisConfig | None, payload: dict[str, Any]) -> str:
    if config is None or not config.enabled:
        return ""

    prompt = build_analysis_prompt(payload)
    providers = [config.provider]
    if config.fallback_provider and config.fallback_provider not in providers:
        providers.append(config.fallback_provider)

    errors = []
    for provider in providers:
        if not config.provider_ready(provider):
            errors.append(f"{provider}: missing api key")
            continue
        try:
            if provider == "mock":
                return mock_analysis(payload)
            if provider == "deepseek":
                return call_deepseek(config, prompt)
            if provider == "gemini":
                return call_gemini(config, prompt)
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
    return "AI analysis unavailable: " + "; ".join(errors)


def mock_analysis(payload: dict[str, Any]) -> str:
    report_type = payload.get("report_type", "report")
    market = payload.get("market", "")
    trading_date = payload.get("trading_date", "")
    raw_lines = payload.get("raw_lines", 0)
    normalized_lines = payload.get("normalized_lines", 0)
    parse_errors = payload.get("raw_json_parse_errors", 0)
    symbol_count = payload.get("symbol_count", 0)
    lines = [
        f"Mock analysis: {market} {trading_date} {report_type} report generated.",
        f"本报告包含 raw={raw_lines} 条、normalized={normalized_lines} 条、symbols={symbol_count} 个。",
    ]
    if parse_errors:
        lines.append(f"注意：raw JSON parse errors={parse_errors}，需要检查原始数据质量。")
    else:
        lines.append("未发现 raw JSON 解析错误。")

    symbol_summary = payload.get("symbol_summary") or []
    if symbol_summary:
        active = ", ".join(str(item.get("symbol", "")) for item in symbol_summary[:5])
        lines.append(f"本窗口有数据的标的：{active}。")

    return "\n".join(lines)


def build_analysis_prompt(payload: dict[str, Any]) -> str:
    compact_payload = compact_for_prompt(payload)
    if "daily" in compact_payload and "current_day" not in compact_payload:
        compact_payload["current_day"] = compact_payload["daily"]
    history = compact_payload.get("history_context", {})
    history_available = bool(isinstance(history, dict) and history.get("history_available"))
    return (
        "You are analyzing deterministic market data pipeline output. "
        "Do not invent data. Do not recalculate metrics beyond the supplied fields. "
        "For daily reports, analyze current_day together with history_context. "
        "Use history_context only as compressed metrics history; never ask for or infer raw/normalized JSONL. "
        "If history_context is missing or insufficient, explicitly say the historical context is insufficient. "
        "Summarize data quality, notable price/volume movement, risk signals, historical trend, and any gaps. "
        "Answer in concise Chinese.\n\n"
        f"History context available: {history_available}\n"
        f"Payload:\n{json.dumps(compact_payload, ensure_ascii=False, default=str)}"
    )


def call_deepseek(config: AIAnalysisConfig, prompt: str) -> str:
    url = config.deepseek_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": config.deepseek_model,
        "messages": [
            {"role": "system", "content": "You are a deterministic market data report analyst."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    response = post_json(
        url,
        body,
        headers={
            "Authorization": f"Bearer {config.deepseek_api_key}",
            "Content-Type": "application/json",
        },
        timeout_seconds=config.timeout_seconds,
    )
    choices = response.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    return str(message.get("content", "")).strip()


def call_gemini(config: AIAnalysisConfig, prompt: str) -> str:
    model = config.gemini_model
    if not model.startswith("models/"):
        model = f"models/{model}"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent"
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 900,
        },
    }
    response = post_json(
        url,
        body,
        headers={
            "x-goog-api-key": config.gemini_api_key,
            "Content-Type": "application/json",
        },
        timeout_seconds=config.timeout_seconds,
    )
    candidates = response.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "\n".join(str(part.get("text", "")).strip() for part in parts if part.get("text")).strip()


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body[:500]}") from exc


def compact_for_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "report_type",
        "market",
        "trading_date",
        "period_start",
        "period_end",
        "raw_lines",
        "normalized_lines",
        "raw_json_parse_errors",
        "symbol_count",
        "window_count",
        "configured_windows",
        "quality",
        "symbol_summary",
        "daily",
        "history_context",
    ]
    return {key: payload[key] for key in keys if key in payload}
