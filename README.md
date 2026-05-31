# Market Data Pipeline

中文文档: [README.zh-CN.md](README.zh-CN.md)

api-report-agent is a deterministic market data pipeline for HK and US market data.

It is intentionally not an Agent platform. Pipeline flow, collection, file writes, metrics, quality checks, replay, and market calendar logic are implemented with deterministic Python code.

## Runtime Shape

```text
systemd
  -> scripts/run_pipeline.py
  -> market session guard
  -> collector
  -> data/raw
  -> data/normalized
  -> data/metrics
  -> data/quality
```

## Configuration

Non-sensitive configuration lives in `config/registry.yaml`. This file is the project configuration registry and is safe to commit. It stores repeated operational settings such as provider selection, collection intervals, output paths, notification routing, email delivery options, AI model names, and watched symbols.

`.env` is reserved for sensitive values only: API tokens, provider credentials, and passwords. Do not add non-sensitive runtime settings to `.env`; add them to `config/registry.yaml` and map them in `core/config_registry.py` if code needs an environment-compatible key during migration.

Recommended first-time setup:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Registry example:

```yaml
market_data:
  provider: mock
  collection:
    interval_seconds: 120
    output_dir: data/raw

pipeline:
  loop_sleep_seconds: 10
  force_rebuild: false
```

Longbridge credentials:

```env
LONGBRIDGE_APP_KEY=your_app_key
LONGBRIDGE_APP_SECRET=your_app_secret
LONGBRIDGE_ACCESS_TOKEN=your_access_token
```

Email reports:

```yaml
email:
  enabled: false
  intraday_enabled: true
  intraday_interval_hours: 2
  smtp:
    host: smtp.example.com
    port: 587
    username: report@example.com
    use_tls: true
    force_ipv4: true
    retries: 3
    retry_seconds: 5
  from: report@example.com
  to:
    - ops@example.com
  subject_prefix: "[api-report-agent]"
```

Only the SMTP password belongs in `.env`:

```env
SMTP_PASSWORD=
```

Notifications:

```yaml
notifications:
  channels:
    - email
    - archive
  archive_dir: /opt/api-report-agent/data/notifications
```

All project notifications go through `core.notification.notify()`. This project only supports `email` and local `archive`; if `telegram` appears in `NOTIFY_CHANNELS`, it is ignored and never called from api-report-agent. Telegram delivery should be handled by the separate `tg_schedule_bot` host by pulling the notification archive over SSH.

Intraday email reports are sent during market hours every two hours by default, using only data collected in that two-hour window. Daily email reports are still sent after market close once daily metrics and quality files exist.

On ECS/VPS hosts without an IPv6 default route, keep `SMTP_FORCE_IPV4=true`. This avoids DNS returning an IPv6 SMTP address that fails with `[Errno 101] Network is unreachable`. SMTP delivery retries are controlled by `SMTP_RETRIES` and `SMTP_RETRY_SECONDS`.

Test the exact project email settings from `.env`:

```bash
python scripts/test_email.py
```

The script uses the same `EmailConfig` and SMTP sender as the pipeline. It prints a masked config summary before sending. If you need to test SMTP while `EMAIL_ENABLED=false`, run:

```bash
python scripts/test_email.py --ignore-enabled
```

Test the raw SMTP delivery path:

```bash
python -m scripts.test_smtp_delivery
```

Test archive notification only:

```bash
python -m scripts.test_notify
```

View today's notification archive:

```bash
scripts/notifications_tail.sh
scripts/notifications_tail.sh 100
```

Optional AI analysis can be included in email reports. AI is only used for report summarization and never controls collection, scheduling, metrics, or quality logic.

```yaml
ai:
  analysis_enabled: false
  provider: mock
  fallback_provider: gemini
  timeout_seconds: 30
  deepseek:
    base_url: https://api.deepseek.com
    model: deepseek-v4-flash
  gemini:
    model: gemini-2.5-flash
```

Only AI provider keys belong in `.env`:

```env
DEEPSEEK_API_KEY=
GEMINI_API_KEY=
```

Use `ai.provider: mock` to include a deterministic default analysis without any API key. Use `ai.provider: deepseek` when a DeepSeek key is configured.

Edit watched symbols in `config/registry.yaml`:

```yaml
symbols:
  - symbol: QQQ.US
    market: US
    asset_type: equity_etf
    liquidity_class: high
    include_in_movers: true
    sessions:
      - regular
      - extended
    enabled: true
```

Old `config/symbols.json` files remain supported as a compatibility fallback, but new non-sensitive settings should go into `config/registry.yaml`. The extended symbol fields let the service separate regular and extended-session behavior without moving to a database.

## Run

The long-running process is started directly:

```bash
python scripts/run_pipeline.py
python -m scripts.pipeline_runner
```

In production, systemd should run `scripts/run_pipeline.py` directly from the project directory.

Post-market offline processing can be run after market close:

```bash
python scripts/post_market_pipeline.py --market HK --date 2026-05-08
python scripts/post_market_pipeline.py --market US --date 2026-05-08
```

