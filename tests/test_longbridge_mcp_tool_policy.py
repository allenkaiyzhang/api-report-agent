"""Tests for Longbridge MCP tool policy — using official Longbridge MCP tool names.

Tests:
  - All trading tools are permanently blocked
  - All account-read tools are disabled by default
  - Allowed market tools pass the policy
  - Unknown tools are default-deny
  - Account-read tools can be enabled via config
  - PermissionError is raised for blocked tools
"""

from __future__ import annotations

import unittest

from app.policy.tool_policy import (
    LongbridgeToolPolicy,
    PolicyResult,
    ToolCategory,
)


class TestLongbridgeToolPolicy(unittest.TestCase):
    """Verify tool blocking policy for all tool categories."""

    def setUp(self):
        self.policy = LongbridgeToolPolicy(account_read_enabled=False)

    # ── Trading tools ──────────────────────────────────────────

    def test_trading_tools_list(self):
        """All expected trading tools are in the blocked list."""
        expected = {
            "submit_order", "replace_order", "cancel_order", "withdrawals",
            "dca_create", "dca_update", "dca_stop", "dca_pause", "dca_resume",
            "alert_add", "alert_delete",
            "create_watchlist_group", "delete_watchlist_group",
            "sharelist_add", "sharelist_create", "sharelist_delete", "sharelist_remove",
        }
        self.assertEqual(self.policy.trading_tools, frozenset(expected))

    def test_trading_tools_blocked(self):
        for tool in self.policy.trading_tools:
            result = self.policy.check_tool(tool)
            self.assertFalse(result.allowed, f"Trading tool {tool} should be blocked")
            self.assertEqual(result.category, ToolCategory.TRADING)

    def test_trading_tools_raise_permission_error(self):
        for tool in self.policy.trading_tools:
            with self.assertRaises(PermissionError, msg=f"Should raise for: {tool}"):
                self.policy.assert_allowed(tool)

    # ── Account-read tools ─────────────────────────────────────

    def test_account_read_tools_list(self):
        """All expected account-read tools are in the list."""
        expected = {
            "account_balance", "stock_positions", "today_orders",
            "history_orders", "today_executions", "history_executions",
            "statement_list",
        }
        self.assertEqual(self.policy.account_read_tools, frozenset(expected))

    def test_account_read_disabled_by_default(self):
        for tool in self.policy.account_read_tools:
            result = self.policy.check_tool(tool)
            self.assertFalse(
                result.allowed,
                f"Account-read tool {tool} should be blocked by default",
            )
            self.assertEqual(result.category, ToolCategory.ACCOUNT_READ)

    def test_account_read_raises_permission_error(self):
        for tool in self.policy.account_read_tools:
            with self.assertRaises(PermissionError, msg=f"Should raise for: {tool}"):
                self.policy.assert_allowed(tool)

    def test_account_read_allowed_when_enabled(self):
        policy = LongbridgeToolPolicy(account_read_enabled=True)
        for tool in policy.account_read_tools:
            result = policy.check_tool(tool)
            self.assertTrue(result.allowed, f"Should allow: {tool}")
            self.assertEqual(result.category, ToolCategory.ACCOUNT_READ)

    def test_trading_blocked_even_when_account_read_enabled(self):
        policy = LongbridgeToolPolicy(account_read_enabled=True)
        for tool in policy.trading_tools:
            result = policy.check_tool(tool)
            self.assertFalse(result.allowed, f"Trading {tool} still blocked")
            with self.assertRaises(PermissionError):
                policy.assert_allowed(tool)

    # ── Allowed market tools ────────────────────────────────────

    def test_allowed_market_tools_list(self):
        """Verify the allowed market tools list."""
        allowed = self.policy.allowed_market_tools
        self.assertIn("candlesticks", allowed)
        self.assertIn("trading_session", allowed)

    def test_allowed_market_tools_pass(self):
        for tool in self.policy.allowed_market_tools:
            result = self.policy.check_tool(tool)
            self.assertTrue(result.allowed, f"Market tool {tool} should be allowed")
            self.assertEqual(result.category, ToolCategory.ALLOWED_MARKET)

    # ── Default-deny unknown tools ──────────────────────────────

    def test_unknown_tool_blocked(self):
        """Unknown/invented tools should be blocked by default."""
        unknown = [
            "some_random_tool",
            "get_secret_data",
            "invented_tool_name",
            "admin_delete_everything",
        ]
        for tool in unknown:
            result = self.policy.check_tool(tool)
            self.assertFalse(result.allowed, f"Unknown tool {tool} should be blocked")
            self.assertEqual(result.category, ToolCategory.UNKNOWN)

    def test_unknown_tool_raises_permission_error(self):
        with self.assertRaises(PermissionError):
            self.policy.assert_allowed("unknown_tool_xyz")

    # ── Tool mapping ────────────────────────────────────────────

    def test_default_tool_map(self):
        """Default tool map has expected internal→official name mappings."""
        self.assertEqual(self.policy.get_mapped_tool("candles"), "candlesticks")
        self.assertEqual(self.policy.get_mapped_tool("market_status"), "trading_session")
        self.assertEqual(self.policy.get_mapped_tool("fundamentals"), "get_stock_info")

    def test_update_from_discovery(self):
        """Tool discovery updates the mapping."""
        self.policy.update_from_discovery([
            "candlesticks",
            "trading_session",
            "get_stock_quote",
            "get_intraday",
        ])
        self.assertEqual(self.policy.get_mapped_tool("candles"), "candlesticks")
        self.assertEqual(self.policy.get_mapped_tool("market_status"), "trading_session")
        self.assertEqual(self.policy.get_mapped_tool("quote"), "get_stock_quote")
        self.assertEqual(self.policy.get_mapped_tool("intraday"), "get_intraday")

    def test_unknown_internal_op_returns_none(self):
        """An internal op with no mapping returns None."""
        self.assertIsNone(self.policy.get_mapped_tool("nonexistent_op"))

    # ── Policy metadata ─────────────────────────────────────────

    def test_to_dict(self):
        d = self.policy.to_dict()
        self.assertFalse(d["account_read_enabled"])
        self.assertTrue(d["default_deny_unknown"])
        self.assertIn("submit_order", d["trading_tools_blocked"])
        self.assertIn("stock_positions", d["account_read_tools_disabled"])

    def test_no_overlap_between_lists(self):
        """No tool should appear in both allowed and blocked lists."""
        allowed = set(self.policy.allowed_market_tools)
        trading = set(self.policy.trading_tools)
        account = set(self.policy.account_read_tools)

        self.assertEqual(allowed & trading, set(), "Allowed ∩ Trading should be empty")
        self.assertEqual(allowed & account, set(), "Allowed ∩ Account-read should be empty")
        self.assertEqual(trading & account, set(), "Trading ∩ Account-read should be empty")


if __name__ == "__main__":
    unittest.main()
