# Praxis

A plugin that turns the workflow rules you already wrote in `CLAUDE.md` into things
that actually fire at the moment they are needed — a merge that stops while a blocking
review finding is still open, a "done" that will not go out without evidence behind it,
a worktree workflow that is not skipped because the change looked small. Skills you
invoke by name, and hooks that fire whether or not anyone remembers them. One runtime,
packaged for Claude Code, Codex, Cursor, Gemini, and OpenCode.

> **Note:** Skills may be added, removed, or restructured at any time without prior notice. This is a personal toolbox — not a stable API.

## The name

*Praxis* (πρᾶξις) is theory carried into action — where a stated principle stops being a
statement and becomes something done. That is this repository's whole design, written in
[`ETHOS.md`](ETHOS.md) as **spec defines, hook enforces**: every hook here is the
structural enforcement of a rule that already existed as prose in a `CLAUDE.md` or a
memory entry, and exists precisely because the prose had already failed at the moment it
was needed. The skills sit on the same axis — `strike`, `debt`, `spec-drift`, and
`merge-briefing` all make an already-decided rule reachable at execution time rather
than deciding anything new.

The word carries none of that domain on its own, which is what the paragraph above is
for. It is also a crowded name: the Praxis API framework (Ruby), PraxisEMR, and several
unrelated npm and PyPI packages share it. None of them share a namespace with this
repository — the surfaces that resolve here are `devseunggwan/praxis`, the `praxis:`
skill prefix behind `/praxis:retrospect`, and the `PRAXIS_*` environment variables.

## Installation

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

## Skills

Eighteen skills, grouped as Discovery, Development, Discipline, and Session Management.
The full table — trigger keywords, when to use each, example invocation — lives in
[`docs/skills.md`](docs/skills.md).

If you are new, start with `/praxis:using-praxis`: it asks what you are trying to do and
routes you to the skill that fits, which is faster than reading the catalogue. The three
worth knowing by name on day one:

| Skill | What it is for |
| ------- | ---------------- |
| `/praxis:using-praxis` | Finding the right skill when you don't know what exists yet |
| `/praxis:retrospect` | After a session that went badly — find the friction's root cause and act on it |
| `/praxis:merge-briefing` | Before merging — probe all three finding surfaces, then brief and ask |

