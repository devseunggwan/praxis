# Architecture

The component graph of praxis — how skills, hooks, providers, and platform
manifests relate. Values come from [`ETHOS.md`](ETHOS.md); implementation
mechanisms come from [`DESIGN.md`](DESIGN.md). This file describes the
*wiring*.

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

## Hook index

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
