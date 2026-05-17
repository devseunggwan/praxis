# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

Each skill is an orchestrator with pluggable steps. External integrations (issue tracker, PR tool, code review) are routed via the project's CLAUDE.md — no hardcoded dependencies.

## Prerequisites

| Tier | What works | Dependencies |
|------|-----------|--------------|
| **Standalone** | recover-sessions, strike / strikes / reset-strikes | `gh` CLI, `jq` (for strike skills) |
| **Enhanced** | + retrospect | + oh-my-claudecode |
| **Full** | + all cmux-* skills | + cmux |
| **Multi-provider** | + codex/gemini routing in cmux-* | + codex-cli, gemini-cli |

## Skills (12)

> **Invocation**: praxis entries are *skills*, not subagents. Always call them
> via `Skill(skill="praxis:<name>")` — `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found`. See [RUNTIME_CONSTRAINTS.md §3](RUNTIME_CONSTRAINTS.md)
> for the full rationale and Agent-vs-Skill mapping table.

### Development

| Skill | Purpose |
|-------|---------|
| `retrospect` | Session retrospect — find friction root causes, propose improvements |
| `codex-review-wrap` | Worktree-aware wrapper for `/codex:review` — forces explicit target selection, premise-verification gate, flip detection across rounds |

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
| `cmux-browser` | Browser automation E2E testing via cmux browser CLI — SPA hydration wait included |

## Design Principles

- **CLAUDE.md is the interface**: no config files — project instructions define routing
- **SRP per skill**: each skill has one responsibility
- **Discipline over convenience**: Iron Laws gate each phase, no skipping

## Provider Routing

Skills that dispatch external CLI workers (`cmux-delegate`) can route tasks to multiple AI providers. When only `claude` is installed, the system behaves exactly as before — no errors, no degradation.

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

## Hooks

Every hook ships with full spec in `docs/hook/<name>.md` — design rationale,
matrix of blocked vs. passed commands, response JSON, parsing
guarantees, fail-safe paths, and test summary. CLAUDE.md only carries the
index; consult the per-hook file before editing.

Design contract shared by all hooks:

- **Spec defines, hook enforces.** Each hook is the structural enforcement of
  a rule that already exists in `CLAUDE.md` or a memory entry. Memory-based
  feedback alone has historically failed (≥5 recurrences) — hooks replace the
  memo when the pattern proves recurrent.
- **Structural tokenization, not regex.** `hooks/_hook_utils.py`
  (`safe_tokenize` → `iter_command_starts` → `strip_prefix`) is the shared
  primitive. Quoted strings, comments, env prefixes, wrapper commands, and
  shell control-flow keywords are handled consistently across all Bash hooks.
- **Fail-open on infrastructure errors.** Missing `jq` / `python3`, malformed
  JSON stdin, unreadable transcript, unknown tool name → exit 0. The hook
  never breaks Claude Code; it only nudges.
- **Session state via `session_id`.** Per-session memory (intent flags,
  DESCRIBE history) keys on the payload's `session_id` field. PPID is a
  back-compat fallback for direct CLI / test invocation only.
- **No agent-attachable bypass for high-stakes gates.** `pre-merge-approval-gate`
  intentionally has no marker; `completion-verify` and `retrospect-mix-check`
  same. Bypass marker (`# side-effect:ack`, `# title-length:ack`) exists only
  where the false-positive cost outweighs the silent-bypass risk.
