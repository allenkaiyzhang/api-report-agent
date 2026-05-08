# Market Data Pipeline

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

## Data Layout

```text
data/raw/{market}/{trading_date}.jsonl
data/normalized/{market}/{trading_date}.jsonl
data/metrics/{market}/{trading_date}/windows.json
data/metrics/{market}/{trading_date}/window_{window_id}.json
data/metrics/{market}/{trading_date}/daily.json
data/quality/{market}/{trading_date}.json
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
