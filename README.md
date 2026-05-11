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

Copy `.env.example` to `.env` and set provider credentials when needed.

Recommended first-time setup:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/symbols_example.json config/symbols.json
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

```env
MARKET_DATA_PROVIDER=mock
DATA_COLLECTION_INTERVAL_SECONDS=120
DATA_COLLECTION_OUTPUT_DIR=data/raw
DATA_COLLECTION_FILE_TIMEZONE=Asia/Shanghai
PIPELINE_LOOP_SLEEP_SECONDS=10
PIPELINE_FORCE_REBUILD=false
```

Longbridge credentials:

```env
LONGBRIDGE_APP_KEY=your_app_key
LONGBRIDGE_APP_SECRET=your_app_secret
LONGBRIDGE_ACCESS_TOKEN=your_access_token
```

Email reports:

```env
EMAIL_ENABLED=false
EMAIL_INTRADAY_ENABLED=true
EMAIL_INTRADAY_INTERVAL_HOURS=2
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=true
SMTP_FORCE_IPV4=true
SMTP_RETRIES=3
SMTP_RETRY_SECONDS=5
EMAIL_FROM=
EMAIL_TO=
EMAIL_SUBJECT_PREFIX=[api-report-agent]
```

Notifications:

```env
NOTIFY_CHANNELS=email,archive
NOTIFICATION_ARCHIVE_DIR=/opt/api-report-agent/data/notifications
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

```env
AI_ANALYSIS_ENABLED=false
AI_PROVIDER=mock
AI_FALLBACK_PROVIDER=gemini
AI_TIMEOUT_SECONDS=30
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
```

Use `AI_PROVIDER=mock` to include a deterministic default analysis without any API key. Use `AI_PROVIDER=deepseek` when a DeepSeek key is configured.

Edit watched symbols in `config/symbols.json`:

```json
{
  "symbols": [
    {
      "symbol": "QQQ.US",
      "market": "US",
      "asset_type": "equity_etf",
      "liquidity_class": "high",
      "include_in_movers": true,
      "sessions": ["regular", "extended"],
      "enabled": true
    }
  ]
}
```

Old `symbol` + `enabled` entries remain supported. The extended fields let the service separate regular and extended-session behavior without moving to a database.

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

## Deployment

1. Clone or copy the repository to the server, for example `/opt/api-report-agent`.
2. Create a virtual environment and install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`, then set `MARKET_DATA_PROVIDER`, Longbridge credentials, email settings, and AI settings.
4. Copy `config/symbols_example.json` to `config/symbols.json` and keep only the symbols you want to collect.
5. Run a foreground smoke test with `MARKET_DATA_PROVIDER=mock python scripts/run_pipeline.py`; stop it after one successful loop.
6. Install the systemd unit for the pipeline.
7. Monitor `logs/`, `runtime/pipeline_status.json`, and `journalctl` after deployment.

Minimal pipeline systemd unit:

```ini
[Unit]
Description=api-report-agent market data pipeline
After=network.target

[Service]
WorkingDirectory=/opt/api-report-agent
EnvironmentFile=/opt/api-report-agent/.env
ExecStart=/opt/api-report-agent/.venv/bin/python scripts/run_pipeline.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Optional post-market cron examples:

```cron
10 17 * * 1-5 cd /opt/api-report-agent && ./.venv/bin/python scripts/post_market_pipeline.py --market HK
10 17 * * 1-5 cd /opt/api-report-agent && ./.venv/bin/python scripts/post_market_pipeline.py --market US
```

Adjust cron times to the server timezone and the target market close.

## Redeploy

Use the bundled redeploy script on ECS:

```bash
chmod +x redeploy.sh
sudo ./redeploy.sh
```

The script runs in `/opt/api-report-agent`, checks `.env`, creates `.venv` if missing, installs `requirements.txt`, runs `systemctl daemon-reload`, restarts `api-report-agent`, prints service status, and appends output to `/opt/api-report-agent/deploy.log`.

## Simple QA

Run the automated test suite before deployment:

```bash
python -m unittest discover tests
```

Useful manual checks:

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
  symbols.json

core/
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
