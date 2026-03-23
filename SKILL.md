---
name: opencode-driven-development
description: Use OpenCode as a meta-subagent for heavy coding work. Offload engineering tasks to OMO's multi-agent harness so Hermes stays focused on planning, memory, and orchestration.
version: 2.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [opencode, delegation, multi-agent, coding, orchestration, omo, subagent]
    related_skills: [subagent-driven-development, writing-plans, code-review]
---

# OpenCode-Driven Development

## The Mental Model

Hermes already has `delegate_task` for spawning subagents. But those are just Hermes clones with basic tools — one agent, one model, sequential execution.

**OpenCode is a meta-subagent.** One `opencode` call spawns an entire engineering team:

```
You (Hermes) — brain, memory, planning, user-facing
  │
  ├── read_file, terminal, web_search     ← do it yourself (quick stuff)
  ├── delegate_task                        ← simple Hermes subagent (basic subtasks)
  └── opencode                             ← OMO meta-subagent (real engineering)
        └── Sisyphus auto-orchestrates:
              ├── Hephaestus  (deep implementation, parallel)
              ├── Oracle      (architecture advice)
              ├── Librarian   (docs research, parallel)
              ├── Explore     (codebase grep, parallel)
              └── ...5+ agents running simultaneously
```

One opencode call can trigger dozens of parallel tool executions across multiple models. It has LSP, AST-grep, hash-anchored edits, background agents — things you don't have.

**This is how you lighten your cognitive load.** Don't manage 50 tool calls to implement a feature. Hand it off to OpenCode and focus on the bigger picture.

## The Decision Framework

Every task, ask yourself:

### Do it yourself (read_file, terminal, web_search)
- Reading a file to understand something
- Running a quick command
- Looking something up
- Answering a question
- Single-line fix you already know

### delegate_task (Hermes subagent)
- Simple focused subtask that needs basic file/terminal
- Tasks where you want to control the exact toolset
- Non-coding work (research, analysis, writing)

### opencode (OMO meta-subagent)
- **Multi-file implementation** — feature that touches 3+ files
- **Refactoring** — rename, restructure, move code across files
- **Bug fixing** — needs investigation, testing, verification
- **Test writing** — needs to run tests and iterate
- **Any task where you'd make 10+ tool calls** — offload it
- **Anything where parallel agents help** — OMO fires 5 agents at once
- **LSP-precision work** — workspace rename, find all references

**Rule of thumb:** If you'd need more than 3-4 tool calls, use opencode. The overhead of one opencode dispatch is cheaper than managing the complexity yourself.

## How OMO Amplifies You

When you dispatch to OpenCode, here's what happens inside:

1. **Sisyphus** receives your task, analyzes it
2. Breaks it into subtasks with dependency ordering
3. **Wave 1**: fires independent subtasks in parallel as background agents
   - Hephaestus implements component A
   - Librarian researches the API docs
   - Explore greps the codebase for patterns
4. **Wave 2**: dependent tasks fire when Wave 1 completes
5. Results aggregate back — Sisyphus synthesizes
6. You get structured output: text summary, file diffs, tool call count

