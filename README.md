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

```env
MARKET_DATA_PROVIDER=mock
DATA_COLLECTION_INTERVAL_SECONDS=120
DATA_COLLECTION_OUTPUT_DIR=data/raw
DATA_COLLECTION_FILE_TIMEZONE=Asia/Shanghai
PIPELINE_LOOP_SLEEP_SECONDS=10
PIPELINE_FORCE_REBUILD=false
API_CONTROL_TOKEN=change-me
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
EMAIL_FROM=
EMAIL_TO=
EMAIL_SUBJECT_PREFIX=[api-report-agent]
```

Intraday email reports are sent during market hours every two hours by default, using only data collected in that two-hour window. Daily email reports are still sent after market close once daily metrics and quality files exist.

Test the exact project email settings from `.env`:

```bash
python scripts/test_email.py
```

The script uses the same `EmailConfig` and SMTP sender as the pipeline. It prints a masked config summary before sending. If you need to test SMTP while `EMAIL_ENABLED=false`, run:

```bash
python scripts/test_email.py --ignore-enabled
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

Extended records are written to `data/raw/US/extended/{session_window_id}.jsonl` and reports are written to `data/reports/extended/`. Extended quality rules are isolated from regular daily reports. See [docs/extended_session.md](docs/extended_session.md).

## API and UI

Start the local API server:

```bash
uvicorn api_server:app --host 127.0.0.1 --port 8000
```

Read-only JSON endpoints include `/health`, `/symbols`, `/markets/{market}/latest`, `/sessions/{market}/regular/latest`, `/sessions/{market}/extended/latest`, `/quotes/{symbol}/latest`, and `/reports`.

Control endpoints require:

```text
X-API-Token: value-of-API_CONTROL_TOKEN
```

UI pages:

```text
/ui/dashboard
/ui/reports
/ui/control
```

Keep the API bound to localhost and access it through SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 user@your-ecs-host
```

systemd example:

```ini
[Unit]
Description=api-report-agent web API
After=network.target

[Service]
WorkingDirectory=/opt/api-report-agent
EnvironmentFile=/opt/api-report-agent/.env
ExecStart=/opt/api-report-agent/.venv/bin/uvicorn api_server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable api-report-agent-web.service
sudo systemctl restart api-report-agent-web.service
sudo journalctl -u api-report-agent-web.service -f
```

See [docs/api_server.md](docs/api_server.md).

## Data Layout

```text
data/raw/{market}/regular/{trading_date}.jsonl
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
