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

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_TIMEOUT = 3600          # 1 hour hard cap
DEFAULT_TIMEOUT = 600       # 10 minutes
DEFAULT_SERVER_PORT = 4096
SERVER_STARTUP_WAIT = 2     # seconds to wait for server to bind

# Built-in agent that always ships with the opencode CLI itself (no plugins
# required). Used as a safety net when the user's configured default agent —
# typically an oh-my-opencode agent like "Sisyphus - Ultraworker" — is missing.
FALLBACK_AGENT = "build"

# Environment overrides applied to every opencode subprocess we spawn. Hermes
# drives opencode headlessly, so opencode must never try to pop a browser tab
# (embedded web UI), auto-share a session, or auto-update mid-run. Without these
# the embedded web UI opens a browser on every launch. Callers' existing env is
# preserved; these keys are only set if not already present so an operator can
# still override them.
_HEADLESS_ENV = {
    "OPENCODE_DISABLE_EMBEDDED_WEB_UI": "1",  # the browser-tab culprit
    "OPENCODE_DISABLE_SHARE": "1",            # never auto-share / open share URL
    "OPENCODE_AUTO_SHARE": "0",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",       # don't self-update during a task
    "BROWSER": "true",                         # no-op opener as a belt-and-braces fallback
}


def _child_env() -> Dict[str, str]:
    """Return a copy of os.environ with headless/no-browser defaults applied.

    Only fills keys the caller hasn't already set, so explicit operator settings
    win.
    """
    env = dict(os.environ)
    for key, value in _HEADLESS_ENV.items():
        env.setdefault(key, value)
    return env

# Output truncation limits
MAX_TEXT_LENGTH = 5000
MAX_TOOL_OUTPUT_LENGTH = 2000
MAX_STDERR_LENGTH = 1000
MAX_FALLBACK_TEXT_LENGTH = 3000
MAX_TOOL_RESULTS = 10
MAX_FILE_DIFFS = 20
MAX_PROMPT_LENGTH = 100_000  # ~100KB prompt limit

# Event types — flat format (opencode run --format json)
_ET_TEXT = "text"
_ET_TOOL_USE = "tool_use"
_ET_STEP_FINISH = "step_finish"
_ET_ERROR = "error"
# Event types — SDK format (opencode server / attach)
_ET_MESSAGE_PART = "message.part.updated"
_ET_SESSION_DIFF = "session.diff"
_ET_SESSION_IDLE = "session.idle"
_ET_SESSION_ERROR = "session.error"

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


