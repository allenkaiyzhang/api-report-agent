#!/usr/bin/env python
"""Render and validate the market-report-agent systemd unit template."""

from __future__ import annotations

import argparse
from pathlib import Path

PLACEHOLDERS = {
    "{{DEPLOY_ROOT}}": "deploy_root",
    "{{VENV_DIR}}": "venv_dir",
    "{{SERVICE_USER}}": "service_user",
    "{{SERVICE_GROUP}}": "service_group",
}


def render_service(
    template_path: Path,
    output_path: Path,
    deploy_root: Path,
    venv_dir: Path,
    service_user: str,
    service_group: str,
) -> str:
    if not deploy_root.is_dir():
        raise ValueError(f"DEPLOY_ROOT does not exist: {deploy_root}")

    content = template_path.read_text(encoding="utf-8")
    values = {
        "deploy_root": str(deploy_root),
        "venv_dir": str(venv_dir),
        "service_user": service_user,
        "service_group": service_group,
    }
    for placeholder, key in PLACEHOLDERS.items():
        content = content.replace(placeholder, values[key])

    unresolved = [placeholder for placeholder in PLACEHOLDERS if placeholder in content]
    if unresolved or "{{" in content or "}}" in content:
        raise ValueError(f"Rendered service contains unresolved placeholders: {unresolved}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8", newline="\n")
    return content


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--venv-dir", type=Path, required=True)
    parser.add_argument("--service-user", default="deploy")
    parser.add_argument("--service-group", default="deploy")
    args = parser.parse_args()

    content = render_service(
        args.template,
        args.output,
        args.deploy_root.resolve(),
        args.venv_dir.resolve(),
        args.service_user,
        args.service_group,
    )
    print(f"Rendered systemd unit: {args.output}")
    print(f"WorkingDirectory={args.deploy_root.resolve()}")
    print(f"ExecStart={args.venv_dir.resolve()}/bin/python scripts/market_report_agent.py")
    if "{{" in content:
        raise SystemExit("ERROR: unresolved systemd template placeholder")


if __name__ == "__main__":
    main()