- **Compound-Bash cascade advisory (issue #229).** When a PreToolUse(Bash)
  hook rejects (block) or asks-and-may-deny a compound command (`&&`, `||`,
  `;`, `|`, newline) containing a state-changing step (`> file`, `<<EOF >`,
  `mkdir`, `tee`, `cp`/`mv`/`rm`/`touch`, `curl -o`, `wget -O`), every hook
  appends the shared `_hook_utils.compound_cascade_hint(command)` text to
  its block/ask message. The advisory clarifies that bash never executed
  ANY part of the rejected command — files the redirect/mkdir/download
  would have created do NOT exist on disk — so the agent should not retry
  the second half expecting the first half to have landed. Single-command
  rejections receive no suffix (no cascade to warn about).

### Hook index

| Hook | Event | Purpose | Spec |
|------|-------|---------|------|
| `block-gh-state-all` | PreToolUse | Hard-block invalid `gh search ... --state all` flag combo | [docs/hook/block-gh-state-all.md](docs/hook/block-gh-state-all.md) |
| `gh-flag-verify` | PreToolUse | Block `gh <subcmd>` calls with flags not in the subcommand's accepted set | [docs/hook/gh-flag-verify.md](docs/hook/gh-flag-verify.md) |
| `cli-flag-incompat-advisory` | PreToolUse | Advisory nudge for known mode-incompatible flag combos in other CLIs (`git merge-tree --name-only` 3-arg form, `kubectl --use-protocol-buffers`) — issue #248 | [docs/hook/cli-flag-incompat-advisory.md](docs/hook/cli-flag-incompat-advisory.md) |
| `block-ask-end-option` | PreToolUse | Block `AskUserQuestion` options carrying end-option markers when the most recent user message has no stop signal (strict default; advisory opt-out via `PRAXIS_ASK_END_ADVISORY=1`) | [docs/hook/block-ask-end-option.md](docs/hook/block-ask-end-option.md) |
| `side-effect-scan` | PreToolUse | Ask before commands with collateral side effects (`git commit/push`, `gh pr merge/create`, `kubectl apply`) | [docs/hook/side-effect-scan.md](docs/hook/side-effect-scan.md) |
| `memory-hint` | PreToolUse | Surface hookable memory entries by keyword at decision-construction time (advisory, never blocks) | [docs/hook/memory-hint.md](docs/hook/memory-hint.md) |
| `codex-review-route` | UserPromptSubmit | Warn when `/codex:review` runs in a multi-worktree repo (cwd mismatch risk) | [docs/hook/codex-review-route.md](docs/hook/codex-review-route.md) |
| `builtin-task-postuse` | PostToolUse | Correct upstream "agent spawn" false positives on `TaskCreate` / `TaskUpdate` / etc. | [docs/hook/builtin-task-postuse.md](docs/hook/builtin-task-postuse.md) |
| `completion-verify` | Stop | Block "done / 완료" claims without same-turn Bash verification evidence pasted into the message | [docs/hook/completion-verify.md](docs/hook/completion-verify.md) |
| `retrospect-mix-check` | Stop | Block retrospect Stage 3 outputs that default `tool` / `workflow` / `spec-gap` findings to memory-only | [docs/hook/retrospect-mix-check.md](docs/hook/retrospect-mix-check.md) |
| `external-write-falsify-check` (opt-in) | PreToolUse | Warn before posting hypothesis-stage text to PR / issue / Slack / Notion; also detects author-exempt unverified identifiers in mapping tables and code blocks (issue #183) | [docs/hook/external-write-falsify-check.md](docs/hook/external-write-falsify-check.md) |
| `commit-title-length-check` | PreToolUse | Ask when `git commit` title exceeds 50 chars (configurable via `CLAUDE_COMMIT_TITLE_MAX`) | [docs/hook/commit-title-length-check.md](docs/hook/commit-title-length-check.md) |
| `pre-merge-approval-gate` | PreToolUse | Surface per-PR approval prompt for `gh pr merge` in direct sessions (background agents pass) | [docs/hook/pre-merge-approval-gate.md](docs/hook/pre-merge-approval-gate.md) |
| `cross-boundary-preflight` | PreToolUse | Block heredoc body in `gh pr/issue create`; ask with four-point checklist on cross-repo `--repo` writes | [docs/hook/cross-boundary-preflight.md](docs/hook/cross-boundary-preflight.md) |
| `cross-repo-worktree-preflight` | PreToolUse | Ask before `git worktree remove <abs-path>` when the path is not registered in cwd repo's worktree list (issue #246) | [docs/hook/cross-repo-worktree-preflight.md](docs/hook/cross-repo-worktree-preflight.md) |
| `session-intent` | UserPromptSubmit + PreToolUse | Gate read-intent → mutation-pivot session drift on `gh` mutating commands | [docs/hook/session-intent.md](docs/hook/session-intent.md) |
| `trino-describe-first` | PreToolUse + PostToolUse | Require `DESCRIBE <table>` before Trino MCP query references that table | [docs/hook/trino-describe-first.md](docs/hook/trino-describe-first.md) |
| `pre-edit-protected-branch-guard` | PreToolUse | Block Edit/Write/NotebookEdit on protected branches (main/dev/prod/master) when dirty and target not already in dirty diff, or when clean tree and recent commits show `(#NNN)` PR-workflow signal (issue #231) | [docs/hook/pre-edit-protected-branch-guard.md](docs/hook/pre-edit-protected-branch-guard.md) |
| `pre-edit-md-escape-advisory` | PreToolUse(Edit) + PostToolUse(Read) | Advisory nudge when Edit on a `.md` file carries escape-sensitive tokens (`\|`, `\[`, `\]`, HTML entities) without a recorded Read in this session — Obsidian wikilink / HTML entity format mis-recall (issue #230) | [docs/hook/pre-edit-md-escape-advisory.md](docs/hook/pre-edit-md-escape-advisory.md) |
| `external-api-literal-trigger` | PreToolUse | Advisory nudge when ALL_CAPS enum candidates or 3-part SQL identifiers are written without prior retrieval verification (issue #202) | [docs/hook/external-api-literal-trigger.md](docs/hook/external-api-literal-trigger.md) |
| `block-manufactured-action-menu` | PreToolUse | Warn (advisory) or block (strict) when AskUserQuestion surfaces a "shall we proceed?" menu after the user already issued a command-intent signal | [docs/hook/block-manufactured-action-menu.md](docs/hook/block-manufactured-action-menu.md) |
| `output-block-falsify-advisory` | PreToolUse | Advisory nudge to run output-block falsification gate before surfacing `(Recommended)` options or bulk-action commands (issue #221) | [docs/hook/output-block-falsify-advisory.md](docs/hook/output-block-falsify-advisory.md) |
| `pre-gh-pr-create-dedup-gate` | PreToolUse | Run `gh pr list --search` against the resolved target repo before `gh pr create`; surface artifact unconditionally to stderr, hard-block on repo-resolution / gh-call failure (issue #234) | [docs/hook/pre-gh-pr-create-dedup-gate.md](docs/hook/pre-gh-pr-create-dedup-gate.md) |
| `advisory-wrapper-signature-verify` | PreToolUse | Advisory nudge to verify wrapped function signatures before writing wrapper/client code with delegation patterns (issue #235) | [docs/hook/advisory-wrapper-signature-verify.md](docs/hook/advisory-wrapper-signature-verify.md) |
| `block-pr-without-caller-evidence` | PreToolUse | Block `gh pr create` / `gh pr new` unless the effective PR body contains a `Caller chain verified:` line (closes stdin / missing-file / TOCTOU bypasses; issue #158) | [docs/hook/block-pr-without-caller-evidence.md](docs/hook/block-pr-without-caller-evidence.md) |
| `verify-commit-flag-override` | PreToolUse | Deny `git commit` invocations that override hooks / signing / hook path / template (`--no-verify`, `--no-gpg-sign`, `-S`, `-c commit.gpgsign=`, `-c core.hooksPath=`, `-c commit.template=`) without env verification; opt-out via `PRAXIS_SKIP_COMMIT_FLAG_CHECK=1` (issue #184) | [docs/hook/verify-commit-flag-override.md](docs/hook/verify-commit-flag-override.md) |
| `strike-counter` | SessionStart + UserPromptSubmit + Stop | Session-scoped three-strike discipline — emits 1/2-strike reminder context, hard-blocks at 3, requires non-empty reflection file before reset; state under `${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/strikes/` (issue #126) | [docs/hook/strike-counter.md](docs/hook/strike-counter.md) |

### Hook ordering and precedence

- PreToolUse hooks run **in parallel**. Decision precedence is
  `deny > defer > ask > allow`. Order in `hooks.json` is presentational.
- Stop hooks run **sequentially in array order**:
  `completion-verify` → `retrospect-mix-check` → `strike-counter stop`.
  Each gate is independent; first `decision: block` wins, fix it and re-run.
- PostToolUse hooks run **sequentially**; corrective `additionalContext`
  emissions are additive, not exclusive.

### Adding a new hook

1. Survey ≥2 sibling implementations under `hooks/` for the convention
   (state-key naming, payload field access, exit-code semantics). See the
   `Sibling Convention Survey` rule in CLAUDE.md.
2. Write the hook + tests under `hooks/`, register in `hooks/hooks.json`.
3. Create `docs/hook/<name>.md` using an existing spec as the template
   (`Why this exists` / `What is blocked` / `Response` / `Parsing guarantees`
   / `Tests`).
4. Add a row to the index table above.
5. Run `./scripts/check-plugin-manifests.py` to confirm packaging is clean.

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
