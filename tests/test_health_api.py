import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.main import CollectRunRequest, get_report, health, require_api_token, run_collection_once, status_report, symbols
from core.runtime_support import BASE_DIR


def test_health_payload_is_dependency_light() -> None:
    assert health() == {"status": "ok", "service": "api-report-agent"}


def test_status_requires_token(monkeypatch) -> None:
    monkeypatch.setenv("API_TOKEN", "test-token")

    with pytest.raises(HTTPException) as exc:
        require_api_token(None)

    assert exc.value.status_code == 401


def test_status_returns_runtime_payload(monkeypatch) -> None:
    monkeypatch.setenv("API_TOKEN", "test-token")

    require_api_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token"))
    payload = status_report()

    assert payload["service"] == "api-report-agent"
    assert "health" in payload
    assert "runtime" in payload


def test_symbols_returns_registry_symbols(monkeypatch) -> None:
    monkeypatch.setenv("API_TOKEN", "test-token")

    payload = symbols()

    assert payload["count"] >= 1
    assert any(item["symbol"] == "QQQ.US" for item in payload["symbols"])


def test_get_report_returns_json_artifact(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("API_TOKEN", "test-token")
    report_dir = BASE_DIR / "data" / "reports" / "US"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "2099-01-02_health.json"
    report_path.write_text('{"status": "ok"}', encoding="utf-8")

    try:
        payload = get_report("US", "2099-01-02", "health")
    finally:
        report_path.unlink(missing_ok=True)

    assert payload["type"] == "health"
    assert payload["content"] == {"status": "ok"}


def test_collect_run_uses_mock_provider(monkeypatch) -> None:
    monkeypatch.setenv("API_TOKEN", "test-token")

    payload = run_collection_once(
        CollectRunRequest(provider="mock", symbols=["QQQ.US"], output_dir="tests/api_collect_output")
    )

    assert payload["status"] == "ok"
