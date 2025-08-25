#!/usr/bin/env python3
"""
MCP Git client for the official Git MCP server.

This client launches the `mcp-server-git` (Python reference server) via stdio
and exposes a small CLI to call its tools directly.

Supported launch strategies (auto-detected in order):
  1) uvx mcp-server-git
  2) uv run mcp-server-git
  3) python -m mcp_server_git
  4) mcp-server-git (on PATH)

Examples
--------
# List tools
python -m mcp_tools.git.git_client --repo /path/to/repo tools

# Status
python -m mcp_tools.git.git_client --repo . status

# Diff vs branch
python -m mcp_tools.git.git_client --repo . diff --target main

# Stage and commit
python -m mcp_tools.git.git_client --repo . add --files mcp_tools/git/git_client.py
python -m mcp_tools.git.git_client --repo . commit --message "feat(git): add MCP git client"

# Show log
python -m mcp_tools.git.git_client --repo . log --max-count 5

Notes
-----
* Most tools require `repo_path` which we default to --repo if not provided.
* Tool names & schemas follow the official server (git_status, git_diff_unstaged,
  git_diff_staged, git_diff, git_commit, git_add, git_reset, git_log,
  git_create_branch, git_checkout, git_show, git_init, git_branch).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ----------------------------
# Helpers
# ----------------------------

def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _build_server_params(repo_path: str, cmd: Optional[str], extra_args: Optional[Sequence[str]]) -> Tuple[str, List[str]]:
    """Build (command, args) to launch the Git MCP server.

    Priority:
      1) explicit --server-cmd
      2) uvx
      3) uv run
      4) python -m mcp_server_git
      5) mcp-server-git
    """
    base_args = list(extra_args or [])

    if cmd:  # user override
        command = cmd
        args: List[str] = [*base_args]
        # If user gave no executable tool name, assume they included it in args
        if command in {"uv", "uvx"} and (not args or args[0] not in {"mcp-server-git", "run"}):
            # Default to running the server with uv/uvx
            if command == "uvx":
                args = ["mcp-server-git", *args]
            else:
                args = ["run", "mcp-server-git", *args]
        # Ensure repository argument is present for this server
        if "--repository" not in args and "-r" not in args:
            args += ["--repository", os.path.abspath(repo_path)]
        return command, args

    # Auto-detect
    if _which("uvx"):
        return "uvx", ["mcp-server-git", "--repository", os.path.abspath(repo_path)]
    if _which("uv"):
        return "uv", ["run", "mcp-server-git", "--repository", os.path.abspath(repo_path)]
    if _which("python"):
        return "python", ["-m", "mcp_server_git", "--repository", os.path.abspath(repo_path)]
    # fallback
    return "mcp-server-git", ["--repository", os.path.abspath(repo_path)]


async def _connect(repo_path: str, server_cmd: Optional[str], server_args: Optional[Sequence[str]]):
    command, args = _build_server_params(repo_path, server_cmd, server_args)

    server_params = StdioServerParameters(command=command, args=list(args), env=None)
    stack = AsyncExitStack()
    transport = await stack.enter_async_context(stdio_client(server_params))
    stdio, write = transport

    session = await stack.enter_async_context(ClientSession(stdio, write))
    await session.initialize()

    return stack, session


# ----------------------------
# Client wrapper
# ----------------------------

class GitMCPClient:
    def __init__(self, repo_path: str, server_cmd: Optional[str] = None, server_args: Optional[Sequence[str]] = None):
        self.repo_path = os.path.abspath(repo_path)
        self.server_cmd = server_cmd
        self.server_args = list(server_args) if server_args else None
        self._stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None

    async def __aenter__(self) -> "GitMCPClient":
        self._stack, self.session = await _connect(self.repo_path, self.server_cmd, self.server_args)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._stack:
            await self._stack.aclose()

    async def list_tools(self) -> List[str]:
        assert self.session
        resp = await self.session.list_tools()
        return [t.name for t in resp.tools]

    async def call(self, tool: str, args: Optional[Dict[str, Any]] = None) -> Any:
        assert self.session
        args = dict(args or {})
        # Default repo_path for convenience
        args.setdefault("repo_path", self.repo_path)
        result = await self.session.call_tool(tool, args)
        # result.content is a list of content parts (usually a single text/json)
        return result.model_dump()

    # --------------
    # Convenience wrappers for common tools (names follow the server)
    # --------------
    async def status(self):
        return await self.call("git_status")

    async def diff_unstaged(self, context_lines: int = 3):
        return await self.call("git_diff_unstaged", {"context_lines": context_lines})

    async def diff_staged(self, context_lines: int = 3):
        return await self.call("git_diff_staged", {"context_lines": context_lines})

    async def diff(self, target: str, context_lines: int = 3):
        return await self.call("git_diff", {"target": target, "context_lines": context_lines})

    async def add(self, files: Sequence[str]):
        return await self.call("git_add", {"files": list(files)})

    async def reset(self):
        return await self.call("git_reset")

    async def commit(self, message: str):
        return await self.call("git_commit", {"message": message})

    async def log(self, max_count: int = 10):
        return await self.call("git_log", {"max_count": max_count})

    async def create_branch(self, branch_name: str, start_point: Optional[str] = None):
        args: Dict[str, Any] = {"branch_name": branch_name}
        if start_point:
            args["start_point"] = start_point
        return await self.call("git_create_branch", args)

    async def checkout(self, branch_name: str):
        return await self.call("git_checkout", {"branch_name": branch_name})

    async def show(self, revision: str):
        return await self.call("git_show", {"revision": revision})

    async def init(self):
        # note: server expects repo_path to be the directory to init.
        return await self.call("git_init")

    async def branch(self, branch_type: str = "local", contains: Optional[str] = None, not_contains: Optional[str] = None):
        args: Dict[str, Any] = {"branch_type": branch_type}
        if contains:
            args["contains"] = contains
        if not_contains:
            args["not_contains"] = not_contains
        return await self.call("git_branch", args)


# ----------------------------
# CLI
# ----------------------------

def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MCP Git client (stdio) for mcp-server-git")
    p.add_argument("--repo", required=True, help="Path to the Git repository (used as repo_path)")
    p.add_argument("--server-cmd", default=None, help="Override server command (e.g. 'uvx', 'uv', 'python', or full path)")
    p.add_argument("--server-args", nargs=argparse.REMAINDER, default=None, help="Extra args after -- to pass to the server (e.g. -- --repository /path)")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tools", help="List available tools")
    sub.add_parser("status", help="git status")

    sp = sub.add_parser("diff-unstaged", help="git diff (unstaged)")
    sp.add_argument("--context-lines", type=int, default=3)

    sp = sub.add_parser("diff-staged", help="git diff (staged)")
    sp.add_argument("--context-lines", type=int, default=3)

    sp = sub.add_parser("diff", help="git diff vs target")
    sp.add_argument("--target", required=True)
    sp.add_argument("--context-lines", type=int, default=3)

    sp = sub.add_parser("add", help="git add <files>")
    sp.add_argument("--files", nargs="+", required=True)

    sub.add_parser("reset", help="git reset (unstage all)")

    sp = sub.add_parser("commit", help="git commit -m <message>")
    sp.add_argument("--message", required=True)

    sp = sub.add_parser("log", help="git log")
    sp.add_argument("--max-count", type=int, default=10)

    sp = sub.add_parser("create-branch", help="git branch <name> [<start_point>]")
    sp.add_argument("--name", required=True)
    sp.add_argument("--start-point", default=None)

    sp = sub.add_parser("checkout", help="git checkout <branch>")
    sp.add_argument("--name", required=True)

    sp = sub.add_parser("show", help="git show <revision>")
    sp.add_argument("--revision", required=True)

    sp = sub.add_parser("init", help="git init (in --repo)")

    sp = sub.add_parser("branch", help="git branch list")
    sp.add_argument("--type", dest="branch_type", choices=["local", "remote", "all"], default="local")
    sp.add_argument("--contains", default=None)
    sp.add_argument("--not-contains", default=None)

    # generic: call any tool by name with JSON args
    sp = sub.add_parser("call", help="Call an arbitrary tool with JSON args")
    sp.add_argument("--tool", required=True, help="Tool name (e.g. git_status)")
    sp.add_argument("--json", default="{}", help="JSON object of args (repo_path defaulted)")

    return p


async def _run_cli(args: argparse.Namespace) -> int:
    server_args = None
    if args.server_args:
        # If user passes things after --, argparse gives them here including the leading '--' sometimes
        server_args = list(args.server_args)
        # Strip a leading '--' placeholder if present
        if server_args and server_args[0] == "--":
            server_args = server_args[1:]

    async with GitMCPClient(args.repo, server_cmd=args.server_cmd, server_args=server_args) as client:
        if args.cmd == "tools":
            tools = await client.list_tools()
            print(json.dumps({"tools": tools}, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "status":
            out = await client.status()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "diff-unstaged":
            out = await client.diff_unstaged(args.context_lines)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "diff-staged":
            out = await client.diff_staged(args.context_lines)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "diff":
            out = await client.diff(args.target, args.context_lines)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "add":
            out = await client.add(args.files)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "reset":
            out = await client.reset()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "commit":
            out = await client.commit(args.message)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "log":
            out = await client.log(args.max_count)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "create-branch":
            out = await client.create_branch(args.name, args.start_point)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "checkout":
            out = await client.checkout(args.name)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "show":
            out = await client.show(args.revision)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "init":
            out = await client.init()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "branch":
            out = await client.branch(args.branch_type, args.contains, args.not_contains)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        elif args.cmd == "call":
            try:
                payload = json.loads(args.json)
                if not isinstance(payload, dict):
                    raise ValueError("--json must be a JSON object")
            except Exception as e:
                print(f"Invalid JSON for --json: {e}", file=sys.stderr)
                return 2
            out = await client.call(args.tool, payload)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _make_parser()
    ns = parser.parse_args(argv)
    try:
        return asyncio.run(_run_cli(ns))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
