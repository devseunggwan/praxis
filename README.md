# Praxis

A plugin that turns the workflow rules you already wrote in `CLAUDE.md` into things
that actually fire at the moment they are needed — a merge that stops while a blocking
review finding is still open, a "done" that will not go out without evidence behind it,
a worktree workflow that is not skipped because the change looked small. Skills you
invoke by name, and hooks that fire whether or not anyone remembers them. One runtime,
packaged for Claude Code, Codex, and Cursor.

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
# Register this repo as a marketplace (its root is .agents/plugins/marketplace.json)
codex marketplace add https://github.com/devseunggwan/praxis
codex plugin install praxis
```

Codex reads `.agents/plugins/marketplace.json` as the marketplace root and
`plugins/praxis/.codex-plugin/plugin.json` as the plugin root. The `skills/`,
`hooks/`, and `scripts/` directories inside `plugins/praxis/` are symlinks
into the repo-root runtime — there is no source duplication.

### Direct skill install (fallback)

When the plugin surface isn't available, Claude Code still loads personal
skills from `~/.claude/skills/<skill-name>/SKILL.md` (project skills from
`.claude/skills/`). Clone the repo and link the skill directories you want:

```bash
git clone https://github.com/devseunggwan/praxis.git ~/projects/praxis
mkdir -p ~/.claude/skills
ln -s ~/projects/praxis/skills/<skill-name> ~/.claude/skills/<skill-name>
```

Skills installed this way are invoked as `/<skill-name>` rather than
`/praxis:<skill-name>`, and the hooks are not installed — this path ships
skills only. Skills that call a bundled helper through `CLAUDE_PLUGIN_ROOT`
(`strike`, `spec-drift`, the `cmux-*` skills) need that variable exported to
the clone path, e.g. `export CLAUDE_PLUGIN_ROOT=~/projects/praxis`.

## Where to start

Three reading paths, each 3–4 steps. Time estimates are for a first read.

**Fix or add a hook** (~25 min)

1. [`ETHOS.md` → Hook Ethos](ETHOS.md#hook-ethos) — why a hook exists at all and what it may never do (5 min)
2. [`DESIGN.md` → Adding a new hook](DESIGN.md#adding-a-new-hook) — the shared contracts and the per-hook checklist (10 min)
3. The hook's own `hooks/<role>/<name>/spec.md`, found via [`docs/hook/INDEX.md`](docs/hook/INDEX.md) — what it blocks, passes, and how it fails open (5 min)
4. [`CONTRIBUTING.md` → Adding or modifying a hook](CONTRIBUTING.md#adding-or-modifying-a-hook) — registration, tests, and the runtime canary (5 min)

**Change a skill** (~15 min)

1. [`RUNTIME_CONSTRAINTS.md`](RUNTIME_CONSTRAINTS.md) — the fixed Claude Code limits a skill must fit inside (5 min)
2. [`skills/SKILL.md.tmpl`](skills/SKILL.md.tmpl) — the frontmatter and section skeleton to copy (2 min)
3. [`CONTRIBUTING.md` → Adding or modifying a skill](CONTRIBUTING.md#adding-or-modifying-a-skill) — the live-runtime verification gate (8 min)

**Change packaging or manifests** (~10 min)

1. [`ARCHITECTURE.md` → Multi-Platform Packaging](ARCHITECTURE.md#multi-platform-packaging) — canonical source, generated outputs, add-a-platform flow (6 min)
2. [`CONTRIBUTING.md` → Packaging](CONTRIBUTING.md#packaging) — which files are generated and how to regenerate them (4 min)

## Skills

Eighteen skills, grouped as Discovery, Development, Discipline, and Session Management.
The full table — trigger keywords, when to use each, example invocation — lives in
[`docs/skills.md`](docs/skills.md).

If you are new, `/praxis:using-praxis` maps situations onto the skill that handles each —
sessions lost to a crash, a broken rule you want on record, a review whose comments have
piled up — which is a shorter read than the full table. The three worth knowing by name
on day one:

| Skill | What it is for |
| ------- | ---------------- |
| `/praxis:using-praxis` | Finding the right skill when you don't know what exists yet |
| `/praxis:retrospect` | After a session that went badly — find the friction's root cause and act on it |
| `/praxis:merge-briefing` | Before merging — probe all three finding surfaces, then brief and ask |

Praxis also ships `bypass-review`, a shell wrapper with no `SKILL.md`. It is **not**
invocable as `/praxis:*`; it reads the review bypass-telemetry event logs. See
[CONTRIBUTING.md → Local development](CONTRIBUTING.md#local-development) for every
shipped CLI wrapper.

## Hooks

Hooks are the larger half of praxis: **98 hooks**, registered at 113 points across
`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `Stop`,
`SubagentStop`, `UserPromptSubmit`, and `SessionStart`. They run
without being invoked, so this section is the one to read before installing — it is what
changes about your session.

They divide into four roles. Two of them block by default, and a third can be
promoted into blocking:

| Role | Count | What it does |
| ------ | ------- | -------------- |
| `preflight-gate` | 37 | Inspects a tool call before it runs and can deny it |
| `completion-verify` | 13 | Fires at `Stop` — can block a response that claims completion without evidence |
| `advisory-nudge` | 43 | Prints a warning to stderr and lets the call through — 18 read a `PRAXIS_*_STRICT` variable that makes them stop the call instead |
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
rather than to a broken session. And most blocks arrive with their own way out: the
shared deny-message helper (`hooks/_lib/block_message.py`) prints the hook's bypass
variable in the message, so you rarely have to go looking for it.

