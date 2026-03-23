"""
Smoke tests for Hermes → OpenCode integration.

Tests the opencode tool end-to-end by calling opencode_handler() directly,
bypassing the Hermes agent loop. Each test gets an isolated git-initialized
temp directory.

Run:
    cd /Users/master/.hermes/hermes-agent
    python -m pytest tests/integration/test_opencode_smoke.py -v -x --timeout=600

Fast subset:
    python -m pytest tests/integration/test_opencode_smoke.py -v -k "status or missing_prompt or timeout" --timeout=30
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

# Ensure hermes-agent is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.opencode_tool import opencode_handler, check_opencode_requirements


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _skip_if_no_opencode():
    if not check_opencode_requirements():
        pytest.skip("opencode CLI not found on PATH")


@pytest.fixture
def workdir(tmp_path):
    """Git-initialized temp directory for OpenCode."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True,
    )
    return tmp_path


def _run(args: dict) -> dict:
    """Call opencode_handler and parse the JSON result."""
    raw = opencode_handler(args)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Test 1: Status check (baseline — no subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_status_check():
    result = _run({"action": "status"})
    assert result["opencode_available"] is True
    assert result["server_running"] is False


# ---------------------------------------------------------------------------
# Test 2: Missing prompt error (input validation)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_missing_prompt_error():
    result = _run({"action": "run", "prompt": ""})
    assert "error" in result


# ---------------------------------------------------------------------------
# Test 3: Create a single file
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(180)
def test_create_single_file(workdir):
    result = _run({
        "action": "run",
        "prompt": (
            "Create a file called hello.py that contains a function greet(name) "
            "which returns f'Hello, {name}!'. Include a main block that calls "
            "greet('World') and prints the result."
        ),
        "directory": str(workdir),
        "timeout": 120,
    })

    assert result["status"] == "completed", f"Got: {result}"
    assert result["tool_calls"] > 0

    hello = workdir / "hello.py"
    assert hello.exists(), "hello.py was not created"
    content = hello.read_text()
    assert "def greet" in content

    # Verify it runs
    out = subprocess.run(
        [sys.executable, str(hello)],
        capture_output=True, text=True, cwd=workdir,
    )
    assert "Hello, World!" in out.stdout


# ---------------------------------------------------------------------------
# Test 4: Edit an existing file without destroying it
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(180)
def test_edit_existing_file(workdir):
    # Setup: create and commit calculator.py
    calc = workdir / "calculator.py"
    calc.write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "."], cwd=workdir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=workdir, capture_output=True,
    )

    result = _run({
        "action": "run",
        "prompt": (
            "The file calculator.py has an add function. Add a subtract(a, b) "
            "function that returns a - b, and a multiply(a, b) function that "
            "returns a * b. Do not modify the existing add function."
        ),
        "directory": str(workdir),
        "timeout": 120,
    })

    assert result["status"] == "completed", f"Got: {result}"
    assert result["tool_calls"] > 0

    content = calc.read_text()
    assert "def add" in content, "Original add function was removed"
    assert "def subtract" in content, "subtract not added"
    assert "def multiply" in content, "multiply not added"


# ---------------------------------------------------------------------------
# Test 5: Multi-file project scaffold
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(240)
def test_multi_file_scaffold(workdir):
    result = _run({
        "action": "run",
        "prompt": (
            "Create a Python package called 'mathlib' with this structure: "
            "mathlib/__init__.py (exports add and subtract), "
            "mathlib/operations.py (implements add(a,b) and subtract(a,b)), "
            "and tests/test_operations.py (pytest tests for both functions). "
            "Make sure the tests pass when run with pytest."
        ),
        "directory": str(workdir),
        "timeout": 360,
    })

    assert result["status"] == "completed", f"Got: {result}"
    assert result["tool_calls"] >= 2

    assert (workdir / "mathlib" / "__init__.py").exists()
    assert (workdir / "mathlib" / "operations.py").exists()
    assert (workdir / "tests" / "test_operations.py").exists()

    # Run the tests
    test_run = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_operations.py", "-v"],
        cwd=workdir, capture_output=True, text=True,
    )
    assert test_run.returncode == 0, f"Tests failed:\n{test_run.stdout}\n{test_run.stderr}"