**What you DON'T have to do:**
- Manage parallel execution
- Pick the right model for each subtask (OMO's category routing handles it)
- Handle LSP operations (rename, references, diagnostics)
- Deal with hash-anchored edits (zero stale-line errors)
- Retry failed subtasks (OMO has fallback chains)

## Dispatch Patterns

### Offload and Move On

The most common pattern. Hand off the work, move to the next thing:

```
opencode(
    action="run",
    prompt="Implement user authentication with JWT. Files: src/auth/, tests/auth/. Use bcrypt for passwords, 15min token expiry. Run pytest when done.",
    directory="/project"
)
```

While OpenCode's agents are working, you can:
- Update your todo list
- Check memory for the next task
- Communicate with the user
- Plan the next dispatch

### Parallel Offload

Multiple independent tasks — fire them all:

```
# These hit different parts of the codebase — safe to parallelize
opencode(action="run", prompt="Add rate limiting to API routes in src/api/", directory="/project")
opencode(action="run", prompt="Write migration for new user_preferences table", directory="/project")
opencode(action="run", prompt="Add Stripe webhook handler in src/webhooks/", directory="/project")
```

Each one spawns its own Sisyphus → parallel agents. You just kicked off potentially 15+ agents with 3 tool calls.

### Heavy Lift

For the big stuff — give Hephaestus full autonomy:

```
opencode(
    action="run",
    agent="hephaestus",
    prompt="Refactor the monolithic OrderService into separate services: OrderCreation, OrderFulfillment, OrderNotification. Maintain all existing tests. Add new tests for service boundaries.",
    directory="/project"
)
```

Hephaestus will explore the codebase, understand the patterns, plan the refactor, execute it, and verify tests — all autonomously.

### Scout Then Strike

Use OpenCode for recon, then for execution:

```
# First: get a plan from Prometheus
opencode(
    action="run",
    agent="prometheus",
    prompt="Analyze this codebase and create a plan for adding GraphQL support alongside the existing REST API."
)

# Review the plan, adjust if needed, then execute
opencode(
    action="run",
    prompt="Execute the GraphQL integration plan. [paste relevant parts or reference the plan file]"
)
```

### Iterative Session

For work that needs your feedback between rounds:

```
# Round 1
result = opencode(action="run", prompt="Implement the dashboard page per the wireframe in docs/dashboard.png", directory="/project")

# You review: looks good but needs responsive layout
opencode(
    action="session",
    session_id=result.session.id,
    prompt="Make the dashboard responsive. Mobile: stack cards vertically. Tablet: 2-column grid. Desktop: current 3-column layout."
)
```

## Injecting Your Context

You have memory. OpenCode doesn't. Bridge the gap:

```
opencode(
    action="run",
    prompt="""Fix the flaky test in tests/api/test_orders.py::test_concurrent_checkout.

    CONTEXT FROM MEMORY:
    - This project uses PostgreSQL with row-level locking for inventory
    - Previous fix attempt (2 weeks ago) tried adding retry logic but it masked the real issue
    - User prefers explicit locks over optimistic concurrency
    - Test environment uses SQLite (this may be the root cause)
    """,
    directory="/project"
)
```

**Before every dispatch, ask:** Do I know something from memory that would help OpenCode do this right the first time? If yes, include it.

## What You Do After Each Dispatch

1. **Check status** — `completed` or `error`?
2. **Read the summary** — what did it actually do?
3. **Verify file changes** — expected files modified?
4. **Update memory** — anything learned? conventions discovered? decisions made?
5. **Update todos** — mark complete, add follow-ups if needed
6. **Tell the user** — concise summary of what was done

## Integration with Hermes Systems

### Memory + OpenCode
- Inject project conventions into prompts → OpenCode codes your user's way
- After results, save new patterns/decisions to memory → future dispatches are smarter
- Memory compounds: each task teaches Hermes something that makes the next dispatch better

### Cron + OpenCode
- `every morning`: "Run full test suite in /project, report failures"
- `every friday`: "Check for outdated npm packages, create upgrade PR if safe"
- `every 2h`: "Check CI status, auto-fix lint errors on failing builds"

### Cross-Platform + OpenCode
- User on Telegram: "fix the login bug"
- You: dispatch to opencode, get results, reply on Telegram with summary
- User never knows the implementation details — they just see it's fixed

### delegate_task + OpenCode
You can even use both together:
- `delegate_task` for non-coding work (research, drafting docs, analysis)
- `opencode` for the actual engineering
- Both running in parallel, you coordinating

## Agent Selection Cheat Sheet

| Situation | Agent | Why |
|-----------|-------|-----|
| General feature work | *(default)* | Let Sisyphus auto-route |
| Complex multi-system change | `sisyphus` | Explicit orchestration with parallel agents |
| Big refactor / greenfield | `hephaestus` | Deep autonomous worker, doesn't need hand-holding |
| Need a plan before coding | `prometheus` | Interviews, identifies scope, builds plan |
| Architecture question | `oracle` | Technical advisor, won't start coding |
| Checklist of small tasks | `atlas` | Todo-driven, checks items off |

## Red Flags

- **Over-dispatching**: Don't use opencode to read a file. Use `read_file`.
- **Under-specifying**: "Fix the bug" → bad. "Fix the null pointer in src/api/users.py:47 when email is missing" → good.
- **Ignoring results**: Always read what came back. Don't fire-and-forget without checking.
- **Micromanaging**: Don't break a task into 20 tiny opencode calls. Give Sisyphus a meaty task and let it orchestrate.
- **Skipping memory**: If you know something relevant, inject it. OpenCode is stateless — you're its memory.
- **Forgetting verification**: Always include "run tests" or equivalent in the prompt.

## The Bottom Line

```
Small task (< 3 tool calls)  →  do it yourself
Medium task (basic subtask)  →  delegate_task
Heavy task (real engineering) →  opencode

You think. OpenCode builds.
You remember. OpenCode executes.
You plan. OpenCode parallelizes.
```
