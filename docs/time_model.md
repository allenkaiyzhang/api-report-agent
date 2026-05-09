# Time Model

api-report-agent uses UTC as the internal time format for new records.

Market timezone is stored separately and used for parsing provider timestamps, deriving `trading_date`, building session windows, and UI display.

## Market Timezones

The project uses IANA timezone names:

```text
US -> America/New_York
HK -> Asia/Hong_Kong
JP -> Asia/Tokyo
EU -> Europe/London
```

Do not use fixed offsets such as `UTC-5` for US data. `America/New_York` is required so EDT/EST daylight-saving changes are handled by `zoneinfo`.

## Longbridge Timestamp Normalization

Longbridge quote timestamps are treated as market-local time, not UTC.

```text
Longbridge timestamp
  -> attach market timezone
  -> convert to UTC
  -> store source_timestamp_utc
```

Example:

```json
{
  "symbol": "NVDA.US",
  "timestamp": "2026-05-12 09:30:00"
}
```

Becomes:

```json
{
  "market": "US",
  "market_timezone": "America/New_York",
  "source_timestamp_raw": "2026-05-12 09:30:00",
  "source_timestamp_utc": "2026-05-12T13:30:00Z"
}
```

## New Raw Time Fields

New raw quote records include:

```json
{
  "market": "US",
  "market_timezone": "America/New_York",
  "session": "regular",
  "trading_date": "2026-05-12",
  "session_window_id": "US_REGULAR_2026-05-12",
  "collected_at_utc": "2026-05-12T14:32:01Z",
  "source_timestamp_raw": "2026-05-12 09:30:00",
  "source_timestamp_utc": "2026-05-12T13:30:00Z"
}
```

Legacy fields such as `collected_at`, `timestamp`, and `event_time` remain supported for backward compatibility.

## Trading Date

`trading_date` is not the UTC date. It is derived from market local time plus market timezone.

US after-hours data may be collected when UTC has already moved to Saturday, but the regular `trading_date` can still be Friday. Extended sessions use a window id spanning the full window:

```text
US_EXT_2026-05-08_TO_2026-05-11
```

## Session Model

Supported sessions:

```text
regular
extended
```

Future sessions can add:

```text
premarket
afterhours
```

Regular raw files keep the existing path:

```text
data/raw/US/2026-05-12.jsonl
```

Extended raw files:

```text
data/raw/US/extended/US_EXT_2026-05-09_TO_2026-05-12.jsonl
```

Extended data does not participate in regular daily reports or regular movers.

## API Time Format

API responses expose new timestamps as UTC ISO8601 strings:

```json
{
  "collected_at_utc": "2026-05-12T14:32:01Z",
  "source_timestamp_utc": "2026-05-12T13:30:00Z"
}
```

UI display may convert UTC to SGT, HKT, or ET. Sorting, windows, and aggregation should continue to use UTC fields.
