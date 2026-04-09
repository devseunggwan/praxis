# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

Each skill is an orchestrator with pluggable steps. External integrations (issue tracker, PR tool, code review) are routed via the project's CLAUDE.md — no hardcoded dependencies.

## Prerequisites

| Tier | What works | Dependencies |
|------|-----------|--------------|
| **Standalone** | turbo-setup, recover-sessions | `gh` CLI |
| **Enhanced** | + turbo-implement, turbo-completion, debug, retrospect | + oh-my-claudecode |
| **Full** | + all cmux-* skills | + cmux |

## Skills (12)

### Workflow Lifecycle

| Skill | Purpose | Pluggable Steps |
|-------|---------|-----------------|
| `turbo-setup` | Compound setup — issue + plan + branch + worktree + deps in one pass | issue creation, planning |
| `turbo-implement` | Implementation orchestrator — selects execution mode and chains to delivery | ralph, autopilot (pluggable) |
| `turbo-completion` | Compound completion — verify + review + PR + merge + cleanup (--verify-only for standalone verification) | code review, PR creation |

### Development

| Skill | Purpose |
|-------|---------|
| `debug` | Systematic 4-phase debugging — root cause investigation before any fix |
| `retrospect` | Session retrospect — find friction root causes, propose improvements |

### Session Management

| Skill | Purpose |
|-------|---------|
| `cmux-save-sessions` | Save cmux session list as JSON snapshot |
| `cmux-resume-sessions` | Restore cmux workspaces from JSON snapshot |
| `cmux-recover-sessions` | Bulk recover sessions after crash (cmux backend) |
| `recover-sessions` | Bulk recover sessions after power loss (tmux backend) |
| `cmux-session-manager` | Daily session lifecycle — status dashboard, cleanup, reorganize |
| `cmux-delegate` | Delegate a task to an independent session with auto-collected context |
| `cmux-orchestrator` | Dispatch and supervise parallel Claude Code workers in cmux |

## Architecture

```
Project CLAUDE.md (routing config)
        │
        ▼
┌─ turbo-setup ────────────────────────────────────┐
│  issue(pluggable) → plan(pluggable) → branch     │
│  → worktree → deps                               │
└──────────────────────────────────────────────────┘
        │
        ▼
┌─ turbo-implement ────────────────────────────────┐
│  context → mode select → execute → chain         │
│  modes: manual | ralph | autopilot | guided      │
└──────────────────────────────────────────────────┘
        │
        ▼
┌─ turbo-completion ───────────────────────────────┐
│  Stage 0: mode detect (verify-only / full / merge)│
│  Full:  verify → review(pluggable) → PR(pluggable)│
│  Both:  compound → merge → cleanup → learn       │
└──────────────────────────────────────────────────┘
```

**Pluggable** = delegated to project's CLAUDE.md routing. Default: `gh` CLI.
**Built-in** = git operations, universal across all projects.

## Design Principles

- **Orchestrator + pluggable steps**: turbo-* stay as single skills, each step is swappable via CLAUDE.md routing
- **CLAUDE.md is the interface**: no config files — project instructions define routing
- **SRP per skill**: each skill has one responsibility, chaining connects them
- **Discipline over convenience**: Iron Laws gate each phase, no skipping
