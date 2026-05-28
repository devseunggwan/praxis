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
| `block-gh-state-all` | PreToolUse | Hard-block invalid `gh search ... --state all` flag combo | [hooks/preflight-gate/block-gh-state-all/spec.md](hooks/preflight-gate/block-gh-state-all/spec.md) |
| `gh-flag-verify` | PreToolUse | Block `gh <subcmd>` calls with flags not in the subcommand's accepted set | [hooks/preflight-gate/gh-flag-verify/spec.md](hooks/preflight-gate/gh-flag-verify/spec.md) |
| `gh-json-validator` | PreToolUse | Block `gh <subcmd> --json <fields>` calls whose field names are not in the subcommand's valid JSON projection (issue #391) | [hooks/preflight-gate/gh-json-validator/spec.md](hooks/preflight-gate/gh-json-validator/spec.md) |
| `gh-label-verify` | PreToolUse | Block `gh (issue\|pr) (create\|edit)` calls whose `--label` values are absent from the target repo's label set (issue #385) | [hooks/preflight-gate/gh-label-verify/spec.md](hooks/preflight-gate/gh-label-verify/spec.md) |
| `cli-flag-incompat-advisory` | PreToolUse | Advisory nudge for known mode-incompatible flag combos in other CLIs (`git merge-tree --name-only` 3-arg form, `kubectl --use-protocol-buffers`) — issue #248 | [hooks/advisory-nudge/cli-flag-incompat-advisory/spec.md](hooks/advisory-nudge/cli-flag-incompat-advisory/spec.md) |
| `inspection-chain-advisory` | PreToolUse | Advisory nudge when 2+ inspection-only commands are chained with `&&` (non-match exit silently drops downstream probes) — issue #469 | [hooks/advisory-nudge/inspection-chain-advisory/spec.md](hooks/advisory-nudge/inspection-chain-advisory/spec.md) |
| `destructive-bash-guard` | PreToolUse | Advisory (or strict-mode `ask`) before destructive bash (`rm -rf`, `sudo`/`doas`, `dd`, `mkfs`, `chmod -R 777`, block-device redirects, `git clean -f`/`reset --hard`, `find -delete`, `truncate -s 0`, `shred`, fork bomb) — issue #463 | [hooks/advisory-nudge/destructive-bash-guard/spec.md](hooks/advisory-nudge/destructive-bash-guard/spec.md) |
| `protected-paths-guard` | PreToolUse | Advisory (or strict-mode block) on Edit/Write/NotebookEdit calls targeting sensitive files (`.env`, private keys, `.ssh/`, `credentials`, `.netrc`, `.npmrc`) — issue #464 | [hooks/advisory-nudge/protected-paths-guard/spec.md](hooks/advisory-nudge/protected-paths-guard/spec.md) |
| `block-ask-end-option` | PreToolUse | Block `AskUserQuestion` options carrying end-option markers when the most recent user message has no stop signal (strict default; advisory opt-out via `PRAXIS_ASK_END_ADVISORY=1`) | [hooks/preflight-gate/block-ask-end-option/spec.md](hooks/preflight-gate/block-ask-end-option/spec.md) |
| `side-effect-scan` | PreToolUse | Ask before commands with collateral side effects (`git commit/push`, `gh pr merge/create`, `kubectl apply`) | [hooks/preflight-gate/side-effect-scan/spec.md](hooks/preflight-gate/side-effect-scan/spec.md) |
| `memory-hint` | PreToolUse | Surface hookable memory entries by keyword at decision-construction time (advisory, never blocks) | [hooks/advisory-nudge/memory-hint/spec.md](hooks/advisory-nudge/memory-hint/spec.md) |
| `codex-review-route` | UserPromptSubmit | Warn when `/codex:review` runs in a multi-worktree repo (cwd mismatch risk) | [hooks/advisory-nudge/codex-review-route/spec.md](hooks/advisory-nudge/codex-review-route/spec.md) |
| `postcompact-context` | UserPromptSubmit | Inject session_id / cwd / branch / active PR / strike state as `additionalContext` on the first prompt after a Claude Code compaction (transcript JSONL `isCompactSummary: true` scan, per-uuid dedup) — issue #472 | [hooks/advisory-nudge/postcompact-context/spec.md](hooks/advisory-nudge/postcompact-context/spec.md) |
| `builtin-task-postuse` | PostToolUse | Correct upstream "agent spawn" false positives on `TaskCreate` / `TaskUpdate` / etc. | [hooks/postuse-correction/builtin-task-postuse/spec.md](hooks/postuse-correction/builtin-task-postuse/spec.md) |
| `completion-verify` | Stop | Block "done / 완료" claims without same-turn Bash verification evidence pasted into the message | [hooks/completion-verify/completion-verify/spec.md](hooks/completion-verify/completion-verify/spec.md) |
| `retrospect-mix-check` | Stop | Block retrospect Stage 3 outputs that default `tool` / `workflow` / `spec-gap` findings to memory-only | [hooks/completion-verify/retrospect-mix-check/spec.md](hooks/completion-verify/retrospect-mix-check/spec.md) |
| `completion-signal-gate` | Stop | Advisory nudge when completion-signal phrase (EN/KR) appears without an evidence-block in the same turn; also flags cross-plugin slash commands in wrong repo context (issue #392) | [hooks/completion-verify/completion-signal-gate/spec.md](hooks/completion-verify/completion-signal-gate/spec.md) |
| `external-write-falsify-check` (opt-in) | PreToolUse | Warn before posting hypothesis-stage text to PR / issue / Slack / Notion; also detects author-exempt unverified identifiers in mapping tables and code blocks (issue #183) | [hooks/advisory-nudge/external-write-falsify-check/spec.md](hooks/advisory-nudge/external-write-falsify-check/spec.md) |
| `commit-title-length-check` | PreToolUse | Ask when `git commit` title exceeds 50 chars (configurable via `CLAUDE_COMMIT_TITLE_MAX`) | [hooks/preflight-gate/commit-title-length-check/spec.md](hooks/preflight-gate/commit-title-length-check/spec.md) |
| `commit-title-format-check` | PreToolUse | Block `git commit`, `gh pr create`, `gh issue create` when title does not match Conventional Commits format; advisory mode via `PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0` | [hooks/preflight-gate/commit-title-format-check/spec.md](hooks/preflight-gate/commit-title-format-check/spec.md) |
| `branch-name-check` | PreToolUse | Block branch creation (`git checkout -b`, `git switch -c`, `git worktree add -b`) when the new branch name does not match `PRAXIS_BRANCH_NAME_REGEX`; whitelist via `PRAXIS_BRANCH_NAME_WHITELIST`; advisory mode via `PRAXIS_BRANCH_NAME_STRICT=0` (issue #434) | [hooks/preflight-gate/branch-name-check/spec.md](hooks/preflight-gate/branch-name-check/spec.md) |
| `pre-merge-approval-gate` | PreToolUse | Surface per-PR approval prompt for `gh pr merge` in direct sessions (background agents pass) | [hooks/preflight-gate/pre-merge-approval-gate/spec.md](hooks/preflight-gate/pre-merge-approval-gate/spec.md) |
| `cross-boundary-preflight` | PreToolUse | Block heredoc body in `gh pr/issue create`; ask with four-point checklist on cross-repo `--repo` writes | [hooks/preflight-gate/cross-boundary-preflight/spec.md](hooks/preflight-gate/cross-boundary-preflight/spec.md) |
| `session-intent` | UserPromptSubmit + PreToolUse | Gate read-intent → mutation-pivot session drift on `gh` mutating commands | [hooks/preflight-gate/session-intent/spec.md](hooks/preflight-gate/session-intent/spec.md) |
| `pre-edit-protected-branch-guard` | PreToolUse | Block Edit/Write/NotebookEdit on protected branches (main/dev/prod/master) when dirty and target not already in dirty diff, or when clean tree and recent commits show `(#NNN)` PR-workflow signal (issue #231) | [hooks/preflight-gate/pre-edit-protected-branch-guard/spec.md](hooks/preflight-gate/pre-edit-protected-branch-guard/spec.md) |
| `worktree-edit-gate` | PreToolUse | Block Edit/Write on source files when the repo HEAD is on a base branch — opt-in via `PRAXIS_WORKTREE_ENFORCED_REPOS`; default no-op; blocks Edit AND Write (issue #437) | [hooks/preflight-gate/worktree-edit-gate/spec.md](hooks/preflight-gate/worktree-edit-gate/spec.md) |
| `pre-edit-md-escape-advisory` | PreToolUse(Edit) + PostToolUse(Read) | Advisory nudge when Edit on a `.md` file carries escape-sensitive tokens (`\|`, `\[`, `\]`, HTML entities) without a recorded Read in this session — Obsidian wikilink / HTML entity format mis-recall (issue #230) | [hooks/postuse-correction/pre-edit-md-escape-advisory/spec.md](hooks/postuse-correction/pre-edit-md-escape-advisory/spec.md) |
| `external-api-literal-trigger` | PreToolUse | Advisory nudge when ALL_CAPS enum candidates or 3-part SQL identifiers are written without prior retrieval verification (issue #202) | [hooks/advisory-nudge/external-api-literal-trigger/spec.md](hooks/advisory-nudge/external-api-literal-trigger/spec.md) |
| `block-manufactured-action-menu` | PreToolUse | Warn (advisory) or block (strict) when AskUserQuestion surfaces a manufactured menu — question-form ("shall we proceed?") or affirmative-form ("그대로 진행" / "execute now") — after the user already issued a command-intent signal (issue #377) | [hooks/preflight-gate/block-manufactured-action-menu/spec.md](hooks/preflight-gate/block-manufactured-action-menu/spec.md) |
| `output-block-falsify-advisory` | PreToolUse | Advisory nudge to run output-block falsification gate before surfacing `(Recommended)` options or bulk-action commands (issue #221) | [hooks/advisory-nudge/output-block-falsify-advisory/spec.md](hooks/advisory-nudge/output-block-falsify-advisory/spec.md) |
| `count-assertion-verify` | PreToolUse | Advisory nudge when `grep -c` with alternation (`\|` BRE or `|` ERE/PCRE) is run without per-arm verification; prevents citing inflated alternation counts (issue #277) | [hooks/advisory-nudge/count-assertion-verify/spec.md](hooks/advisory-nudge/count-assertion-verify/spec.md) |
| `pre-gh-pr-create-dedup-gate` | PreToolUse | Run `gh pr list --search` against the resolved target repo before `gh pr create`; surface artifact unconditionally to stderr, hard-block on repo-resolution / gh-call failure (issue #234) | [hooks/preflight-gate/pre-gh-pr-create-dedup-gate/spec.md](hooks/preflight-gate/pre-gh-pr-create-dedup-gate/spec.md) |
| `advisory-wrapper-signature-verify` | PreToolUse | Advisory nudge to verify wrapped function signatures before writing wrapper/client code with delegation patterns (issue #235) | [hooks/advisory-nudge/advisory-wrapper-signature-verify/spec.md](hooks/advisory-nudge/advisory-wrapper-signature-verify/spec.md) |
| `block-pr-without-caller-evidence` | PreToolUse | Block `gh pr create` / `gh pr new` unless the effective PR body contains a `Caller chain verified:` line (closes stdin / missing-file / TOCTOU bypasses; issue #158) | [hooks/preflight-gate/block-pr-without-caller-evidence/spec.md](hooks/preflight-gate/block-pr-without-caller-evidence/spec.md) |
| `block-pr-without-precommit-evidence` | PreToolUse | Block `gh pr create` / `gh pr new` unless the effective PR body contains one of three pre-commit marker lines (`Pre-commit verified:` / `Pre-commit: verified by CI (...)` / `Pre-commit: n/a (...)`); `--repo` does NOT bypass (issue #406) | [hooks/preflight-gate/block-pr-without-precommit-evidence/spec.md](hooks/preflight-gate/block-pr-without-precommit-evidence/spec.md) |
| `verify-commit-flag-override` | PreToolUse | Deny `git commit` invocations that override hooks / signing / hook path / template (`--no-verify`, `--no-gpg-sign`, `-S`, `-c commit.gpgsign=`, `-c core.hooksPath=`, `-c commit.template=`) without env verification; opt-out via `PRAXIS_SKIP_COMMIT_FLAG_CHECK=1` (issue #184) | [hooks/preflight-gate/verify-commit-flag-override/spec.md](hooks/preflight-gate/verify-commit-flag-override/spec.md) |
| `block-sciomc-finding-commit` | PreToolUse | Block content `git commit` after a sciomc/reviewer finding marker when no user-design consensus re-fetch happened in between; escape via `[user-approved]` token or `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` (issue #374) | [hooks/preflight-gate/block-sciomc-finding-commit/spec.md](hooks/preflight-gate/block-sciomc-finding-commit/spec.md) |
| `block-commit-without-codex-review` | PreToolUse | Block content `git commit` when `praxis:codex-review-wrap` has not been invoked this session (Skill tool_use or slash command); escape via `[skip-codex-review]` token or `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1`; claude-host only (issue #425) | [hooks/preflight-gate/block-commit-without-codex-review/spec.md](hooks/preflight-gate/block-commit-without-codex-review/spec.md) |
| `block-gh-issue-create-without-dup-search` | PreToolUse | Block `gh issue create` when no prior `gh search issues` / `issue list` overlaps the new issue's title keywords; escape via `[dup-checked]` token, personal-repo carve-out, or `CLAUDE_HOOK_BYPASS_DUP_GATE=1` (issue #374) | [hooks/preflight-gate/block-gh-issue-create-without-dup-search/spec.md](hooks/preflight-gate/block-gh-issue-create-without-dup-search/spec.md) |
| `block-child-repo-issue-create` | PreToolUse | Block `gh issue create` on hub-mediated org child repos when `PRAXIS_HUB_MEDIATED_ORGS` is configured; NO-OP by default; bypass via `PRAXIS_HOOK_BYPASS_HUB_ENFORCE` (issue #436) | [hooks/preflight-gate/block-child-repo-issue-create/spec.md](hooks/preflight-gate/block-child-repo-issue-create/spec.md) |
| `skill-gate-commands` | PreToolUse | Block configured external-mutation commands (`gh pr create`, `gh pr merge`, `git push origin`) when required skill not invoked this session; NO-OP by default; opt-in via `PRAXIS_SKILL_GATED_COMMANDS`; bypass via `PRAXIS_HOOK_BYPASS_SKILL_GATE` (issue #438) | [hooks/preflight-gate/skill-gate-commands/spec.md](hooks/preflight-gate/skill-gate-commands/spec.md) |
| `strike-counter` | SessionStart + UserPromptSubmit + Stop | Session-scoped three-strike discipline — emits 1/2-strike reminder context, hard-blocks at 3, requires non-empty reflection file before reset; state under `${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/strikes/` (issue #126) | [hooks/completion-verify/strike-counter/spec.md](hooks/completion-verify/strike-counter/spec.md) |
| `external-write-path-existence-check` | PreToolUse | Advisory nudge when a `gh issue/pr` body file (via `--body-file`) contains markdown links referencing repo paths that do not exist on disk (phase 1: markdown links; phase 2 deferred: inline-code tokens; issue #324) | [hooks/advisory-nudge/external-write-path-existence-check/spec.md](hooks/advisory-nudge/external-write-path-existence-check/spec.md) |
| `path-probe-gate` | PreToolUse | Advisory nudge (opt-in strict: deny) when Write/Edit/NotebookEdit targets a nested worktree path whose immediate parent directory has not been enumerated this session — structural enforcement of the One-Probe-Before-Action Gate rule for the Write surface (issue #386) | [hooks/advisory-nudge/path-probe-gate/spec.md](hooks/advisory-nudge/path-probe-gate/spec.md) |
| `jq-config-empty-dict-advisory` | PreToolUse | Advisory nudge when `jq` reads a config file (settings.json, hooks.json, ~/.claude/*.json, ~/.codex/*.json) that is empty or invalid JSON (issue #323) | [hooks/advisory-nudge/jq-config-empty-dict-advisory/spec.md](hooks/advisory-nudge/jq-config-empty-dict-advisory/spec.md) |
| `bash-worktree-existence-advisory` | PreToolUse | Advisory nudge when `cd <path>` targets a path that does not exist on disk (issue #322) | [hooks/advisory-nudge/bash-worktree-existence-advisory/spec.md](hooks/advisory-nudge/bash-worktree-existence-advisory/spec.md) |
| `version-bump-evidence-check` | PreToolUse | Advisory nudge (opt-in strict) when `gh issue/pr` body describes an external version bump with no changelog URL, Fetched: line, or cross-reference matrix (issue #327) | [hooks/advisory-nudge/version-bump-evidence-check/spec.md](hooks/advisory-nudge/version-bump-evidence-check/spec.md) |
| `momentum-rule-retrieval-gate` | PreToolUse | Advisory nudge at high-momentum action points (`gh pr merge`, `cmux new-workspace`, `git push --force`) — surfaces relevant CLAUDE.md rules + memory entries to prevent "Loaded ≠ Retrieved" failures (issue #326) | [hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md](hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md) |
| `bulk-write-memory-checkpoint` | PreToolUse | Advisory nudge when bulk-writing to SOT-flagged paths (vault/, wiki/, .claude/, skills/, AGENTS.md/CLAUDE.md companions) — reminds to checkpoint memory before the write loop to prevent "Loaded ≠ Retrieved" failures (issue #443) | [hooks/advisory-nudge/bulk-write-memory-checkpoint/spec.md](hooks/advisory-nudge/bulk-write-memory-checkpoint/spec.md) |
| `bypass-telemetry` | PostToolUse(Bash) | Observe-only: log bypass-env usage (`CLAUDE_HOOK_BYPASS_*` / `PRAXIS_*BYPASS*`) to daily JSONL (`~/.praxis/telemetry/bypass-events-YYYY-MM-DD.jsonl`) — never blocks (issue #441 Phase 1; Phase 2 review CLI + Phase 3 HTTP deferred) | [hooks/postuse-correction/bypass-telemetry/spec.md](hooks/postuse-correction/bypass-telemetry/spec.md) |

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
