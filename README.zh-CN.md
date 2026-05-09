# 市场数据管道

English documentation: [README.md](README.md)

api-report-agent 是一个面向港股和美股市场数据的确定性数据管道。

它不是 Agent 平台。pipeline 流程、数据采集、文件写入、metrics、quality 检查、replay 和市场日历逻辑都由确定性的 Python 代码实现。

## 运行结构

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

## 配置

复制 `.env.example` 为 `.env`，并按需配置数据源凭证。

```env
MARKET_DATA_PROVIDER=mock
DATA_COLLECTION_INTERVAL_SECONDS=120
DATA_COLLECTION_OUTPUT_DIR=data/raw
DATA_COLLECTION_FILE_TIMEZONE=Asia/Shanghai
PIPELINE_LOOP_SLEEP_SECONDS=10
PIPELINE_FORCE_REBUILD=false
```

Longbridge 凭证：

```env
LONGBRIDGE_APP_KEY=your_app_key
LONGBRIDGE_APP_SECRET=your_app_secret
LONGBRIDGE_ACCESS_TOKEN=your_access_token
```

邮件报告：

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

盘中邮件默认在交易时段内每 2 小时发送一次，只包含这 2 小时窗口内采集到的数据。盘后邮件仍会在收盘后、daily metrics 和 quality 文件都存在时发送。

使用 `.env` 中的真实项目邮件配置测试发送：

```bash
python scripts/test_email.py
```

该脚本使用和 pipeline 相同的 `EmailConfig` 与 SMTP 发送逻辑。发送前会打印脱敏后的配置摘要。如果需要在 `EMAIL_ENABLED=false` 时测试 SMTP，可运行：

```bash
python scripts/test_email.py --ignore-enabled
```

邮件中可以附带可选 AI 分析。AI 只用于报告摘要，不控制采集、调度、metrics 或 quality 逻辑。

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

使用 `AI_PROVIDER=mock` 可以在没有 API key 的情况下生成确定性的默认分析。配置 DeepSeek key 后可使用 `AI_PROVIDER=deepseek`。

在 `config/symbols.json` 中编辑需要关注的股票：

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

该文件只保留 `symbol` 和 `enabled`。名称、类型等元数据应来自 provider reference data。watch reason 预留给未来单独文件实现。

## 运行

长期运行进程直接启动：

```bash
python scripts/run_pipeline.py
```

生产环境中，systemd 应从项目目录直接运行 `scripts/run_pipeline.py`。

盘后离线处理可在收盘后执行：

```bash
python scripts/post_market_pipeline.py --market HK --date 2026-05-08
python scripts/post_market_pipeline.py --market US --date 2026-05-08
```

安全 shell hook：

```bash
scripts/run_post_market.sh HK 2026-05-08
scripts/run_post_market.sh US 2026-05-08
```

它会 finalize metrics/quality，生成 reports/features/timeline，归档 raw JSONL，并写入 health report。

## 数据布局

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

Raw 数据只追加写入。Normalized、metrics 和 quality 都是确定性的派生层，可由 raw 数据重建。

## 模块

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

`scripts/replay.py`、`scripts/debug_chart.py`、`scripts/healthcheck.py` 和 `scripts/cleanup.py` 暴露可导入函数，用于运维和调试。它们没有被组织成复杂 CLI 命令树。

## 设计规则

- 只使用 deterministic pipeline。
- 不引入 Agent framework。
- 不引入 LLM controller。
- 不引入 autonomous planner。
- 不引入 AI orchestration。
- 输入和输出明确。
- 文件系统优先。
- JSON 输出稳定。
- 可 replay、可审计。
- 缺失输入应记录日志并跳过，不应让进程致命失败。

未来 AI report generation 可以消费 finalized metrics、events 和 timelines，但不能控制采集、调度、metrics、quality、replay 或数据写入。

## 文档维护

更新项目用法、配置、部署或设计规则时，请同步维护 `README.md` 和 `README.zh-CN.md`。
