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
systemd/market-report-agent.service.template
tests/
```

Runtime artifacts under `.venv/`, `data/raw/`, `data/clean/`, `reports/`,
`logs/`, caches, and `.env` are ignored.

## Dependencies

- **Production / deploy:** `requirements.txt` — minimal runtime dependencies (used by `scripts/deploy.sh`).
- **CI / dev:** `requirements-dev.txt` — includes `pytest`, `httpx`, and all production dependencies
  via `-r requirements.txt`. Use this for local development and testing.

## Configuration

Requires Python 3.11+.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt   # CI / dev
# or: pip install -r requirements.txt # production only
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

The smoke test and mocked MCP transport tests do not prove real Longbridge data
retrieval. Real integration requires an externally authorized MCP session,
successful tool discovery against the Longbridge endpoint, and compatible
provider response schemas.

## Validation Model

This repository follows a multi-stage validation model designed to prevent environment drift between Windows development machines and the Linux production system:

1. **Windows Local = Fast Local Pre-check:** Fast local verification of codebase, unit tests, and smoke scenarios via `scripts/verify.py`.
2. **GitHub Actions Ubuntu CI = Merge Gate:** Rigorously runs on every `push` and `pull_request` to verify cross-platform correctness under Ubuntu with Python 3.11 and 3.12, syntax-checks Bash deployment scripts, and validates systemd templates.
3. **workflow_dispatch CD = Manual Release Gate:** A manually triggered deployment workflow in GitHub Actions that first verifies the target commit's CI workflow has succeeded via the GitHub API, then deploys via SSH with network prechecks (DNS and HTTP connectivity to GitHub), uncommitted working tree checks, exact SHA checkout, and remote orchestration. The CD gate **refuses to deploy** any commit whose CI has not passed.
4. **ECS/VPS Post-Deploy Verify = Production Gate:** Executes on the actual runtime server via `scripts/post_deploy_verify.sh` to verify the systemd unit, inspect log streams, run provider-specific health validation, and verify smoke tests.

**Important Environment & Retrieval Rules:**
- **Local passing does not prove Linux deployability:** Line-endings, shell syntax (bash syntax validation), and systemd service constructs must be checked on a Linux platform or via GitHub Actions.
- **CI does not prove real Longbridge retrieval:** The automated CI runner and smoke tests use mocked connections and do not connect to live endpoints. Real integration tests are run out-of-band on staging or production.
- **`longbridge_mcp` health check without auth must fail:** Running the health check on real `longbridge_mcp` without `LONGBRIDGE_MCP_AUTH_HEADER` configured is expected to fail with exit code `1` and a descriptive error message. It will never silently fall back to mock data.
- **Real production retrieval requires:**
  - `LONGBRIDGE_MCP_AUTH_HEADER` set with an externally authorized Bearer token.
  - Successful tool discovery against the live Longbridge MCP endpoint.
  - Compatibility of live responses with configured schemas.

### Validation Commands

#### Windows / Local:
```bash
python scripts/verify.py
```

#### Linux / Local:
```bash
python scripts/verify.py
bash -n scripts/deploy.sh
```

#### GitHub Actions:
Runs automatically on every `push` and `pull_request` to the `main` branch.

#### ECS / VPS Deployment & Verification:
To ensure environment hygiene, **always deploy only GitHub commits that have passed the Ubuntu CI gate**. Do not `scp` a dirty or untested Windows working tree to the production machine.

Execute the manual CD workflow from the GitHub Actions tab (under the "Deploy to ECS" workflow) with the target commit SHA, or perform manual verification on the server:
```bash
cd /opt/api-report-agent
# (Make sure to run post-deployment validation scripts)
bash scripts/post_deploy_verify.sh
```

## ECS / EC2 / VPS Deployment

### Prerequisites

The deploy user (running `scripts/deploy.sh` or `scripts/post_deploy_verify.sh`)
requires **passwordless sudo** for `install`, `systemctl`, and `journalctl`.
Add to `/etc/sudoers` or `/etc/sudoers.d/<user>`:

```
deploy ALL=(ALL) NOPASSWD: /usr/bin/install, /usr/bin/systemctl, /usr/bin/journalctl
```

Verify with:

```bash
sudo -n true
sudo -n systemctl status market-report-agent --no-pager
```

The scripts auto-detect root vs non-root and will fail clearly if passwordless
sudo is not configured.

### Deployment

The deployment model is `venv + systemd`. Run:

```bash
sudo bash scripts/deploy.sh
```

By default, `DEPLOY_ROOT` is the current repository root and `VENV_DIR` is
`$DEPLOY_ROOT/.venv`. A custom `DEPLOY_ROOT` must already contain the project
checkout. Set both environment variables to use custom paths:

```bash
sudo DEPLOY_ROOT=/srv/api-report-agent VENV_DIR=/srv/venvs/api-report-agent \
  bash scripts/deploy.sh
