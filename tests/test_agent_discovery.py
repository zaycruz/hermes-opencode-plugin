"""
Unit tests for the agent-discovery path of the opencode tool.

These lock in two fixes to `_list_agents` / `_omo_installed`:

1. opencode 2.0.20 truncates `opencode debug agents` at exactly 327680 bytes
   (320 KiB) when stdout is a pipe, so the parser must read from a FILE.
2. `opencode agent list` prints its help text (with exit code 0) on 2.x, so the
   legacy text parser must reject help rows instead of scraping them into bogus
   "agents".

The parser and OMO-detection tests below are hermetic (no opencode needed); the
final test exercises the live 2.x JSON path and skips when opencode is absent.

Run:
    cd hermes-opencode-plugin
    python -m pytest tests/test_agent_discovery.py -v
"""

import os
import sys

import pytest

_plugin_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _plugin_root)

from opencode_tool import (  # noqa: E402
    _list_agents,
    _omo_installed,
    _parse_agent_list_text,
    _query_agents_json,
    check_opencode_requirements,
)


# Representative legacy `opencode agent list` rows (opencode 1.x). Names may be
# decorated ("Sisyphus - Ultraworker"); the role in parens is a bare word.
LEGACY_AGENT_LIST = (
    "  build (subagent)\n"
    "\u200bSisyphus - Ultraworker (primary)\n"
    "\u200b  Oracle (primary)\n"
    "  plan (subagent)\n"
)

# Representative `opencode agent list` HELP output (opencode 2.x). Every line
# below actually appears in that help text — including the "(optional)" row that
# a naive "has a paren" parser wrongly accepted as an agent named
# "directory string    Directory to start OpenCode in".
HELP_TEXT_SAMPLE = (
    "DESCRIPTION\n"
    "  OpenCode command line interface\n"
    "\n"
    "USAGE\n"
    "  opencode <subcommand> [flags] [<directory>]\n"
    "\n"
    "ARGUMENTS\n"
    "  directory string    Directory to start OpenCode in (optional)\n"
    "    --completions <bash|zsh|fish|sh>     Print shell completion script\n"
    "    --log-level <all|trace|debug>        Sets the minimum log level\n"
    "    --print-logs                         Print logs to stderr\n"
    "    mcp                Manage MCP\n"
)


# ---------------------------------------------------------------------------
# Legacy (1.x) text parsing
# ---------------------------------------------------------------------------

def test_parse_legacy_agent_list_returns_names():
    agents = _parse_agent_list_text(LEGACY_AGENT_LIST)
    assert agents == ["build", "Sisyphus - Ultraworker", "Oracle", "plan"]


def test_parse_rejects_help_text_rows():
    """The 2.x help text must not be scraped into bogus 'agents'."""
    assert _parse_agent_list_text(HELP_TEXT_SAMPLE) == []


def test_parse_empty_and_missing_paren_are_empty():
    assert _parse_agent_list_text("") == []
    assert _parse_agent_list_text(None) == []
    assert _parse_agent_list_text("just some words\nno parens here\n") == []


# ---------------------------------------------------------------------------
# OMO detection
# ---------------------------------------------------------------------------

def test_omo_detected_for_slim_ids():
    """JSON path (2.x) returns bare ids."""
    assert _omo_installed(["build", "orchestrator", "observer"]) is True
    assert _omo_installed(["oracle", "librarian", "explorer", "designer", "fixer"]) is True


def test_omo_detected_for_legacy_decorated_names():
    """Text path (1.x) returns decorated names."""
    assert _omo_installed(["build", "Sisyphus - Ultraworker", "Oracle"]) is True


def test_omo_not_detected_without_omo_agents():
    assert _omo_installed(["build", "plan", "summary"]) is False
    assert _omo_installed([]) is False


def test_omo_rejects_help_text_scrape():
    """Regression: help text must not be read as an installed OMO harness."""
    assert _omo_installed(_parse_agent_list_text(HELP_TEXT_SAMPLE)) is False


# ---------------------------------------------------------------------------
# Live JSON path (opencode 2.x) — skips when the CLI is unavailable
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_query_agents_json_parses_full_output():
    """`opencode debug agents` JSON must parse despite exceeding the pipe limit.

    On opencode 2.0.20 the payload is ~360 KB; capturing it via a pipe truncates
    at 327680 bytes and breaks json.loads(). Reading from a file returns the
    full document. If the payload is smaller than the pipe limit this test still
    passes — it only asserts the parser works, not which transport was needed.
    """
    if not check_opencode_requirements():
        pytest.skip("opencode CLI not found on PATH")
    agents = _query_agents_json(["opencode", "debug", "agents"], 30)
    assert isinstance(agents, list)
    assert agents, "expected at least one agent from the live install"
    assert "build" in agents, "the built-in `build` agent should always be present"


@pytest.mark.integration
def test_list_agents_live():
    if not check_opencode_requirements():
        pytest.skip("opencode CLI not found on PATH")
    agents = _list_agents(30)
    assert agents, "expected a non-empty agent list from the live install"
