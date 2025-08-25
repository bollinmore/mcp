import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict
from pathlib import Path

# ------------------------------
# Required: Local LLM (Ollama) integration
# ------------------------------
try:
    import ollama  # Make sure you do NOT have a local file named "ollama.py"
except Exception as e:
    ollama = None
    _OLLAMA_IMPORT_ERROR = e
else:
    _OLLAMA_IMPORT_ERROR = None

SYS_PROMPT = (
    """
You are the MCP Host planner. Given a user prompt, extract a strict JSON plan:
{
  "intent": "verb_noun",
  "target": "one of: cve, pfcm, product_options, inspector, hello, git",
  "args": {"k": "v"}
}
Only output the JSON object. No extra text.
    """
).strip()


def plan_with_ollama(user_text: str, model: str = "llama3.1") -> Dict[str, Any]:
    """Generate a task plan using a local LLM via Ollama.

    Requires the `ollama` package and an available local model (e.g., `ollama pull llama3.1`).
    """
    if ollama is None:
        # Give a helpful error if import failed (e.g., file shadowing by `ollama.py`).
        hint = (
            "\nHint: If you have a local file named 'ollama.py' or a '__pycache__/ollama*.pyc', "
            "rename/delete it so it doesn't shadow the real package."
        )
        raise RuntimeError(f"Failed to import ollama: {_OLLAMA_IMPORT_ERROR}{hint}")

    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": user_text},
        ],
        options={"temperature": 0},
    )
    content = resp["message"]["content"].strip()
    # Attempt to parse the model output as strict JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Fallback: try to extract JSON object heuristically
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise

import threading


def plan_with_ollama_timeout(user_text: str, model: str = "llama3.1", timeout: float = 20.0) -> Dict[str, Any]:
    """Run plan_with_ollama with a hard timeout. Raises TimeoutError on deadline."""
    result: Dict[str, Any] = {}
    err: Dict[str, Any] = {}

    def _runner():
        nonlocal result, err
        try:
            result = plan_with_ollama(user_text, model=model)
        except Exception as e:  # capture any error to re-raise in caller
            err["exc"] = e

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"ollama planning timed out after {timeout} seconds")
    if "exc" in err:
        raise err["exc"]
    return result


def plan_with_ollama_required(user_text: str, model: str = "llama3.1", timeout: float = 20.0) -> Dict[str, Any]:
    """Planner that *requires* Ollama. If Ollama is unavailable or planning fails, raise a clear error."""
    if ollama is None:
        hint = (
            "\nHint: If you have a local file named 'ollama.py' or a '__pycache__/ollama*.pyc', "
            "rename/delete it so it doesn't shadow the real package."
        )
        raise RuntimeError(
            f"Ollama is required but not available: {_OLLAMA_IMPORT_ERROR}{hint}"
        )

    # Use the timeout-protected planner; propagate any exceptions for clarity
    return plan_with_ollama_timeout(user_text, model=model, timeout=timeout)


def dispatch_stub(plan: Dict[str, Any]) -> Dict[str, Any]:
    """A minimal dispatcher stub.
    Replace this with real routing to your MCP Clients/Servers.
    """
    target = plan.get("target")
    args = plan.get("args", {})

    # Example mocked behavior.
    if target == "cve":
        result = {"affected": False, "checked": args}
    elif target == "pfcm":
        result = {"status": "ok", "changes": ["PCD:X=1"]}
    elif target == "product_options":
        result = {"status": "ok", "message": "product options queried", "args": args}
    elif target == "inspector":
        result = {"status": "ok", "checks": ["env", "network", "permissions"]}
    elif target == "hello":
        ensure_discovered()
        inv = TOOL_REGISTRY.get("hello")
        result = inv(args) if inv else {"ok": False, "error": "hello tool not found in registry"}
    elif target == "git":
        ensure_discovered()
        inv = TOOL_REGISTRY.get("git")
        result = inv(args) if inv else {"ok": False, "error": "git tool not found in registry"}
    else:
        result = {"status": "unsupported_target", "target": target}

    return {"plan": plan, "result": result}



# ------------------------------
# Tool discovery registry
# ------------------------------

TOOL_REGISTRY: Dict[str, Any] = {}