The complete list, with each hook's events, hosts, strict/bypass knobs, and the external
commands it may run, is the generated
[Hook Operating Matrix](docs/hook-operating-matrix.md). Per-hook specs live at
`hooks/<role>/<name>/spec.md`, indexed by [`docs/hook/INDEX.md`](docs/hook/INDEX.md),
and [DESIGN.md → Hook Design Contracts](DESIGN.md#hook-design-contracts) covers the
contracts every hook follows.

## Turning it off

A hook that blocks something you meant to do is not a wall. There are three levers.

**One gate.** 60 of the 98 hooks declare an opt-out or tuning variable. Which variable
belongs to which hook, and what setting it actually does to that hook, is the table in
[`docs/bypass-vars.md`](docs/bypass-vars.md); the generated
[Hook Operating Matrix](docs/hook-operating-matrix.md) carries the same mapping with each
hook's default alongside it. Read the row before setting the variable — the hooks differ
from each other, which is why this section points at the table instead of summarizing it.

**Set it where Claude Code can see it** — its own environment, before the session starts:

```bash
export PRAXIS_HOOK_BYPASS_SKILL_GATE=1   # <one-line reason>
```

An assignment written in front of the command (`VAR=1 git …`) does **not** work. Hooks
read `os.environ` of their own process, which never sees a variable scoped to the tool
call, so the gate blocks exactly as before. Use the shell export above, or the `env`
block in your `settings.json`.

**All of it.** On a plugin install, `claude plugin disable praxis` (or `/plugin` in the
session) switches the whole plugin off — skills and hooks together, since both are
declared in one manifest and `disable` has no hook-only option. To keep the skills and
stop a gate, use the opt-out above instead. Only a manual install registers praxis hooks
in `settings.json` as separate entries; there, dropping them leaves the skills working.

## Prerequisites

Most skills delegate to external agents or session managers. Install the dependencies that match your usage tier.

| Dependency | Required for | Install |
| ------------ | ------------- | --------- |
| **gh CLI** | Standalone (`recover-sessions`), strike skills, PR/issue ops | `brew install gh` |
| **jq** | Strike skills (session-scoped counter parsing) | `brew install jq` |
| **oh-my-claudecode** | Agent delegation (tracer, analyst, critic, code-reviewer) | `omc install` |
| **cmux** | Session management skills (cmux-*) | Mac app installer |
| **codex-cli, gemini-cli** | Multi-provider routing in `cmux-delegate` | per upstream docs |

### Hook dependencies

Hooks fail open, so a missing component never breaks a session — the hooks that
key on it simply never fire, and nothing says so. `hooks/manifest.json` declares
those components per hook in its `requires` field (#1158); this table is the
reader's view of that field, and `scripts/check-plugin-manifests.py` Rule 27
checks the two against each other in both directions (#1332).

| Component | Hooks inert without it | Install |
| ----------- | ------------------------ | --------- |
| `cmux` | `model-routing-advisory` | Mac app installer (the Full tier below) |
| `codex-plugin` | `codex-review-route` | `/plugin marketplace add openai/codex-plugin-cc`, then `/plugin install codex@openai-codex` |
| `hookable-memory-store` | `memory-hint` | a memory directory whose entries carry `hookable:` frontmatter, located per `hooks/_lib/_memory_dir.py` (`PRAXIS_MEMORY_DIR` overrides) |
| `zsh` | `block-unmatched-glob` | `brew install zsh`, or the distro package |

`builtin-task-postuse` is the one hook whose premise is another plugin rather
than a component: it corrects an oh-my-claudecode `pre-tool-enforcer` false
positive and is registered for the Claude host only (`hosts`), so it carries no
`requires` row. None of these components is declared as a `plugin.json`
`dependencies` entry — the harness has no optional-dependency concept, so a
declaration would turn every tier below into a hard requirement; see
[ARCHITECTURE.md → Why `plugin.json` declares no `dependencies`](ARCHITECTURE.md#why-pluginjson-declares-no-dependencies).

### Compatibility Tiers

| Tier | What works | What you need |
| ------ | ----------- | --------------- |
| **Standalone** | recover-sessions, strike / strikes / reset-strikes, debt | `gh` CLI, `jq`; `recover-sessions` also needs `tmux`; `debt` needs only `git` |
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

To add a new platform, add a `manifests/platforms/<name>.json` file listing
its outputs, add its `host_id` to the `hosts` enum in
`hooks/manifest.schema.json` (a test asserts the enum and the platform set
are equal), and run the build script — no changes to skills, hooks, or
existing platforms required.

## Local Development

When you work from a clone rather than the plugin cache, the CLI wrappers
shipped by skills (`cmux-recover-sessions`, `claude-recover`,
`cmux-save-sessions`, …) are installed as `~/.local/bin` symlinks into
whichever clone ran `scripts/install.sh` — so a patch reaches the version that
runs at the shell only if it lands in that clone. One clone per machine keeps
the links honest; `verify-symlinks.sh` tells you when they are not.

```bash
# Install / refresh CLI symlinks (idempotent)
./scripts/install.sh

# Verify symlinks point at this clone (CI / SessionStart hook)
./scripts/verify-symlinks.sh
```

See [CONTRIBUTING.md → Local development](CONTRIBUTING.md#local-development) for
the full list of shipped CLI wrappers and drift-recovery rationale.

## Security & Privacy

- [SECURITY.md](SECURITY.md) — vulnerability reporting and supported versions
- [PRIVACY.md](PRIVACY.md) — what praxis reads, executes, and never transmits

## License

MIT License
