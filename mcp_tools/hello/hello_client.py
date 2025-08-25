#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hello MCP **server** that responds to hello_client.py.
- Implements stdio JSON-RPC protocol.
- Supports initialize, tools/list, tools/call.
"""

import sys
import json
import time
import subprocess
from typing import Any, Dict, Optional
from pathlib import Path

JSON = Dict[str, Any]


def write_json(obj: JSON) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def handle_initialize(req_id: Any, params: Optional[JSON]) -> None:
    write_json({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "serverInfo": {"name": "HelloServer", "version": "1.0.0"},
            "capabilities": {},
            "protocolVersion": "2025-06-18",
        },
    })


def handle_tools_list(req_id: Any) -> None:
    write_json({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "tools": [
                {"name": "hello", "description": "Say hello"},
            ],
            "nextCursor": None,
        },
    })


def handle_tools_call(req_id: Any, params: Optional[JSON]) -> None:
    name = params.get("name") if params else None
    arguments = params.get("arguments") if params else {}
    if name == "hello":
        message = arguments.get("message", "Hello!")
        write_json({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"response": f"Hello from server! You said: {message}"},
        })
    else:
        write_json({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": f"Unknown tool: {name}"},
        })


def handle_prompts_list(req_id: Any, params: Optional[JSON]) -> None:
    write_json({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "prompts": [],
            "nextCursor": None
        }
    })


def handle_resources_list(req_id: Any, params: Optional[JSON]) -> None:
    write_json({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "resources": [],
            "nextCursor": None
        }
    })


def handle_resources_templates_list(req_id: Any, params: Optional[JSON]) -> None:
    write_json({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "resourceTemplates": [],
            "nextCursor": None
        }
    })

def launch_server() -> subprocess.Popen:
    """Launch hello_server.py as a stdio JSON-RPC server and return the Popen."""
    here = Path(__file__).resolve().parent
    server_py = here / "hello_server.py"
    if not server_py.exists():
        print(f"[hello_client] ERROR: {server_py} not found.", file=sys.stderr)
        sys.exit(2)

    cmd = [sys.executable, str(server_py), "--verbose"]
    # Launch server over stdio; keep pipes to allow health checks / diagnostics
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return proc

def main() -> int:
    proc = launch_server()
    try:
        # small delay to let server boot (stdio listeners set up)
        time.sleep(0.1)
        if proc.poll() is not None:
            err_out = ""
            try:
                if proc.stderr is not None:
                    err_out = (proc.stderr.read() or "").strip()
            except Exception:
                pass
            print(f"[hello_client] Server failed to start. Stderr:\n{err_out}", file=sys.stderr)
            return 2
        print("[hello_client] hello_server launched (pid=%s)" % proc.pid, file=sys.stderr)
        
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            try:
                req = json.loads(line)
            except Exception:
                # Ignore malformed input
                continue

            req_id = req.get("id")
            method = req.get("method")

            # Notifications have no "id": do not respond (per JSON-RPC) and do not error
            if req_id is None:
                if method == "notifications/initialized" or (isinstance(method, str) and method.startswith("notifications/")):
                    continue  # silently accept/ignore notifications
                else:
                    continue  # ignore any other notifications as well

            params = req.get("params")

            if method == "initialize":
                handle_initialize(req_id, params)
            elif method == "tools/list":
                handle_tools_list(req_id)
            elif method == "tools/call":
                handle_tools_call(req_id, params)
            elif method == "prompts/list":
                handle_prompts_list(req_id, params)
            elif method == "resources/list":
                handle_resources_list(req_id, params)
            elif method == "resources/templates/list":
                handle_resources_templates_list(req_id, params)
            else:
                write_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"}
                })

        return 0

    finally:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass

if __name__ == "__main__":
    sys.exit(main())