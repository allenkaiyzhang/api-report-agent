# Data Retention Policy

The pipeline uses file-system storage only.

## Raw

- Path: `data/raw/{market}/{trading_date}.jsonl`
- Append-only.
- Keep longest.
- Archive older files with gzip when disk pressure grows.

## Reference

- Path: `data/reference/{market}/{trading_date}.json`
- Daily provider snapshot.
- Keep permanently unless storage requires archive.

## Normalized

- Path: `data/normalized/{market}/{trading_date}.jsonl`
- Derived from raw and reference.
- Suggested hot retention: 3 to 6 months.
- Can be rebuilt from raw/reference.

## Metrics

- Path: `data/metrics/{market}/{trading_date}/`
- Keep permanently.
- Primary input for reports and long-term analysis.

## Quality

- Path: `data/quality/{market}/{trading_date}.json`
- Keep permanently.
- Required for audit and AI consumption gates.
