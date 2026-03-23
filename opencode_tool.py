#!/usr/bin/env python3
"""
OpenCode Tool -- Dispatch coding tasks to OpenCode + Oh-My-OpenCode

Gives Hermes the ability to delegate software engineering tasks to OpenCode's
agent harness, which includes the full oh-my-opencode (OMO) agent ecosystem:
Sisyphus (orchestrator), Hephaestus (deep worker), Oracle (advisor),
Librarian (researcher), Explore (grep), and more.

Supports two modes:
  1. **run** -- Fire-and-forget: send a prompt, wait for completion, get results.
     Uses `opencode run --format json` under the hood.
  2. **session** -- Managed session: create a session, send messages, continue
     conversations. Uses the OpenCode server + SDK for long-running work.

The tool auto-starts an OpenCode server when session mode is first used.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Dict, Optional

# Note: registration is handled by the plugin __init__.py, not here.

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_opencode_server_process: Optional[subprocess.Popen] = None
_opencode_server_port: Optional[int] = None
_opencode_server_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def check_opencode_requirements() -> bool:
    """Check if the opencode CLI is installed and available."""
    return shutil.which("opencode") is not None


# ---------------------------------------------------------------------------
# Server management (for session mode)
# ---------------------------------------------------------------------------

def _start_server(port: int = 4096) -> int:
    """Start an OpenCode headless server if not already running.

    Returns the port the server is listening on.
    """
    global _opencode_server_process, _opencode_server_port

    with _opencode_server_lock:
        if _opencode_server_process and _opencode_server_process.poll() is None:
            return _opencode_server_port

        cmd = ["opencode", "serve", "--port", str(port)]
        logger.info("Starting OpenCode server: %s", " ".join(cmd))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        # Give it a moment to bind
        time.sleep(2)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(f"OpenCode server failed to start: {stderr}")

        _opencode_server_process = proc
        _opencode_server_port = port
        logger.info("OpenCode server started on port %d (pid %d)", port, proc.pid)
        return port


def _stop_server():
    """Stop the OpenCode server if running."""
    global _opencode_server_process, _opencode_server_port

    with _opencode_server_lock:
        if _opencode_server_process and _opencode_server_process.poll() is None:
            logger.info("Stopping OpenCode server (pid %d)", _opencode_server_process.pid)
            os.killpg(os.getpgid(_opencode_server_process.pid), signal.SIGTERM)
            _opencode_server_process.wait(timeout=10)
        _opencode_server_process = None
        _opencode_server_port = None


# ---------------------------------------------------------------------------
# Core: opencode run (fire-and-forget)
# ---------------------------------------------------------------------------

def _run_task(
    prompt: str,
    directory: Optional[str] = None,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    variant: Optional[str] = None,
    session_id: Optional[str] = None,
    files: Optional[list] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    """Execute a task via `opencode run` and return structured results."""

    cmd = ["opencode", "run", "--format", "json"]

    if directory:
        cmd.extend(["--dir", directory])
    if agent:
        cmd.extend(["--agent", agent])
    if model:
        cmd.extend(["--model", model])
    if variant:
        cmd.extend(["--variant", variant])
    if session_id:
        cmd.extend(["--session", session_id])
    if files:
        for f in files:
            cmd.extend(["--file", f])

    cmd.append("--")
    cmd.append(prompt)

    logger.info("Running opencode: %s", " ".join(cmd[:10]) + "...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=directory or os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "error": f"Task timed out after {timeout}s",
            "prompt": prompt,
        }

    # Parse JSON event stream output
    #
    # OpenCode emits one JSON object per line. The event format uses a flat
    # structure with top-level "type" field:
    #   {"type": "text",        "part": {"type": "text", "text": "..."}, ...}
    #   {"type": "tool_use",    "part": {"tool": "write", "state": {...}}, ...}
    #   {"type": "step_finish", "part": {"reason": "stop", ...}, ...}
    #   {"type": "error",       "error": {...}, ...}
    events = []
    text_parts = []
    tool_results = []
    file_diffs = []
    session_info = {}
    errors = []

    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)

            etype = event.get("type", "")
            part = event.get("part", {})
            props = event.get("properties", {})

            # --- Text output ---
            if etype == "text":
                text_parts.append(part.get("text", ""))

            # --- Tool calls (flat format) ---
            elif etype == "tool_use":
                state = part.get("state", {})
                if state.get("status") == "completed":
                    tool_results.append({
                        "tool": part.get("tool", ""),
                        "input": state.get("input"),
                        "output": str(state.get("output", ""))[:2000],
                    })
                    # Extract file diffs from tool metadata
                    metadata = state.get("metadata", {})
                    if metadata.get("files"):
                        for f in metadata["files"]:
                            file_diffs.append({
                                "path": f.get("relativePath", f.get("filePath", "")),
                                "type": f.get("type", ""),
                                "additions": f.get("additions", 0),
                                "deletions": f.get("deletions", 0),
                            })

            # --- SDK format (message.part.updated) ---
            elif etype == "message.part.updated":
                sdk_part = props.get("part", {})
                ptype = sdk_part.get("type", "")
                if ptype == "text":
                    text_parts.append(sdk_part.get("text", ""))
                elif ptype == "tool":
                    state = sdk_part.get("state", {})
                    if state.get("status") == "completed":
                        tool_results.append({
                            "tool": sdk_part.get("tool", ""),
                            "input": state.get("input"),
                            "output": str(state.get("output", ""))[:2000],
                        })

            # --- Session info ---
            elif etype == "session.diff":
                file_diffs.extend(props.get("diffs", []))
            elif etype == "session.idle":
                session_info = props if props else event
            elif etype == "session.error":
                errors.append(props if props else event)

            # --- Error events ---
            elif etype == "error":
                errors.append(event.get("error", event))

            # --- Session ID extraction ---
            if "sessionID" in event and "id" not in session_info:
                session_info["id"] = event["sessionID"]

        except json.JSONDecodeError:
            if line and not line.startswith("{"):
                text_parts.append(line)

    # Build summary
    final_text = "\n".join(text_parts).strip()
    if not final_text and result.stdout:
        final_text = result.stdout[:3000]

    response = {
        "status": "completed" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "text": final_text[:5000],
        "tool_calls": len(tool_results),
        "files_changed": len(file_diffs),
    }

    if tool_results:
        response["tool_results"] = tool_results[-10:]
    if file_diffs:
        response["file_diffs"] = file_diffs[:20]
    if session_info:
        response["session"] = session_info
    if errors:
        response["errors"] = errors
    if result.stderr:
        response["stderr"] = result.stderr[:1000]

    return response


# ---------------------------------------------------------------------------
# Core: session management
# ---------------------------------------------------------------------------

def _session_prompt(
    session_id: str,
    prompt: str,
    directory: Optional[str] = None,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    """Send a message to an existing OpenCode session."""

    port = _start_server()
    attach_url = f"http://localhost:{port}"

    cmd = [
        "opencode", "run",
        "--format", "json",
        "--attach", attach_url,
        "--session", session_id,
    ]

    if directory:
        cmd.extend(["--dir", directory])
    if agent:
        cmd.extend(["--agent", agent])
    if model:
        cmd.extend(["--model", model])

    cmd.append("--")
    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=directory or os.getcwd(),
        )
        return {
            "status": "completed" if result.returncode == 0 else "error",
            "output": result.stdout[:5000],
            "stderr": result.stderr[:1000] if result.stderr else None,
            "session_id": session_id,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"Session prompt timed out after {timeout}s"}


# ---------------------------------------------------------------------------
# Handler (entry point for the tool registry)
# ---------------------------------------------------------------------------

def opencode_handler(args: Dict[str, Any], **kwargs) -> str:
    """Main handler for the opencode tool.

    Actions:
      run       -- Execute a one-shot coding task
      session   -- Send a message to a managed session (starts server if needed)
      status    -- Check if OpenCode server is running
      stop      -- Stop the OpenCode server
    """
    action = args.get("action", "run")
    prompt = args.get("prompt", "")
    directory = args.get("directory")
    agent = args.get("agent")
    model = args.get("model")
    variant = args.get("variant")
    session_id = args.get("session_id")
    files = args.get("files", [])
    timeout = args.get("timeout", 600)

    try:
        if action == "run":
            if not prompt:
                return json.dumps({"error": "prompt is required for 'run' action"})
            result = _run_task(
                prompt=prompt,
                directory=directory,
                agent=agent,
                model=model,
                variant=variant,
                session_id=session_id,
                files=files,
                timeout=timeout,
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        elif action == "session":
            if not prompt:
                return json.dumps({"error": "prompt is required for 'session' action"})
            if not session_id:
                return json.dumps({"error": "session_id is required for 'session' action"})
            result = _session_prompt(
                session_id=session_id,
                prompt=prompt,
                directory=directory,
                agent=agent,
                model=model,
                timeout=timeout,
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        elif action == "status":
            with _opencode_server_lock:
                running = (
                    _opencode_server_process is not None
                    and _opencode_server_process.poll() is None
                )
            return json.dumps({
                "server_running": running,
                "port": _opencode_server_port if running else None,
                "opencode_available": check_opencode_requirements(),
            })

        elif action == "stop":
            _stop_server()
            return json.dumps({"status": "stopped"})

        else:
            return json.dumps({"error": f"Unknown action: {action}. Use: run, session, status, stop"})

    except Exception as e:
        logger.exception("opencode tool error: %s", e)
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# Schema (OpenAI function-calling format)
# ---------------------------------------------------------------------------

OPENCODE_SCHEMA = {
    "name": "opencode",
    "description": (
        "Dispatch software engineering tasks to OpenCode, a powerful coding agent harness "
        "with multi-model orchestration (Sisyphus, Hephaestus, Oracle, Librarian, Explore). "
        "Use this for complex coding tasks: implementing features, fixing bugs, refactoring, "
        "running tests, code review, and multi-file changes. OpenCode has its own file editing, "
        "terminal, LSP, AST-grep, and background agent tools — it handles execution end-to-end. "
        "You provide the task description and context; OpenCode does the coding."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "session", "status", "stop"],
                "description": (
                    "Action to perform. "
                    "'run': Execute a one-shot coding task (fire-and-forget, starts its own session). "
                    "'session': Send a message to an existing managed session (for multi-turn work). "
                    "'status': Check if OpenCode server is running. "
                    "'stop': Stop the OpenCode server."
                ),
                "default": "run",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "The coding task or instruction to send to OpenCode. Be specific: include "
                    "file paths, expected behavior, constraints, and any relevant context. "
                    "OpenCode's agents will plan and execute the work autonomously."
                ),
            },
            "directory": {
                "type": "string",
                "description": (
                    "Working directory for the task. OpenCode will operate on files in this "
                    "directory. Defaults to the current working directory."
                ),
            },
            "agent": {
                "type": "string",
                "description": (
                    "OpenCode agent to use. Common agents: "
                    "'sisyphus' (main orchestrator, delegates to specialists), "
                    "'atlas' (todo-driven orchestrator), "
                    "'hephaestus' (deep autonomous worker for complex tasks), "
                    "'prometheus' (strategic planner, interviews before coding). "
                    "Leave empty to use the default agent."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Model override in provider/model format (e.g., 'anthropic/claude-opus-4-6', "
                    "'openai/gpt-5.2'). Leave empty to use OpenCode's configured default."
                ),
            },
            "variant": {
                "type": "string",
                "description": (
                    "Model variant for reasoning effort (e.g., 'high', 'max', 'minimal'). "
                    "Provider-specific. Leave empty for default."
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Session ID for continuing a multi-turn conversation. Required for "
                    "'session' action. For 'run', optionally continue a previous session."
                ),
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "File paths to attach to the message as context.",
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum time in seconds to wait for completion. Default: 600 (10 min).",
                "default": 600,
            },
        },
        "required": ["prompt"],
    },
}


# Registration is handled by the plugin __init__.py via PluginContext.register_tool().
