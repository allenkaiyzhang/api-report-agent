from __future__ import annotations

from fastapi import FastAPI


app = FastAPI(title="API Report Agent")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api-report-agent"}
