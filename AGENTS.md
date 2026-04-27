# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

Each skill is an orchestrator with pluggable steps. External integrations (issue tracker, PR tool, code review) are routed via the project's CLAUDE.md — no hardcoded dependencies.

## Prerequisites

| Tier | What works | Dependencies |
|------|-----------|--------------|
| **Standalone** | turbo-setup, recover-sessions, strike / strikes / reset-strikes | `gh` CLI, `jq` (for strike skills) |
| **Enhanced** | + turbo-implement, turbo-completion, debug, retrospect | + oh-my-claudecode |
| **Full** | + all cmux-* skills | + cmux |
| **Multi-provider** | + codex/gemini routing in cmux-*, turbo-implement | + codex-cli, gemini-cli |

## Skills (15)

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

### Discipline

| Skill | Purpose |
|-------|---------|
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
│  modes: manual | ralph | autopilot | guided | codex│
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

## Provider Routing

Skills that dispatch external CLI workers (`cmux-orchestrator`, `cmux-delegate`, `turbo-implement`) can route tasks to multiple AI providers. When only `claude` is installed, the system behaves exactly as before — no errors, no degradation.

### Provider CLI Spec

| Provider | Non-interactive command | Output format | Stdin prompt | Write access |
|----------|----------------------|---------------|-------------|-------------|
| `claude` | `cat $F \| claude --model {m} --output-format stream-json --permission-mode auto` | stream-json (JSONL) | `cat file \| claude` | Full |
| `codex` | `cat $F \| codex exec {m:+-m m} -o $RESULT_FILE` | stdout verbose logs + last message isolated in `$RESULT_FILE` (preferred); `--json` JSONL also supported | `cat file \| codex exec` | Sandbox-restricted — explicit fallback required |
| `gemini` | `gemini -p "$(cat $F)" --approval-mode yolo {m:+-m m}` | stream-json (`-o stream-json`) | via `-p` flag | Full |

All providers share the same completion sentinel: `; echo '===WORKER_DONE===' >> $LOG` appended after the CLI exits.

### Model Notation

Unified `--model` flag across all skills: `<provider>:<model>` or bare model name.

| Notation | Resolves to | CLI command |
|----------|-------------|-------------|
| `opus`, `sonnet`, `haiku` | `claude:{name}` | `claude --model {name}` |
| `claude` | Claude default model | `claude` |
| `claude:opus` | Claude Opus | `claude --model opus` |
| `codex` | Codex default model | `codex exec` |
| `codex:o3` | Codex with o3 | `codex exec -m o3` |
| `gemini` | Gemini default model | `gemini` |
| `gemini:flash` | Gemini Flash | `gemini -m flash` |

Bare names (`opus`, `sonnet`, `haiku`) always resolve to Claude — full backward compatibility.

### Task-Type Routing

Two-phase routing: task keywords select the provider, then complexity selects the model.

**Phase 1 — Task type to provider:**

| Task pattern | Provider | Rationale |
|-------------|----------|-----------|
| implement, fix, refactor, code generation | `codex` | Code-centric, fast execution |
| search, analyze, summarize, large context | `gemini` | Large context window, search integration |
| review, design, architecture, security, debug | `claude` | Reasoning depth, nuanced judgment |
| Default (unmatched) | `claude` | Safe default |

**Phase 2 — Complexity to model (claude only; codex/gemini use provider defaults):**

| Provider | Low | Medium | High |
|----------|-----|--------|------|
| `claude` | haiku | sonnet | opus |
| `codex` | (default) | (default) | (default or explicit) |
| `gemini` | (default) | (default) | (default or explicit) |

### Fallback Policy

1. **Pre-flight**: `command -v <cli>` before dispatch. If missing → fall back to `claude:sonnet` with warning.
2. **Runtime**: Worker failure → re-dispatch with `claude` as fallback provider.
3. **Graceful**: If only `claude` is installed, all routing resolves to claude. Original behavior preserved.

> **codex write detection**: After a codex worker completes, run `git status` to verify files were actually written. An empty diff after a code-generation task is a strong signal of sandbox write failure — trigger a claude fallback re-dispatch immediately.
> <!-- TODO: automate re-dispatch on empty git diff -->

### Provider Resolution Logic

Skills parse `--model` using this algorithm:

```
input = "--model" value

if input matches /^(codex|gemini)(?::(.+))?$/:
  provider = match[1]           # "codex" or "gemini"
  sub_model = match[2] || ""    # "" or "o3" or "flash" (colon stripped)
elif input in ["opus", "sonnet", "haiku"]:
  provider = "claude"
  sub_model = input
elif input matches /^claude(?::(.+))?$/:
  provider = "claude"
  sub_model = match[1] || ""
else:
  provider = "claude"
  sub_model = input
```

