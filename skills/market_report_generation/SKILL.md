# Market Report Generation

## When to Use
Invoke this skill when the market report agent needs to generate reports from validated market data. This skill covers the report generation and notification dispatch phases.

## Required Inputs
- **Validated MarketReportDataset**: Must have `validated=True` before generation
- **Report type**: intraday_brief, daily_close_report, or event_alert
- **Market context**: US or HK market identifier
- **ReportGenerator instance**: Configured with appropriate thresholds

## Report Types

### 1. intraday_brief
- **When**: Every 2 hours during active market sessions
- **Format**: Short Markdown summary, notification-first
- **Content**: Price snapshot table, notable movers (≥3% change), session info
- **Max symbols**: 10 per report

### 2. daily_close_report
- **When**: After market close (post-market delay of 15 minutes)
- **Format**: Full Markdown report
- **Content**:
  - Market summary (advancers/decliners/unchanged)
  - Closing prices table
  - Top 5 gainers and losers
  - Last 5 days candle data per symbol
- **Delivery**: Full report via email, summary via notification

### 3. event_alert
- **When**: Abnormal price movement (≥3%) or volume spike (≥3x 20d average)
- **Format**: Short Markdown alert
- **Content**: Triggering events with thresholds, current price, change%
- **Delivery**: Immediate notification (console, email, webhook)

## Allowed Longbridge MCP Tools

Same as data collection — report generation does not call MCP tools directly.
All data comes from the validated MarketReportDataset.

## Data Integrity Rules
1. **Do NOT generate reports from invalid data.** Always check `dataset.validated` before generating.
2. **Do NOT fabricate missing market data.** If a symbol has no quotes, exclude it from the report.
3. **Do NOT silently skip failed validation.** Log validation errors and mark runs as SKIPPED.
4. **Do NOT assume default values** for missing fields — report them as "N/A" or omit them.

## Missing Data Handling in Reports
- Symbols with no quote data: Excluded from tables, noted in report footer.
- Missing candle data: Candle section shows "No candle data available" for that symbol.
- Missing market status: Session shows "unknown", report is marked as degraded.
- Partial data: Report is generated for available symbols only; missing symbols are listed.

## No Fabrication Rules
- **Never invent** closing prices, volume numbers, or change percentages.
- **Never copy** previous day's data for missing data points.
- **Never estimate** market trends from incomplete data.
- **Never fill** report tables with placeholder values.

## Regular-Hours vs Extended-Hours for Reports
- Intraday briefs during regular hours include full candle/intraday data.
- Intraday briefs during extended hours are quote-only.
- Daily close reports only use regular-session data.
- Extended-session data is not included in daily close reports.

## Currency and Timezone for Reports
- All prices in the report use the market's native currency (USD/HKD).
- Timestamps in reports are shown in market-local time.
- Report filenames use UTC dates (YYYY-MM-DD).
- Email subjects include the market and UTC timestamp.

## Notification Rules
1. Notification failures must NOT corrupt report files.
2. Reports are saved to disk before notification dispatch.
3. Each notification channel is independent — failure in one does not block others.
4. All notification attempts are audit-logged.

## Report Storage
- Reports saved to `reports/{YYYY-MM-DD}/{report_type}_{market}.md`
- Run logs saved to `data/run_logs.jsonl`
- Audit logs saved to `logs/notifications/{YYYY-MM-DD}.jsonl`
