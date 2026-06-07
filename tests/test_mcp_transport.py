"""Mocked MCP transport tests for Streamable HTTP tuple handling."""

from __future__ import annotations

import sys
import types
import logging
from contextlib import asynccontextmanager

import pytest
import httpx

from clients.longbridge_mcp_client import LongbridgeMcpClient

STREAMABLE_CALLS: list[dict] = []


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


def _install_fake_mcp(
    monkeypatch: pytest.MonkeyPatch, transport, session_class=FakeSession
) -> None:
    STREAMABLE_CALLS.clear()
    session_class.instances = []
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = session_class
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamable_http_client(
        url: str, *, http_client=None, terminate_on_close: bool = True
    ):
        STREAMABLE_CALLS.append({
            "url": url,
            "http_client": http_client,
            "terminate_on_close": terminate_on_close,
        })
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
    call = STREAMABLE_CALLS[-1]
    assert "headers" not in call
    assert isinstance(call["http_client"], httpx.AsyncClient)
    assert call["http_client"].headers["Authorization"] == "Bearer test"
    assert call["http_client"].follow_redirects is True
    assert call["http_client"].timeout.read == 30.0
    assert call["http_client"].is_closed


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


def test_streamable_client_receives_http_client_without_headers_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mcp(monkeypatch, ("read", "write", lambda: "id"))
    LongbridgeMcpClient(auth_header="Bearer secret-value")._list_tools_raw()

    call = STREAMABLE_CALLS[-1]
    assert set(call) == {"url", "http_client", "terminate_on_close"}
    assert isinstance(call["http_client"], httpx.AsyncClient)
    assert call["http_client"].headers["Authorization"] == "Bearer secret-value"
    assert call["http_client"].follow_redirects is True
    assert call["http_client"].timeout.read == 30.0


def test_legacy_streamable_client_alias_is_import_only_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.instances = []
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamablehttp_client(url, **kwargs):
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
    async def streamable_http_client(url, **kwargs):
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


def test_missing_auth_fails_before_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LONGBRIDGE_MCP_AUTH_HEADER", raising=False)
    with pytest.raises(RuntimeError, match="auth header not configured"):
        LongbridgeMcpClient(auth_header="")._list_tools_raw()


def test_transport_error_does_not_log_or_raise_full_authorization(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "Bearer secret-token-value"
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamable_http_client(url, **kwargs):
        raise RuntimeError(f"provider rejected {secret}")
        yield

    streamable.streamable_http_client = streamable_http_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as exc_info:
        LongbridgeMcpClient(auth_header=secret)._call_mcp_raw("quote", {})

    assert "secret-token-value" not in str(exc_info.value)
    assert "secret-token-value" not in caplog.text
    assert "REDACTED" in str(exc_info.value)


def test_streamable_open_exception_group_surfaces_leaf_cause(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamable_http_client(url, **kwargs):
        raise ExceptionGroup(
            "transport task group",
            [httpx.ConnectTimeout("connection timed out")],
        )
        yield

    streamable.streamable_http_client = streamable_http_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as exc_info:
        LongbridgeMcpClient(auth_header="Bearer test")._list_tools_raw()

    message = str(exc_info.value)
    assert "streamable HTTP session open" in message
    assert "ConnectTimeout: connection timed out" in message
    assert "ConnectTimeout: connection timed out" in caplog.text


def test_streamable_lifecycle_exception_group_surfaces_in_health(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable = types.ModuleType("mcp.client.streamable_http")

    @asynccontextmanager
    async def streamable_http_client(url, **kwargs):
        yield ("read", "write", lambda: "id")
        raise ExceptionGroup(
            "transport close task group",
            [httpx.RemoteProtocolError("server disconnected unexpectedly")],
        )

    streamable.streamable_http_client = streamable_http_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable)

    with caplog.at_level(logging.ERROR):
        health = LongbridgeMcpClient(auth_header="Bearer test").health_check()

    assert not health["ok"]
    assert health["status"] == "discovery_failed"
    assert "RemoteProtocolError: server disconnected unexpectedly" in health["error"]
    assert "RemoteProtocolError: server disconnected unexpectedly" in caplog.text


@pytest.mark.parametrize(
    ("stage", "operation", "expected_stage"),
    [
        ("initialize", "_list_tools_raw", "session.initialize"),
        ("list_tools", "_list_tools_raw", "list_tools"),
        ("call_tool", "_call_mcp_raw", "call_tool(quote)"),
    ],
)
def test_session_exception_groups_surface_sanitized_leaf_causes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
    operation: str,
    expected_stage: str,
) -> None:
    secret = "Bearer secret-token-value"

    class FailingSession(FakeSession):
        async def initialize(self):
            if stage == "initialize":
                raise ExceptionGroup(
                    "initialize task group",
                    [httpx.HTTPStatusError(
                        f"401 unauthorized for {secret}",
                        request=httpx.Request("POST", "https://mcp.longbridge.com"),
                        response=httpx.Response(401),
                    )],
                )
            await super().initialize()

        async def list_tools(self):
            if stage == "list_tools":
                raise ExceptionGroup(
                    "list task group",
                    [httpx.RemoteProtocolError(f"invalid response for {secret}")],
                )
            return await super().list_tools()

        async def call_tool(self, name, arguments):
            if stage == "call_tool":
                raise ExceptionGroup(
                    "call task group",
                    [httpx.ConnectTimeout(f"timeout using {secret}")],
                )
            return await super().call_tool(name, arguments)

    _install_fake_mcp(
        monkeypatch, ("read", "write", lambda: "id"), session_class=FailingSession
    )
    mcp_client = LongbridgeMcpClient(auth_header=secret)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as exc_info:
        if operation == "_list_tools_raw":
            mcp_client._list_tools_raw()
        else:
            mcp_client._call_mcp_raw("quote", {"symbols": ["QQQ"]})

    message = str(exc_info.value)
    assert expected_stage in message
    assert {
        "initialize": "HTTPStatusError: 401 unauthorized",
        "list_tools": "RemoteProtocolError: invalid response",
        "call_tool": "ConnectTimeout: timeout",
    }[stage] in message
    assert "secret-token-value" not in message
    assert "secret-token-value" not in caplog.text
    assert "REDACTED" in message
