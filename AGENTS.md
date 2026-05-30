# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

Each skill is an orchestrator with pluggable steps. External integrations (issue tracker, PR tool, code review) are routed via the project's CLAUDE.md — no hardcoded dependencies.

## Documentation map

| File | Purpose |
|------|---------|
| [`ETHOS.md`](ETHOS.md) | Why praxis exists — values and principles that gate every skill, hook, and manifest; includes [Autonomy vs Convention](ETHOS.md#autonomy-vs-convention) boundary table |
| [`DESIGN.md`](DESIGN.md) | How hooks are built — structural-tokenization, session_id keying, compound-bash-cascade, ordering, and add-a-new-hook flow |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Skill ↔ hook ↔ manifest dependency graph — provider routing, hook index, multi-platform packaging |
| [`RUNTIME_CONSTRAINTS.md`](RUNTIME_CONSTRAINTS.md) | Fixed Claude Code runtime limits every skill must respect |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Skill and hook contribution conventions, live-runtime verification gate |

## Prerequisites

| Tier | What works | Dependencies |
|------|-----------|--------------|
| **Standalone** | recover-sessions, strike / strikes / reset-strikes | `gh` CLI, `jq` (for strike skills) |
| **Enhanced** | + retrospect, codex-review-wrap | + oh-my-claudecode |
| **Full** | + all cmux-* skills | + cmux |
| **Multi-provider** | + codex/gemini routing in cmux-* | + codex-cli, gemini-cli |

## Skills (15)

> **Invocation**: praxis entries are *skills*, not subagents. Always call them
> via `Skill(skill="praxis:<name>")` — `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found`. See [RUNTIME_CONSTRAINTS.md §3](RUNTIME_CONSTRAINTS.md)
> for the full rationale and Agent-vs-Skill mapping table.

### Discovery

| Skill | Purpose |
|-------|---------|
| `using-praxis` | Onboarding entry point — maps scenarios to the right skill for new praxis users |
| `writing-praxis-skill` | Guide for authoring a new SKILL.md — template, SRP, trigger keyword design, frontmatter conventions |

### Development

| Skill | Purpose |
|-------|---------|
| `retrospect` | Session retrospect — find friction root causes, propose improvements |
| `codex-review-wrap` | Worktree-aware wrapper for `/codex:review` — forces explicit target selection, premise-verification gate, flip detection across rounds |

### Discipline

| Skill | Purpose |
|-------|---------|
| `bypass-review` | Review bypass-telemetry event logs written by the bypass-telemetry hook — aggregate and inspect JSONL records |
| `strike` | Declare a rule violation — session-scoped counter, escalating signal (1진 warning → 2진 review → 3진 Stop-hook block) |
| `strikes` | Show current strike count + recorded violation reasons for the active session |
| `reset-strikes` | Reset the session strike counter to 0 after a 3진 block (required to unblock responses) |

### Session Management

| Skill | Purpose |
|-------|---------|
| `cmux-save-sessions` | Save cmux session list as JSON snapshot |
| `cmux-resume-sessions` | Restore cmux workspaces from JSON snapshot |
| `cmux-recover-sessions` | Bulk recover sessions after crash (cmux backend) |
| `recover-sessions` | Bulk recover sessions after power loss (tmux backend) |
| `cmux-session-manager` | Daily session lifecycle — status dashboard, cleanup, reorganize |
| `cmux-delegate` | Delegate a task to an independent session with auto-collected context |
| `cmux-browser` | Browser automation E2E testing via cmux browser CLI — SPA hydration wait included |

## Hooks

Praxis ships a PreToolUse / PostToolUse / Stop / UserPromptSubmit hook suite
that structurally enforces the rules captured in [`ETHOS.md`](ETHOS.md).
See [`DESIGN.md`](DESIGN.md) for the shared design contracts (structural
tokenization, fail-open, session_id keying, compound-bash cascade) and
[`ARCHITECTURE.md → Hook index`](ARCHITECTURE.md#hook-index) for the full
list. Per-hook specs live at [`hooks/<role>/<name>/spec.md`](hooks/); the
[`docs/hook/INDEX.md`](docs/hook/INDEX.md) index links to them.

Hooks support host-aware filtering via an optional `hosts` field on each hook
entry in `hooks/manifest.json`. When `hosts` is absent the hook is included for
all platforms (default). When present it must be an array of host identifiers
(`"claude"`, `"codex"`, etc.) — the hook is written only to the platform whose
`host_id` in `manifests/platforms/<name>.json` appears in that list. The build
script (`scripts/build-plugin-manifests.py`) reads this field and writes a
platform-specific `hooks.json` for each platform under its plugin directory.
Each per-hook `spec.md` carries a `Supported hosts:` line that documents the
classification; `scripts/check-plugin-manifests.py` verifies that every hook
entry has a corresponding spec file.

## Provider Routing

Skills that dispatch external CLI workers (`cmux-delegate`) can route tasks
to multiple AI providers via a unified `--model` flag using
`<provider>:<model>` notation. Bare names (`opus`, `sonnet`, `haiku`) always
resolve to Claude — full backward compatibility. See
[`ARCHITECTURE.md → Provider Routing`](ARCHITECTURE.md#provider-routing) for
the CLI spec, model notation, task-type routing matrix, fallback policy, and
resolution algorithm.

## Multi-Platform Packaging

Runtime source (`skills/`, `hooks/`, `scripts/`) is shared; per-platform
manifests (Claude, Codex, Cursor, Gemini, OpenCode) are generated from
canonical metadata. See
[`ARCHITECTURE.md → Multi-Platform Packaging`](ARCHITECTURE.md#multi-platform-packaging)
for the source files, generated outputs, and add-a-new-platform flow.

## Local Development

### Canonical clone path

This repository should live at **`~/projects/praxis`**. The CLI tools shipped
by skills (e.g. `cmux-recover-sessions`, `claude-recover`, `cmux-save-sessions`,
`cmux-browser`) are symlinked from `~/.local/bin` into this clone, so patches
you commit here land in the version that actually runs at the shell. Keeping a
second clone under a legacy name risks `~/.local/bin` symlinks pointing at stale
code — a real failure mode previously hit during recover-sessions debugging.

After every `git pull` or worktree operation, run `./scripts/verify-symlinks.sh` to confirm all `~/.local/bin` entries still resolve to this clone.

### CLI tools (not skills)

These are shell wrappers installed via `scripts/install.sh` into `~/.local/bin`.
They are not AI skills — they have no `SKILL.md` and cannot be invoked as `/praxis:*`.

| Binary | Source | Purpose |
|--------|--------|---------|
| `cmux-browser` | `skills/cmux-browser/cmux-browser` | Pass-through for `cmux browser`; intercepts selector-missing errors and adds subcommand-specific usage hints |

### Install / refresh CLI symlinks

```bash
# From inside this clone:
./scripts/install.sh
```

Idempotent. Existing valid links are left alone; missing or drifted ones
are corrected. Re-run after pulls or after adding a new CLI script.

### Verify symlinks point at this clone

```bash
./scripts/verify-symlinks.sh
```

Exits non-zero on drift, so it can be wired into CI or a SessionStart hook
to catch "patch landed in the wrong clone" before it bites a future session.

## Issue & PR Conventions

- **Partial-scope PR**: When a PR addresses only a subset of an issue's body
  (e.g., "items 1-3 implemented, P-redesign deferred to follow-up"), use
  `Refs #N` (or `Addresses #N (items X, Y; Z deferred)`) in the PR body
  **instead of** `Closes #N`. GitHub's `Closes` keyword auto-closes the issue
  on merge regardless of deferred items inside the issue body, orphaning their
  tracking thread.
- **Full-scope PR**: `Closes #N` per global CLAUDE.md (Issue & PR Rules).
- **Agent prompts that delegate PR authorship**: do not hardcode `Closes #N` —
  instruct the agent to choose `Closes` vs `Refs` based on whether the PR
  addresses the issue's full scope.
