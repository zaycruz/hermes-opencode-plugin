"""
OpenCode plugin for Hermes Agent.

Registers the `opencode` tool which dispatches coding tasks to OpenCode's
multi-agent harness (oh-my-opencode). Lives in ~/.hermes/plugins/opencode/
so it survives Hermes updates.
"""

import os
import sys

# Ensure the plugin directory is importable
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from opencode_tool import (
    OPENCODE_SCHEMA,
    check_opencode_requirements,
    opencode_handler,
)


def register(ctx):
    """Called by the Hermes plugin system at startup."""
    ctx.register_tool(
        name="opencode",
        toolset="opencode",
        schema=OPENCODE_SCHEMA,
        handler=opencode_handler,
        check_fn=check_opencode_requirements,
        requires_env=[],
        is_async=False,
        description=(
            "Dispatch coding tasks to OpenCode's multi-agent harness "
            "(Sisyphus, Hephaestus, Oracle, etc.)"
        ),
        emoji="\U0001f5a5\ufe0f",
    )
