from app.main import health


def test_health_payload_is_dependency_light() -> None:
    assert health() == {"status": "ok", "service": "api-report-agent"}
