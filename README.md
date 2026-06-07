# API Report Agent

Read-only market-report workflow using the Longbridge official remote MCP:

`Longbridge MCP -> collect -> clean -> validate -> intraday/daily report -> notify`

The only production workflow entrypoint is `scripts/market_report_agent.py`.
`scripts/run_pipeline.py` and `clients/market_client.py` are deprecated legacy
Longbridge SDK paths and are not used by deploy or systemd.

## Safety And Auth

- Trading/write tools are permanently blocked.
- Account-read tools are disabled by default.
- Unknown MCP tools are denied.
- Real provider mode requires MCP protocol tool discovery before data calls.
- Production never silently falls back to mock.
- The repository does not implement Longbridge OAuth 2.1. It accepts an
  externally obtained authorized session header through
  `LONGBRIDGE_MCP_AUTH_HEADER`.

The discovered tools must map the internal quote, candlestick, intraday, and
trading-session operations. Discovery or mapping failure blocks the real provider.

## Project Structure

```text
clients/                     MarketDataClient, Longbridge MCP adapter, mock
app/policy/                  default-deny MCP tool policy
core/mcp_*.py                collect, clean, validate, schedule, report, notify
scripts/market_report_agent.py  production entrypoint
scripts/healthcheck_mcp.py   provider-aware health check
scripts/smoke_test.py        no-auth mock smoke test
scripts/deploy.sh            venv + systemd + health + smoke deployment
systemd/market-report-agent.service
tests/
```

Runtime artifacts under `.venv/`, `data/raw/`, `data/clean/`, `reports/`,
`logs/`, caches, and `.env` are ignored.

## Configuration

Requires Python 3.11+.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Production `.env`:

```env
APP_ENV=production
MARKET_DATA_PROVIDER=longbridge_mcp
LONGBRIDGE_MCP_URL=https://mcp.longbridge.com
LONGBRIDGE_MCP_AUTH_HEADER=Bearer <externally-obtained-token>
ACCOUNT_READ_ENABLED=false
```

Do not commit `.env` or tokens. Mock is permitted only through
`--provider mock`, `APP_ENV=test`, or the smoke-test workflow.

## Local Run

```bash
python scripts/market_report_agent.py --health --provider mock
python scripts/market_report_agent.py --once --provider mock
python scripts/market_report_agent.py --provider longbridge_mcp
```

The continuous scheduler uses market-local dates and two-hour window keys.
Daily reports require a completed session close, elapsed post-market delay,
known market status, and date-aligned quote/candle/intraday data.

## Test And Verify

```bash
python -m pytest -q
python scripts/healthcheck_mcp.py --json --provider mock
python scripts/market_report_agent.py --health --provider mock
python scripts/market_report_agent.py --once --provider mock
python scripts/market_report_agent.py --health --provider longbridge_mcp
python scripts/smoke_test.py
```

Without an authorized Longbridge session, the real-provider health command must
fail clearly with a non-zero exit code.

## ECS / EC2 / VPS Deployment

The deployment model is `venv + systemd`. Run:

```bash
sudo bash scripts/deploy.sh
```

`scripts/deploy.sh` installs dependencies, installs/restarts
`market-report-agent.service`, runs provider-aware health, then runs
`scripts/smoke_test.py` with venv Python. Its shell fallback is invoked with
`bash`, never Python. Critical failures exit non-zero.

Systemd operations:

```bash
sudo systemctl enable --now market-report-agent
sudo systemctl status market-report-agent
sudo systemctl restart market-report-agent
sudo journalctl -u market-report-agent -f
```

## Troubleshooting

- `LONGBRIDGE_MCP_AUTH_HEADER not set`: provide an externally authorized header.
- `discovery_failed`: verify endpoint, auth/session validity, network, and MCP SDK.
- `Required MCP operations not mapped`: inspect the provider's discovered tools.
- `missing_session_close`: provider did not supply a reliable completed close.
- `post-market delay not elapsed`: wait until the configured delay passes.
- `timestamps do not align`: provider data is stale or belongs to another session.
- `Provider not configured`: set `MARKET_DATA_PROVIDER=longbridge_mcp`; use explicit
  mock only for tests.

## Extension Boundary

Add provider integrations behind `MarketDataClient`. Do not call raw MCP tools
from report workflows, enable trading, or reintroduce the legacy SDK path as a
production route.