## PreToolUse gh search --state all Block

`hooks/block-gh-state-all.sh` intercepts every Bash tool call and hard-blocks
the invalid flag combination `gh search <subcmd> ... --state all`.

### Why this exists

`gh issue list` and `gh pr list` accept `--state all`, but `gh search issues`
/ `gh search prs` only accept `--state {open|closed}`. Conflating these
produces `invalid argument "all" for "--state" flag` at runtime. A feedback
memo (`feedback_verify_cli_flags.md`) was tried first but produced 5+
recurrences — structural enforcement replaced the memo.

### What is blocked

| Command | Action |
|---------|--------|
| `gh search issues "q" --state all` | **BLOCKED** (exit 2) |
| `gh search prs "q" --state=all` | **BLOCKED** (exit 2) |
| `gh search repos foo --limit 1 --state all` | **BLOCKED** (exit 2) |
| `gh issue list --state all` | **PASS** (legitimate usage) |
| `gh pr list --state all` | **PASS** (legitimate usage) |
| `gh search issues "q" --state open` | **PASS** |
| `gh search issues "q"` (no --state) | **PASS** |

### Workarounds when --state all is needed

- Omit `--state` entirely — `gh search` returns results regardless of state by default.
- Run two calls: `--state open` then `--state closed`, then merge results.

### Tests

```bash
bash hooks/test-block-gh-state-all.sh
```

Covers 14 cases: 4 block paths, 7 pass paths, non-Bash tool passthrough, and malformed stdin fail-open.

## PreToolUse Side-Effect Scan

`hooks/side-effect-scan.sh` intercepts every Bash tool call and flags commands
with collateral side effects before the agent runs them. Goal: prevent the
"primary-effect only" blind spot that has caused unintended merges, unintended
prod deploys, and stray auto-commits from CLIs that write to git internally.

### Detection categories

| Category | Trigger examples | Risk |
|----------|------------------|------|
| `git-commit` | `git commit`, `git merge`, `git rebase`, `git cherry-pick`, `git revert`, `iceberg-schema migrate`, `iceberg-schema promote`, `omc ralph` | Commits to the wrong branch or under the wrong author |
| `git-push` | `git push` | Remote published without intent |
| `gh-merge` | `gh pr merge`, `gh pr create`, `gh workflow run` | Unintended PR state change or workflow dispatch |
| `kubectl-apply` | `kubectl apply`, `kubectl delete`, `kubectl replace`, `kubectl patch` | Shared cluster mutation |

### Response

When any category matches, the hook emits:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "[category] reason..."
  }
}
```

Claude Code surfaces this as a permission prompt so the user can confirm or
redirect before the command executes.

### Prod emphasis

If any token on the command line matches `prod`, `production`,
`--env prod`/`--environment=prod`, the reason is prefixed with a
`⚠️  PROD scope` warning so the reviewer treats it with extra care.

### Opt-out marker

Known-intentional invocations can bypass the hook by embedding the literal
marker anywhere in the command:

```bash
git push origin main  # side-effect:ack
```

Use sparingly — the marker is a deliberate assertion that the side effect is
exactly what the current step requires.

### Parsing guarantees

Commands are tokenized with `shlex.shlex(..., posix=True, punctuation_chars=";|&")`
(not regex), so:

- Quotes (`"`/`'`) protect literal strings from being parsed as commands.
- Shell operators (`;`, `|`, `&`, `&&`, `||`) are always emitted as standalone
  tokens, even when typed without surrounding whitespace — `git push&&echo ok`
  and `echo x|git push origin main` both split cleanly and each segment is
  scanned for command starts.
- Env prefixes (`FOO=1 git push`), wrapper commands (`env`, `sudo`, `nice`,
  `time`, `stdbuf`, `ionice`), and their option flags are peeled from argv
  before matching — including both `--user admin` (separate value) and
  `--user=admin` (embedded), plus bare flags like `env -i`, `sudo -E`,
  `stdbuf -oL`. Nested wrappers (`sudo -E env GIT_TRACE=1 git push`) are
  unwrapped iteratively.
- Shell control-flow keywords (`if`, `then`, `elif`, `else`, `fi`, `while`,
  `until`, `do`, `done`, `for`, `case`, `esac`, `in`, `function`, `!`, `{`,
  `}`) are peeled from the start of each segment so `if true; then git push`,
  `for x in 1; do kubectl apply`, and `if git push; then ...` all reach the
  real executable.
