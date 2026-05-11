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

首次部署建议步骤：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/symbols_example.json config/symbols.json
```

Windows PowerShell 中使用下面的命令启用虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

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
SMTP_FORCE_IPV4=true
SMTP_RETRIES=3
SMTP_RETRY_SECONDS=5
EMAIL_FROM=
EMAIL_TO=
EMAIL_SUBJECT_PREFIX=[api-report-agent]
```

通知：

```env
NOTIFY_CHANNELS=email,archive
NOTIFICATION_ARCHIVE_DIR=/opt/api-report-agent/data/notifications
```

项目内所有通知统一走 `core.notification.notify()`。本项目只支持 `email` 和本地 `archive`；如果 `NOTIFY_CHANNELS` 中出现 `telegram`，会被忽略，api-report-agent 不会直接调用 Telegram。Telegram 推送由另一台 `tg_schedule_bot` 主机通过 SSH 拉取通知归档后完成。

盘中邮件默认在交易时段内每 2 小时发送一次，只包含这 2 小时窗口内采集到的数据。盘后邮件仍会在收盘后、daily metrics 和 quality 文件都存在时发送。

如果 ECS/VPS 没有 IPv6 default route，建议保持 `SMTP_FORCE_IPV4=true`，避免 DNS 返回 IPv6 SMTP 地址后触发 `[Errno 101] Network is unreachable`。SMTP 重试次数和间隔由 `SMTP_RETRIES`、`SMTP_RETRY_SECONDS` 控制。

使用 `.env` 中的真实项目邮件配置测试发送：

```bash
python scripts/test_email.py
```

该脚本使用和 pipeline 相同的 `EmailConfig` 与 SMTP 发送逻辑。发送前会打印脱敏后的配置摘要。如果需要在 `EMAIL_ENABLED=false` 时测试 SMTP，可运行：

```bash
python scripts/test_email.py --ignore-enabled
```

测试底层 SMTP 发送链路：

```bash
python -m scripts.test_smtp_delivery
```

只测试本地 archive 通知：

```bash
python -m scripts.test_notify
```

查看当天通知归档：

```bash
scripts/notifications_tail.sh
scripts/notifications_tail.sh 100
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
      "market": "US",
      "asset_type": "equity_etf",
      "liquidity_class": "high",
      "include_in_movers": true,
      "sessions": ["regular", "extended"],
      "enabled": true
    }
  ]
}
```

旧的 `symbol` + `enabled` 格式仍然兼容。新增字段用于区分 regular 和 extended session 行为，不需要引入数据库。

## 运行

长期运行进程直接启动：

```bash
python scripts/run_pipeline.py
python -m scripts.pipeline_runner
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

美股盘外采集与 regular pipeline 分离：

```bash
python -m scripts.extended_pipeline --once
python -m scripts.extended_pipeline --interval-seconds 1800
python -m scripts.extended_report --market US --date 2026-05-12
```

盘外记录写入 `data/raw/US/extended/{session_window_id}.jsonl`，报告写入 `data/reports/extended/`。周末两天不采集；跨周末 extended window 只采周五盘后和周一盘前。盘外 quality 规则不会影响 regular daily report。详见 [docs/extended_session.md](docs/extended_session.md)。

## 部署

1. 将仓库 clone 或复制到服务器，例如 `/opt/api-report-agent`。
2. 创建虚拟环境，并执行 `pip install -r requirements.txt` 安装依赖。
3. 复制 `.env.example` 为 `.env`，配置 `MARKET_DATA_PROVIDER`、Longbridge 凭证、邮件和 AI。
4. 复制 `config/symbols_example.json` 为 `config/symbols.json`，只保留需要采集的标的。
5. 先用 `MARKET_DATA_PROVIDER=mock python scripts/run_pipeline.py` 做前台冒烟测试，确认至少完成一次循环后停止。
6. 生产环境安装 pipeline 的 systemd 服务。
7. 部署后持续观察 `logs/`、`runtime/pipeline_status.json` 和 `journalctl`。

最小 pipeline systemd 示例：

```ini
[Unit]
Description=api-report-agent market data pipeline
After=network.target

[Service]
WorkingDirectory=/opt/api-report-agent
EnvironmentFile=/opt/api-report-agent/.env
ExecStart=/opt/api-report-agent/.venv/bin/python scripts/run_pipeline.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

可选盘后 cron 示例：

```cron
10 17 * * 1-5 cd /opt/api-report-agent && ./.venv/bin/python scripts/post_market_pipeline.py --market HK
10 17 * * 1-5 cd /opt/api-report-agent && ./.venv/bin/python scripts/post_market_pipeline.py --market US
```

请按服务器时区和目标市场收盘时间调整 cron。

## Redeploy

在 ECS 上使用项目自带脚本重新部署：

```bash
chmod +x redeploy.sh
sudo ./redeploy.sh
```

脚本固定在 `/opt/api-report-agent` 执行，会检查 `.env`，缺失 `.venv` 时自动创建，安装 `requirements.txt`，执行 `systemctl daemon-reload`，重启 `api-report-agent`，输出 service 状态，并追加写入 `/opt/api-report-agent/deploy.log`。

## 简单 QA

部署前运行自动化测试：

```bash
python -m unittest discover tests
```

常用人工检查：

- `python scripts/healthcheck.py` 应能完成，且没有非预期错误。
- 配好 SMTP 后，`python scripts/test_email.py --ignore-enabled` 应能发出测试邮件。
- 对已有 raw 数据的日期运行 `python scripts/post_market_pipeline.py --market US --date YYYY-MM-DD`，应生成报告文件。
- 完成一次采集循环后，确认 `data/raw/`、`data/normalized/`、`data/metrics/`、`data/quality/` 和 `runtime/pipeline_status.json` 有更新。

## 数据布局

```text
data/raw/{market}/{trading_date}.jsonl
data/raw/US/extended/{session_window_id}.jsonl
data/normalized/{market}/{trading_date}.jsonl
data/metrics/{market}/{trading_date}/windows.json
data/metrics/{market}/{trading_date}/window_{window_id}.json
data/metrics/{market}/{trading_date}/daily.json
data/quality/{market}/{trading_date}.json
data/reports/{market}/{trading_date}_market_summary.json
data/reports/{market}/{trading_date}_timeline.json
data/reports/{market}/{trading_date}_ai_summary.md
data/reports/{market}/{trading_date}_health.json
data/reports/extended/
data/notifications/{YYYY-MM-DD}.jsonl
data/features/{market}/{trading_date}.json
data/archive/raw/{market}/{trading_date}.jsonl.gz
```

Raw 数据只追加写入。Normalized、metrics 和 quality 都是确定性的派生层，可由 raw 数据重建。

## 时间模型

新记录在系统内部统一使用 UTC。市场时区会单独保存，只用于 normalize、`trading_date`、session window 和 UI 展示。

Longbridge timestamp 按市场本地时间处理：

```text
Longbridge timestamp -> 绑定市场时区 -> 转换为 UTC -> source_timestamp_utc
```

示例：

```json
{
  "source_timestamp_raw": "2026-05-12 09:30:00",
  "market_timezone": "America/New_York",
  "source_timestamp_utc": "2026-05-12T13:30:00Z"
}
```

`trading_date` 来自市场本地日期，不等于 UTC 日期。美股使用 `America/New_York`，由 `zoneinfo` 自动处理 EDT/EST 夏令时切换。详见 [docs/time_model.md](docs/time_model.md)。

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
  pipeline_runner.py
  run_pipeline.py
  extended_pipeline.py
  extended_report.py
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