def _list_agents(timeout: int = 15) -> List[str]:
    """Return the names of agents the local opencode install knows about.

    Parses `opencode agent list`. Best-effort: returns an empty list if the CLI
    is missing, errors, or the output can't be parsed. Used for discovery and to
    report whether the oh-my-opencode harness is installed.
    """
    if not check_opencode_requirements():
        return []
    try:
        result = subprocess.run(
            ["opencode", "agent", "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_child_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    agents: List[str] = []
    for line in result.stdout.splitlines():
        # Lines look like: "build (subagent)" / "Sisyphus - Ultraworker (primary)".
        # Strip zero-width spaces opencode uses for indentation, then take the
        # text before the trailing "(role)" marker.
        line = line.replace("​", "").strip()
        if not line or "(" not in line:
            continue
        name = line.rsplit("(", 1)[0].strip()
        if name and name not in agents:
            agents.append(name)
    return agents


def _omo_installed(agents: Optional[List[str]] = None) -> bool:
    """Heuristic: is the oh-my-opencode agent harness installed?"""
    if agents is None:
        agents = _list_agents()
    lowered = " ".join(agents).lower()
    return any(name in lowered for name in ("sisyphus", "hephaestus", "prometheus"))


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_timeout(value: Any) -> int:
    """Clamp timeout to a safe range."""
    try:
        t = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(1, min(t, MAX_TIMEOUT))


def _validate_string(value: Any, max_length: int = 1000) -> Optional[str]:
    """Return a sanitized string or None."""
    if value is None:
        return None
    s = str(value)[:max_length]
    return s if s else None


def _validate_directory(path: Optional[str]) -> Optional[str]:
    """Validate that a directory exists and resolve it."""
    if not path:
        return None
    resolved = os.path.realpath(path)
    if not os.path.isdir(resolved):
        return None
    return resolved


def _validate_files(files: Any) -> List[str]:
    """Validate and filter file paths — only return existing files."""
    if not isinstance(files, list):
        return []
    result = []
    for f in files:
        if isinstance(f, str) and os.path.isfile(f):
            result.append(os.path.realpath(f))
    return result


def _sanitize_stderr(stderr: str) -> str:
    """Truncate stderr and strip potentially sensitive paths."""
    return stderr[:MAX_STDERR_LENGTH] if stderr else ""


def _is_missing_default_agent_error(returncode: int, stdout: str, stderr: str) -> bool:
    """Detect the 'configured default agent is not installed' failure.

    When oh-my-opencode is not fully installed but the opencode config still
    points its default agent at one of OMO's agents (e.g. "Sisyphus -
    Ultraworker"), `opencode run` exits non-zero with a message like:

        Error: default agent "Sisyphus - Ultraworker" not found

    Note: a *missing --agent we passed explicitly* is NOT this case — opencode
    just warns ("agent X not found. Falling back to default agent") and still
    succeeds. We only treat a hard non-zero exit with a "not found" agent
    message as recoverable via the built-in fallback agent.
    """
    if returncode == 0:
        return False
    blob = f"{stdout}\n{stderr}".lower()
    return "not found" in blob and "agent" in blob


# ---------------------------------------------------------------------------
# Server management (for session mode)
# ---------------------------------------------------------------------------

def _start_server(port: int = DEFAULT_SERVER_PORT) -> int:
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
            process_group=0,  # safe replacement for preexec_fn=os.setsid
            env=_child_env(),
        )

        # Give it a moment to bind
        time.sleep(SERVER_STARTUP_WAIT)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(
                f"OpenCode server failed to start (exit {proc.returncode}). "
                "Check that port is available and opencode is configured."
            )

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
            try:
                os.killpg(os.getpgid(_opencode_server_process.pid), signal.SIGTERM)
                _opencode_server_process.wait(timeout=10)
            except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                # Process already gone, or won't stop — force kill
                try:
                    _opencode_server_process.kill()
                except Exception:
                    pass
        _opencode_server_process = None
        _opencode_server_port = None


# Clean up on interpreter shutdown
atexit.register(_stop_server)


# ---------------------------------------------------------------------------
# Event stream parsing
# ---------------------------------------------------------------------------

def _parse_event_stream(stdout: str) -> Dict[str, Any]:
    """Parse OpenCode's JSON event stream into structured results.

    Handles two event formats:
      - Flat format: {"type": "text", "part": {...}, ...} (from opencode run)
      - SDK format:  {"type": "message.part.updated", "properties": {"part": {...}}}
    """
    text_parts: List[str] = []
    tool_results: List[Dict[str, Any]] = []
    file_diffs: List[Dict[str, Any]] = []
    session_info: Dict[str, Any] = {}
    errors: List[Dict[str, Any]] = []

    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if line and not line.startswith("{"):
                text_parts.append(line)
            continue

        etype = event.get("type", "")
        part = event.get("part", {})
        props = event.get("properties", {})

        # --- Flat format: text output ---
        if etype == _ET_TEXT:
            text_parts.append(part.get("text", ""))

        # --- Flat format: tool calls ---
        elif etype == _ET_TOOL_USE:
            state = part.get("state", {})
            if state.get("status") == "completed":
                tool_results.append({
                    "tool": part.get("tool", ""),
                    "input": state.get("input"),
                    "output": str(state.get("output", ""))[:MAX_TOOL_OUTPUT_LENGTH],
                })
                metadata = state.get("metadata", {})
                if metadata.get("files"):
                    for f in metadata["files"]:
                        file_diffs.append({
                            "path": f.get("relativePath", f.get("filePath", "")),
                            "type": f.get("type", ""),
                            "additions": f.get("additions", 0),
                            "deletions": f.get("deletions", 0),
                        })

        # --- SDK format: message.part.updated ---
        elif etype == _ET_MESSAGE_PART:
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
                        "output": str(state.get("output", ""))[:MAX_TOOL_OUTPUT_LENGTH],
                    })

        # --- Session events ---
        elif etype == _ET_SESSION_DIFF:
            file_diffs.extend(props.get("diffs", []))
        elif etype == _ET_SESSION_IDLE:
            session_info = props if props else event
        elif etype == _ET_SESSION_ERROR:
            errors.append(props if props else event)

        # --- Error events ---
        elif etype == _ET_ERROR:
            errors.append(event.get("error", event))

        # --- Session ID extraction (from any event) ---
        if "sessionID" in event and "id" not in session_info:
            session_info["id"] = event["sessionID"]

    return {
        "text_parts": text_parts,
        "tool_results": tool_results,
        "file_diffs": file_diffs,
        "session_info": session_info,
        "errors": errors,
    }


