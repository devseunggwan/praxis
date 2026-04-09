# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

> **Note:** Skills may be added, removed, or restructured at any time without prior notice. This is a personal toolbox — not a stable API.

## Skills

### Workflow

| Skill | Description |
|-------|-------------|
| `turbo-setup` | Compound setup — issue + plan + branch + worktree + deps in one step |
| `turbo-implement` | Implementation orchestrator — mode selection (manual/ralph/autopilot/guided) and chaining |
| `turbo-completion` | Compound completion — verify + review + PR + merge + cleanup in one step (--verify-only for standalone verification) |

### Development

| Skill | Description |
|-------|-------------|
| `debug` | Systematic 4-phase debugging — root cause investigation before any fix attempt |
| `retrospect` | Session retrospect — scan conversation against CLAUDE.md, find friction root causes, propose and execute improvements |

### Session Management

| Skill | Description |
|-------|-------------|
| `recover-sessions` | Bulk recover Claude Code sessions after power loss (tmux backend) |
| `cmux-recover-sessions` | Bulk recover Claude Code sessions after crash or power loss (cmux backend) |
| `cmux-save-sessions` | Save cmux session list as a JSON snapshot for later restore |
| `cmux-resume-sessions` | Restore cmux workspaces from a saved JSON snapshot |
| `cmux-session-manager` | cmux session lifecycle automation — status, cleanup, init, report |
| `cmux-orchestrator` | Dispatch and supervise multiple Claude Code workers in cmux workspaces |

## Prerequisites

Most skills delegate to external agents or session managers. Install the dependencies that match your usage tier.

| Dependency | Required for | Install |
|------------|-------------|---------|
| **oh-my-claudecode** | Agent delegation (tracer, analyst, ultraqa, code-reviewer) | `omc install` |
| **cmux** | Session management skills (cmux-*) | `npm i -g @anthropic/cmux` |
| **gh CLI** | Issue/PR operations (turbo-*) | `brew install gh` |

### Compatibility Tiers

| Tier | What works | What you need |
|------|-----------|---------------|
| **Standalone** | turbo-setup, recover-sessions | `gh` CLI only |
| **Enhanced** | + turbo-implement, turbo-completion, debug, retrospect | + oh-my-claudecode |
| **Full** | + all cmux-* skills | + cmux |

> Skills in higher tiers fall back to manual/built-in alternatives when their dependencies are missing, but with reduced functionality.

## Installation

### Plugin (recommended)

```bash
/plugin marketplace add https://github.com/devseunggwan/praxis
/plugin install praxis
```

### Manual

```bash
git clone https://github.com/devseunggwan/praxis.git ~/projects/praxis
claude skill add ~/projects/praxis/skills/<skill-name>
```

## License

MIT License
