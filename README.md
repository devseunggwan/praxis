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
| **Full** | + all cmux-* skills, + turbo-setup auto-opens cmux workspace after worktree creation | + cmux |

> Skills in higher tiers fall back to manual/built-in alternatives when their dependencies are missing, but with reduced functionality.

## Installation

Praxis ships a single runtime (`skills/`, `hooks/`, `scripts/`) with
platform-specific packaging adapters generated from a canonical source in
`manifests/`. Three install surfaces are supported.

### Claude Code — plugin (recommended)

```bash
/plugin marketplace add https://github.com/devseunggwan/praxis
/plugin install praxis
```

Claude Code reads `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`
directly from the repo root.

### Codex — marketplace + plugin

```bash
# Register the local marketplace (points at this repo's .agents/plugins/marketplace.json)
codex marketplace add https://github.com/devseunggwan/praxis
codex plugin install praxis
```

Codex reads `.agents/plugins/marketplace.json` as the marketplace root and
`plugins/praxis/.codex-plugin/plugin.json` as the plugin root. The `skills/`,
`hooks/`, and `scripts/` directories inside `plugins/praxis/` are symlinks
into the repo-root runtime — there is no source duplication.

### Direct skill install (fallback)

When the plugin surface isn't available:

```bash
git clone https://github.com/devseunggwan/praxis.git ~/projects/praxis
claude skill add ~/projects/praxis/skills/<skill-name>
```

## Packaging internals

Platform manifests are generated, not hand-edited. The canonical source is
`manifests/plugin.base.json` (common metadata) plus one file per platform
under `manifests/platforms/`.

```bash
# Regenerate every platform manifest + adapter shell symlinks
./scripts/build-plugin-manifests.py

# Verify committed manifests match the canonical source (CI / pre-merge)
./scripts/check-plugin-manifests.py
```

Generated artifacts are committed:

- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `.agents/plugins/marketplace.json`
- `plugins/praxis/.codex-plugin/plugin.json`
- `plugins/praxis/{skills,hooks,scripts}` (symlinks into repo root)

To add a new platform, drop a `manifests/platforms/<name>.json` file listing
its outputs and run the build script — no changes to skills, hooks, or
existing platforms required.

## License

MIT License