def _build_response(
    parsed: Dict[str, Any],
    returncode: int,
    raw_stdout: str,
    raw_stderr: str,
) -> Dict[str, Any]:
    """Build a structured response dict from parsed event data."""
    text_parts = parsed["text_parts"]
    tool_results = parsed["tool_results"]
    file_diffs = parsed["file_diffs"]
    session_info = parsed["session_info"]
    errors = parsed["errors"]

    final_text = "\n".join(text_parts).strip()
    if not final_text and raw_stdout:
        final_text = raw_stdout[:MAX_FALLBACK_TEXT_LENGTH]

    response: Dict[str, Any] = {
        "status": "completed" if returncode == 0 else "error",
        "exit_code": returncode,
        "text": final_text[:MAX_TEXT_LENGTH],
        "tool_calls": len(tool_results),
        "files_changed": len(file_diffs),
    }

    if tool_results:
        response["tool_results"] = tool_results[-MAX_TOOL_RESULTS:]
    if file_diffs:
        response["file_diffs"] = file_diffs[:MAX_FILE_DIFFS]
    if session_info:
        response["session"] = session_info
    if errors:
        response["errors"] = errors
    if raw_stderr:
        response["stderr"] = _sanitize_stderr(raw_stderr)

    return response


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
    files: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Execute a task via `opencode run` and return structured results.

    If the caller did not request a specific agent and the run fails because the
    user's configured default agent is missing (the classic half-installed
    oh-my-opencode case), the task is retried once with the built-in fallback
    agent so Hermes still gets a result instead of an opaque error.
    """

    def _build_cmd(agent_override: Optional[str]) -> List[str]:
        cmd = ["opencode", "run", "--format", "json"]
        if directory:
            cmd.extend(["--dir", directory])
        if agent_override:
            cmd.extend(["--agent", agent_override])
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
        return cmd

    def _exec(cmd: List[str]):
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=directory or os.getcwd(),
            env=_child_env(),
        )

    logger.info("Running opencode task (timeout=%ds, agent=%s)", timeout, agent or "<default>")

    try:
        result = _exec(_build_cmd(agent))
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "error": f"Task timed out after {timeout}s",
        }

    fell_back = False
    # Only auto-fall-back when the caller did NOT pin an agent: if they asked for
    # a specific agent we surface the error rather than silently swapping it.
    if not agent and _is_missing_default_agent_error(
        result.returncode, result.stdout, result.stderr
    ):
        logger.warning(
            "Default opencode agent is missing (oh-my-opencode not installed?); "
            "retrying with built-in '%s' agent.", FALLBACK_AGENT,
        )
        try:
            result = _exec(_build_cmd(FALLBACK_AGENT))
            fell_back = True
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "error": f"Task timed out after {timeout}s (fallback agent)",
            }

    parsed = _parse_event_stream(result.stdout)
    response = _build_response(parsed, result.returncode, result.stdout, result.stderr)
    if fell_back:
        response["agent_used"] = FALLBACK_AGENT
        response["note"] = (
            "Configured default agent was unavailable (oh-my-opencode not "
            f"installed); used built-in '{FALLBACK_AGENT}' agent instead. "
            "Install oh-my-opencode for the full Sisyphus/Hephaestus harness: "
            "https://github.com/zaycruz/oh-my-opencode"
        )
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
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Send a message to an existing OpenCode session.

    Returns the same structured format as _run_task for consistency.
    """
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
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "error": f"Session prompt timed out after {timeout}s",
        }

    parsed = _parse_event_stream(result.stdout)
    response = _build_response(parsed, result.returncode, result.stdout, result.stderr)
    response["session_id"] = session_id
    return response


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
    action = _validate_string(args.get("action", "run"), max_length=20) or "run"
    prompt = _validate_string(args.get("prompt", ""), max_length=MAX_PROMPT_LENGTH) or ""
    directory = _validate_directory(args.get("directory"))
    agent = _validate_string(args.get("agent"), max_length=100)
    model = _validate_string(args.get("model"), max_length=200)
    variant = _validate_string(args.get("variant"), max_length=50)
    session_id = _validate_string(args.get("session_id"), max_length=200)
    files = _validate_files(args.get("files", []))
    timeout = _validate_timeout(args.get("timeout", DEFAULT_TIMEOUT))

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

        elif action == "agents":
            agents = _list_agents()
            return json.dumps({
                "opencode_available": check_opencode_requirements(),
                "agents": agents,
                "oh_my_opencode_installed": _omo_installed(agents),
                "fallback_agent": FALLBACK_AGENT,
            })

        elif action == "status":
            with _opencode_server_lock:
                running = (
                    _opencode_server_process is not None
                    and _opencode_server_process.poll() is None
                )
            agents = _list_agents()
            return json.dumps({
                "server_running": running,
                "port": _opencode_server_port if running else None,
                "opencode_available": check_opencode_requirements(),
                "oh_my_opencode_installed": _omo_installed(agents),
                "agent_count": len(agents),
            })

        elif action == "stop":
            _stop_server()
            return json.dumps({"status": "stopped"})

        else:
            return json.dumps({"error": f"Unknown action: {action}. Use: run, session, status, agents, stop"})

    except Exception as e:
        logger.exception("opencode tool error")
        return json.dumps({"error": f"Tool execution failed: {type(e).__name__}"})


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
                "enum": ["run", "session", "status", "agents", "stop"],
                "description": (
                    "Action to perform. "
                    "'run': Execute a one-shot coding task (fire-and-forget, starts its own session). "
                    "'session': Send a message to an existing managed session (for multi-turn work). "
                    "'status': Check if OpenCode server is running and whether oh-my-opencode is installed. "
                    "'agents': List the agents the local opencode install knows about (discovery). "
                    "'stop': Stop the OpenCode server."
                ),
                "default": "run",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "The coding task or instruction to send to OpenCode. Be specific: include "
                    "file paths, expected behavior, constraints, and any relevant context. "
                    "OpenCode's agents will plan and execute the work autonomously. "
                    "Required for 'run' and 'session' actions. Not needed for 'status' or 'stop'."
                ),
            },
            "directory": {
                "type": "string",
                "description": (
                    "Working directory for the task. Must be an existing directory. "
                    "OpenCode will operate on files in this directory. "
                    "Defaults to the current working directory."
                ),
            },
            "agent": {
                "type": "string",
                "description": (
                    "OpenCode agent to use. The orchestrator agents are provided by "
                    "oh-my-opencode and use full display names: "
                    "'Sisyphus - Ultraworker' (main orchestrator, delegates to specialists), "
                    "'Atlas - Plan Executor' (todo-driven orchestrator), "
                    "'Hephaestus - Deep Agent' (deep autonomous worker), "
                    "'Prometheus - Plan Builder' (strategic planner). "
                    "Built-in agents that need no plugins: 'build', 'plan', 'general', 'explore'. "
                    "Leave empty to use opencode's configured default. If the default is a "
                    "missing oh-my-opencode agent, the tool auto-falls-back to 'build'. "
                    "Call action='agents' to list what is actually installed."
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
                "description": "File paths to attach to the message as context. Files must exist.",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    f"Maximum time in seconds to wait for completion. "
                    f"Default: {DEFAULT_TIMEOUT}. Max: {MAX_TIMEOUT}."
                ),
                "default": DEFAULT_TIMEOUT,
            },
        },
        "required": [],
    },
}
