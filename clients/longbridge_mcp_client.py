"""LongbridgeMcpClient — controlled adapter for Longbridge Remote MCP.

Connects to the Longbridge official Remote MCP endpoint via Streamable HTTP.
Uses OAuth 2.1 client authorization.
Blocks trading tools in code (not just in prompts).
Account-read tools are disabled by default and require explicit config.
Default-deny for unknown tools.

Reference:
  Longbridge official MCP: https://open.longbridge.com/docs/mcp
  Global endpoint: https://mcp.longbridge.com
  Mainland China endpoint: https://mcp.longbridge.cn
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

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


class LongbridgeMcpClient(MarketDataClient):
    """Adapter that wraps Longbridge Remote MCP with tool blocking.

    Does NOT implement a custom MCP server — connects to the Longbridge
    official Remote MCP endpoint using Streamable HTTP transport.

    Configuration (env vars):
        LONGBRIDGE_MCP_URL — MCP endpoint URL (default: https://mcp.longbridge.com)
        LONGBRIDGE_MCP_OAUTH_TOKEN — OAuth 2.1 token for MCP
        ACCOUNT_READ_ENABLED — set "true" to enable account-read tools
    """

    def __init__(
        self,
        mcp_url: str | None = None,
        oauth_token: str | None = None,
        account_read_enabled: bool = False,
    ) -> None:
        self._mcp_url = mcp_url or os.getenv("LONGBRIDGE_MCP_URL", "") or _DEFAULT_MCP_URL
        self._oauth_token = oauth_token or os.getenv("LONGBRIDGE_MCP_OAUTH_TOKEN", "")
        self._account_read_enabled = account_read_enabled or (
            os.getenv("ACCOUNT_READ_ENABLED", "false").lower() == "true"
        )

        # Tool policy (default-deny)
        self._policy = LongbridgeToolPolicy(account_read_enabled=self._account_read_enabled)

        # MCP session state
        self._connected = False
        self._discovery_done = False
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

    # ── Tool discovery ───────────────────────────────────────────

    def discover_tools(self) -> list[str]:
        """List available tools from the Longbridge MCP endpoint.

        Runs tool discovery if not yet performed.
        Returns the list of discovered tool names.
        """
        if self._discovery_done:
            return sorted(self._policy._discovered_tools)  # type: ignore[attr-defined]

        self._ensure_session()

        try:
            result = self._call_mcp_raw("tools/list", {})
            tools = self._extract_tool_names(result)
            if tools:
                self._policy.update_from_discovery(tools)
                logger.info("Discovered %d Longbridge MCP tools: %s", len(tools), tools)
            else:
                logger.warning("Tool discovery returned no tools; using default mapping")
        except Exception as exc:
            logger.warning("Tool discovery failed: %s; using default tool mapping", exc)

        self._discovery_done = True
        return sorted(self._policy._discovered_tools)  # type: ignore[attr-defined]

    def _extract_tool_names(self, response: Any) -> list[str]:
        """Extract tool names from a tools/list response."""
        data = self._unwrap_content(response)
        if isinstance(data, dict):
            tools = data.get("tools", [])
            if isinstance(tools, list):
                return [t.get("name", "") for t in tools if isinstance(t, dict) and t.get("name")]
        return []

    # ── MCP transport ────────────────────────────────────────────

    def _ensure_session(self) -> None:
        """Validate that we have a usable MCP session / auth.

        Does NOT actually connect — that happens on first tool call.
        This validates that required configuration is present.
        """
        if not self._oauth_token:
            self._session_error = (
                "Longbridge MCP OAuth token not configured. "
                "Set LONGBRIDGE_MCP_OAUTH_TOKEN environment variable. "
                "See https://open.longbridge.com/docs/mcp for OAuth setup."
            )
            raise RuntimeError(self._session_error)

    def _call_mcp_raw(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a Longbridge MCP tool via Streamable HTTP transport.

        This is the raw MCP call — policy enforcement happens at a higher level.
        """
        self._ensure_session()

        try:
            import asyncio
            from mcp import ClientSession

            # Try Streamable HTTP transport first (preferred for Longbridge official MCP)
            async def _call() -> Any:
                try:
                    # Attempt Streamable HTTP transport (MCP SDK >= 1.0)
                    from mcp.client.streamable_http import streamablehttp_client
                    async with streamablehttp_client(
                        url=self._mcp_url,
                        headers={"Authorization": f"Bearer {self._oauth_token}"},
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool(tool_name, arguments=arguments)
                            return result
                except (ImportError, AttributeError):
                    # Fall back to SSE transport
                    from mcp.client.sse import sse_client
                    async with sse_client(
                        url=self._mcp_url,
                        headers={"Authorization": f"Bearer {self._oauth_token}"},
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool(tool_name, arguments=arguments)
                            return result

            return asyncio.run(_call())

        except ImportError:
            raise RuntimeError(
                "MCP SDK not installed. Install with: pip install mcp>=1.0.0"
            )
        except Exception as exc:
            logger.error("MCP tool '%s' failed: %s", tool_name, exc)
            raise

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
        # Resolve internal op to actual tool name
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
        # Check if OAuth is configured
        if not self._oauth_token:
            return {
                "ok": False,
                "provider": "longbridge_mcp",
                "status": "not_configured",
                "error": "LONGBRIDGE_MCP_OAUTH_TOKEN not set",
            }

        if self._session_error and not self._connected:
            return {
                "ok": False,
                "provider": "longbridge_mcp",
                "status": "session_error",
                "error": self._session_error,
            }

        try:
            self._call_mcp_raw("trading_session", {"market": "US"})
            self._connected = True
            return {
                "ok": True,
                "provider": "longbridge_mcp",
                "status": "connected",
                "endpoint": self._mcp_url,
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider": "longbridge_mcp",
                "status": "connection_failed",
                "error": str(exc)[:300],
                "endpoint": self._mcp_url,
            }

    # ── MarketDataClient implementation ──────────────────────────

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        results: list[Quote] = []
        for symbol in symbols:
            try:
                raw = self._call_mcp_tool("quote", {"symbols": [symbol]})
                results.extend(self._parse_quotes(raw, symbol))
            except PermissionError:
                raise
            except Exception as exc:
                logger.error("Quote fetch failed for %s: %s", symbol, exc)
        return results

    def get_candles(self, symbols: list[str], period: str = "day", count: int = 20) -> list[Candle]:
        results: list[Candle] = []
        for symbol in symbols:
            try:
                raw = self._call_mcp_tool("candles", {
                    "symbol": symbol,
                    "period": period,
                    "count": count,
                })
                results.extend(self._parse_candles(raw, symbol))
            except PermissionError:
                raise
            except Exception as exc:
                logger.error("Candles fetch failed for %s: %s", symbol, exc)
        return results

    def get_intraday(self, symbols: list[str]) -> list[IntradayPoint]:
        results: list[IntradayPoint] = []
        for symbol in symbols:
            try:
                raw = self._call_mcp_tool("intraday", {"symbol": symbol})
                results.extend(self._parse_intraday(raw, symbol))
            except PermissionError:
                raise
            except Exception as exc:
                logger.error("Intraday fetch failed for %s: %s", symbol, exc)
        return results

    def get_market_status(self, markets: list[str]) -> list[MarketStatusInfo]:
        results: list[MarketStatusInfo] = []
        for market in markets:
            try:
                raw = self._call_mcp_tool("market_status", {"market": market})
                results.append(self._parse_market_status(raw, market))
            except PermissionError:
                raise
            except Exception as exc:
                logger.error("Market status fetch failed for %s: %s", market, exc)
        return results

    def get_fundamentals(self, symbols: list[str]) -> list[FundamentalData]:
        results: list[FundamentalData] = []
        for symbol in symbols:
            try:
                raw = self._call_mcp_tool("fundamentals", {"symbols": [symbol]})
                results.extend(self._parse_fundamentals(raw, symbol))
            except PermissionError:
                raise
            except Exception as exc:
                logger.error("Fundamental fetch failed for %s: %s", symbol, exc)
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
