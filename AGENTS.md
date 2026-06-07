# AGENTS.md

## Production entrypoint
Use scripts/market_report_agent.py as the production MCP workflow.

## Legacy path
scripts/run_pipeline.py and clients/market_client.py are legacy unless explicitly migrated.

## Safety
- Never enable trading tools.
- Unknown Longbridge MCP tools are denied by default.
- Account-read tools are disabled by default.
- Production mode must never silently fall back to mock.

## Verification
Run:
- python -m pytest -q
- python scripts/healthcheck_mcp.py --json --provider mock
- python scripts/smoke_test.py
- python scripts/market_report_agent.py --health --provider mock
- python scripts/market_report_agent.py --once --provider mock
- python scripts/market_report_agent.py --health --provider longbridge_mcp

Expected:
- mock paths pass
- longbridge_mcp without auth fails clearly and exits non-zero
- real MCP mode requires protocol tool discovery and never falls back to mock
- reports are generated only from validated datasets
