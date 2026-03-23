# hermes-opencode-plugin

OpenCode integration plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Dispatch software engineering tasks to [OpenCode](https://opencode.ai/)'s multi-agent harness ([oh-my-opencode](https://github.com/code-yeongyu/oh-my-openagent)).

## What This Does

Adds an `opencode` tool to Hermes that lets it delegate coding tasks to OpenCode's agent army:

```
Hermes (brain, memory, planning, user-facing)
  ├── read_file, terminal, web_search     ← quick stuff
  ├── delegate_task                        ← basic Hermes subagent
  └── opencode                             ← OMO meta-subagent
        └── Sisyphus orchestrates:
              ├── Hephaestus  (deep implementation)
              ├── Oracle      (architecture advice)
              ├── Librarian   (docs research)
              ├── Explore     (codebase grep)
              └── ...all running in parallel
```

One `opencode` call can trigger 5+ agents running in parallel across multiple models with LSP, AST-grep, and hash-anchored edits.

## Requirements

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed and running
- [OpenCode](https://opencode.ai/) CLI installed (`opencode` on PATH)
- [oh-my-opencode](https://github.com/code-yeongyu/oh-my-openagent) plugin configured in OpenCode

## Install

```bash
# Clone into Hermes plugins directory
git clone https://github.com/zaycruz/hermes-opencode-plugin.git ~/.hermes/plugins/opencode

# Install the skill (optional but recommended)
mkdir -p ~/.hermes/skills/software-development/opencode-driven-development
cp ~/.hermes/plugins/opencode/SKILL.md ~/.hermes/skills/software-development/opencode-driven-development/SKILL.md
```

Restart Hermes. The `opencode` tool will auto-register via the plugin system — no core modifications needed.

## Update

```bash
cd ~/.hermes/plugins/opencode && git pull
```

Your Hermes core stays untouched. `hermes update` won't break anything.

## Usage

Once installed, Hermes can use the tool in conversation:

```
User: "Add dark mode to the settings page"

Hermes (internally):
  → Checks memory for project conventions
  → Dispatches to OpenCode:
      opencode(action="run", prompt="Add dark mode...", directory="/project")
  → OMO's Sisyphus orchestrates implementation
  → Reviews results, updates memory
  → Reports back to user
```

### Actions

| Action | Description |
|--------|-------------|
| `run` | Fire-and-forget coding task. Returns structured results. |
| `session` | Send a message to an existing session (multi-turn work). |
| `status` | Check if OpenCode CLI is available. |
| `stop` | Stop the OpenCode background server. |

### Agent Selection

| Agent | Best For |
|-------|----------|
| *(default)* | Most tasks — Sisyphus auto-routes |
| `hephaestus` | Deep autonomous implementation |
| `prometheus` | Strategic planning before coding |
| `oracle` | Architecture decisions |
| `atlas` | Checklist-driven execution |

### Decision Framework

The included skill teaches Hermes when to use each tool:

| Task Size | Tool |
|-----------|------|
| < 3 tool calls | Do it yourself (`read_file`, `terminal`) |
| Basic subtask | `delegate_task` (Hermes subagent) |
| Real engineering | `opencode` (OMO meta-subagent) |

## Smoke Tests

10 integration tests covering coding, automation, error handling, agent selection, and session continuity:

```bash
cd ~/.hermes/plugins/opencode

# Run all tests (takes ~15 minutes)
python -m pytest tests/test_opencode_smoke.py -v -x -n 0

# Run fast tests only (~10 seconds)
python -m pytest tests/test_opencode_smoke.py -v -k "status or missing_prompt or timeout" -n 0
```

### Test Results

| # | Test | What It Proves |
|---|------|---------------|
| 1 | `test_status_check` | CLI detection works |
| 2 | `test_missing_prompt_error` | Input validation, error handling |
| 3 | `test_create_single_file` | End-to-end file creation |
| 4 | `test_edit_existing_file` | Non-destructive editing |
| 5 | `test_multi_file_scaffold` | Package creation + test verification |
| 6 | `test_git_operations` | Non-coding agentic work (git) |
| 7 | `test_agent_selection_hephaestus` | Agent routing via --agent flag |
| 8 | `test_timeout_handling` | Graceful timeout degradation |
| 9 | `test_spec_to_code` | Context-aware coding from spec file |
| 10 | `test_session_continuity` | Multi-turn session with incremental changes |

## Architecture

```
~/.hermes/plugins/opencode/          ← this repo
    plugin.yaml                      ← Hermes plugin manifest
    __init__.py                      ← registers tool via PluginContext
    opencode_tool.py                 ← tool logic (self-contained)
    SKILL.md                         ← teaches Hermes how to use it
    tests/                           ← smoke tests

~/.hermes/hermes-agent/              ← untouched upstream
```

The plugin registers into Hermes's tool registry at startup via `PluginContext.register_tool()`. Plugin-registered tools automatically bypass the toolset filter, so no core modifications are needed.

## How It Works

1. Hermes calls `opencode(action="run", prompt="...", directory="/project")`
2. The tool spawns `opencode run --format json` as a subprocess
3. OpenCode + OMO's agents execute the task (Sisyphus, Hephaestus, etc.)
4. JSON event stream is parsed for: text output, tool calls, file diffs, session ID
5. Structured result returned to Hermes: status, files changed, tool count, summary

## Contributing

PRs welcome. The main areas for improvement:

- **Async dispatch** — Currently blocks the parent agent. Could use background tasks.
- **SDK integration** — Replace CLI subprocess with OpenCode's Node.js SDK for proper programmatic control.
- **Smarter parsing** — Extract more structured data from the event stream.

## License

MIT
