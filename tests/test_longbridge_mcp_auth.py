"""Tests for Longbridge MCP Authorization Exchange."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
import pytest

from scripts.longbridge_auth_exchange import unwrap_token, perform_exchange, update_env


class FakeAuthSession:
    instances: list["FakeAuthSession"] = []

    def __init__(self, read, write):
        self.read = read
        self.write = write
        self.initialized = False
        self.call_tool_args = None
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        self.initialized = True

    async def call_tool(self, name, arguments):
        self.call_tool_args = (name, arguments)
        if name == "authenticate":
            auth_code = arguments.get("auth_code")
            if auth_code == "valid_code_123":
                # Return standard MCP tool response text block
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"token": "mocked_access_token_456"})
                        }
                    ]
                }
            else:
                raise ValueError("Invalid auth code")
        raise ValueError(f"Unknown tool: {name}")


def _install_fake_auth_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAuthSession.instances = []
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeAuthSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamable_http_client(**kwargs):
        yield ("read", "write")

    streamable.streamable_http_client = streamable_http_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable)


def test_unwrap_token_various_formats() -> None:
    # 1. Standard text block containing JSON
    raw_json = {
        "content": [
            {
                "type": "text",
                "text": '{"token": "my_secret_token"}'
            }
        ]
    }
    assert unwrap_token(raw_json) == "my_secret_token"

    # 2. Standard text block containing JSON with access_token key
    raw_json_access = {
        "content": [
            {
                "type": "text",
                "text": '{"access_token": "my_access_token"}'
            }
        ]
    }
    assert unwrap_token(raw_json_access) == "my_access_token"

    # 3. Standard text block containing raw token string (not JSON)
    raw_text = {
        "content": [
            {
                "type": "text",
                "text": "my_raw_token"
            }
        ]
    }
    assert unwrap_token(raw_text) == "my_raw_token"

    # 4. Dictionary token
    raw_dict = {"token": "my_dict_token"}
    assert unwrap_token(raw_dict) == "my_dict_token"


def test_perform_exchange_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_auth_mcp(monkeypatch)
    token = asyncio.run(perform_exchange(
        auth_url="https://mcp.longbridge.cn/agent",
        auth_code="valid_code_123"
    ))
    assert token == "mocked_access_token_456"

    # Verify FakeAuthSession was called with expected tool and argument
    assert len(FakeAuthSession.instances) == 1
    session = FakeAuthSession.instances[0]
    assert session.initialized
    assert session.call_tool_args == ("authenticate", {"auth_code": "valid_code_123"})


def test_perform_exchange_invalid_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_auth_mcp(monkeypatch)
    with pytest.raises(Exception):
        asyncio.run(perform_exchange(
            auth_url="https://mcp.longbridge.cn/agent",
            auth_code="invalid_code"
        ))


def test_update_env_updates_existing_values() -> None:
    env_file = Path("test_env_temp.env")
    try:
        initial_content = (
            "MARKET_DATA_PROVIDER=longbridge_mcp\n"
            "LONGBRIDGE_MCP_URL=https://mcp.longbridge.com\n"
            "LONGBRIDGE_MCP_AUTH_HEADER=\n"
        )
        env_file.write_text(initial_content, encoding="utf-8")

        update_env(
            env_path=env_file,
            mcp_url="https://mcp.longbridge.cn",
            auth_header="Bearer mocked_access_token_456"
        )

        updated_content = env_file.read_text(encoding="utf-8")
        assert "LONGBRIDGE_MCP_URL=https://mcp.longbridge.cn" in updated_content
        assert "LONGBRIDGE_MCP_AUTH_HEADER=Bearer mocked_access_token_456" in updated_content
        assert "MARKET_DATA_PROVIDER=longbridge_mcp" in updated_content
    finally:
        if env_file.exists():
            env_file.unlink()


def test_update_env_creates_file_if_not_exists() -> None:
    env_file = Path("test_env_temp_new.env")
    if env_file.exists():
        env_file.unlink()
    try:
        update_env(
            env_path=env_file,
            mcp_url="https://mcp.longbridge.cn",
            auth_header="Bearer mocked_access_token_456"
        )

        assert env_file.exists()
        content = env_file.read_text(encoding="utf-8")
        assert "LONGBRIDGE_MCP_URL=https://mcp.longbridge.cn" in content
        assert "LONGBRIDGE_MCP_AUTH_HEADER=Bearer mocked_access_token_456" in content
    finally:
        if env_file.exists():
            env_file.unlink()
