# Claude Code Skills

Personal collection of Claude Code skills for development workflow automation.

> **Note:** Skills may be added, removed, or restructured at any time without prior notice. This is a personal toolbox — not a stable API.

## Skills

### Workflow

| Skill | Description |
|-------|-------------|
| `turbo-setup` | Compound setup — issue + plan + branch + worktree + deps in one step |
| `turbo-deliver` | Compound delivery — verify + review + PR + merge + cleanup + compounding in one step |
| `finish-branch` | Complete a development branch lifecycle — merge PR, clean up worktree and branch |
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

```bash
git clone https://github.com/devseunggwan/my-skills.git ~/projects/my-skills
```

Register as a Claude Code skill directory:

```bash
claude skill add ~/projects/my-skills/skills/<skill-name>
```

## License

MIT License
