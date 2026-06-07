# Takeover Verification Note

Production entrypoint: `scripts/market_report_agent.py`.

The Longbridge MCP adapter uses an external authorized-session header. This is
not a complete OAuth 2.1 implementation. Real Longbridge auth/session and response
schemas have not been tested from this repository.

Release verification commands and their exact results must be recorded from the
current worktree before merge. The real-provider health check is expected to fail
non-zero when no authorized session is configured.
