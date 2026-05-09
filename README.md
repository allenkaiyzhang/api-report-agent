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
      "enabled": true
    }
  ]
}
```

Only `symbol` and `enabled` belong in this file. Name/type metadata should come from provider reference data. Watch reasons are intentionally left for a future dedicated file.

## Run

The long-running process is started directly:

```bash
python scripts/run_pipeline.py
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

## Data Layout

```text
data/raw/{market}/{trading_date}.jsonl
data/normalized/{market}/{trading_date}.jsonl
data/metrics/{market}/{trading_date}/windows.json
data/metrics/{market}/{trading_date}/window_{window_id}.json
data/metrics/{market}/{trading_date}/daily.json
data/quality/{market}/{trading_date}.json
data/reports/{market}/{trading_date}_market_summary.json
data/reports/{market}/{trading_date}_timeline.json
data/reports/{market}/{trading_date}_ai_summary.md
data/reports/{market}/{trading_date}_health.json
data/features/{market}/{trading_date}.json
data/archive/raw/{market}/{trading_date}.jsonl.gz
```

Raw data is append-only. Normalized, metrics, and quality layers are deterministic derived outputs and can be rebuilt from raw data.

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
  run_pipeline.py
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
