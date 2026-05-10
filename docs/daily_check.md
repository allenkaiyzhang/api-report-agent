# Daily Check

`scripts/daily_check.py` is a read-only P0 production inspection tool.

It does not modify data, does not trigger the pipeline, and does not auto-fix anything.

## Manual Run

```bash
python scripts/daily_check.py --date 2026-05-08 --markets HK,US
```

Write report to a file:

```bash
python scripts/daily_check.py --date 2026-05-08 --markets HK,US --output runtime/daily_check_2026-05-08.json
```

Send the same report through `notify()` using configured notification channels:

```bash
python scripts/daily_check.py --date 2026-05-08 --markets HK,US --email
```

Or enable it by environment:

```env
DAILY_CHECK_EMAIL_ENABLED=true
```

`notify()` writes the local archive first, then attempts email if enabled. Telegram is not called by this project.

## Checks

- systemd service active state
- raw JSONL existence, line count, JSON parse errors
- normalized JSONL existence, line count, parse errors, invalid ratio, duplicate records
- reference snapshot existence and non-empty symbols
- metrics directory, window files, empty windows, daily.json, finalized state
- quality overall grade, usable_for_analysis, invalid lines, duplicate records
- reports count and AI summary existence
- disk used percent and `data/` size

## Exit Codes

- `0`: ok
- `1`: warning
- `2`: critical

## Cron Example

```cron
30 22 * * 1-5 cd /opt/api-report-agent && /opt/api-report-agent/.venv/bin/python scripts/daily_check.py --date $(date +\%F) --markets HK,US --output runtime/daily_check_$(date +\%F).json --email
```

For US trading dates, make sure the date passed matches the US market trading date you want to inspect.
