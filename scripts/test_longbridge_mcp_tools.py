#!/usr/bin/env python
"""Initialize Longbridge MCP and print discovered tool names without calling tools."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


def _normalize_transport(transport: Any) -> tuple[Any, Any]:
    if not isinstance(transport, tuple) or len(transport) not in (2, 3):
        raise RuntimeError(
            "Unsupported MCP transport shape; expected a 2-tuple or 3-tuple"
        )
    read, write = transport[0], transport[1]
    if read is None or write is None:
        raise RuntimeError("Unsupported MCP transport shape; read/write streams required")
    return read, write


async def _list_tools(url: str, auth_header: str, timeout_seconds: float) -> list[str]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx.AsyncClient(
        headers={"Authorization": auth_header},
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as transport:
            read, write = _normalize_transport(transport)
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                return sorted(str(tool.name) for tool in response.tools)


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")
    url = os.getenv("LONGBRIDGE_MCP_URL", "https://mcp.longbridge.com")
    auth_header = os.getenv("LONGBRIDGE_MCP_AUTH_HEADER", "")
    timeout_seconds = float(os.getenv("LONGBRIDGE_MCP_TIMEOUT_SECONDS", "30"))

    if not auth_header:
        raise SystemExit("LONGBRIDGE_MCP_AUTH_HEADER not set")

    try:
        tools = asyncio.run(_list_tools(url, auth_header, timeout_seconds))
    except Exception as exc:
        message = str(exc).replace(auth_header, "[REDACTED_AUTHORIZATION]")
        token_parts = auth_header.split(" ", 1)
        if len(token_parts) == 2 and token_parts[1]:
            message = message.replace(token_parts[1], "[REDACTED_TOKEN]")
        raise SystemExit(f"Longbridge MCP tool discovery failed: {message}") from exc

    print(f"Endpoint: {url}")
    print(f"Discovered tools: {len(tools)}")
    for name in tools:
        print(name)


if __name__ == "__main__":
    main()
