"""Longbridge MCP tool policy — default-deny for unknown tools, block trading/account-read.

Enforces:
1. Trading/write tools are ALWAYS blocked.
2. Account-read tools are disabled by default (require explicit config).
3. Unknown tools are denied by default.
4. Only explicitly allowed read-only market/fundamental tools pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolCategory(str, Enum):
    ALLOWED_MARKET = "allowed_market"
    ACCOUNT_READ = "account_read"
    TRADING = "trading"
    UNKNOWN = "unknown"


@dataclass
class PolicyResult:
    allowed: bool
    category: ToolCategory
    reason: str = ""
    requires_config: bool = False


# ── Official Longbridge MCP tool names ───────────────────────────
# These are the tool names discovered from the Longbridge official MCP.
# They are NOT invented — they map to what the Longbridge Remote MCP exposes.

# Trading/write tools — ALWAYS blocked
_TRADING_TOOLS: set[str] = {
    "submit_order",
    "replace_order",
    "cancel_order",
    "withdrawals",
    "dca_create",
    "dca_update",
    "dca_stop",
    "dca_pause",
    "dca_resume",
    "alert_add",
    "alert_delete",
    "create_watchlist_group",
    "delete_watchlist_group",
    "sharelist_add",
    "sharelist_create",
    "sharelist_delete",
    "sharelist_remove",
}

# Account-read tools — disabled by default
_ACCOUNT_READ_TOOLS: set[str] = {
    "account_balance",
    "stock_positions",
    "today_orders",
    "history_orders",
    "today_executions",
    "history_executions",
    "statement_list",
}

# Allowed read-only market/fundamental tools
# Using official Longbridge MCP tool names where known,
# with fallback aliases for discovery mapping
_ALLOWED_MARKET_TOOLS: set[str] = {
    # Official Longbridge MCP tool names
    "candlesticks",
    "trading_session",
    # Additional allowed read-only tools (verified via tool discovery)
    "get_stock_info",
    "get_calc_indexes",
    "get_watchlist",
    "get_option_chain",
    "get_option_snapshot",
    "get_option_underlying_info",
    "get_option_expiration_date",
    # Quote/intraday may be discovered under various names
    "get_stock_quote",
    "get_intraday",
}

# Map internal operations to official Longbridge MCP tool names
# This is populated/updated via tool discovery at runtime
_DEFAULT_TOOL_MAP: dict[str, str] = {
    "quote": "get_stock_quote",        # may be remapped after discovery
    "candles": "candlesticks",
    "market_status": "trading_session",
    "intraday": "get_intraday",
    "fundamentals": "get_stock_info",
}


class LongbridgeToolPolicy:
    """Enforces tool allow/block/deny policy for Longbridge MCP tools.

    Rules (in order):
    1. Trading tools → always blocked
    2. Account-read tools → blocked unless account_read_enabled=True
    3. Allowed market tools → allowed
    4. Unknown tools → blocked (default-deny)
    """

    def __init__(self, account_read_enabled: bool = False) -> None:
        self._account_read_enabled = account_read_enabled
        self._tool_map: dict[str, str] = dict(_DEFAULT_TOOL_MAP)
        self._discovered_tools: set[str] = set()

    @property
    def account_read_enabled(self) -> bool:
        return self._account_read_enabled

    @property
    def trading_tools(self) -> set[str]:
        return frozenset(_TRADING_TOOLS)

    @property
    def account_read_tools(self) -> set[str]:
        return frozenset(_ACCOUNT_READ_TOOLS)

    @property
    def allowed_market_tools(self) -> set[str]:
        return frozenset(_ALLOWED_MARKET_TOOLS)

    def update_from_discovery(self, discovered_tool_names: list[str]) -> None:
        """Update tool map based on MCP tool discovery results.

        This maps internal operation names to the actual tool names
        discovered from the Longbridge MCP endpoint.
        """
        discovered = set(discovered_tool_names)
        self._discovered_tools = discovered

        # Map internal ops to discovered official names
        remap: dict[str, str] = {}

        # candlesticks → candles
        if "candlesticks" in discovered:
            remap["candles"] = "candlesticks"

        # trading_session → market_status
        if "trading_session" in discovered:
            remap["market_status"] = "trading_session"

        # stock_positions → account positions (if enabled)
        if "stock_positions" in discovered:
            remap["positions"] = "stock_positions"

        # today_orders → orders (if enabled)
        if "today_orders" in discovered:
            remap["orders"] = "today_orders"

        # Quote tool detection
        for name in discovered:
            if "quote" in name.lower() and "stock" in name.lower():
                remap["quote"] = name
                break

        # Intraday detection
        for name in discovered:
            if "intraday" in name.lower():
                remap["intraday"] = name
                break

        # Stock info / fundamentals
        for name in discovered:
            if "stock_info" in name.lower() or "fundamental" in name.lower():
                remap["fundamentals"] = name
                break

        self._tool_map.update(remap)

    def get_mapped_tool(self, internal_name: str) -> str | None:
        """Resolve an internal operation name to the actual MCP tool name."""
        return self._tool_map.get(internal_name)

    def check_tool(self, tool_name: str) -> PolicyResult:
        """Evaluate whether a tool is allowed, and why."""
        if tool_name in _TRADING_TOOLS:
            return PolicyResult(
                allowed=False,
                category=ToolCategory.TRADING,
                reason=f"Trading tool '{tool_name}' is permanently blocked",
            )

        if tool_name in _ACCOUNT_READ_TOOLS:
            if self._account_read_enabled:
                return PolicyResult(
                    allowed=True,
                    category=ToolCategory.ACCOUNT_READ,
                    reason=f"Account-read tool '{tool_name}' explicitly enabled",
                    requires_config=True,
                )
            return PolicyResult(
                allowed=False,
                category=ToolCategory.ACCOUNT_READ,
                reason=f"Account-read tool '{tool_name}' is disabled (set ACCOUNT_READ_ENABLED=true to enable)",
            )

        if tool_name in _ALLOWED_MARKET_TOOLS:
            return PolicyResult(
                allowed=True,
                category=ToolCategory.ALLOWED_MARKET,
                reason=f"Market tool '{tool_name}' is allowed",
            )

        # Default-deny: unknown tools are blocked
        return PolicyResult(
            allowed=False,
            category=ToolCategory.UNKNOWN,
            reason=f"Unknown tool '{tool_name}' is blocked by default-deny policy",
        )

    def is_allowed(self, tool_name: str) -> bool:
        return self.check_tool(tool_name).allowed

    def assert_allowed(self, tool_name: str) -> None:
        result = self.check_tool(tool_name)
        if not result.allowed:
            raise PermissionError(result.reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_read_enabled": self._account_read_enabled,
            "trading_tools_blocked": sorted(_TRADING_TOOLS),
            "account_read_tools_disabled": (
                sorted(_ACCOUNT_READ_TOOLS) if not self._account_read_enabled else []
            ),
            "allowed_market_tools": sorted(_ALLOWED_MARKET_TOOLS),
            "tool_map": dict(self._tool_map),
            "discovered_tools": sorted(self._discovered_tools),
            "default_deny_unknown": True,
        }
