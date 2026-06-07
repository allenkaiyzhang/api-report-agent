"""LongbridgeMcpClient — controlled adapter for Longbridge Remote MCP.

Connects to the Longbridge official Remote MCP endpoint via Streamable HTTP.
Uses an externally-supplied authorized MCP transport (not a full OAuth 2.1 client).
Blocks trading tools in code (not just in prompts).
Account-read tools are disabled by default and require explicit config.
Default-deny for unknown tools.

Reference:
  Longbridge official MCP: https://open.longbridge.com/docs/mcp
  Global endpoint: https://mcp.longbridge.com
  Mainland China endpoint: https://mcp.longbridge.cn

Session/auth model:
  This adapter requires an already-authorized MCP session or transport.
  The LONGBRIDGE_MCP_AUTH_HEADER env var provides a pre-obtained authorization
  header value (e.g. "Bearer <token>"). This is NOT a full OAuth 2.1 client
  implementation — it is an external-session model where the auth is obtained
  out-of-band. A future real OAuth client can be plugged in via the same
  transport seam.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.policy.tool_policy import LongbridgeToolPolicy
from clients.market_data_client import (
    Candle,
    FundamentalData,
    IntradayPoint,
    MarketDataClient,
    MarketStatusInfo,
    Quote,
)

logger = logging.getLogger(__name__)

# ── Default MCP endpoint ─────────────────────────────────────────
_DEFAULT_MCP_URL = "https://mcp.longbridge.com"

_REQUIRED_INTERNAL_OPERATIONS = frozenset(
    {"quote", "candles", "intraday", "market_status"}
)


class LongbridgeMcpClient(MarketDataClient):
    """Adapter that wraps Longbridge Remote MCP with tool blocking.

    Does NOT implement a custom MCP server — connects to the Longbridge
    official Remote MCP endpoint using Streamable HTTP transport.

    Configuration (env vars):
        LONGBRIDGE_MCP_URL — MCP endpoint URL (default: https://mcp.longbridge.com)
        LONGBRIDGE_MCP_AUTH_HEADER — Authorization header value for MCP transport
            (e.g. "Bearer <token>"). This is a pre-obtained auth header, not
            a full OAuth 2.1 client flow. Obtain the token through Longbridge's
            official auth mechanism before starting this service.
        ACCOUNT_READ_ENABLED — set "true" to enable account-read tools
    """

    def __init__(
        self,
        mcp_url: str | None = None,
        auth_header: str | None = None,
        account_read_enabled: bool = False,
    ) -> None:
        self._mcp_url = mcp_url or os.getenv("LONGBRIDGE_MCP_URL", "") or _DEFAULT_MCP_URL
        self._auth_header = auth_header or os.getenv("LONGBRIDGE_MCP_AUTH_HEADER", "")
        self._account_read_enabled = account_read_enabled or (
            os.getenv("ACCOUNT_READ_ENABLED", "false").lower() == "true"
        )

        # Tool policy (default-deny, discovery-first)
        self._policy = LongbridgeToolPolicy(account_read_enabled=self._account_read_enabled)

        # MCP session state
        self._connected = False
        self._discovery_done = False
        self._discovery_error: str | None = None
        self._session_error: str | None = None

        # Raw response cache (preserve original provider data)
        self._last_raw_responses: dict[str, Any] = {}

    # ── Policy delegation ────────────────────────────────────────

    @property
    def policy(self) -> LongbridgeToolPolicy:
        return self._policy

    @property
    def provider_name(self) -> str:
        return "longbridge_mcp"

    def is_account_read_enabled(self) -> bool:
        return self._account_read_enabled

    def is_trading_enabled(self) -> bool:
        return False

    def get_last_raw(self, tool_name: str) -> Any:
        """Return the raw provider response for a tool call (for audit)."""
        return self._last_raw_responses.get(tool_name)

    # ── Tool discovery (mandatory in production) ─────────────────

    def discover_tools(self) -> list[str]:
        """List available tools from the Longbridge MCP endpoint.

        In real provider mode, discovery is mandatory — failure blocks
        all data operations.

        Returns the list of discovered tool names.
        Raises RuntimeError if discovery fails and no fallback tools exist.
        """
        if self._discovery_done:
            if self._discovery_error:
                raise RuntimeError(self._discovery_error)
            return sorted(self._policy.get_discovered_tools())

        self._ensure_session()

        try:
            result = self._list_tools_raw()
            tools = self._extract_tool_names(result)
            if tools:
                self._policy.update_from_discovery(tools)
                logger.info("Discovered %d Longbridge MCP tools: %s", len(tools), tools)

                missing_required = {
                    operation
                    for operation in _REQUIRED_INTERNAL_OPERATIONS
                    if not self._policy.has_mapping(operation)
                }
                if missing_required:
                    self._discovery_error = (
                        f"Required MCP operations not mapped from discovered tools: "
                        f"{', '.join(sorted(missing_required))}. Discovered: {sorted(tools)}"
                    )
                    logger.error(self._discovery_error)
            else:
                self._discovery_error = (
                    "Tool discovery returned no tools from Longbridge MCP endpoint. "
                    "Cannot proceed in production mode without discovered tools."
                )
                logger.error(self._discovery_error)
        except Exception as exc:
            detail = self._sanitize_error_message(exc)
            self._discovery_error = (
                f"Tool discovery failed: {detail}. "
                "Cannot proceed in production mode without tool discovery."
            )
            logger.error(self._discovery_error)

        self._discovery_done = True

        if self._discovery_error:
            raise RuntimeError(self._discovery_error)

        return sorted(self._policy.get_discovered_tools())

    def _extract_tool_names(self, response: Any) -> list[str]:
        """Extract tool names from a tools/list response."""
        data = {"tools": getattr(response, "tools")} if hasattr(response, "tools") else self._unwrap_content(response)
        if isinstance(data, dict):
            tools = data.get("tools", [])
            if isinstance(tools, list):
                names: list[str] = []
                for tool in tools:
                    if isinstance(tool, dict) and tool.get("name"):
                        names.append(str(tool["name"]))
                    elif getattr(tool, "name", None):
                        names.append(str(tool.name))
                return names
        return []

    def is_discovery_ok(self) -> bool:
        """Return True if tool discovery succeeded."""
        return self._discovery_done and self._discovery_error is None

    def get_discovery_error(self) -> str | None:
        """Return discovery error message if any."""
        return self._discovery_error

    # ── MCP transport ────────────────────────────────────────────

    def _ensure_session(self) -> None:
        """Validate that we have a usable MCP auth header.

        Does NOT actually connect — that happens on first tool call.
        This validates that required configuration is present.
        """
        if not self._auth_header:
            self._session_error = (
                "Longbridge MCP auth header not configured. "
                "Set LONGBRIDGE_MCP_AUTH_HEADER environment variable "
                "(e.g. 'Bearer <token>'). "
                "See https://open.longbridge.com/docs/mcp for auth setup."
            )
            raise RuntimeError(self._session_error)

    def _call_mcp_raw(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a Longbridge MCP tool via Streamable HTTP transport.

        This is the raw MCP call — policy enforcement happens at a higher level.
        """
        self._ensure_session()

        try:
            import asyncio

            async def _call() -> Any:
                async with self._open_mcp_session() as session:
                    return await session.call_tool(tool_name, arguments=arguments)

            return asyncio.run(_call())

        except ImportError:
            raise RuntimeError(
                "MCP SDK not installed. Install with: pip install mcp>=1.0.0"
            )
        except Exception as exc:
            detail = self._sanitize_error_message(exc)
            logger.error("MCP tool '%s' failed: %s", tool_name, detail)
            raise RuntimeError(f"MCP tool '{tool_name}' failed: {detail}") from exc

    def _list_tools_raw(self) -> Any:
        """Use the MCP protocol list_tools operation, not an invented tool call."""
        self._ensure_session()
        try:
            import asyncio

            async def _list() -> Any:
                async with self._open_mcp_session() as session:
                    return await session.list_tools()

            return asyncio.run(_list())
        except ImportError as exc:
            raise RuntimeError("MCP SDK not installed. Install with: pip install mcp>=1.0.0") from exc

    @staticmethod
    def _normalize_mcp_transport(transport: Any) -> tuple[Any, Any]:
        """Extract read/write streams from supported MCP client transport shapes."""
        if not isinstance(transport, tuple):
            raise RuntimeError(
                "Unsupported MCP transport shape: expected a 2-tuple or 3-tuple, "
                f"got {type(transport).__name__}"
            )
        if len(transport) not in (2, 3):
            raise RuntimeError(
                "Unsupported MCP transport tuple length: expected 2 or 3 items, "
                f"got {len(transport)}"
            )
        read, write = transport[0], transport[1]
        if read is None or write is None:
            raise RuntimeError("Unsupported MCP transport shape: read/write streams are required")
        return read, write

    def _sanitize_error_message(self, error: Exception) -> str:
        """Remove the configured Authorization value from provider errors."""
        message = str(error)
        if self._auth_header:
            message = message.replace(self._auth_header, "[REDACTED_AUTHORIZATION]")
            parts = self._auth_header.split(" ", 1)
            if len(parts) == 2 and parts[1]:
                message = message.replace(parts[1], "[REDACTED_TOKEN]")
        return message

    @asynccontextmanager
    async def _open_mcp_session(self) -> AsyncIterator[Any]:
        """Open and initialize an MCP session over Streamable HTTP or import-only SSE fallback."""
        from mcp import ClientSession

        try:
            from mcp.client.streamable_http import streamable_http_client
        except ImportError:
            try:
                from mcp.client.streamable_http import streamablehttp_client as streamable_http_client
            except ImportError:
                streamable_http_client = None

        async with AsyncExitStack() as stack:
            if streamable_http_client is None:
                from mcp.client.sse import sse_client

                transport_context = sse_client(
                    url=self._mcp_url,
                    headers={"Authorization": self._auth_header},
                )
            else:
                import httpx

                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(headers={"Authorization": self._auth_header})
                )
                transport_context = streamable_http_client(
                    url=self._mcp_url,
                    http_client=http_client,
                )

            transport = await stack.enter_async_context(transport_context)
            read, write = self._normalize_mcp_transport(transport)
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    def _call_mcp_tool(self, internal_op: str, arguments: dict[str, Any]) -> Any:
        """Call a Longbridge MCP tool with policy enforcement.

        Args:
            internal_op: Internal operation name (e.g., 'quote', 'candles', 'market_status')
            arguments: Tool arguments dict

        Returns:
            Raw MCP response

        Raises:
            PermissionError: If the tool is blocked by policy
            RuntimeError: If MCP is not configured or call fails
        """
        self.discover_tools()
        actual_tool = self._policy.get_mapped_tool(internal_op)
        if not actual_tool:
            raise RuntimeError(
                f"No MCP tool mapping for internal operation '{internal_op}'. "
                "Run tool discovery or check tool policy configuration."
            )

        # Policy check
        self._policy.assert_allowed(actual_tool)

        raw = self._call_mcp_raw(actual_tool, arguments)
        self._last_raw_responses[actual_tool] = raw
        return raw

    # ── Health check ─────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """Check connectivity and return health status."""
        # Check if auth is configured
        if not self._auth_header:
            return {
                "ok": False,
                "provider": "longbridge_mcp",
                "status": "not_configured",
                "error": "LONGBRIDGE_MCP_AUTH_HEADER not set",
                "mcp_endpoint": self._mcp_url,
            }

        if self._session_error and not self._connected:
            return {
                "ok": False,
                "provider": "longbridge_mcp",
                "status": "session_error",
                "error": self._session_error,
                "mcp_endpoint": self._mcp_url,
            }

        # Check discovery
        try:
            self.discover_tools()
        except Exception as exc:
            return {
                "ok": False,
                "provider": "longbridge_mcp",
                "status": "discovery_failed",
                "error": self._sanitize_error_message(exc)[:300],
                "mcp_endpoint": self._mcp_url,
            }

        try:
            self._call_mcp_tool("market_status", {"market": "US"})
            self._connected = True
            return {
                "ok": True,
                "provider": "longbridge_mcp",
                "status": "connected",
                "mcp_endpoint": self._mcp_url,
                "discovery": "ok",
                "discovery_detail": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider": "longbridge_mcp",
                "status": "connection_failed",
                "error": self._sanitize_error_message(exc)[:300],
                "mcp_endpoint": self._mcp_url,
                "discovery": "ok",
                "discovery_detail": None,
            }

    # ── MarketDataClient implementation ──────────────────────────

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        self.discover_tools()
        results: list[Quote] = []
        for symbol in symbols:
            raw = self._call_mcp_tool("quote", {"symbols": [symbol]})
            results.extend(self._parse_quotes(raw, symbol))
        return results

    def get_candles(self, symbols: list[str], period: str = "day", count: int = 20) -> list[Candle]:
        self.discover_tools()
        results: list[Candle] = []
        for symbol in symbols:
            raw = self._call_mcp_tool("candles", {
                "symbol": symbol,
                "period": period,
                "count": count,
            })
            results.extend(self._parse_candles(raw, symbol))
        return results

    def get_intraday(self, symbols: list[str]) -> list[IntradayPoint]:
        self.discover_tools()
        results: list[IntradayPoint] = []
        for symbol in symbols:
            raw = self._call_mcp_tool("intraday", {"symbol": symbol})
            results.extend(self._parse_intraday(raw, symbol))
        return results

    def get_market_status(self, markets: list[str]) -> list[MarketStatusInfo]:
        self.discover_tools()
        results: list[MarketStatusInfo] = []
        for market in markets:
            raw = self._call_mcp_tool("market_status", {"market": market})
            results.append(self._parse_market_status(raw, market))
        return results

    def get_fundamentals(self, symbols: list[str]) -> list[FundamentalData]:
        self.discover_tools()
        results: list[FundamentalData] = []
        for symbol in symbols:
            raw = self._call_mcp_tool("fundamentals", {"symbols": [symbol]})
            results.extend(self._parse_fundamentals(raw, symbol))
        return results

    # ── Parsers ──────────────────────────────────────────────────

    def _infer_market(self, symbol: str) -> str:
        return "HK" if symbol.upper().endswith(".HK") else "US"

    def _parse_quotes(self, raw: Any, symbol: str) -> list[Quote]:
        data = self._unwrap_content(raw)
        items = data if isinstance(data, list) else [data]
        market = self._infer_market(symbol)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            latest = float(item.get("last_done", 0) or 0)
            prev = float(item.get("prev_close", 0) or 0)
            change = ((latest - prev) / prev * 100) if prev else 0.0
            results.append(Quote(
                symbol=str(item.get("symbol", symbol)),
                market=market,
                latest_price=round(latest, 4),
                previous_close=round(prev, 4),
                change_percent=round(change, 2),
                open=float(item.get("open", 0) or 0),
                high=float(item.get("high", 0) or 0),
                low=float(item.get("low", 0) or 0),
                volume=int(item.get("volume", 0) or 0),
                turnover=float(item.get("turnover", 0) or 0),
                bid=float(item.get("bid", 0) or 0),
                ask=float(item.get("ask", 0) or 0),
                trade_status=str(item.get("trade_status", "")),
                currency=str(item.get("currency", "")),
                timestamp=str(item.get("timestamp", datetime.now(timezone.utc).isoformat())),
                source="longbridge_mcp",
            ))
        return results

    def _parse_candles(self, raw: Any, symbol: str) -> list[Candle]:
        data = self._unwrap_content(raw)
        items = data if isinstance(data, list) else [data]
        market = self._infer_market(symbol)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(Candle(
                symbol=symbol,
                market=market,
                close=float(item.get("close", 0) or 0),
                open=float(item.get("open", 0) or 0),
                low=float(item.get("low", 0) or 0),
                high=float(item.get("high", 0) or 0),
                volume=int(item.get("volume", 0) or 0),
                turnover=float(item.get("turnover", 0) or 0),
                timestamp=str(item.get("timestamp", "")),
                trade_session=str(item.get("trade_session", "")),
                source="longbridge_mcp",
            ))
        return results

    def _parse_intraday(self, raw: Any, symbol: str) -> list[IntradayPoint]:
        data = self._unwrap_content(raw)
        items = data if isinstance(data, list) else [data]
        market = self._infer_market(symbol)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(IntradayPoint(
                symbol=symbol,
                market=market,
                price=float(item.get("price", 0) or 0),
                volume=int(item.get("volume", 0) or 0),
                turnover=float(item.get("turnover", 0) or 0),
                timestamp=str(item.get("timestamp", "")),
                source="longbridge_mcp",
            ))
        return results

    def _parse_market_status(self, raw: Any, market: str) -> MarketStatusInfo:
        data = self._unwrap_content(raw)
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            data = {}
        return MarketStatusInfo(
            market=market,
            is_open=data.get("is_open", False) in (True, "true", "1"),
            session=str(data.get("session", "closed")),
            current_session_open=str(data.get("current_session_open", "")),
            current_session_close=str(data.get("current_session_close", "")),
            last_close=str(data.get("last_close", "")),
            next_open=str(data.get("next_open", "")),
            next_close=str(data.get("next_close", "")),
            timestamp=str(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            source="longbridge_mcp",
        )

    def _parse_fundamentals(self, raw: Any, symbol: str) -> list[FundamentalData]:
        data = self._unwrap_content(raw)
        items = data if isinstance(data, list) else [data]
        market = self._infer_market(symbol)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(FundamentalData(
                symbol=str(item.get("symbol", symbol)),
                market=market,
                name_cn=str(item.get("name_cn", "")),
                name_en=str(item.get("name_en", "")),
                exchange=str(item.get("exchange", "")),
                currency=str(item.get("currency", "")),
                lot_size=int(item.get("lot_size", 0) or 0),
                eps=float(item.get("eps", 0) or 0),
                eps_ttm=float(item.get("eps_ttm", 0) or 0),
                bps=float(item.get("bps", 0) or 0),
                dividend_yield=float(item.get("dividend_yield", 0) or 0),
                board=str(item.get("board", "")),
                source="longbridge_mcp",
            ))
        return results

    @staticmethod
    def _unwrap_content(raw: Any) -> Any:
        """Unwrap MCP tool result: handles content list, text, or dict."""
        if isinstance(raw, dict):
            content = raw.get("content")
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                joined = "".join(texts)
                if joined.strip().startswith("{") or joined.strip().startswith("["):
                    try:
                        return json.loads(joined)
                    except json.JSONDecodeError:
                        return joined
                return joined
            return raw
        if isinstance(raw, list):
            return raw
        return {"value": str(raw)}
