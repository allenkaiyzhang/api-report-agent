# AI Data Consumption Rules

AI is only allowed in the report layer.

## Allowed Inputs

AI may read:

- `data/metrics`
- `data/quality`
- `data/reference`

## Forbidden Inputs

AI must not directly read raw JSONL:

- `data/raw`

Raw data is an audit and replay source, not an AI prompt source.

## Quality Gate

If `quality.usable_for_analysis` is `false`, AI reports must not generate trend conclusions.

The report may summarize data quality problems, but it must not infer market structure or risk direction from unusable data.

## Pipeline Control

AI must not control:

- collection
- scheduling
- market calendar
- normalization
- metrics
- quality validation
- file writes