```

The deploy script renders `systemd/market-report-agent.service.template` with
the selected project root, venv, and configurable `SERVICE_USER` /
`SERVICE_GROUP`. It rejects missing roots and unresolved placeholders. Structural
rendering can be checked without installing or restarting systemd:

```bash
bash scripts/deploy.sh --dry-run
```

`scripts/deploy.sh` installs dependencies (default: verbose `pip install` output; set `PIP_QUIET=1` to suppress), installs/restarts the rendered
`market-report-agent.service`, runs provider-aware health, then runs
`scripts/smoke_test.py` with venv Python. Its shell fallback is invoked with
`bash`, never Python. Critical failures exit non-zero.

`scripts/post_deploy_verify.sh` checks the systemd service is running via
`systemctl is-active` and exits non-zero if the service is inactive or failed.
It then inspects journal logs, runs smoke tests, and performs a provider-aware
health check.

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
- **DNS Failure / `Could not resolve host github.com`:**
  - *Symptom:* The remote deployment precheck fails to resolve `github.com`.
  - *Cause:* Misconfigured or inactive DNS server under `/etc/resolv.conf`.
  - *Fix:* Verify internet connectivity on the server. Inspect and repair `/etc/resolv.conf` (e.g. add `nameserver 8.8.8.8`).
- **Dirty Working Tree (tracked modifications):**
  - *Symptom:* CD fails stating tracked files have been modified on the remote host.
  - *Cause:* Local edits to version-controlled files exist on the deployment server.
  - *Fix:* Stash or commit local modifications on the server. If deployment is needed
    regardless, use the `allow_dirty` workflow parameter.
  - *Chmod-only changes:* If the modifications are only executable-bit changes
    (common for `scripts/*.sh`), either commit the executable bits or run
    `git config core.filemode false` on the deploy host to ignore filemode changes.
  - *Note:* `.gitignore` does not affect tracked files such as `scripts/*.sh` —
    if a tracked script has local modifications, it will block deployment regardless
    of `.gitignore` entries.
- **Stale .gitignore on remote host:**
  - *Symptom:* Deployment previously failed even after `.gitignore` was updated in the
    repository, because the remote host still used the old `.gitignore`.
  - *Cause:* `.gitignore` changes only apply after the remote repo has fetched the
    commit containing them. The CD workflow previously checked the working tree
    before fetching, so the stale `.gitignore` was used.
  - *Fix:* The CD workflow now fetches before checking the working tree and excludes
    known runtime data paths (`data/metrics/`, `data/normalized/`, `data/quality/`,
    `data/archive/`, `data/notifications/`, `logs/`, `*.log`) from blocking
    deployment. Untracked runtime data triggers a warning but does not block.
- **Missing `MARKET_DATA_PROVIDER`:**
  - *Symptom:* Deployment exits with `ERROR: MARKET_DATA_PROVIDER is missing or empty in .env`.
  - *Cause:* The production `.env` file does not specify the provider.
  - *Fix:* Ensure `.env` is populated with `MARKET_DATA_PROVIDER=longbridge_mcp` or `MARKET_DATA_PROVIDER=mock`.
- **Systemd Restart Failure:**
  - *Symptom:* `deploy.sh` fails with `ERROR: market-report-agent failed to start`.
  - *Cause:* Missing folder permissions, invalid `.env` configuration, or python runtime error in background execution.
  - *Fix:* Run `systemctl status market-report-agent` or `journalctl -u market-report-agent -n 100 --no-pager` to inspect traceback.
- **Smoke Test Failure:**
  - *Symptom:* Smoke tests fail during the verification phase.
  - *Cause:* Import error, dependency mismatch, or invalid JSON schemas.
  - *Fix:* Ensure dependencies match `requirements.txt` and python versions are correct. Run `python scripts/smoke_test.py --verbose`.

## Extension Boundary

Add provider integrations behind `MarketDataClient`. Do not call raw MCP tools
from report workflows, enable trading, or reintroduce the legacy SDK path as a
production route.
