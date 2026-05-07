# Market Data Pipeline

轻量 Python 行情数据管道，用于采集港股/美股行情数据，并生成可回放、可审计的数据中间层。

当前阶段只做数据采集、清洗、标准化、窗口指标、daily metrics 和 quality report。不做 AI 分析、不调用 LLM、不写 Prompt、不做交易。

## 功能

- Python 3.11+
- 默认支持 mock 行情数据
- 可选接入 Longbridge OpenAPI 拉取真实行情数据
- 港股/美股交易时段内每 2 分钟采集一次
- raw append-only 原始数据
- normalized 标准化数据
- window metrics / daily metrics
- quality report
- replay、debug chart、healthcheck、cleanup 运维工具

## 安装

```bash
pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
```

默认 mock 模式：

```env
MARKET_DATA_PROVIDER=mock
DATA_COLLECTION_INTERVAL_SECONDS=120
DATA_COLLECTION_OUTPUT_DIR=data/raw
DATA_COLLECTION_FILE_TIMEZONE=Asia/Shanghai
PIPELINE_LOOP_SLEEP_SECONDS=10
PIPELINE_FORCE_REBUILD=false
```

Longbridge 模式：

```env
MARKET_DATA_PROVIDER=longbridge
LONGBRIDGE_APP_KEY=your_app_key
LONGBRIDGE_APP_SECRET=your_app_secret
LONGBRIDGE_ACCESS_TOKEN=your_access_token
```

## 采集

统一入口：

```bash
python main.py run
```

单独启动采集 agent：

```bash
python main.py collect
```

长期稳定运行完整管道：

```bash
python main.py run
```

`run_pipeline.py` 会循环执行：

- 每 2 分钟采集一次交易时段内数据
- 采集后自动 normalize 当天 raw 文件
- 窗口结束后自动生成缺失的 `window_*.json`
- 收盘后自动生成 `daily.json` 和 `quality.json`
- 任一步骤失败都会写日志和 runtime 状态，不会让整个循环退出

## 数据链路

```text
data/raw/{market}/{trading_date}.jsonl
  -> data/normalized/{market}/{trading_date}.jsonl
  -> data/metrics/{market}/{trading_date}/windows.json
  -> data/metrics/{market}/{trading_date}/window_{window_id}.json
  -> data/metrics/{market}/{trading_date}/daily.json
  -> data/quality/{market}/{trading_date}.json
```

Raw 层是 append-only，不覆盖、不修改、不删除。

## 数据管道 CLI

```bash
python main.py data normalize --market HK --date 2026-05-07
python main.py data windows --market HK --date 2026-05-07
python main.py data metrics --market HK --date 2026-05-07
python main.py data daily --market HK --date 2026-05-07
python main.py data quality --market HK --date 2026-05-07
python main.py data all --market HK --date 2026-05-07
```

`all` 执行顺序：

```text
normalize -> windows -> metrics -> daily -> quality
```

## Replay / Debug

```bash
python main.py replay --market HK --date 2026-05-07
python main.py replay --market HK --date 2026-05-07 --window 0930_1030
```

生成 debug 图：

```bash
python main.py chart --market HK --date 2026-05-07 --symbol 0700.HK
```

输出到：

```text
charts/0700.HK_2026-05-07.png
```

## 运维工具

```bash
python main.py health
python main.py cleanup
```

默认保留策略：

- raw / normalized / metrics / quality：永久
- charts：30 天
- logs：14 天

## 项目结构

```text
api-report-agent/
├── main.py
├── data_pipeline.py        # compatibility wrapper
├── market_data_agent.py    # compatibility wrapper
├── run_pipeline.py         # compatibility wrapper
├── replay.py               # compatibility wrapper
├── debug_chart.py          # compatibility wrapper
├── healthcheck.py          # compatibility wrapper
├── cleanup.py              # compatibility wrapper
├── requirements.txt
├── .env.example
├── README.md
├── config/
│   └── symbols.csv
├── clients/
│   ├── __init__.py
│   └── market_client.py
├── core/
│   ├── __init__.py
│   ├── data_pipeline.py
│   ├── loader.py
│   ├── runtime_support.py
│   ├── trading_hours.py
│   ├── market_data_store.py
│   └── market_data_cleaner.py
├── scripts/
│   ├── __init__.py
│   ├── market_data_agent.py
│   ├── run_pipeline.py
│   ├── replay.py
│   ├── debug_chart.py
│   ├── healthcheck.py
│   └── cleanup.py
├── data/
│   ├── raw/
│   ├── normalized/
│   ├── metrics/
│   └── quality/
├── logs/
├── runtime/
├── charts/
└── tests/
```
