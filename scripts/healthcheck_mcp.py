"""Health check CLI for Market Report Agent.

Usage:
  python scripts/healthcheck_mcp.py
  python scripts/healthcheck_mcp.py --json
  python scripts/healthcheck_mcp.py --json --provider mock
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def run_health(provider: str | None = None) -> dict:
    env_file = _PROJECT_ROOT / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)

    results = {
        "status": "ok",
        "service": "market-report-agent",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    effective_provider = provider or os.getenv("MARKET_DATA_PROVIDER", "")
    if not effective_provider and os.getenv("APP_ENV") == "test":
        effective_provider = "mock"

    mock_allowed = (
        provider == "mock"
        or os.getenv("APP_ENV") == "test"
        or os.getenv("SMOKE_TEST_MODE", "").lower() in ("1", "true")
    )

    if effective_provider == "mock" and not mock_allowed:
        results["checks"]["provider"] = {
            "ok": False,
            "provider": "mock",
            "error": "Mock provider is not allowed from production environment/config",
        }
    elif effective_provider == "mock":
        try:
            from clients.mock_market_data_client import MockMarketDataClient
            results["checks"]["provider"] = MockMarketDataClient().health_check()
        except Exception as exc:
            results["checks"]["provider"] = {"ok": False, "provider": "mock", "error": str(exc)}
    elif effective_provider in ("longbridge_mcp", "longbridge"):
        try:
            from clients.longbridge_mcp_client import LongbridgeMcpClient
            lb = LongbridgeMcpClient()
            results["checks"]["provider"] = lb.health_check()
        except Exception as exc:
            results["checks"]["provider"] = {
                "ok": False,
                "provider": "longbridge_mcp",
                "error": str(exc)[:300],
            }
    else:
        results["checks"]["provider"] = {
            "ok": False,
            "error": "No provider selected; use --provider mock or --provider longbridge_mcp",
        }

    # 3. Run logs availability
    try:
        from core.mcp_datastore import McpDataStore
        store = McpDataStore()
        runs = store.get_recent_runs(limit=1)
        results["checks"]["run_logs"] = {
            "ok": True,
            "recent_runs": len(runs),
        }
    except Exception as exc:
        results["checks"]["run_logs"] = {"ok": False, "error": str(exc)[:200]}

    # 4. Report storage
    try:
        reports = store.list_reports()
        results["checks"]["reports"] = {
            "ok": True,
            "count": len(reports),
        }
    except Exception as exc:
        results["checks"]["reports"] = {"ok": False, "error": str(exc)[:200]}

    # 5. Schema files
    schema_dir = _PROJECT_ROOT / "config" / "schemas"
    schema_files = [
        "quote.schema.json",
        "candle.schema.json",
        "intraday.schema.json",
        "market_status.schema.json",
        "market_report_dataset.schema.json",
    ]
    schemas_ok = True
    missing = []
    for f in schema_files:
        if not (schema_dir / f).exists():
            schemas_ok = False
            missing.append(f)
    results["checks"]["schemas"] = {
        "ok": schemas_ok,
        "missing": missing,
    }

    # 6. jsonschema availability
    try:
        import jsonschema  # noqa: F401
        results["checks"]["jsonschema"] = {"ok": True}
    except ImportError:
        results["checks"]["jsonschema"] = {
            "ok": False,
            "error": "jsonschema not installed — schema validation will fail",
        }
        results["status"] = "degraded"

    # 7. Tool policy
    try:
        from app.policy.tool_policy import LongbridgeToolPolicy
        policy = LongbridgeToolPolicy()
        trading_count = len(policy.trading_tools)
        account_count = len(policy.account_read_tools)
        results["checks"]["tool_policy"] = {
            "ok": True,
            "trading_tools_blocked": trading_count,
            "account_read_tools_disabled": account_count,
            "default_deny": True,
        }
    except Exception as exc:
        results["checks"]["tool_policy"] = {"ok": False, "error": str(exc)[:200]}

    # Aggregate status
    if not all(c.get("ok", False) for c in results["checks"].values() if isinstance(c, dict) and "ok" in c):
        results["status"] = "degraded"

    return results


def main():
    parser = argparse.ArgumentParser(description="Market Report Agent Health Check")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--provider", type=str, default=None, help="Provider to check (mock, longbridge_mcp)")
    args = parser.parse_args()

    health = run_health(provider=args.provider)

    if args.json:
        print(json.dumps(health, indent=2, ensure_ascii=False))
    else:
        print(f"Service: {health['service']}")
        print(f"Status:  {health['status']}")
        print(f"Time:    {health['timestamp']}")
        print()
        for name, check in health["checks"].items():
            if isinstance(check, dict):
                ok = check.get("ok", False)
                icon = "✓" if ok else "✗"
                print(f"  {icon} {name}: {check}")

    if health["status"] != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
