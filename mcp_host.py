import argparse
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict
from pathlib import Path

# ------------------------------
# Optional: Local LLM (Ollama) integration
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
  "target": "one of: cve, pfcm, product_options, inspector, hello",
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


def naive_plan(user_text: str) -> Dict[str, Any]:
    """Fallback planner without LLM, minimal heuristics.
    - If the text mentions 'hello', target 'hello' and try to extract a name after 'to '.
    - Otherwise route to 'inspector' with no args as a safe default.
    """
    txt = (user_text or "").strip()
    low = txt.lower()
    if "hello" in low:
        name = ""
        # naive extraction: anything after ' to '
        if " to " in low:
            try:
                after = txt[low.index(" to ") + 4 :].strip()
                # take the last token if multiple words, else whole
                name = after.split()[0] if after else ""
            except Exception:
                name = ""
        args: Dict[str, Any] = {}
        if name:
            args["text"] = f"Hello, {name}!"
        else:
            args["text"] = "Hello!"
        return {"intent": "say_hello", "target": "hello", "args": args}
    # default safe route
    return {"intent": "inspect_env", "target": "inspector", "args": {}}


def plan_auto(user_text: str, model: str = "llama3.1", timeout: float = 20.0) -> Dict[str, Any]:
    """Best-effort planner: try Ollama with timeout, fall back to naive heuristic."""
    if ollama is not None:
        try:
            return plan_with_ollama_timeout(user_text, model=model, timeout=timeout)
        except Exception:
            pass
    return naive_plan(user_text)


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


def ensure_discovered() -> None:
    if not TOOL_REGISTRY:
        discover_tools()

# ------------------------------
# Original behavior: launch server, then client
# ------------------------------

def launch_stack_and_run_client() -> int:
    """Launch mcp_server.py, wait a moment, run mcp_client.py, then terminate server."""
    server_process = subprocess.Popen([sys.executable, "mcp_server.py"])  # nosec B603
    try:
        time.sleep(1.0)  # give server time to come up
        result = subprocess.run([sys.executable, "mcp_client.py"])  # nosec B603
        return result.returncode
    finally:
        # Gracefully terminate server
        try:
            server_process.terminate()
            try:
                server_process.wait(timeout=3)
            except Exception:
                server_process.kill()
        except Exception:
            pass


# ------------------------------
# CLI entry
# ------------------------------

def main():
    parser = argparse.ArgumentParser(description="MCP Host with optional local LLM planning (Ollama)")
    parser.add_argument(
        "--plan",
        help=(
            "Run the Ollama planning example with the given USER_TEXT and print the JSON plan, "
            "instead of launching server+client."
        ),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", "llama3.1"),
        help="Ollama model name (default: %(default)s).",
    )
    args = parser.parse_args()

    ensure_discovered()

    if args.plan:
        plan = plan_auto(args.plan, model=args.model, timeout=20.0)
        out = dispatch_stub(plan)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # Default: preserve original behavior
    rc = launch_stack_and_run_client()
    sys.exit(rc)


if __name__ == "__main__":
    main()
