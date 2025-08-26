#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hello MCP server implemented with FastMCP (stdio transport by default).

Ref: Microsoft MCP for Beginners (LLM client solution server)
https://raw.githubusercontent.com/microsoft/mcp-for-beginners/refs/heads/main/03-GettingStarted/03-llm-client/solution/python/server.py

This server exposes:
- Tool `hello(message: str) -> str`: returns a greeting with current local date-time
- Resource `greeting://{name}`: returns a personalized greeting (useful for demos)

FastMCP auto-generates tool schemas from type hints + docstrings, so keep them clear.
"""
from __future__ import annotations

from datetime import datetime
from mcp.server.fastmcp import FastMCP

# Create an MCP server
mcp = FastMCP("Hello Server")


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool()
def hello(message: str) -> str:
    """Return a greeting containing the provided message and the current local date-time."""
    return f"hello: {message} @ {_now_iso()}"


# Optional example resource (mirrors the upstream example style)
@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting as a simple dynamic resource."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    # FastMCP's run() is a blocking, synchronous call for the selected transport (default: stdio).
    mcp.run()