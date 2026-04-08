# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

> **Note:** Skills may be added, removed, or restructured at any time without prior notice. This is a personal toolbox — not a stable API.

## Skills

### Workflow

| Skill | Description |
|-------|-------------|
| `turbo-setup` | Compound setup — issue + plan + branch + worktree + deps in one step |
| `turbo-implement` | Implementation orchestrator — mode selection (manual/ralph/autopilot/guided) and chaining |
| `turbo-deliver` | Compound delivery — auto-detects PR state for full pipeline or merge-only mode |
| `verify-completion` | Enforce verification evidence before any completion claim |

### Development

| Skill | Description |
|-------|-------------|
| `debug` | Systematic 4-phase debugging — root cause investigation before any fix attempt |
| `brainstorm` | Diamond Model brainstorming — diverge ideas, then converge with quantified evaluation |
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
