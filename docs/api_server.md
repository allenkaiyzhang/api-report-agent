# API Server and Operations UI

The API server is a lightweight local control plane for api-report-agent.

It is intentionally bound to localhost only:

```bash
uvicorn api_server:app --host 127.0.0.1 --port 8000
```

## Endpoints

Read-only JSON endpoints:

```text
GET /health
GET /symbols
GET /markets/{market}/latest
GET /sessions/{market}/regular/latest
GET /sessions/{market}/extended/latest
GET /quotes/{symbol}/latest
GET /reports
GET /reports/{report_id}
```

Control endpoints:

```text
POST /control/run-regular-pipeline
POST /control/run-extended-pipeline
POST /control/run-daily-report
POST /control/run-extended-report
```

Control endpoints require:

```text
X-API-Token: value-of-API_CONTROL_TOKEN
```

## UI

```text
/ui/dashboard
/ui/reports
/ui/control
```

The UI uses Jinja2 templates only. It does not use React, Vue, or a database.

## SSH Tunnel

Keep the server bound to `127.0.0.1` on ECS and access it through SSH:

```bash
ssh -L 8000:127.0.0.1:8000 user@your-ecs-host
```

Then open:

```text
http://127.0.0.1:8000/ui/dashboard
```

## systemd Example

`/etc/systemd/system/api-report-agent-web.service`:

```ini
[Unit]
Description=api-report-agent web API
After=network.target

[Service]
WorkingDirectory=/opt/api-report-agent
EnvironmentFile=/opt/api-report-agent/.env
ExecStart=/opt/api-report-agent/.venv/bin/uvicorn api_server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Commands:

```bash
sudo systemctl daemon-reload
sudo systemctl enable api-report-agent-web.service
sudo systemctl restart api-report-agent-web.service
sudo journalctl -u api-report-agent-web.service -f
```
