#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hello MCP **client** that auto-launches the local hello server.
- Spawns `hello_server.py` as a child process over stdio JSON-RPC.
- Performs `tools/list` discovery, then `tools/call` to invoke the hello tool.
- Shuts the server down on exit.
"""

import sys
import json
import time
import argparse
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

JSON = Dict[str, Any]


def _write_json(obj: JSON, stdout) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()


def _readline_json(stdin) -> Optional[JSON]:
    line = stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    return json.loads(line)


class HelloClient:
    def __init__(self, stdin, stdout, proc=None):
        # stdin/out here are the CHILD PROCESS pipes
        self.stdin = stdin   # child's stdout (we read responses)
        self.stdout = stdout # child's stdin  (we write requests)
        self._id = 0
        self.proc = proc

    def request(self, method: str, params: Optional[JSON] = None) -> JSON:
        self._id += 1
        obj: JSON = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            obj["params"] = params
        try:
            _write_json(obj, self.stdout)
        except BrokenPipeError as e:
            # Gather server stderr if the child already exited
            err_msg = None
            if self.proc is not None:
                try:
                    if self.proc.poll() is not None and self.proc.stderr is not None:
                        err_msg = (self.proc.stderr.read() or "").strip()
                except Exception:
                    pass
            raise RuntimeError(f"Broken pipe to server. Server stderr: {err_msg}") from e

        resp = _readline_json(self.stdin)
        if not resp:
            raise RuntimeError("No response from server")
        if "error" in resp:
            raise RuntimeError(str(resp["error"]))
        return resp["result"]


def launch_server() -> subprocess.Popen:
    """Launch hello_server.py as a stdio JSON-RPC server and return the Popen."""
    here = Path(__file__).resolve().parent
    server_py = here / "hello_server.py"
    if not server_py.exists():
        print(f"[hello_client] ERROR: {server_py} not found.", file=sys.stderr)
        sys.exit(2)

    cmd = [sys.executable, str(server_py), "--verbose"]
    # text=True => str I/O; bufsize=1 for line-buffered
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return proc


def graceful_shutdown(proc: subprocess.Popen) -> None:
    try:
        # Try a JSON-RPC hint if server supports it; otherwise just terminate
        try:
            if proc.stdin and not proc.stdin.closed:
                _write_json({"jsonrpc": "2.0", "method": "server/exit"}, proc.stdin)
        except Exception:
            pass
        # Give it a moment to exit cleanly
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except Exception:
                proc.kill()
    finally:
        # Close pipes
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stderr and not proc.stderr.closed:
                # Drain a bit of stderr to avoid zombie pipes
                try:
                    proc.stderr.read(2048)
                except Exception:
                    pass
                proc.stderr.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Hello MCP Client (auto-launch server)")
    parser.add_argument("--message", default="Hello!", help="Message to send to hello tool")
    args = parser.parse_args()

    proc = launch_server()
    try:
        # small delay to let server boot (stdio listeners set up)
        time.sleep(0.1)
        if proc.poll() is not None:
            # Server failed to start; surface stderr
            err_out = ""
            try:
                if proc.stderr is not None:
                    err_out = (proc.stderr.read() or "").strip()
            except Exception:
                pass
            print(f"[hello_client] Server failed to start. Stderr:\n{err_out}", file=sys.stderr)
            return 2
        client = HelloClient(proc.stdout, proc.stdin, proc=proc)

        # 1) Discover tools
        tools = client.request("tools/list")
        print("Discovered tools:", tools)

        # 2) Call hello tool
        call_result = client.request(
            "tools/call",
            params={"name": "hello", "arguments": {"message": args.message}},
        )
        print("Call result:", call_result)
        return 0
    finally:
        graceful_shutdown(proc)


if __name__ == "__main__":
    sys.exit(main())