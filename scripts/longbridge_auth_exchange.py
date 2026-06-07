#!/usr/bin/env python
"""Longbridge MCP Authorization Bootstrap Script.

Exchanges a 10-minute single-use auth code for an access token via the
Longbridge MCP authentication endpoint, and configures the main MCP service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger("longbridge_auth")


@asynccontextmanager
async def open_auth_session(url: str) -> AsyncIterator[Any]:
    """Open and initialize an MCP session over Streamable HTTP or SSE fallback."""
    try:
        from mcp import ClientSession
    except ImportError:
        raise RuntimeError(
            "MCP SDK not installed. Please install with: pip install mcp>=1.0.0"
        )

    try:
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        try:
            from mcp.client.streamable_http import streamablehttp_client as streamable_http_client
        except ImportError:
            streamable_http_client = None

    if streamable_http_client is None:
        from mcp.client.sse import sse_client
        transport_context = sse_client(url=url, headers={})
    else:
        transport_context = streamable_http_client(url=url, headers={})

    async with transport_context as transport:
        # Extract read/write streams
        if not isinstance(transport, tuple):
            raise RuntimeError(
                f"Unsupported MCP transport shape: expected a tuple, got {type(transport).__name__}"
            )
        if len(transport) not in (2, 3):
            raise RuntimeError(
                f"Unsupported MCP transport tuple length: expected 2 or 3 items, got {len(transport)}"
            )
        read, write = transport[0], transport[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def unwrap_token(raw: Any) -> str:
    """Safely extract token/access_token from tool response."""
    content = None
    if isinstance(raw, dict):
        content = raw.get("content")
    elif hasattr(raw, "content"):
        content = getattr(raw, "content")

    token = None
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
            elif hasattr(block, "type") and getattr(block, "type") == "text":
                texts.append(getattr(block, "text", ""))
        joined = "".join(texts)
        if joined.strip().startswith("{") or joined.strip().startswith("["):
            try:
                data = json.loads(joined)
                if isinstance(data, dict):
                    token = data.get("token") or data.get("access_token")
            except json.JSONDecodeError:
                pass
        if not token:
            token = joined.strip()

    # If not found yet, check dictionary keys
    if not token:
        if isinstance(raw, dict):
            token = raw.get("token") or raw.get("access_token")
            if not token and "content" in raw:
                c = raw["content"]
                if isinstance(c, dict):
                    token = c.get("token") or c.get("access_token")
        elif hasattr(raw, "token"):
            token = getattr(raw, "token")
        elif hasattr(raw, "access_token"):
            token = getattr(raw, "access_token")

    if not token:
        raise ValueError(f"Could not extract token from response: {raw}")

    return token


async def perform_exchange(auth_url: str, auth_code: str) -> str:
    """Connect to auth endpoint and call authenticate."""
    async with open_auth_session(auth_url) as session:
        # Call authenticate with auth_code
        response = await session.call_tool(
            "authenticate", arguments={"auth_code": auth_code}
        )
        return unwrap_token(response)


def update_env(env_path: Path, mcp_url: str, auth_header: str) -> None:
    """Write or update .env with the new settings."""
    lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        # Initialize from .env.example if it exists
        example_path = env_path.parent / ".env.example"
        if example_path.exists():
            try:
                with open(example_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception as e:
                logger.warning("Could not read .env.example: %s", e)

    mcp_url_found = False
    auth_header_found = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("LONGBRIDGE_MCP_URL="):
            new_lines.append(f"LONGBRIDGE_MCP_URL={mcp_url}\n")
            mcp_url_found = True
        elif stripped.startswith("LONGBRIDGE_MCP_AUTH_HEADER="):
            new_lines.append(f"LONGBRIDGE_MCP_AUTH_HEADER={auth_header}\n")
            auth_header_found = True
        else:
            new_lines.append(line)

    if not mcp_url_found:
        new_lines.append(f"\nLONGBRIDGE_MCP_URL={mcp_url}\n")
    if not auth_header_found:
        new_lines.append(f"LONGBRIDGE_MCP_AUTH_HEADER={auth_header}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exchange Longbridge MCP single-use auth code for persistent access token."
    )
    parser.add_argument(
        "--auth-code",
        required=True,
        help="The single-use auth code provided by Longbridge (valid for 10 minutes)."
    )
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Update .env with LONGBRIDGE_MCP_URL and LONGBRIDGE_MCP_AUTH_HEADER."
    )
    parser.add_argument(
        "--auth-url",
        default="https://mcp.longbridge.cn/agent",
        help="The bootstrap auth endpoint (default: https://mcp.longbridge.cn/agent)."
    )
    parser.add_argument(
        "--mcp-url",
        default="https://mcp.longbridge.cn",
        help="The main MCP service endpoint (default: https://mcp.longbridge.cn)."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging."
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    # Validate auth code
    if not args.auth_code.strip():
        print("ERROR: --auth-code cannot be empty.", file=sys.stderr)
        sys.exit(1)

    auth_url = args.auth_url.strip()
    mcp_url = args.mcp_url.strip()
    auth_code = args.auth_code.strip()

    # Never log the full token or the auth code by default.
    masked_code = f"{auth_code[:3]}...{auth_code[-3:]}" if len(auth_code) > 6 else "..."
    logger.info("Connecting to auth endpoint: %s to exchange auth code: %s", auth_url, masked_code)

    try:
        token = await perform_exchange(auth_url, auth_code)
    except Exception as exc:
        logger.error("Authentication exchange failed: %s", exc)
        sys.exit(1)

    auth_header = f"Bearer {token}"
    masked_token = f"Bearer {token[:4]}...{token[-4:]}" if len(token) > 8 else "Bearer ..."
    logger.info("Successfully received access token: %s", masked_token)

    # Safe instructions
    instructions_masked = f"""
======================================================================
Longbridge MCP Authorization Exchange Successful!
======================================================================

Auth Endpoint: {auth_url}
Main Endpoint: {mcp_url}
Access Token:  {masked_token}

The access token is now ready and configured in your .env file:

LONGBRIDGE_MCP_URL={mcp_url}
LONGBRIDGE_MCP_AUTH_HEADER={masked_token}

Note: The auth code you provided has been consumed and is now invalid.
If your token expires in the future, you will need to obtain a new auth
code and repeat this process.
======================================================================
"""

    instructions_full = f"""
======================================================================
Longbridge MCP Authorization Exchange Successful!
======================================================================

Auth Endpoint: {auth_url}
Main Endpoint: {mcp_url}
Access Token:  {masked_token}

The access token is now ready. Configure your environment by setting:

LONGBRIDGE_MCP_URL={mcp_url}
LONGBRIDGE_MCP_AUTH_HEADER={auth_header}

Note: The auth code you provided has been consumed and is now invalid.
If your token expires in the future, you will need to obtain a new auth
code and repeat this process.
======================================================================
"""

    if args.write_env:
        # Resolve project root and .env path
        project_root = Path(__file__).resolve().parent.parent
        env_path = project_root / ".env"
        try:
            update_env(env_path, mcp_url, auth_header)
            logger.info("Successfully updated env file: %s", env_path)
            print(f"Success! Updated your .env file at {env_path.resolve()}")
            print(instructions_masked)
        except Exception as exc:
            logger.error("Failed to write to .env file: %s", exc)
            sys.exit(1)
    else:
        # If --write-env is not provided, print instructions with full token to stdout
        # so the user can copy/paste it safely, but do not log it through any logger.
        print(instructions_full)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
