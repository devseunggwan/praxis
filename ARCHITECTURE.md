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

> See [docs/hook/INDEX.md](docs/hook/INDEX.md) for the categorized index (preflight-gate / advisory-nudge / postuse-correction / completion-verify).

| Hook | Event | Purpose | Spec |
|------|-------|---------|------|
| `block-gh-state-all` | PreToolUse | Hard-block invalid `gh search ... --state all` flag combo | [docs/hook/block-gh-state-all.md](docs/hook/block-gh-state-all.md) |
| `gh-flag-verify` | PreToolUse | Block `gh <subcmd>` calls with flags not in the subcommand's accepted set | [docs/hook/gh-flag-verify.md](docs/hook/gh-flag-verify.md) |
| `gh-json-validator` | PreToolUse | Block `gh <subcmd> --json <fields>` calls whose field names are not in the subcommand's valid JSON projection (issue #391) | [docs/hook/gh-json-validator.md](docs/hook/gh-json-validator.md) |
| `gh-label-verify` | PreToolUse | Block `gh (issue\|pr) (create\|edit)` calls whose `--label` values are absent from the target repo's label set (issue #385) | [docs/hook/gh-label-verify.md](docs/hook/gh-label-verify.md) |
| `cli-flag-incompat-advisory` | PreToolUse | Advisory nudge for known mode-incompatible flag combos in other CLIs (`git merge-tree --name-only` 3-arg form, `kubectl --use-protocol-buffers`) — issue #248 | [docs/hook/cli-flag-incompat-advisory.md](docs/hook/cli-flag-incompat-advisory.md) |
| `block-ask-end-option` | PreToolUse | Block `AskUserQuestion` options carrying end-option markers when the most recent user message has no stop signal (strict default; advisory opt-out via `PRAXIS_ASK_END_ADVISORY=1`) | [docs/hook/block-ask-end-option.md](docs/hook/block-ask-end-option.md) |
| `side-effect-scan` | PreToolUse | Ask before commands with collateral side effects (`git commit/push`, `gh pr merge/create`, `kubectl apply`) | [docs/hook/side-effect-scan.md](docs/hook/side-effect-scan.md) |
| `memory-hint` | PreToolUse | Surface hookable memory entries by keyword at decision-construction time (advisory, never blocks) | [docs/hook/memory-hint.md](docs/hook/memory-hint.md) |
| `codex-review-route` | UserPromptSubmit | Warn when `/codex:review` runs in a multi-worktree repo (cwd mismatch risk) | [docs/hook/codex-review-route.md](docs/hook/codex-review-route.md) |
| `builtin-task-postuse` | PostToolUse | Correct upstream "agent spawn" false positives on `TaskCreate` / `TaskUpdate` / etc. | [docs/hook/builtin-task-postuse.md](docs/hook/builtin-task-postuse.md) |
| `completion-verify` | Stop | Block "done / 완료" claims without same-turn Bash verification evidence pasted into the message | [docs/hook/completion-verify.md](docs/hook/completion-verify.md) |
| `retrospect-mix-check` | Stop | Block retrospect Stage 3 outputs that default `tool` / `workflow` / `spec-gap` findings to memory-only | [docs/hook/retrospect-mix-check.md](docs/hook/retrospect-mix-check.md) |
| `completion-signal-gate` | Stop | Advisory nudge when completion-signal phrase (EN/KR) appears without an evidence-block in the same turn; also flags cross-plugin slash commands in wrong repo context (issue #392) | [docs/hook/completion-signal-gate.md](docs/hook/completion-signal-gate.md) |
| `external-write-falsify-check` (opt-in) | PreToolUse | Warn before posting hypothesis-stage text to PR / issue / Slack / Notion; also detects author-exempt unverified identifiers in mapping tables and code blocks (issue #183) | [docs/hook/external-write-falsify-check.md](docs/hook/external-write-falsify-check.md) |
| `commit-title-length-check` | PreToolUse | Ask when `git commit` title exceeds 50 chars (configurable via `CLAUDE_COMMIT_TITLE_MAX`) | [docs/hook/commit-title-length-check.md](docs/hook/commit-title-length-check.md) |
| `pre-merge-approval-gate` | PreToolUse | Surface per-PR approval prompt for `gh pr merge` in direct sessions (background agents pass) | [docs/hook/pre-merge-approval-gate.md](docs/hook/pre-merge-approval-gate.md) |
| `cross-boundary-preflight` | PreToolUse | Block heredoc body in `gh pr/issue create`; ask with four-point checklist on cross-repo `--repo` writes | [docs/hook/cross-boundary-preflight.md](docs/hook/cross-boundary-preflight.md) |
| `session-intent` | UserPromptSubmit + PreToolUse | Gate read-intent → mutation-pivot session drift on `gh` mutating commands | [docs/hook/session-intent.md](docs/hook/session-intent.md) |
| `pre-edit-protected-branch-guard` | PreToolUse | Block Edit/Write/NotebookEdit on protected branches (main/dev/prod/master) when dirty and target not already in dirty diff, or when clean tree and recent commits show `(#NNN)` PR-workflow signal (issue #231) | [docs/hook/pre-edit-protected-branch-guard.md](docs/hook/pre-edit-protected-branch-guard.md) |
| `pre-edit-md-escape-advisory` | PreToolUse(Edit) + PostToolUse(Read) | Advisory nudge when Edit on a `.md` file carries escape-sensitive tokens (`\|`, `\[`, `\]`, HTML entities) without a recorded Read in this session — Obsidian wikilink / HTML entity format mis-recall (issue #230) | [docs/hook/pre-edit-md-escape-advisory.md](docs/hook/pre-edit-md-escape-advisory.md) |
| `external-api-literal-trigger` | PreToolUse | Advisory nudge when ALL_CAPS enum candidates or 3-part SQL identifiers are written without prior retrieval verification (issue #202) | [docs/hook/external-api-literal-trigger.md](docs/hook/external-api-literal-trigger.md) |
| `block-manufactured-action-menu` | PreToolUse | Warn (advisory) or block (strict) when AskUserQuestion surfaces a manufactured menu — question-form ("shall we proceed?") or affirmative-form ("그대로 진행" / "execute now") — after the user already issued a command-intent signal (issue #377) | [docs/hook/block-manufactured-action-menu.md](docs/hook/block-manufactured-action-menu.md) |
| `output-block-falsify-advisory` | PreToolUse | Advisory nudge to run output-block falsification gate before surfacing `(Recommended)` options or bulk-action commands (issue #221) | [docs/hook/output-block-falsify-advisory.md](docs/hook/output-block-falsify-advisory.md) |
| `count-assertion-verify` | PreToolUse | Advisory nudge when `grep -c` with alternation (`\|` BRE or `|` ERE/PCRE) is run without per-arm verification; prevents citing inflated alternation counts (issue #277) | [docs/hook/count-assertion-verify.md](docs/hook/count-assertion-verify.md) |
| `pre-gh-pr-create-dedup-gate` | PreToolUse | Run `gh pr list --search` against the resolved target repo before `gh pr create`; surface artifact unconditionally to stderr, hard-block on repo-resolution / gh-call failure (issue #234) | [docs/hook/pre-gh-pr-create-dedup-gate.md](docs/hook/pre-gh-pr-create-dedup-gate.md) |
| `advisory-wrapper-signature-verify` | PreToolUse | Advisory nudge to verify wrapped function signatures before writing wrapper/client code with delegation patterns (issue #235) | [docs/hook/advisory-wrapper-signature-verify.md](docs/hook/advisory-wrapper-signature-verify.md) |
| `block-pr-without-caller-evidence` | PreToolUse | Block `gh pr create` / `gh pr new` unless the effective PR body contains a `Caller chain verified:` line (closes stdin / missing-file / TOCTOU bypasses; issue #158) | [docs/hook/block-pr-without-caller-evidence.md](docs/hook/block-pr-without-caller-evidence.md) |
| `block-pr-without-precommit-evidence` | PreToolUse | Block `gh pr create` / `gh pr new` unless the effective PR body contains one of three pre-commit marker lines (`Pre-commit verified:` / `Pre-commit: verified by CI (...)` / `Pre-commit: n/a (...)`); `--repo` does NOT bypass (issue #406) | [docs/hook/block-pr-without-precommit-evidence.md](docs/hook/block-pr-without-precommit-evidence.md) |
| `verify-commit-flag-override` | PreToolUse | Deny `git commit` invocations that override hooks / signing / hook path / template (`--no-verify`, `--no-gpg-sign`, `-S`, `-c commit.gpgsign=`, `-c core.hooksPath=`, `-c commit.template=`) without env verification; opt-out via `PRAXIS_SKIP_COMMIT_FLAG_CHECK=1` (issue #184) | [docs/hook/verify-commit-flag-override.md](docs/hook/verify-commit-flag-override.md) |
| `block-sciomc-finding-commit` | PreToolUse | Block content `git commit` after a sciomc/reviewer finding marker when no user-design consensus re-fetch happened in between; escape via `[user-approved]` token or `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` (issue #374) | [docs/hook/block-sciomc-finding-commit.md](docs/hook/block-sciomc-finding-commit.md) |
| `block-gh-issue-create-without-dup-search` | PreToolUse | Block `gh issue create` when no prior `gh search issues` / `issue list` overlaps the new issue's title keywords; escape via `[dup-checked]` token, personal-repo carve-out, or `CLAUDE_HOOK_BYPASS_DUP_GATE=1` (issue #374) | [docs/hook/block-gh-issue-create-without-dup-search.md](docs/hook/block-gh-issue-create-without-dup-search.md) |
| `strike-counter` | SessionStart + UserPromptSubmit + Stop | Session-scoped three-strike discipline — emits 1/2-strike reminder context, hard-blocks at 3, requires non-empty reflection file before reset; state under `${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/strikes/` (issue #126) | [docs/hook/strike-counter.md](docs/hook/strike-counter.md) |
| `external-write-path-existence-check` | PreToolUse | Advisory nudge when a `gh issue/pr` body file (via `--body-file`) contains markdown links referencing repo paths that do not exist on disk (phase 1: markdown links; phase 2 deferred: inline-code tokens; issue #324) | [docs/hook/external-write-path-existence-check.md](docs/hook/external-write-path-existence-check.md) |
| `path-probe-gate` | PreToolUse | Advisory nudge (opt-in strict: deny) when Write/Edit/NotebookEdit targets a nested worktree path whose immediate parent directory has not been enumerated this session — structural enforcement of the One-Probe-Before-Action Gate rule for the Write surface (issue #386) | [docs/hook/path-probe-gate.md](docs/hook/path-probe-gate.md) |

## Multi-Platform Packaging

Runtime source (`skills/`, `hooks/`, `scripts/`) is shared. Platform-specific
packaging is *generated* from canonical metadata, not hand-edited:

- `manifests/plugin.base.json` — shared metadata (name, description, author,
  repository, homepage, category, keywords). `VERSION` is the authoritative
  version string.
- `manifests/platforms/{claude,codex,cursor,gemini,opencode}.json` — per-platform output list.
- `scripts/build-plugin-manifests.py` — regenerate every artifact. Idempotent.
- `scripts/check-plugin-manifests.py` — CI drift gate. Verifies generated
  files match the source and that the Codex adapter shell's symlinks
  (`plugins/praxis/{skills,hooks,scripts}`) point at the repo root.

Platform manifests support two optional top-level fields:
- `excluded_hooks` — hook script names (without `.sh`) to omit when generating
  `filtered-hooks` outputs. Also serves as compatibility documentation.
- `excluded_skills` — reserved for future per-platform skill filtering.

Generated (committed) outputs:

| Path | Consumer |
|------|----------|
| `.claude-plugin/plugin.json` | Claude plugin root |
| `.claude-plugin/marketplace.json` | Claude marketplace catalog |
| `.agents/plugins/marketplace.json` | Codex marketplace root |
| `plugins/praxis/.codex-plugin/plugin.json` | Codex plugin root |
| `plugins/praxis/{skills,hooks,scripts}` | Symlinks into repo-root runtime |
| `.cursor-plugin/plugin.json` | Cursor IDE plugin root |
| `.cursor-plugin/hooks/hooks.json` | Cursor-compatible hooks (filtered) |
| `gemini-extension.json` | Gemini CLI extension catalog |
| `.opencode/plugin.json` | OpenCode plugin root |
| `.opencode/hooks/hooks.json` | OpenCode-compatible hooks (filtered) |

**Do not edit generated files directly.** Change `manifests/*.json` (or
`VERSION`) and re-run the build script. Run `./scripts/check-plugin-manifests.py`
before committing if you touched any packaging surface.

Adding a new platform = one file at `manifests/platforms/<name>.json` + one
build run. No skill, hook, or existing-platform changes required.
