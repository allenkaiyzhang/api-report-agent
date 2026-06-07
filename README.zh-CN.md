# API Report Agent

只读市场报告工作流：

`Longbridge 官方 MCP -> 采集 -> 清洗 -> 校验 -> 盘中/收盘报告 -> 通知`

唯一生产入口是 `scripts/market_report_agent.py`。`scripts/run_pipeline.py` 和
`clients/market_client.py` 是已弃用的旧 Longbridge SDK 路径，部署和 systemd
不得使用它们。

## 安全与认证

- 交易/写入工具永久禁止。
- 账户读取工具默认禁止。
- 未知 MCP 工具默认拒绝。
- 真实 provider 必须先完成 MCP 协议工具发现。
- 生产模式不会静默回退到 mock。
- 本仓库未实现完整 Longbridge OAuth 2.1；使用外部已授权会话提供的
  `LONGBRIDGE_MCP_AUTH_HEADER`。

## 依赖

- **生产 / 部署:** `requirements.txt` — 最小运行时依赖（由 `scripts/deploy.sh` 使用）。
- **CI / 开发:** `requirements-dev.txt` — 通过 `-r requirements.txt` 包含所有生产依赖，外加
  `pytest`、`httpx`。本地开发和测试请使用此文件。

## 本地运行与验证

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt   # CI / 开发
# 或: pip install -r requirements.txt # 仅生产依赖
cp .env.example .env

python -m pytest -q
python scripts/healthcheck_mcp.py --json --provider mock
python scripts/market_report_agent.py --health --provider mock
python scripts/market_report_agent.py --once --provider mock
python scripts/market_report_agent.py --health --provider longbridge_mcp
python scripts/smoke_test.py
```

未配置真实授权会话时，`longbridge_mcp` 健康检查必须明确失败并返回非零状态。

## 配置

```env
APP_ENV=production
MARKET_DATA_PROVIDER=longbridge_mcp
LONGBRIDGE_MCP_URL=https://mcp.longbridge.com
LONGBRIDGE_MCP_AUTH_HEADER=Bearer <外部获取的授权令牌>
ACCOUNT_READ_ENABLED=false
```

不要提交 `.env` 或密钥。mock 只用于显式 `--provider mock`、`APP_ENV=test`
或 smoke test。

## 部署与日志

通过 GitHub Actions 的 "Deploy to ECS" 工作流（`workflow_dispatch`）手动触发部署，
CD 会先验证目标 commit 的 CI 已通过，再远程执行部署脚本。

```bash
sudo bash scripts/deploy.sh
sudo systemctl enable --now market-report-agent
sudo systemctl status market-report-agent
sudo journalctl -u market-report-agent -f
```

`scripts/deploy.sh` 使用 venv + systemd，默认显示 pip install 完整输出（设
`PIP_QUIET=1` 可切换安静模式），执行健康检查和 smoke test 后退出；
关键步骤失败会返回非零状态。

`scripts/post_deploy_verify.sh` 通过 `systemctl is-active` 校验服务运行状态，
若服务 inactive/failed 则 exit 1；随后检查日志、运行 smoke test
和 provider 健康检查。

盘中调度和收盘报告使用市场本地日期，收盘报告
必须通过交易日、已完成 session、延迟、状态和数据时间戳校验。

## 排障

- `LONGBRIDGE_MCP_AUTH_HEADER not set`：配置外部已授权会话头。
- `discovery_failed`：检查 MCP 地址、会话、网络和 MCP SDK。
- `missing_session_close`：provider 未提供可靠的已完成收盘时间。
- `timestamps do not align`：数据陈旧或不属于目标交易日。

新增 provider 必须实现 `MarketDataClient`；报告工作流不得直接调用原始 MCP
工具，不得启用交易功能。
