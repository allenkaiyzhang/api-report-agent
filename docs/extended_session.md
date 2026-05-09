# US Extended Session

Extended-session collection is separate from the regular-session pipeline.

Regular data stays in the existing regular raw files. Extended data is written to:

```text
data/raw/US/extended/{session_window_id}.jsonl
```

Each extended record includes:

```json
{
  "market": "US",
  "session": "extended",
  "trading_date": "2026-05-12",
  "market_timezone": "America/New_York",
  "collected_at_utc": "...",
  "session_window_id": "US_EXT_2026-05-09_TO_2026-05-12",
  "source_timestamp_raw": "2026-05-12 09:10:00",
  "source_timestamp_utc": "2026-05-12T13:10:00Z"
}
```

The extended window is:

```text
previous US regular close -> next US regular open
```

Weekend windows are allowed. For example:

```text
Friday regular close -> Monday regular open
```

Extended collection uses only high-liquidity core symbols:

```text
QQQ.US, SPY.US, AAPL.US, NVDA.US, TSLA.US, GOOG.US
```

It excludes HK symbols, SGOV.US, and low-liquidity symbols.

Run one cycle:

```bash
python -m scripts.extended_pipeline --once
```

Run continuously:

```bash
python -m scripts.extended_pipeline --interval-seconds 1800
```

Generate the extended report once the extended window has ended:

```bash
python -m scripts.extended_report --market US --date 2026-05-12
```

Reports are written to:

```text
data/reports/extended/
```

Extended quality rules are intentionally more permissive:

- duplicate timestamps are allowed;
- low volume is a warning only;
- wide spread is a warning only;
- stale quotes are warnings;
- extended quality does not affect regular daily reports.