- Newlines in the raw command are treated as command separators so multi-line
  Bash blocks (`echo prep\ngit push origin main` across two lines) get the
  second line scanned as a new segment.
- Subshells (`$(...)`) are opaque to shlex and **not** decomposed — an
  acknowledged limitation; rely on the author to use `# side-effect:ack`
  explicitly if they're running side-effecting code through `$()`.

### Tests

`tests/test_side_effect_scan.sh` covers 54 cases — positive detection across
all categories, prod emphasis, opt-out, shlex-aware evasions,
operator-adjacent one-liners, env/sudo prefix peeling, wrapper option flags
(long/short/equals/bare), nested wrappers, shell control-flow keywords,
newline-separated multi-line commands, GNU `time -f FORMAT` / `-o FILE`
arg-taking flags, non-Bash passthrough, malformed input. Run before editing
the hook:

```bash
./tests/test_side_effect_scan.sh
```

## PostToolUse Built-in Task Classification

`hooks/builtin-task-postuse.py` fires after any built-in task **management**
tool executes and emits a corrective context note so Claude is not misled by
upstream hook false positives.

### Why this exists

Claude Code ships two distinct sets of `Task*` tools with completely different
semantics:

| Tool | Role | Spawns subagent? |
|------|------|-----------------|
| `Task` | Agent spawner | **Yes** |
| `TaskCreate` | Create task list entry | No |
| `TaskUpdate` | Update task list entry | No |
| `TaskGet` | Read task list entry | No |
| `TaskList` | List task list entries | No |
| `TaskStop` | Cancel task list entry | No |
| `TaskOutput` | Read task output | No |

Some upstream hooks (e.g. OMC `pre-tool-enforcer`) conflate the management
tools with `Task` and emit misleading "agent spawn" signals for them. This
PostToolUse hook fires immediately after those tools execute and injects a
correction note — "no subagent was spawned, prior signals were false positives"
— so Claude's subsequent reasoning reflects the actual operation.

### Covered tools

`TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList`, `TaskStop`, `TaskOutput`

### Tests

`tests/test_builtin_task_postuse.sh` covers 18 cases: corrective output for
all 6 management tools, silent pass-through for `Task` / `Agent` / `Bash` /
`Edit` / `Write` / `Read` / `Skill`, and edge cases (empty stdin, malformed
JSON, missing tool field). Run before editing the hook:

```bash
./tests/test_builtin_task_postuse.sh
```

## Multi-Platform Packaging

Runtime source (`skills/`, `hooks/`, `scripts/`) is shared. Platform-specific
packaging is *generated* from canonical metadata, not hand-edited:

- `manifests/plugin.base.json` — shared metadata (name, description, author,
  repository, homepage, category, keywords). `VERSION` is the authoritative
  version string.
- `manifests/platforms/{claude,codex}.json` — per-platform output list.
- `scripts/build-plugin-manifests.py` — regenerate every artifact. Idempotent.
- `scripts/check-plugin-manifests.py` — CI drift gate. Verifies generated
  files match the source and that the Codex adapter shell's symlinks
  (`plugins/praxis/{skills,hooks,scripts}`) point at the repo root.

Generated (committed) outputs:

| Path | Consumer |
|------|----------|
| `.claude-plugin/plugin.json` | Claude plugin root |
| `.claude-plugin/marketplace.json` | Claude marketplace catalog |
| `.agents/plugins/marketplace.json` | Codex marketplace root |
| `plugins/praxis/.codex-plugin/plugin.json` | Codex plugin root |
| `plugins/praxis/{skills,hooks,scripts}` | Symlinks into repo-root runtime |

**Do not edit generated files directly.** Change `manifests/*.json` (or
`VERSION`) and re-run the build script. Run `./scripts/check-plugin-manifests.py`
before committing if you touched any packaging surface.

Adding a new platform = one file at `manifests/platforms/<name>.json` + one
build run. No skill, hook, or existing-platform changes required.

## Local Development

### Canonical clone path

This repository should live at **`~/projects/praxis`**. The CLI tools shipped
by skills (e.g. `cmux-recover-sessions`, `claude-recover`, `cmux-save-sessions`)
are symlinked from `~/.local/bin` into this clone, so patches you commit here
land in the version that actually runs at the shell. Keeping a second clone
under a legacy name risks `~/.local/bin` symlinks pointing at stale code —
a real failure mode previously hit during recover-sessions debugging.

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
