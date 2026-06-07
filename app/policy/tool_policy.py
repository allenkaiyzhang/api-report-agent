"""Longbridge MCP tool policy — default-deny for unknown tools, block trading/account-read.

Enforces:
1. Trading/write tools are ALWAYS blocked.
2. Account-read tools are disabled by default (require explicit config).
3. Unknown tools are denied by default.
4. Only discovered and explicitly allowed read-only market tools pass.
5. Production mode requires tool discovery — no unverified hardcoded mappings.
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
# These are populated via tool discovery at runtime.
# Hardcoded names below are for bootstrapping only.

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
    "alert_disable",
    "alert_enable",
    "create_watchlist_group",
    "delete_watchlist_group",
    "update_watchlist_group",
    "sharelist_add",
    "sharelist_create",
    "sharelist_delete",
    "sharelist_remove",
    "sharelist_sort",
    "topic_create",
    "topic_create_reply",
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

_APPROVED_MARKET_TOOLS = frozenset(
    {"quote", "candlesticks", "intraday", "market_status", "trading_session"}
)


class LongbridgeToolPolicy:
    """Enforces tool allow/block/deny policy for Longbridge MCP tools.

    Rules (in order):
    1. Trading tools → always blocked
    2. Account-read tools → blocked unless account_read_enabled=True
    3. Discovered+allowed market tools → allowed
    4. Unknown tools → blocked (default-deny)
    """

    def __init__(self, account_read_enabled: bool = False) -> None:
        self._account_read_enabled = account_read_enabled
        self._tool_map: dict[str, str] = {}
        self._discovered_tools: set[str] = set()
        self._allowed_market_tools: set[str] = set()

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
        return frozenset(self._allowed_market_tools)

    def get_discovered_tools(self) -> set[str]:
        """Return the set of tools discovered at runtime."""
        return frozenset(self._discovered_tools)

    def update_from_discovery(self, discovered_tool_names: list[str]) -> None:
        """Update tool map based on MCP tool discovery results.

        This maps internal operation names to the actual tool names
        discovered from the Longbridge MCP endpoint.

        Compatibility aliases and unknown tools remain denied even if discovered.
        """
        discovered = set(discovered_tool_names)
        self._discovered_tools = discovered
        mappings = {
            "quote": "quote",
            "candles": "candlesticks",
            "intraday": "intraday",
            # Session timestamps are required by scheduler/report validation.
            "market_status": "trading_session",
        }
        self._tool_map = {
            operation: tool
            for operation, tool in mappings.items()
            if tool in discovered
        }
        self._allowed_market_tools = discovered & _APPROVED_MARKET_TOOLS

    def get_mapped_tool(self, internal_name: str) -> str | None:
        """Resolve an internal operation name to the actual MCP tool name.

        Returns None if no mapping exists (tool not discovered/hardcoded).
        """
        return self._tool_map.get(internal_name)

    def has_mapping(self, internal_name: str) -> bool:
        """Check whether an internal operation has a mapped tool."""
        return internal_name in self._tool_map

    def check_tool(self, tool_name: str) -> PolicyResult:
        """Evaluate whether a tool is allowed, and why.

        Checks in order: trading (blocked) → account-read → allowed market → unknown (denied).
        """
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

        if tool_name in self._allowed_market_tools:
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
            "allowed_market_tools": sorted(self._allowed_market_tools),
            "tool_map": dict(self._tool_map),
            "discovered_tools": sorted(self._discovered_tools),
            "default_deny_unknown": True,
        }
