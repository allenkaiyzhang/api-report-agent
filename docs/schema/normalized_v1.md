# Normalized Schema v1

`data/normalized/{market}/{trading_date}.jsonl` is the fact layer derived from raw quote data.

## Contract

Fields are emitted in a stable order. Do not rename or remove fields silently.

| Field | Type | Nullable | Meaning |
| --- | --- | --- | --- |
| schema_version | int | no | Normalized schema version. Current value: `1`. |
| pipeline_version | string | no | Pipeline code/data contract version. |
| record_id | string | yes | Logical primary key: `{symbol}_{event_time}`. Empty when symbol or event_time is missing. |
| event_time | ISO datetime | yes | Quote event time. Always timezone-aware when present. |
| collected_at | ISO datetime | yes | Collection time. Always timezone-aware when present. |
| provider | string | yes | Data provider, such as `longbridge` or `mock`. |
| market | string | no | `HK` or `US`. |
| symbol | string | yes | Normalized symbol, such as `0700.HK` or `QQQ.US`. |
| currency | string | yes | Raw currency, reference currency, or market default. |
| last_price | number | yes | Last traded price. |
| bid | number | yes | Best bid. Null or invalid values disable spread analytics. |
| ask | number | yes | Best ask. Null or invalid values disable spread analytics. |
| spread | number | yes | `ask - bid`, only when bid/ask are valid positive values. |
| spread_pct | number | yes | `spread / last_price`, only when spread is valid. |
| volume_cumulative | int | yes | Cumulative intraday volume. |
| turnover_cumulative | number | yes | Cumulative intraday turnover. |
| is_valid | bool | no | False if critical normalized flags are present. |
| flags | string[] | no | Quality flags such as `missing_event_time`, `duplicate_record`, `invalid_price`. |

## Timezone Rules

- If raw `event_time` has timezone info, it is used first.
- If Longbridge `timestamp` is timezone-naive, US timestamps are interpreted as `Asia/Shanghai` and remain timezone-aware in output.
- HK timezone-naive timestamps are interpreted as `Asia/Hong_Kong`.
- Window matching always converts `event_time` to the market window timezone before comparison.

## Primary Key

`record_id = symbol + "_" + event_time`

This key is used for duplicate detection, lineage, and debugging.