def _make_hello_invoker(tool_dir: Path):
    def _invoke(args: Dict[str, Any]) -> Dict[str, Any]:
        message = str(args.get("text") or args.get("message") or args.get("name") or "hello")
        timeout_sec = float(os.environ.get("MCP_TOOL_TIMEOUT", "8"))
        candidates = []
        py_client = tool_dir / "hello_client.py"
        if py_client.exists():
            # Prefer argparse-style first, then positional fallback
            candidates.append([sys.executable, str(py_client), "--message", message])
            candidates.append([sys.executable, str(py_client), message])
        bin_client = tool_dir / "hello_client"
        if bin_client.exists():
            candidates.append([str(bin_client), "--message", message])
            candidates.append([str(bin_client), message])
        sh_client = tool_dir / "hello.sh"
        if sh_client.exists():
            candidates.append(["bash", str(sh_client), "--message", message])
            candidates.append(["bash", str(sh_client), message])
        if not candidates:
            return {
                "ok": False,
                "error": "Hello tool not found",
                "searched": [str(py_client), str(bin_client), str(sh_client)],
            }
        last_err = None
        for cmd in candidates:
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_sec,
                )
                if proc.returncode == 0:
                    return {
                        "ok": True,
                        "returncode": 0,
                        "stdout": proc.stdout.strip(),
                        "stderr": proc.stderr.strip(),
                        "command": cmd,
                        "timed_out": False,
                        "timeout_sec": timeout_sec,
                    }
                else:
                    last_err = {
                        "ok": False,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout.strip(),
                        "stderr": proc.stderr.strip(),
                        "command": cmd,
                        "timed_out": False,
                        "timeout_sec": timeout_sec,
                    }
                    # continue to next candidate
                    continue
            except subprocess.TimeoutExpired:
                last_err = {
                    "ok": False,
                    "error": f"timeout after {timeout_sec}s",
                    "command": cmd,
                    "timed_out": True,
                    "timeout_sec": timeout_sec,
                }
                continue
            except Exception as e:
                last_err = {"ok": False, "error": str(e), "command": cmd}
                continue
        return {"ok": False, "error": last_err or "Unknown error while invoking hello tool"}
    return _invoke


