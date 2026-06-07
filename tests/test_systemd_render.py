"""Systemd template rendering and deploy path consistency tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.render_systemd_service import render_service

ROOT = Path(__file__).resolve().parents[1]


def test_custom_deploy_root_and_venv_are_rendered(tmp_path: Path) -> None:
    deploy_root = tmp_path / "custom-app"
    deploy_root.mkdir()
    venv_dir = tmp_path / "custom-venv"
    output = tmp_path / "market-report-agent.service"

    content = render_service(
        ROOT / "systemd" / "market-report-agent.service.template",
        output,
        deploy_root,
        venv_dir,
        "report-user",
        "report-group",
    )

    assert f"WorkingDirectory={deploy_root}" in content
    assert f"EnvironmentFile={deploy_root}/.env" in content
    assert f"ExecStart={venv_dir}/bin/python scripts/market_report_agent.py" in content
    assert "User=report-user" in content
    assert "Group=report-group" in content
    assert "{{" not in content
    assert "/opt/api-report-agent" not in content


def test_render_fails_for_missing_deploy_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DEPLOY_ROOT does not exist"):
        render_service(
            ROOT / "systemd" / "market-report-agent.service.template",
            tmp_path / "unit",
            tmp_path / "missing",
            tmp_path / "venv",
            "deploy",
            "deploy",
        )


def test_deploy_uses_rendered_template_and_has_no_hardcoded_opt() -> None:
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    template = (ROOT / "systemd" / "market-report-agent.service.template").read_text(
        encoding="utf-8"
    )

    assert "render_systemd_service.py" in deploy
    assert "DEPLOY_ROOT=" in deploy
    assert "VENV_DIR=" in deploy
    assert "--dry-run" in deploy
    assert "/opt/api-report-agent" not in deploy
    assert "/opt/api-report-agent" not in template