Praxis also ships `bypass-review`, a shell wrapper with no `SKILL.md`. It is **not**
invocable as `/praxis:*`; it reads the review bypass-telemetry event logs. See
[AGENTS.md → Local Development](AGENTS.md#local-development) for every shipped CLI
wrapper.

## Hooks

Hooks are the larger half of praxis: **90 hooks**, registered at 100 points across
`PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit`, and `SessionStart`. They run
without being invoked, so this section is the one to read before installing — it is what
changes about your session.

They divide into four roles. Two of them block by default, and a third can be
promoted into blocking:

| Role | Count | What it does |
| ------ | ------- | -------------- |
| `preflight-gate` | 35 | Inspects a tool call before it runs and can deny it |
| `completion-verify` | 12 | Fires at `Stop` — can block a response that claims completion without evidence |
| `advisory-nudge` | 38 | Prints a warning to stderr and lets the call through — until its `PRAXIS_*_STRICT` variable is set, which turns the same hook into a hard deny |
| `postuse-correction` | 5 | Reacts after a tool call — telemetry, follow-up signals |

Concretely, what a gate stops looks like this — `gh issue create` without a duplicate
search first (`block-gh-issue-create-without-dup-search`), an edit to a file while you
are standing on a protected branch (`pre-edit-protected-branch-guard`), a merge run from
the wrong worktree (`gh-merge-worktree-precondition`), `gh search --state all` which that
subcommand does not accept (`block-gh-state-all`), a foreground `sleep`-and-poll loop
(`foreground-poll-loop-guard`), a commit whose title breaks the repo's format
(`commit-title-format-check`).

Two properties are load-bearing. **Hooks fail open**: a missing `jq`, a malformed stdin
payload, an unreadable transcript — all exit 0, so a broken hook degrades to no hook
rather than to a broken session. And a blocking hook that has a bypass variable **prints
it in its own deny message** (`hooks/_lib/block_message.py`), so the way out arrives with
the block rather than having to be hunted for.

The complete list, with each hook's events, hosts, strict/bypass knobs, and the external
commands it may run, is the generated
[Hook Operating Matrix](docs/hook-operating-matrix.md). Per-hook specs live at
`hooks/<role>/<name>/spec.md`, indexed by [`docs/hook/INDEX.md`](docs/hook/INDEX.md);
[ARCHITECTURE.md → Hook index](ARCHITECTURE.md#hook-index) maps them to the component
graph, and [DESIGN.md → Hook Design Contracts](DESIGN.md#hook-design-contracts) covers
the contracts every hook follows.

## Turning it off

A hook that blocks something you meant to do is not a wall. There are three levers, from
narrowest to widest.

**Opt out of one gate.** 38 of the 90 hooks declare an opt-out variable — set it and
that gate either skips entirely or demotes itself to a warning, depending on the hook.
The form is the variable in front of the command it guards, with a reason:

```bash
PRAXIS_HOOK_BYPASS_SKILL_GATE=1 <command>   # <one-line reason>
```

Which variable belongs to which hook is the table in
[`docs/bypass-vars.md`](docs/bypass-vars.md). Opt-outs are recorded by the
`bypass-telemetry` hook, so `bypass-review` can later show you which gate you keep
routing around — usually a sign the gate is miscalibrated, not that you are
undisciplined.

**Escalate instead.** The opposite lever exists too: 20 hooks read a `PRAXIS_*_STRICT`
variable that promotes an advisory into a hard block. Defaults sit on the permissive
side of that line — an advisory hook stays advisory until you say otherwise.

**All of it.** On a plugin install, `claude plugin disable praxis` (or `/plugin` in the
session) switches the whole plugin off — skills and hooks together, since both are
declared in one manifest and `disable` has no hook-only option. If what you want is the
gates gone but the `/praxis:*` skills kept, that is the first two levers above, not this
one. Only a manual install registers praxis hooks in `settings.json` as separate
entries; there, dropping them leaves the skills working.

## Prerequisites

Most skills delegate to external agents or session managers. Install the dependencies that match your usage tier.

| Dependency | Required for | Install |
| ------------ | ------------- | --------- |
| **gh CLI** | Standalone (`recover-sessions`), strike skills, PR/issue ops | `brew install gh` |
| **jq** | Strike skills (session-scoped counter parsing) | `brew install jq` |
| **oh-my-claudecode** | Agent delegation (tracer, analyst, ultraqa, code-reviewer) | `omc install` |
| **cmux** | Session management skills (cmux-*) | Mac app installer |
| **codex-cli, gemini-cli** | Multi-provider routing in `cmux-delegate` | per upstream docs |

### Compatibility Tiers

| Tier | What works | What you need |
| ------ | ----------- | --------------- |
| **Standalone** | recover-sessions, strike / strikes / reset-strikes, debt | `gh` CLI, `jq`; `debt` needs only `git` |
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

See [ARCHITECTURE.md → Provider Routing](ARCHITECTURE.md#provider-routing) for
the full task-type / complexity routing matrix and fallback policy.

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
skills (e.g. `cmux-recover-sessions`, `claude-recover`, `cmux-save-sessions`)
are symlinked from `~/.local/bin` into this clone, so patches you commit here
land in the version that actually runs at the shell.

```bash
# Install / refresh CLI symlinks (idempotent)
./scripts/install.sh

# Verify symlinks point at this clone (CI / SessionStart hook)
./scripts/verify-symlinks.sh
```

See [AGENTS.md → Local Development](AGENTS.md#local-development) for the full
list of shipped CLI wrappers and drift-recovery rationale.

## Security & Privacy

- [SECURITY.md](SECURITY.md) — vulnerability reporting and supported versions
- [PRIVACY.md](PRIVACY.md) — what praxis reads, executes, and never transmits

## License

MIT License