Safe shell hook:

```bash
scripts/run_post_market.sh HK 2026-05-08
scripts/run_post_market.sh US 2026-05-08
```

It finalizes metrics/quality, generates reports/features/timeline, archives raw JSONL, and writes a health report.

US extended-session collection is separate from the regular pipeline:

```bash
python -m scripts.extended_pipeline --once
python -m scripts.extended_pipeline --interval-seconds 1800
python -m scripts.extended_report --market US --date 2026-05-12
```

Extended records are written to `data/raw/US/extended/{session_window_id}.jsonl` and reports are written to `data/reports/extended/`. Weekend collection is skipped; the weekend extended window only collects Friday after-hours and Monday premarket. Extended quality rules are isolated from regular daily reports. See [docs/extended_session.md](docs/extended_session.md).

## External API

The FastAPI service exposes a small operational API for health, runtime status, one-shot collection, post-market report generation, daily rebuilds, symbol inspection, and report retrieval.

`GET /health` is public and dependency-light. All other endpoints require:

```http
Authorization: Bearer <API_TOKEN>
```

`API_TOKEN` or `API_KEY` must be set in `.env`; these secrets must not be stored in `config/registry.yaml`.

Endpoints:

- `GET /health` returns service liveness.
- `GET /status` returns pipeline health plus `runtime/pipeline_status.json`.
- `GET /symbols` returns the effective watched symbols from `config/symbols.json` if present, otherwise `config/registry.yaml`.
- `POST /collect/run` triggers one collection cycle for currently open markets.
- `POST /pipeline/daily/run` rebuilds normalized data, metrics, daily metrics, and quality for one market/date.
- `POST /reports/post-market/run` runs the full post-market report pipeline for one market/date.
- `GET /reports` lists generated report artifacts. Optional query params: `market=US|HK`, `trading_date=YYYY-MM-DD`.
- `GET /reports/{market}/{trading_date}/{report_type}` returns one report payload. Supported `report_type` values are `market_summary`, `timeline`, `ai_summary`, `health`, `features`, `daily_metrics`, `windows_metrics`, and `quality`.

Examples:

```bash
curl -fsS http://127.0.0.1:8000/health

curl -fsS -H "Authorization: Bearer $API_TOKEN" \
  http://127.0.0.1:8000/status

curl -fsS -X POST -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"mock","symbols":["QQQ.US"]}' \
  http://127.0.0.1:8000/collect/run

curl -fsS -X POST -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"market":"US","trading_date":"2026-05-08"}' \
  http://127.0.0.1:8000/reports/post-market/run

curl -fsS -H "Authorization: Bearer $API_TOKEN" \
  http://127.0.0.1:8000/reports/US/2026-05-08/market_summary
```

Trigger endpoints execute synchronously and return actual output paths. They do not create placeholder success records when source data is missing.

## ECS Deployment

Production ECS deployment uses the repository path `/opt/api-report-agent`, service name `api-report-agent`, runtime user `deploy`, and a local-only API bind at `127.0.0.1:8000`. Public ingress should be handled by Nginx or another gateway.

The ASGI entrypoint is:

```text
app.main:app
```

The health endpoint is dependency-light and does not call Longbridge, DeepSeek, OpenAI, Gemini, upload jobs, or report parsing:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok","service":"api-report-agent"}
```

The systemd unit is stored at `systemd/api-report-agent.service` and uses:

```ini
ExecStart=/opt/api-report-agent/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Manual first-time setup on ECS:

```bash
sudo mkdir -p /opt/api-report-agent
sudo chown -R deploy:deploy /opt/api-report-agent
cd /opt/api-report-agent
git clone <repo-url> .
cp .env.example .env
chmod +x scripts/deploy.sh scripts/smoke_test.sh scripts/tail_logs.sh
scripts/deploy.sh
scripts/smoke_test.sh
```

`scripts/deploy.sh` creates or repairs `.venv`, installs dependencies with `.venv/bin/python -m pip`, copies the systemd unit, restarts through `systemctl`, prints service status, and exits. It never starts `uvicorn` or Python in the foreground.

GitHub Actions deployment is manual only through `.github/workflows/deploy.yml`. Configure these repository secrets:

```text
ECS_HOST
ECS_USER
ECS_SSH_KEY
ECS_PORT
```

Do not configure legacy VPS-prefixed deployment secrets in this repository. `ECS_SSH_KEY` is the private SSH key GitHub Actions uses to log into the ECS host as `deploy`; it is separate from any Git deploy key used by the ECS server to pull the repository.

Manual redeploy on ECS:

```bash
cd /opt/api-report-agent
git pull --ff-only
chmod +x scripts/deploy.sh scripts/smoke_test.sh scripts/tail_logs.sh
scripts/deploy.sh
scripts/smoke_test.sh
```

Systemd management:

```bash
sudo systemctl status api-report-agent --no-pager --full
sudo systemctl restart api-report-agent
sudo systemctl stop api-report-agent
sudo systemctl enable api-report-agent
```

Logs:

