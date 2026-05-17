# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

> **Note:** Skills may be added, removed, or restructured at any time without prior notice. This is a personal toolbox — not a stable API.

## Skills

> **Invocation**: praxis entries are *skills*, not subagents. Always call them
> via `Skill(skill="praxis:<name>")`. `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found` — Agent and Skill resolve disjoint namespaces.
> See [RUNTIME_CONSTRAINTS.md §3](RUNTIME_CONSTRAINTS.md) for the mapping table.

### Development

| Skill | Description |
|-------|-------------|
| `retrospect` | Session retrospect — scan conversation against CLAUDE.md, find friction root causes, propose and execute improvements |
| `codex-review-wrap` | Worktree-aware wrapper for `/codex:review`; forces explicit target selection, premise-verification gate, and flip detection across review rounds |

### Discipline

| Skill | Description |
|-------|-------------|
| `strike` | Declare a rule violation — session-scoped counter, escalating signal (1진 warning → 2진 review → 3진 Stop-hook block) |
| `strikes` | Show current strike count + recorded violation reasons for the active session |
| `reset-strikes` | Reset the session strike counter to 0 after a 3진 block (required to unblock responses) |

### Session Management

| Skill | Description |
|-------|-------------|
| `recover-sessions` | Bulk recover Claude Code sessions after power loss (tmux backend) |
| `cmux-recover-sessions` | Bulk recover Claude Code sessions after crash or power loss (cmux backend) |
| `cmux-save-sessions` | Save cmux session list as a JSON snapshot for later restore |
| `cmux-resume-sessions` | Restore cmux workspaces from a saved JSON snapshot |
| `cmux-session-manager` | Daily session lifecycle — status dashboard, cleanup, reorganize |
| `cmux-delegate` | Delegate a task to an independent session with auto-collected context (supports multi-provider routing) |
| `cmux-browser` | Browser automation E2E testing via `cmux browser` CLI — SPA hydration wait included |

## Hooks

Praxis ships a set of PreToolUse / PostToolUse / Stop / UserPromptSubmit hooks
that structurally enforce rules captured in CLAUDE.md (e.g. side-effect
acknowledgment, completion-evidence requirement, protected-branch edit guard,
manufactured action-menu detection). Hooks fail-open on infrastructure errors
and never break Claude Code — they only nudge or block specific patterns.

See [AGENTS.md → Hooks](AGENTS.md#hooks) for the full index and per-hook
spec links under [`docs/hook/`](docs/hook/).

## Prerequisites

Most skills delegate to external agents or session managers. Install the dependencies that match your usage tier.

| Dependency | Required for | Install |
|------------|-------------|---------|
| **gh CLI** | Standalone (`recover-sessions`), strike skills, PR/issue ops | `brew install gh` |
| **jq** | Strike skills (session-scoped counter parsing) | `brew install jq` |
| **oh-my-claudecode** | Agent delegation (tracer, analyst, ultraqa, code-reviewer) | `omc install` |
| **cmux** | Session management skills (cmux-*) | `npm i -g @anthropic/cmux` |
| **codex-cli, gemini-cli** | Multi-provider routing in `cmux-delegate` | per upstream docs |

### Compatibility Tiers

| Tier | What works | What you need |
|------|-----------|---------------|
| **Standalone** | recover-sessions, strike / strikes / reset-strikes | `gh` CLI, `jq` |
| **Enhanced** | + retrospect, codex-review-wrap | + oh-my-claudecode |
| **Full** | + all cmux-* skills | + cmux |
| **Multi-provider** | + codex/gemini routing in cmux-delegate | + codex-cli, gemini-cli |

> Skills in higher tiers fall back to manual/built-in alternatives when their dependencies are missing, but with reduced functionality.

## Provider Routing

Skills that dispatch external CLI workers (`cmux-delegate`) can route tasks
to multiple AI providers via a unified `--model` flag using
`<provider>:<model>` notation (e.g. `claude:opus`, `codex:o3`,
`gemini:flash`). Bare names (`opus`, `sonnet`, `haiku`) always resolve to
Claude — full backward compatibility. When only `claude` is installed,
the system behaves exactly as before — no errors, no degradation.

See [AGENTS.md → Provider Routing](AGENTS.md#provider-routing) for the full
task-type / complexity routing matrix and fallback policy.

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

## Local Development

This repository should live at **`~/projects/praxis`**. CLI tools shipped by
skills (e.g. `cmux-recover-sessions`, `claude-recover`, `cmux-save-sessions`,
`cmux-browser`) are symlinked from `~/.local/bin` into this clone, so patches
you commit here land in the version that actually runs at the shell.

```bash
# Install / refresh CLI symlinks (idempotent)
./scripts/install.sh

# Verify symlinks point at this clone (CI / SessionStart hook)
./scripts/verify-symlinks.sh
```

See [AGENTS.md → Local Development](AGENTS.md#local-development) for the full
list of shipped CLI wrappers and drift-recovery rationale.

## License

MIT License
