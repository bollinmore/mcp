
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict

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
  "target": "one of: cve, pfcm, product_options, inspector",
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
    else:
        result = {"status": "unsupported_target", "target": target}

    return {"plan": plan, "result": result}


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

    if args.plan:
        plan = plan_with_ollama(args.plan, model=args.model)
        out = dispatch_stub(plan)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # Default: preserve original behavior
    rc = launch_stack_and_run_client()
    sys.exit(rc)


if __name__ == "__main__":
    main()