```bash
scripts/tail_logs.sh
LINES=300 scripts/tail_logs.sh
sudo journalctl -u api-report-agent -n 120 --no-pager
tail -n 120 /opt/api-report-agent/deploy.log
```

Troubleshooting broken virtualenv:

```bash
cd /opt/api-report-agent
rm -rf .venv
scripts/deploy.sh
```

The deploy script only removes `.venv` when it is missing or not executable, or when you remove it manually. It does not delete `data/`, `logs/`, database files, or user generated content.

Troubleshooting SSH handshake failures in GitHub Actions:

- Verify `ECS_HOST`, `ECS_USER`, `ECS_SSH_KEY`, and `ECS_PORT` are set on the GitHub repository.
- Confirm `ECS_SSH_KEY` matches a public key in `/home/deploy/.ssh/authorized_keys` on the ECS host.
- Confirm the ECS security group allows inbound SSH on `ECS_PORT` from GitHub Actions runners or your approved source range.
- Confirm the remote user is `deploy` and the workflow does not switch users after login.

Optional post-market cron examples:

```cron
10 17 * * 1-5 cd /opt/api-report-agent && ./.venv/bin/python scripts/post_market_pipeline.py --market HK
10 17 * * 1-5 cd /opt/api-report-agent && ./.venv/bin/python scripts/post_market_pipeline.py --market US
```

Adjust cron times to the server timezone and the target market close.

## Simple QA

Run the automated test suite before deployment:

```bash
python -m pytest -q
```

Useful manual checks:

- `python -m compileall .` should complete without syntax errors.
- `uvicorn app.main:app --host 127.0.0.1 --port 8000` should expose `GET /health` locally during development.
- `scripts/smoke_test.sh` should print `PASS` against a running service.
- `python scripts/healthcheck.py` should complete without unexpected errors.
- `python scripts/test_email.py --ignore-enabled` should send a test email when SMTP settings are configured.
- `python scripts/post_market_pipeline.py --market US --date YYYY-MM-DD` should generate reports for a date with raw data.
- Verify that `data/raw/`, `data/normalized/`, `data/metrics/`, `data/quality/`, and `runtime/pipeline_status.json` are updated after a collection loop.

## Data Layout

```text
data/raw/{market}/{trading_date}.jsonl
data/raw/US/extended/{session_window_id}.jsonl
data/normalized/{market}/{trading_date}.jsonl
data/metrics/{market}/{trading_date}/windows.json
data/metrics/{market}/{trading_date}/window_{window_id}.json
data/metrics/{market}/{trading_date}/daily.json
data/quality/{market}/{trading_date}.json
data/reports/{market}/{trading_date}_market_summary.json
data/reports/{market}/{trading_date}_timeline.json
data/reports/{market}/{trading_date}_ai_summary.md
data/reports/{market}/{trading_date}_health.json
data/reports/extended/
data/notifications/{YYYY-MM-DD}.jsonl
data/features/{market}/{trading_date}.json
data/archive/raw/{market}/{trading_date}.jsonl.gz
```

Raw data is append-only. Normalized, metrics, and quality layers are deterministic derived outputs and can be rebuilt from raw data.

## Time Model

New records use UTC internally. Market timezone is stored separately and used only for normalization, `trading_date`, session windows, and UI display.

Longbridge timestamps are treated as market-local time:

```text
Longbridge timestamp -> attach market timezone -> convert to UTC -> source_timestamp_utc
```

Example:

```json
{
  "source_timestamp_raw": "2026-05-12 09:30:00",
  "market_timezone": "America/New_York",
  "source_timestamp_utc": "2026-05-12T13:30:00Z"
}
```

`trading_date` is derived from market local time, not UTC date. US uses `America/New_York`, so EDT/EST daylight-saving changes are handled by `zoneinfo`. See [docs/time_model.md](docs/time_model.md).

## Modules

```text
clients/
  market_client.py

core/
  config_registry.py
  data_pipeline.py
  loader.py
  market_calendar.py
  market_data_cleaner.py
  market_data_store.py
  runtime_support.py
  trading_hours.py

scripts/
  pipeline_runner.py
  run_pipeline.py
  extended_pipeline.py
  extended_report.py
  market_data_collector.py
  replay.py
  debug_chart.py
  healthcheck.py
  cleanup.py
```

`scripts/replay.py`, `scripts/debug_chart.py`, `scripts/healthcheck.py`, and `scripts/cleanup.py` expose importable functions for operations and debugging. They are not wired into a CLI command tree.

## Design Rules

- Deterministic pipeline only.
- No Agent framework.
- No LLM controller.
- No autonomous planner.
- No AI orchestration.
- Explicit inputs and outputs.
- File-system-first storage.
- Stable JSON outputs.
- Replayable and auditable processing.
- Missing inputs are logged and skipped, not treated as process-fatal errors.

Future AI report generation may consume finalized metrics, events, and timelines, but it must not control collection, scheduling, metrics, quality, replay, or data writes.

## Documentation Maintenance

Keep `README.md` and `README.zh-CN.md` synchronized when updating project usage, configuration, deployment, or design rules.
