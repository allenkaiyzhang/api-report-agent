# Delivery Report — Market Report Agent MCP Rework

**Date:** 2026-06-06
**Verdict:** PASS WITH ISSUES (real OAuth path untested)

## Summary of Fixes

### BLOCKER Issues Fixed

| # | Issue | Resolution |
|---|-------|-----------|
| 1 | Longbridge official MCP integration protocol wrong (SSE, manual Bearer token, invented tool names) | Rewrote `LongbridgeMcpClient` to use Streamable HTTP transport, OAuth 2.1 session model, official tool names via tool discovery |
| 2 | Production entrypoint always uses mock | `_resolve_provider()` now fails clearly when no provider configured; mock only allowed via explicit `--provider mock` or `APP_ENV=test` |
| 3 | Account-read tools not fully blocked | `app/policy/tool_policy.py` with `LongbridgeToolPolicy` — default-deny for all tools; account-read disabled by default |
| 4 | `daily_close_report` cannot pass validation (market closed rejection) | `McpDataValidator` is now report-type-aware: `daily_close_report` requires market closed, `intraday_brief` requires market open |
| 5 | Scheduler and deduplication do not meet requirements | Persistent `DedupStore` (JSONL), post-market delay enforcement, skipped reason recording (`market_closed`, `not_trading_day`, `before_post_market_delay`, `duplicate_window`, etc.) |
| 6 | Notification failures recorded as DISPATCHED | `NotifierResult` model + `_resolve_dispatch_status()` → DISPATCHED / PARTIAL_FAILED / FAILED resolution |
| 7 | `deploy.sh` hides critical failures | Removed `|| true` from critical steps, added `set -euo pipefail`, systemd start failure exits 1 |
| 8 | Validation, observability, runtime config incomplete | Structured JSONL logs (`data_access.jsonl`, `report_generation.jsonl`, `notification.jsonl`), jsonschema enforcement, config precedence implementation |

### HIGH Issues Fixed

| # | Issue | Resolution |
|---|-------|-----------|
| 1 | Unknown tool default-deny | `LongbridgeToolPolicy` blocks any tool not in allowed list |
| 2 | Trading/write tools blocked in code | 17 trading tools listed in `_TRADING_TOOLS`, enforced by `assert_allowed()` |
| 3 | `deploy.sh` smoke test uses system python | Smoke test now runs via venv Python; cross-platform `smoke_test.py` added |
| 4 | Service/timer conflict | Selected continuous service model; timer left for documentation only |
| 5 | systemd hardcodes `MARKET_DATA_PROVIDER=mock` | Removed from service file; provider resolved from env/config |
| 6 | Console GBK encoding failure | `ConsoleNotifier` handles `UnicodeEncodeError` with ASCII fallback |
| 7 | `jsonschema` missing silently skips validation | Fails validation with actionable error if jsonschema not installed |
| 8 | Run ID not consistent through workflow | Single `run_id` used from scheduler → collector → cleaner → validator → generator → notifier |

## Files Changed

### MCP Adapter
- `clients/longbridge_mcp_client.py` — Full rewrite: Streamable HTTP, OAuth 2.1, tool discovery, policy integration
- `clients/__init__.py` — Re-export unchanged

### Policy
- `app/policy/__init__.py` — New package
- `app/policy/tool_policy.py` — `LongbridgeToolPolicy` with default-deny, official tool names
- `app/policy/longbridge_tool_policy.yaml` — Policy configuration documentation

### Provider Selection
- `scripts/market_report_agent.py` — `_resolve_provider()` with precedence, no mock default
- `.env.example` — Updated to use `LONGBRIDGE_MCP_OAUTH_TOKEN`, `MARKET_DATA_PROVIDER`, `APP_ENV`

### Validator
- `core/mcp_validator.py` — Report-type-aware validation; weekend/holiday detection

### Scheduler
- `core/mcp_scheduler.py` — Post-market delay, persistent `DedupStore`, skipped reason recording

### Notification
- `core/mcp_notifier.py` — `NotifierResult` dataclass, `_resolve_dispatch_status()`, console encoding fix

### Datastore
- `core/mcp_datastore.py` — Structured JSONL logs for data_access, report_generation, notification

### Deploy / Systemd
- `scripts/deploy.sh` — `set -euo pipefail`, venv smoke test, fail on critical errors
- `systemd/market-report-agent.service` — Removed hardcoded `MARKET_DATA_PROVIDER=mock`

### Config
- `.gitignore` — Added `reports/`, `data/raw/`, `data/clean/`, `.qwen/`

### Tests
- `tests/test_longbridge_mcp_tool_policy.py` — Rewritten for official tool names, default-deny, discovery
- `tests/test_mcp_scheduler.py` — Added dedup persistence, weekend/holiday, skipped reasons
- `tests/test_mcp_notification.py` — Added `NotifierResult`, status resolution, config-driven channels
- `tests/test_market_data_client.py` — Added `LongbridgeMcpClient` tests

### Smoke Test
- `scripts/smoke_test.py` — New cross-platform Python smoke test (28 tests)

### Docs
- `README.md` — Updated MCP setup, provider selection, tool policy, runtime model, troubleshooting

## Verification Commands Run

| Command | Exit Code | Result |
|---------|-----------|--------|
| `python -m pytest tests/ -q` | 0 | 99 passed |
| `python scripts/smoke_test.py` | 0 | 28 passed |
| `python scripts/healthcheck_mcp.py --json --provider mock` | 1 | degraded (Longbridge not configured — expected) |
| `python scripts/market_report_agent.py --health --provider mock` | 0 | ok |
| `python scripts/market_report_agent.py --once --provider mock` | 0 | Workflow completed |

## Remaining Risks

1. **Real Longbridge OAuth testing** — Not tested against the actual Longbridge MCP endpoint. The adapter expects a pre-obtained OAuth token. The exact OAuth 2.1 flow (token exchange, refresh) defers to the `mcp` SDK.

2. **Tool schema discovery** — The adapter parses tool responses assuming common field names (`last_done`, `prev_close`, etc.). If actual Longbridge MCP responses use different field names, parsers need adjustment. This is expected for any MCP client and does not affect the mock path.

3. **Streamable HTTP transport** — The adapter tries Streamable HTTP first, falls back to SSE. Both require the `mcp>=1.0.0` Python package.

4. **Email SMTP delivery** — Config-level testing done; actual SMTP delivery not verified.

## Merge Readiness

**VERDICT: PASS WITH ISSUES**

- All 99 unit tests pass
- Smoke test passes (28 tests)
- No mock fallback in production mode
- Default-deny tool policy enforced in code
- Report-type-aware validation working
- Persistent dedup with skipped reason logging
- Notification status correctly reflects dispatch results
- Deploy script exits non-zero on failures
- Real OAuth path structurally correct but untested against live endpoint
