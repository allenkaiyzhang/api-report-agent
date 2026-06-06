# Market Report Data Collection

## When to Use
Invoke this skill when the market report agent needs to collect raw market data from Longbridge MCP. This skill covers the data collection phase of the workflow pipeline.

## Required Inputs
- **Watchlist symbols**: List of stock symbols to monitor (e.g., QQQ.US, HSBC.US)
- **Markets**: Target markets (US, HK)
- **Report type context**: intraday_brief, daily_close_report, or event_alert
- **MarketDataClient instance**: Must be a MarketDataClient implementation (LongbridgeMcpClient or MockMarketDataClient)

## Allowed Longbridge MCP Tools

The following MCP tools are permitted for data collection:

| Tool | Purpose |
|------|---------|
| `get_stock_quote` | Fetch latest real-time quotes (price, volume, change%) |
| `get_candlesticks` | Fetch historical OHLCV candles |
| `get_intraday` | Fetch intraday price/volume data points |
| `get_market_trading_session` | Check if market is open, session type, next open/close |
| `get_stock_info` | Fetch fundamental/static info (name, exchange, EPS, etc.) |
| `get_calc_indexes` | Fetch calculated indexes (volume ratio, change rates) |
| `get_watchlist` | Fetch user's watchlist from Longbridge |

## Forbidden Tools (BLOCKED IN CODE)

These tools are **permanently blocked** and must NEVER be called:

| Tool | Reason |
|------|--------|
| `submit_order` | Trading — always blocked |
| `replace_order` | Trading — always blocked |
| `cancel_order` | Trading — always blocked |
| `withdrawals` | Trading — always blocked |
| `dca_create` | Trading — always blocked |
| `dca_update` | Trading — always blocked |
| `dca_stop` | Trading — always blocked |
| `dca_pause` | Trading — always blocked |
| `dca_resume` | Trading — always blocked |

## Account-Read Tools (Disabled by Default)

These tools require `ACCOUNT_READ_ENABLED=true` to be explicitly set:

| Tool | Purpose |
|------|---------|
| `account_balance` | Account balance inquiry |
| `account_positions` | Current positions |
| `account_orders` | Active orders |
| `account_assets` | Asset summary |
| `get_margin_ratio` | Margin status |
| `subscriptions` | Subscription info |
| `history_orders` | Historical orders |
| `history_deal` | Historical deals |
| `funds` | Fund records |

## Validation Rules
1. All collected data must be associated with a valid `run_id`.
2. Quotes must have non-null `symbol`, `latest_price`, and `timestamp` fields.
3. Candles must have non-null `symbol`, `close`, and `timestamp` fields.
4. Intraday points must have non-null `symbol`, `price`, and `timestamp` fields.
5. Market status must be checked before generating reports — closed markets produce SKIPPED runs.
6. Price values must be positive and within reasonable bounds (0.001 to 1,000,000).
7. Volume values must be non-negative.

## Missing Data Handling
1. **Do NOT fabricate missing market data.** If a quote/candle/intraday call fails, log the error and skip that symbol.
2. If all symbols fail for a market, the entire run is marked SKIPPED with error details.
3. Missing data is logged to the structured log file but does not halt the scheduler.
4. Reports are only generated when valid data exists for at least one symbol.

## No Fabrication Rules
- **Never invent price values** when the data source returns empty or errors.
- **Never extrapolate** missing candles from partial data.
- **Never guess** market status — always query `get_market_trading_session`.
- **Never fill gaps** with previous values without explicit user configuration.

## Regular-Hours vs Extended-Hours Handling
- **US Regular**: 09:30–16:00 ET — full intraday collection
- **US Extended Pre**: 04:00–09:30 ET — limited collection (quotes only)
- **US Extended Post**: 16:00–20:00 ET — limited collection (quotes only)
- **HK Regular**: 09:30–16:00 HKT (with lunch break 12:00–13:00 HKT)
- Extended-hour data is collected but marked with `trade_session` for filtering.

## Currency and Timezone Handling
- **US symbols**: Currency is USD, timezone is `America/New_York`
- **HK symbols**: Currency is HKD, timezone is `Asia/Hong_Kong`
- All internal timestamps are stored in UTC; market-local time is derived from timezone.
- `trading_date` is determined from market-local time, not UTC date.
