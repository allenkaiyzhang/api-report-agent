"""Mocked MCP transport tests for Streamable HTTP tuple handling."""

from __future__ import annotations

import sys
import types
from contextlib import asynccontextmanager

import pytest

from clients.longbridge_mcp_client import LongbridgeMcpClient


class FakeSession:
    instances: list["FakeSession"] = []

    def __init__(self, read, write):
        self.read = read
        self.write = write
        self.initialized = False
        self.list_tools_called = False
        self.call_tool_args = None
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        self.list_tools_called = True
        return {"tools": [{"name": "quote"}]}

    async def call_tool(self, name, arguments):
        self.call_tool_args = (name, arguments)
        return {"content": []}


def _install_fake_mcp(monkeypatch: pytest.MonkeyPatch, transport) -> None:
    FakeSession.instances = []
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamable_http_client(**kwargs):
        yield transport

    streamable.streamable_http_client = streamable_http_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable)


@pytest.mark.parametrize("transport", [("read", "write"), ("read", "write", lambda: "id")])
def test_streamable_transport_accepts_two_and_three_tuples(
    monkeypatch: pytest.MonkeyPatch, transport
) -> None:
    _install_fake_mcp(monkeypatch, transport)
    result = LongbridgeMcpClient(auth_header="Bearer test")._list_tools_raw()

    assert result == {"tools": [{"name": "quote"}]}
    session = FakeSession.instances[-1]
    assert session.read == "read"
    assert session.write == "write"
    assert session.initialized
    assert session.list_tools_called


def test_unsupported_streamable_transport_shape_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mcp(monkeypatch, ("read", "write", "id", "extra"))

    with pytest.raises(RuntimeError, match="expected 2 or 3 items, got 4"):
        LongbridgeMcpClient(auth_header="Bearer test")._list_tools_raw()


def test_call_tool_uses_initialized_streamable_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mcp(monkeypatch, ("read", "write", lambda: "id"))
    result = LongbridgeMcpClient(auth_header="Bearer test")._call_mcp_raw(
        "quote", {"symbols": ["QQQ"]}
    )

    assert result == {"content": []}
    session = FakeSession.instances[-1]
    assert session.initialized
    assert session.call_tool_args == ("quote", {"symbols": ["QQQ"]})


def test_legacy_streamable_client_alias_is_import_only_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.instances = []
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamablehttp_client(**kwargs):
        yield ("read", "write", lambda: "id")

    streamable.streamablehttp_client = streamablehttp_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable)

    LongbridgeMcpClient(auth_header="Bearer test")._list_tools_raw()
    assert FakeSession.instances[-1].list_tools_called


def test_runtime_streamable_error_does_not_fall_back_to_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")
    sse = types.ModuleType("mcp.client.sse")
    sse_called = False

    @asynccontextmanager
    async def streamable_http_client(**kwargs):
        raise ValueError("streamable runtime failure")
        yield

    @asynccontextmanager
    async def sse_client(**kwargs):
        nonlocal sse_called
        sse_called = True
        yield ("read", "write")

    streamable.streamable_http_client = streamable_http_client
    sse.sse_client = sse_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse)

    with pytest.raises(ValueError, match="streamable runtime failure"):
        LongbridgeMcpClient(auth_header="Bearer test")._list_tools_raw()
    assert not sse_called
