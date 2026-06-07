#!/usr/bin/env python
"""Cross-platform verification script for API Report Agent."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_command(
    args: list[str], check_fail: bool = False, capture: bool = False
) -> subprocess.CompletedProcess:
    print(f"\nRunning command: {' '.join(args)}")
    sys.stdout.flush()
    try:
        res = subprocess.run(
            args,
            text=True,
            capture_output=capture,
            check=False,
        )
        print(f"Exit code: {res.returncode}")
        if check_fail and res.returncode != 0:
            print(f"ERROR: Command failed unexpectedly. Exit code: {res.returncode}")
            sys.exit(res.returncode)
        return res
    except Exception as exc:
        print(f"ERROR: Failed to run command: {exc}")
        sys.exit(1)


def main() -> None:
    print("==================================================")
    print("           API Report Agent Verification          ")
    print("==================================================")

    # Step 1: Run unit tests
    # If on Windows, specify custom --basetemp to avoid standard temp folder permissions issue
    pytest_args = [sys.executable, "-m", "pytest", "-q"]
    if os.name == "nt":
        basetemp_dir = Path(__file__).resolve().parent.parent / "pytest_temp"
        basetemp_dir.mkdir(exist_ok=True)
        pytest_args.append(f"--basetemp={basetemp_dir}")
    run_command(pytest_args, check_fail=True)

    # Step 2: Run smoke test
    run_command([sys.executable, "scripts/smoke_test.py"], check_fail=True)

    # Step 3: Run mock health check
    run_command(
        [sys.executable, "scripts/market_report_agent.py", "--health", "--provider", "mock"],
        check_fail=True,
    )

    # Step 4: Run mock once report generation
    run_command(
        [sys.executable, "scripts/market_report_agent.py", "--once", "--provider", "mock"],
        check_fail=True,
    )

    # Step 5: Verify longbridge_mcp health without auth fails with expected errors
    print(
        "\nRunning command (expecting failure): python scripts/market_report_agent.py --health --provider longbridge_mcp"
    )
    res_mcp = subprocess.run(
        [
            sys.executable,
            "scripts/market_report_agent.py",
            "--health",
            "--provider",
            "longbridge_mcp",
        ],
        text=True,
        capture_output=True,
    )
    print(f"Exit code: {res_mcp.returncode}")

    if res_mcp.returncode == 0:
        print("ERROR: longbridge_mcp health check succeeded even without auth configured!")
        sys.exit(1)

    output = res_mcp.stdout + "\n" + res_mcp.stderr
    print("Health Output:")
    print(output)

    # Verify that it did not fall back to mock
    if "mock" in output.lower():
        print("ERROR: longbridge_mcp provider health check fallback to mock detected!")
        sys.exit(1)

    # Verify that the expected error message exists
    if "LONGBRIDGE_MCP_AUTH_HEADER not set" not in output and "not_configured" not in output:
        print(
            "ERROR: Did not see expected 'LONGBRIDGE_MCP_AUTH_HEADER not set' or 'not_configured' error."
        )
        sys.exit(1)

    print("SUCCESS: longbridge_mcp without auth failed correctly as expected.")

    # Linux-only checks
    if os.name != "nt":
        print("\n--- Running Linux-Specific Verification Checks ---")

        # Step 6: bash -n scripts/deploy.sh
        run_command(["bash", "-n", "scripts/deploy.sh"], check_fail=True)

        # Step 7: Render systemd service template to a temp file
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_output = Path(tmpdir) / "market-report-agent.service"
            deploy_root = Path(tmpdir) / "app"
            deploy_root.mkdir()
            venv_dir = deploy_root / ".venv"

            render_args = [
                sys.executable,
                "scripts/render_systemd_service.py",
                "--template",
                "systemd/market-report-agent.service.template",
                "--output",
                str(temp_output),
                "--deploy-root",
                str(deploy_root),
                "--venv-dir",
                str(venv_dir),
                "--service-user",
                "apiagent",
                "--service-group",
                "apiagent",
            ]
            run_command(render_args, check_fail=True)

            # Step 8: Verify no unresolved placeholders
            service_content = temp_output.read_text(encoding="utf-8")
            if "{{" in service_content or "}}" in service_content:
                print("ERROR: Rendered service file has unresolved placeholders")
                print(service_content)
                sys.exit(1)

            # Step 9: Verify expected systemd paths
            expected_wd = f"WorkingDirectory={deploy_root}"
            expected_env = f"EnvironmentFile={deploy_root}/.env"
            expected_exec = f"ExecStart={venv_dir}/bin/python scripts/market_report_agent.py"

            if expected_wd not in service_content:
                print(f"ERROR: Rendered service missing expected WorkingDirectory: {expected_wd}")
                sys.exit(1)
            if expected_env not in service_content:
                print(f"ERROR: Rendered service missing expected EnvironmentFile: {expected_env}")
                sys.exit(1)
            if expected_exec not in service_content:
                print(f"ERROR: Rendered service missing expected ExecStart: {expected_exec}")
                sys.exit(1)

            print("SUCCESS: Systemd template rendered and validated successfully with correct paths.")

    print("\n==================================================")
    print("         ALL VERIFICATION CHECKS PASSED           ")
    print("==================================================")


if __name__ == "__main__":
    main()