def _make_git_invoker(tool_dir: Path):
    """Invoker for mcp_tools/git/git_client.py

    Expected args (examples):
      {"repo": ".", "cmd": "status"}
      {"repo": ".", "cmd": "diff", "target": "main", "context_lines": 5}
      {"repo": ".", "cmd": "add", "files": ["file1", "file2"]}
      {"repo": ".", "cmd": "commit", "message": "feat: ..."}
      {"repo": ".", "cmd": "log", "max_count": 5}
      {"repo": ".", "cmd": "branch", "branch_type": "all", "contains": "<sha>"}
      {"repo": ".", "cmd": "call", "tool": "git_status", "json": {}}
    """
    def _invoke(args: Dict[str, Any]) -> Dict[str, Any]:
        timeout_sec = float(os.environ.get("MCP_TOOL_TIMEOUT", "20"))
        repo = str(args.get("repo") or args.get("repo_path") or ".")
        cmd = str(args.get("cmd") or "status")

        # Locate python client
        py_client = tool_dir / "git" / "git_client.py"
        if not py_client.exists():
            # Also support layout where this file is at mcp_tools/git/git_client.py and we're already in that dir
            alt = tool_dir / "git_client.py"
            py_client = alt if alt.exists() else py_client
        if not py_client.exists():
            return {"ok": False, "error": f"Git client not found at {py_client}"}

        base = [sys.executable, str(py_client), "--repo", repo]

        def run(argv: list[str]) -> Dict[str, Any]:
            try:
                proc = subprocess.run(base + argv, capture_output=True, text=True, timeout=timeout_sec)  # nosec B603
                ok = proc.returncode == 0
                out = proc.stdout.strip()
                err = proc.stderr.strip()
                parsed = None
                try:
                    parsed = json.loads(out) if out else None
                except Exception:
                    parsed = None
                return {
                    "ok": ok,
                    "returncode": proc.returncode,
                    "stdout": out,
                    "stderr": err,
                    "json": parsed,
                    "command": base + argv,
                    "timed_out": False,
                    "timeout_sec": timeout_sec,
                }
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "error": f"timeout after {timeout_sec}s",
                    "command": base + argv,
                    "timed_out": True,
                    "timeout_sec": timeout_sec,
                }
            except Exception as e:
                return {"ok": False, "error": str(e), "command": base + argv}

        # Map cmd to CLI
        if cmd == "status":
            return run(["status"])
        if cmd == "diff-unstaged":
            context = str(int(args.get("context_lines", 3)))
            return run(["diff-unstaged", "--context-lines", context])
        if cmd == "diff-staged":
            context = str(int(args.get("context_lines", 3)))
            return run(["diff-staged", "--context-lines", context])
        if cmd == "diff":
            target = str(args.get("target") or args.get("revision") or "HEAD")
            context = str(int(args.get("context_lines", 3)))
            return run(["diff", "--target", target, "--context-lines", context])
        if cmd == "add":
            files = args.get("files") or []
            if not files:
                return {"ok": False, "error": "'add' requires 'files' list"}
            return run(["add", "--files", *map(str, files)])
        if cmd == "reset":
            return run(["reset"])
        if cmd == "commit":
            msg = str(args.get("message") or "")
            if not msg:
                return {"ok": False, "error": "'commit' requires 'message'"}
            return run(["commit", "--message", msg])
        if cmd == "log":
            mc = str(int(args.get("max_count", 10)))
            return run(["log", "--max-count", mc])
        if cmd == "create-branch":
            name = str(args.get("name") or args.get("branch_name") or "")
            if not name:
                return {"ok": False, "error": "'create-branch' requires 'name'"}
            sp = args.get("start_point") or args.get("start-point")
            argv = ["create-branch", "--name", name]
            if sp:
                argv += ["--start-point", str(sp)]
            return run(argv)
        if cmd == "checkout":
            name = str(args.get("name") or args.get("branch_name") or "")
            if not name:
                return {"ok": False, "error": "'checkout' requires 'name'"}
            return run(["checkout", "--name", name])
        if cmd == "show":
            rev = str(args.get("revision") or "HEAD")
            return run(["show", "--revision", rev])
        if cmd == "init":
            return run(["init"])
        if cmd == "branch":
            bt = str(args.get("branch_type") or "local")
            contains = args.get("contains")
            not_contains = args.get("not_contains")
            argv = ["branch", "--type", bt]
            if contains:
                argv += ["--contains", str(contains)]
            if not_contains:
                argv += ["--not-contains", str(not_contains)]
            return run(argv)
        if cmd == "call":
            tool = str(args.get("tool") or "")
            payload = args.get("json") or {}
            try:
                payload_json = json.dumps(payload, ensure_ascii=False)
            except Exception as e:
                return {"ok": False, "error": f"invalid json payload: {e}"}
            return run(["call", "--tool", tool, "--json", payload_json])

        return {"ok": False, "error": f"unsupported git cmd: {cmd}"}

    return _invoke


def discover_tools() -> None:
    """Discover available MCP tools under ./mcp_tools/* and register invokers.

    Current support: hello (mcp_tools/hello/...).
    """
    global TOOL_REGISTRY
    base_dir = Path(__file__).resolve().parent
    tools_root = base_dir / "mcp_tools"
    if not tools_root.exists():
        return

    # Discover 'hello' tool
    hello_dir = tools_root / "hello"
    if hello_dir.exists() and hello_dir.is_dir():
        TOOL_REGISTRY["hello"] = _make_hello_invoker(hello_dir)

    # Discover 'git' tool
    git_dir = tools_root / "git"
    if git_dir.exists():
        # Support both layouts: mcp_tools/git/git_client.py or mcp_tools/git/git/git_client.py
        TOOL_REGISTRY["git"] = _make_git_invoker(tools_root)


def ensure_discovered() -> None:
    if not TOOL_REGISTRY:
        discover_tools()



# ------------------------------
# CLI entry
# ------------------------------

def main():
    parser = argparse.ArgumentParser(description="MCP Host with natural language planning via Ollama")
    args = parser.parse_args()

    # Always require Ollama for NLP planning
    if ollama is None:
        raise RuntimeError("Ollama is required but not available. Please install the ollama package and ensure no local ollama.py shadows it.")

    ensure_discovered()

    user_text = input("Enter your request: ")
    plan = plan_with_ollama_required(user_text, model="llama3.1", timeout=20.0)
    out = dispatch_stub(plan)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return


if __name__ == "__main__":
    main()
