#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM-driven MCP **client** that connects to the local hello_server.py via stdio,
lists tools, asks an LLM which tool(s) to call, then invokes them.

Adapted from:
https://raw.githubusercontent.com/microsoft/mcp-for-beginners/refs/heads/main/03-GettingStarted/03-llm-client/solution/python/client.py
"""

import os
import sys
import json
import asyncio
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# --- LLM (Azure AI Inference) ---
# The reference sample uses Azure AI Inference with the endpoint
#   https://models.inference.ai.azure.com
# and model "gpt-4o". You must export GITHUB_TOKEN (classic PAT) as your key.
try:
    from azure.ai.inference import ChatCompletionsClient
    from azure.core.credentials import AzureKeyCredential
except Exception as _e:  # pragma: no cover
    ChatCompletionsClient = None  # type: ignore
    AzureKeyCredential = None  # type: ignore


def _hello_server_params() -> StdioServerParameters:
    """Create server parameters to launch hello_server.py over stdio."""
    here = Path(__file__).resolve().parent
    server_py = here / "hello_server.py"
    if not server_py.exists():
        raise FileNotFoundError(f"hello_server.py not found at {server_py}")

    # Launch the Python server via stdio
    return StdioServerParameters(
        command=sys.executable,
        args=[str(server_py), "--verbose"],
        env=None,
    )


def call_llm(prompt: str, functions: list[dict]) -> list[dict]:
    """Call the LLM with a prompt + tool schemas, return tool call plan.

    Returns a list like: [{"name": <tool_name>, "args": {...}}, ...]
    """
    if ChatCompletionsClient is None or AzureKeyCredential is None:
        raise RuntimeError(
            "azure.ai.inference is not installed. Install 'azure-ai-inference' to run this sample."
        )

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Export a GitHub token to use Azure AI Inference."
        )

    endpoint = "https://models.inference.ai.azure.com"
    model_name = "gpt-4o"

    client = ChatCompletionsClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(token),
    )

    # NOTE: We pass the tool/function schema using the `tools` parameter.
    print("[llm] calling model to plan tool calls…", file=sys.stderr)
    response = client.complete(
        messages=[
            {"role": "system", "content": "You are a helpful assistant that decides which tool to call."},
            {"role": "user", "content": prompt},
        ],
        model=model_name,
        tools=functions,
        temperature=0,
        max_tokens=512,
        top_p=1,
    )

    message = response.choices[0].message
    planned: list[dict] = []
    if getattr(message, "tool_calls", None):
        for tool_call in message.tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except Exception:
                args = {}
            planned.append({"name": name, "args": args})
            print(f"[llm] planned tool: {name} args={args}", file=sys.stderr)
    else:
        print("[llm] no tool calls suggested", file=sys.stderr)
    return planned


def convert_to_llm_tool(tool) -> dict:
    """Convert an MCP Tool (from list_tools) into an OpenAI/Azure 'tool' schema."""
    # Be defensive in case the server omits input schema
    props = {}
    try:
        props = tool.inputSchema["properties"]  # type: ignore[index]
    except Exception:
        props = {}

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": getattr(tool, "description", ""),
            "type": "function",
            "parameters": {
                "type": "object",
                "properties": props,
            },
        },
    }


async def run() -> None:
    # Connect to our hello_server.py via stdio
    server_params = _hello_server_params()

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the connection
            await session.initialize()

            # (Optional) List available resources
            resources = await session.list_resources()
            for r in resources:
                print(f"[mcp] resource: {r}")

            # List available tools and convert to LLM tool schema
            tools = await session.list_tools()
            functions: list[dict] = []
            for t in tools.tools:
                print(f"[mcp] tool: {t.name}")
                functions.append(convert_to_llm_tool(t))

            # Example prompt that should trigger the hello tool
            # Adjust wording as your hello_server expects (e.g., expects an arg `message`).
            prompt = "Say hello to 'Alvin' using the hello tool with message='Hi from LLM client'."

            plan = call_llm(prompt, functions)

            # Execute planned tool calls
            for step in plan:
                name = step.get("name")
                args = step.get("args", {})
                result = await session.call_tool(name, arguments=args)
                print("[mcp] tool result:", result.content)


if __name__ == "__main__":
    asyncio.run(run())