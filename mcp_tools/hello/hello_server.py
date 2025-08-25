#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP hello server (stdio + JSON-RPC 2.0)

- methods:
  * tools/list  → 回報可用工具（hello）
  * tools/call  → 呼叫 hello，回傳包含當下日期時間的問候文本
  * initialize  → 協商協定版本與能力 (capabilities)

通訊規則：一行一個 JSON，stdout 只輸出 JSON 回應；任何除錯訊息請用 --verbose 切到 stderr。
"""
from __future__ import annotations

import sys
import json
import argparse
from datetime import datetime
from typing import Any, Dict, Optional

JSON = Dict[str, Any]

HELLO_TOOL: JSON = {
    "name": "hello",
    "title": "Hello",
    "description": "Return a greeting containing the provided message and the current local date-time.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Message to include in the greeting."}
        },
        "required": ["message"],
        "additionalProperties": False,
    },
}

def now_iso() -> str:
    # 使用本地時間；若要固定時區可改成 timezone-aware 的 datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def write_json(obj: JSON) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def log(msg: str, verbose: bool) -> None:
    if verbose:
        sys.stderr.write(msg.rstrip() + "\n")
        sys.stderr.flush()

def handle_initialize(req_id: Any, params: Optional[JSON]) -> None:
    write_json({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {
                "tools": {
                    "listChanged": False
                },
                "prompts": {
                    "listChanged": False
                },
                "resources": {
                    "listChanged": False
                }
            },
            "serverInfo": {
                "name": "hello_server",
                "title": "MCP Hello Server",
                "version": "1.0.0"
            }
        }
    })

def handle_tools_list(req_id: Any) -> None:
    write_json({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [HELLO_TOOL], "nextCursor": None}})

def handle_tools_call(req_id: Any, params: Optional[JSON]) -> None:
    params = params or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}

    if name != "hello":
        write_json({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": f"Unknown tool: {name}"}
        })
        return

    # 參數驗證（最小）
    if "message" not in arguments or not isinstance(arguments["message"], str):
        write_json({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": "Invalid params: 'message' (string) is required"}
        })
        return

    msg = arguments["message"]
    text = f"hello: {msg} @ {now_iso()}"
    write_json({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "isError": False,
            "content": [
                {"type": "text", "text": text}
            ]
        }
    })

def main():
    parser = argparse.ArgumentParser(description="MCP hello server (stdio JSON-RPC 2.0)")
    parser.add_argument("--verbose", action="store_true", help="Print debug logs to stderr")
    args = parser.parse_args()

    log("hello_server started (stdio mode)", args.verbose)

    while True:
        line = sys.stdin.readline()
        if not line:
            log("stdin EOF, exiting.", args.verbose)
            break
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception as e:
            write_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}})
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params")

        try:
            if method == "initialize":
                handle_initialize(req_id, params)
            elif method == "tools/list":
                handle_tools_list(req_id)
            elif method == "tools/call":
                handle_tools_call(req_id, params)
            else:
                write_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}})
        except Exception as e:
            write_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": f"Server error: {e}"}})

if __name__ == "__main__":
    main()