# ---------------------------------------------------------------------------
# Test 6: Git operations (back-office automation)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(180)
def test_git_operations(workdir):
    # Setup: create an unstaged file
    (workdir / "utils.py").write_text("def noop():\n    pass\n")

    result = _run({
        "action": "run",
        "prompt": (
            "This git repo has an unstaged file utils.py. Stage all changes, "
            "commit them with the message 'feat: add utility module', then "
            "create a new branch called 'feature/utils' and switch to it."
        ),
        "directory": str(workdir),
        "timeout": 120,
    })

    assert result["status"] == "completed", f"Got: {result}"
    assert result["tool_calls"] > 0

    # Verify commit
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=workdir, capture_output=True, text=True,
    )
    assert "add utility module" in log.stdout.lower() or "utility" in log.stdout.lower()

    # Verify branch exists
    branches = subprocess.run(
        ["git", "branch"],
        cwd=workdir, capture_output=True, text=True,
    )
    assert "feature/utils" in branches.stdout

    # Verify current branch
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=workdir, capture_output=True, text=True,
    )
    assert "feature/utils" in current.stdout.strip()


# ---------------------------------------------------------------------------
# Test 7: Agent selection (Hephaestus)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(240)
def test_agent_selection_hephaestus(workdir):
    result = _run({
        "action": "run",
        "prompt": (
            "Create a file called deep_work.py that implements a binary_search "
            "function. Include comprehensive docstrings and type hints."
        ),
        "directory": str(workdir),
        "agent": "hephaestus",
        "timeout": 180,
    })

    assert result["status"] == "completed", f"Got: {result}"
    assert result["tool_calls"] > 0

    deep = workdir / "deep_work.py"
    assert deep.exists(), "deep_work.py was not created"
    content = deep.read_text()
    assert "binary_search" in content or "binary" in content


# ---------------------------------------------------------------------------
# Test 8: Timeout handling
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(30)
def test_timeout_handling(workdir):
    result = _run({
        "action": "run",
        "prompt": (
            "Read every single file in the entire filesystem recursively "
            "and produce a detailed summary of each one."
        ),
        "directory": str(workdir),
        "timeout": 5,
    })

    assert result["status"] == "timeout"
    assert "error" in result
    assert "timed out" in result["error"].lower() or "timeout" in result["error"].lower()


# ---------------------------------------------------------------------------
# Test 9: Spec-to-code with file context
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(240)
def test_spec_to_code(workdir):
    # Setup: write a spec file
    spec = workdir / "spec.md"
    spec.write_text(
        "# API Spec\n\n"
        "## GET /health\nReturns {\"status\": \"ok\"}\n\n"
        "## POST /echo\nAccepts JSON body, returns it back unchanged\n\n"
        "## GET /version\nReturns {\"version\": \"1.0.0\"}\n"
    )
    subprocess.run(["git", "add", "."], cwd=workdir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add spec"],
        cwd=workdir, capture_output=True,
    )

    result = _run({
        "action": "run",
        "prompt": (
            "Based on the API specification in spec.md, create an implementation "
            "file called api.py that implements all the endpoints described. "
            "Use Flask."
        ),
        "directory": str(workdir),
        "files": [str(spec)],
        "timeout": 180,
    })

    assert result["status"] == "completed", f"Got: {result}"

    api = workdir / "api.py"
    assert api.exists(), "api.py was not created"
    content = api.read_text()
    assert "health" in content
    assert "echo" in content
    assert "version" in content
    assert "flask" in content.lower() or "Flask" in content


# ---------------------------------------------------------------------------
# Test 10: Session continuity (two turns)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(360)
def test_session_continuity(workdir):
    # Turn 1: create the Counter class
    result1 = _run({
        "action": "run",
        "prompt": (
            "Create a file called counter.py with a Counter class that has "
            "increment(), decrement(), and get_value() methods. Start value at 0."
        ),
        "directory": str(workdir),
        "timeout": 180,
    })

    assert result1["status"] == "completed", f"Turn 1 failed: {result1}"
    assert (workdir / "counter.py").exists()

    content1 = (workdir / "counter.py").read_text()
    assert "class Counter" in content1
    assert "increment" in content1
    assert "decrement" in content1

    # Extract session ID for turn 2
    session_id = result1.get("session", {}).get("id")
    if not session_id:
        pytest.skip("Session ID not available in event stream — cannot test continuity")

    # Turn 2: add more methods in the same session
    result2 = _run({
        "action": "run",
        "prompt": (
            "Now add a reset() method to the Counter class in counter.py that "
            "sets the value back to 0. Also add a __repr__ method that returns "
            "'Counter(value=N)' where N is the current value."
        ),
        "directory": str(workdir),
        "session_id": session_id,
        "timeout": 180,
    })

    assert result2["status"] == "completed", f"Turn 2 failed: {result2}"

    content2 = (workdir / "counter.py").read_text()
    assert "reset" in content2, "reset() not added in turn 2"
    assert "__repr__" in content2, "__repr__ not added in turn 2"
