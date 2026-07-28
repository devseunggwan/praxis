# Architecture

The component graph of praxis — how skills, hooks, providers, and platform
manifests relate. Values come from [`ETHOS.md`](ETHOS.md); implementation
mechanisms come from [`DESIGN.md`](DESIGN.md). This file describes the
*wiring*.

## Architectural shape

Four architectural patterns, one per layer — a reading map for the sections
below.

- **Microkernel (plugin) core.** A small shared kernel (`hooks/_lib/` — the
  fail-open runtime, the dispatcher, and shared helpers) hosts the hook suite
  and the skills as independent plugins. Extending praxis at the kernel level
  means adding one hook directory plus one manifest entry
  ([`CONTRIBUTING.md`](CONTRIBUTING.md) walks the full checklist); the kernel
  only executes, isolates, and aggregates. Counts live in the
  [Hook index](#hook-index) and [`AGENTS.md`](AGENTS.md), not here.
- **Interceptor chain, most-restrictive-wins.** Hooks intercept the host's
  lifecycle (PreToolUse → PostToolUse → Stop, plus UserPromptSubmit and
  SessionStart). Unlike a classic chain-of-responsibility, every member runs
  and decisions aggregate `deny > ask > allow`
  ([§Single-process dispatch](#single-process-dispatch-adr-0002)), with each
  member fail-open isolated.
- **Ports-and-adapters packaging.** The runtime core (`skills/`, `hooks/`,
  `scripts/`) knows nothing about platforms; per-platform artifacts are
  build-time adapters generated from `manifests/`
  ([§Multi-Platform Packaging](#multi-platform-packaging)). Adding a platform
  is one manifest file plus one build run.
- **Declared state + drift gate.** Generated artifacts are committed, and
  `scripts/check-plugin-manifests.py` invariants enforce manifest ↔ output
  parity in CI — the same reconciliation model infrastructure-as-code uses.

The structure is self-similar: to its hosts praxis is a plugin; inside, it is
a microkernel made of plugins.

## Provider Routing

Skills that dispatch external CLI workers (`cmux-delegate`) can route tasks to multiple AI providers. When only `claude` is installed, the system behaves exactly as before — no errors, no degradation.

### Provider CLI Spec

| Provider | Non-interactive command | Output format | Stdin prompt | Write access |
| ---------- | ---------------------- | --------------- | ------------- | ------------- |
| `claude` | `cat $F \| claude --model {m} --output-format stream-json --permission-mode auto` | stream-json (JSONL) | `cat file \| claude` | Full |
| `codex` | `cat $F \| codex exec {m:+-m m} -o $RESULT_FILE` | stdout verbose logs + last message isolated in `$RESULT_FILE` (preferred); `--json` JSONL also supported | `cat file \| codex exec` | Sandbox-restricted — explicit fallback required |
| `gemini` | `gemini -p "$(cat $F)" --approval-mode yolo {m:+-m m}` | stream-json (`-o stream-json`) | via `-p` flag | Full |

All providers share the same completion sentinel: `; echo '===WORKER_DONE===' >> $LOG` appended after the CLI exits.

### Model Notation

Unified `--model` flag across all skills: `<provider>:<model>` or bare model name.

| Notation | Resolves to | CLI command |
| ---------- | ------------- | ------------- |
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
| ------------- | ---------- | ----------- |
| implement, fix, refactor, code generation | `codex` | Code-centric, fast execution |
| search, analyze, summarize, large context | `gemini` | Large context window, search integration |
| review, design, architecture, security, debug | `claude` | Reasoning depth, nuanced judgment |
| Default (unmatched) | `claude` | Safe default |

**Phase 2 — Complexity to model (claude only; codex/gemini use provider defaults):**

| Provider | Low | Medium | High |
| ---------- | ----- | -------- | ------ |
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
> See [docs/hook-operating-matrix.md](docs/hook-operating-matrix.md) for the generated operating matrix with roles, events, host filters, and strict/bypass knobs.

| Hook | Event | Purpose | Spec |
| ------ | ------- | --------- | ------ |
| `block-gh-state-all` | PreToolUse | Hard-block invalid `gh search ... --state all` flag combo | [hooks/preflight-gate/block-gh-state-all/spec.md](hooks/preflight-gate/block-gh-state-all/spec.md) |
| `block-unmatched-glob` | PreToolUse | Hard-block a command whose unquoted glob matches nothing — zsh aborts it before it runs, so the empty result reads as a false negative | [hooks/preflight-gate/block-unmatched-glob/spec.md](hooks/preflight-gate/block-unmatched-glob/spec.md) |
| `gh-flag-verify` | PreToolUse | Block `gh <subcmd>` calls with flags not in the subcommand's accepted set | [hooks/preflight-gate/gh-flag-verify/spec.md](hooks/preflight-gate/gh-flag-verify/spec.md) |
| `gh-json-validator` | PreToolUse | Block `gh <subcmd> --json <fields>` calls whose field names are not in the subcommand's valid JSON projection (issue #391) | [hooks/preflight-gate/gh-json-validator/spec.md](hooks/preflight-gate/gh-json-validator/spec.md) |
| `gh-label-verify` | PreToolUse | Block `gh (issue\|pr) (create\|edit)` calls whose `--label` values are absent from the target repo's label set (issue #385) | [hooks/preflight-gate/gh-label-verify/spec.md](hooks/preflight-gate/gh-label-verify/spec.md) |
| `foreground-poll-loop-guard` | PreToolUse | Block foreground Bash poll-loops (`for/while/until … sleep`) that would hit the 120s ceiling (Exit 143); redirects to native async-wait primitives (issue #745) | [hooks/preflight-gate/foreground-poll-loop-guard/spec.md](hooks/preflight-gate/foreground-poll-loop-guard/spec.md) |
| `cli-flag-incompat-advisory` | PreToolUse | Advisory nudge for known mode-incompatible flag combos in other CLIs (`git merge-tree --name-only` 3-arg form, `kubectl --use-protocol-buffers`) — issue #248 | [hooks/advisory-nudge/cli-flag-incompat-advisory/spec.md](hooks/advisory-nudge/cli-flag-incompat-advisory/spec.md) |
| `inspection-chain-advisory` | PreToolUse | Advisory nudge when 2+ inspection-only commands are chained with `&&` (non-match exit silently drops downstream probes) — issue #469 | [hooks/advisory-nudge/inspection-chain-advisory/spec.md](hooks/advisory-nudge/inspection-chain-advisory/spec.md) |
| `pipefail-advisory` | PreToolUse | Advisory nudge when a mutating `git`/`gh` command is piped into `tail`/`head`/`grep` without `set -o pipefail` (non-zero exit masked by the sink's own exit 0) — issue #788 | [hooks/advisory-nudge/pipefail-advisory/spec.md](hooks/advisory-nudge/pipefail-advisory/spec.md) |
| `secret-print-redaction-advisory` | PreToolUse | Advisory nudge when a live Bash command or an agent-authored script both fetches a secret and routes the value to stdout unmasked (2-signal AND gate; masked/digest output and bare interactive fetches silent) — issue #827 | [hooks/advisory-nudge/secret-print-redaction-advisory/spec.md](hooks/advisory-nudge/secret-print-redaction-advisory/spec.md) |
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
| `readonly-verify-deferral-gate` | Stop | Advisory when the last turn offers a read-only verification (EN/KR) instead of running it — three-signal AND (read intent ∧ deferral ∧ ¬mutation) with a read-already-run suppressor; project-agnostic, extensible via `PRAXIS_READONLY_VERIFY_SIGNALS` (issue #641) | [hooks/completion-verify/readonly-verify-deferral-gate/spec.md](hooks/completion-verify/readonly-verify-deferral-gate/spec.md) |
| `merge-state-claim-gate` | Stop | Advisory when the final message asserts a completed merge/PR/issue/worktree state (EN/KR) with no fresh `gh pr\|issue` or GitHub-MCP state query in the recent transcript (issue #503); applied-on-branch claims require reachability evidence — merge-base/baseRefName (issue #656) | [hooks/completion-verify/merge-state-claim-gate/spec.md](hooks/completion-verify/merge-state-claim-gate/spec.md) |
| `runtime-state-claim-gate` | Stop | Advisory when the final message asserts a runtime/execution state — "X is running in Y" / "로컬은 건드리지 않습니다" (EN/KR) — with no probe tool_use in the current turn; remote/isolation launch modes fall back silently, so location claims need observation, not launch success (issue #809) | [hooks/completion-verify/runtime-state-claim-gate/spec.md](hooks/completion-verify/runtime-state-claim-gate/spec.md) |
| `artifact-verdict-evidence-gate` | Stop | Advisory (default) when the final message surfaces a positive-polarity artifact verdict (삭제 후보/중복/통합 대상/duplicate/superseded) as a candidate list or table without an adjacent `Verdict-evidence:` line; presence-only enforcement, block-promote via `PRAXIS_ARTIFACT_VERDICT_STRICT` (issue #862) | [hooks/completion-verify/artifact-verdict-evidence-gate/spec.md](hooks/completion-verify/artifact-verdict-evidence-gate/spec.md) |
| `negative-existence-verdict-gate` | Stop | Block (default) when the final message surfaces a negative-existence verdict (없습니다/존재하지 않/does not exist) under a registered decision/gate framing (게이트 결과/완료 조건/AC #/Acceptance) without a same-paragraph `Enumerated:` line; presence-only enforcement, advisory-demote via `PRAXIS_NEGATIVE_EXISTENCE_ADVISORY` (issue #804) | [hooks/completion-verify/negative-existence-verdict-gate/spec.md](hooks/completion-verify/negative-existence-verdict-gate/spec.md) |
| `pr-report-destination-gate` | Stop | Advisory (non-blocking) when a session wrote a review/verification local `.md` (scratch dir or report-named) for a PR it worked on (`gh pr view/create/diff/checks` or a `/pull/N` URL) but never posted a successful `gh pr comment`/`gh pr review` there; per-PR correlation survives multi-PR sessions, GET `gh api` and failed posts (is_error) excluded (issue #832) | [hooks/completion-verify/pr-report-destination-gate/spec.md](hooks/completion-verify/pr-report-destination-gate/spec.md) |
| `pr-claim-mutation-gate` | Stop | Block (default) when the final message pairs a PR/review subject with a processed-claim verb (처리했/반영했/resolved/applied) and the current turn contains no **successful** PR-surface mutation (`git push`, `gh pr comment/review`, write-method `gh api` on a comments/reviews/threads endpoint, `resolveReviewThread`, a write-verb GitHub MCP tool); read-only listings, `--dry-run`/`--help` rehearsals, echoed command text and `is_error` results all fail to clear it; advisory-demote via `PRAXIS_PR_CLAIM_ADVISORY` (issue #868) | [hooks/completion-verify/pr-claim-mutation-gate/spec.md](hooks/completion-verify/pr-claim-mutation-gate/spec.md) |
| `proposal-premise-gate` | Stop | Advisory (non-blocking) when the final message surfaces a prose proposal block whose premises are code-checkable (file/symbol/flag existence) and no probe ran in the current turn; prose proposals never reach a PreToolUse surface, so the Stop lane is the only place this can fire; bypass via `PRAXIS_PROPOSAL_PREMISE_BYPASS` (issue #846) | [hooks/completion-verify/proposal-premise-gate/spec.md](hooks/completion-verify/proposal-premise-gate/spec.md) |
| `external-write-falsify-check` (opt-in) | PreToolUse | Warn before posting hypothesis-stage text to PR / issue / Slack / Notion; also detects author-exempt unverified identifiers in mapping tables and code blocks (issue #183) and applied-on-branch claims without a reachability probe (issue #656) | [hooks/advisory-nudge/external-write-falsify-check/spec.md](hooks/advisory-nudge/external-write-falsify-check/spec.md) |
| `commit-title-length-check` | PreToolUse | Ask when `git commit` title exceeds 50 chars (configurable via `CLAUDE_COMMIT_TITLE_MAX`) | [hooks/preflight-gate/commit-title-length-check/spec.md](hooks/preflight-gate/commit-title-length-check/spec.md) |
| `commit-title-format-check` | PreToolUse | Block `git commit`, `gh pr create`, `gh issue create` when title does not match Conventional Commits format; advisory mode via `PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0` | [hooks/preflight-gate/commit-title-format-check/spec.md](hooks/preflight-gate/commit-title-format-check/spec.md) |
| `branch-name-check` | PreToolUse | Block branch creation (`git checkout -b`, `git switch -c`, `git worktree add -b`) when the new branch name does not match `PRAXIS_BRANCH_NAME_REGEX`; whitelist via `PRAXIS_BRANCH_NAME_WHITELIST`; advisory mode via `PRAXIS_BRANCH_NAME_STRICT=0` (issue #434) | [hooks/preflight-gate/branch-name-check/spec.md](hooks/preflight-gate/branch-name-check/spec.md) |
| `pre-merge-approval-gate` | PreToolUse | Surface per-PR approval prompt for `gh pr merge` in direct sessions (background agents pass) | [hooks/preflight-gate/pre-merge-approval-gate/spec.md](hooks/preflight-gate/pre-merge-approval-gate/spec.md) |
| `gh-merge-worktree-precondition` | PreToolUse | Block `gh pr merge --delete-branch`/`-d` when the PR's live head branch (via `gh pr view`) is still checked out in another `git worktree`; bypass via `PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE` (issue #798) | [hooks/preflight-gate/gh-merge-worktree-precondition/spec.md](hooks/preflight-gate/gh-merge-worktree-precondition/spec.md) |
| `pr-state-refetch-gate` | PreToolUse | Re-fetch live `gh pr view` state before a PR-number + merge-intent `AskUserQuestion`; warn (or strict-mode block) when the PR is already MERGED/CLOSED — issue #719 | [hooks/preflight-gate/pr-state-refetch-gate/spec.md](hooks/preflight-gate/pr-state-refetch-gate/spec.md) |
| `cross-boundary-preflight` | PreToolUse | Block heredoc body in `gh pr/issue create`; ask with four-point checklist on cross-repo `--repo` writes | [hooks/preflight-gate/cross-boundary-preflight/spec.md](hooks/preflight-gate/cross-boundary-preflight/spec.md) |
| `session-intent` | UserPromptSubmit + PreToolUse | Gate read-intent → mutation-pivot session drift on `gh` mutating commands | [hooks/preflight-gate/session-intent/spec.md](hooks/preflight-gate/session-intent/spec.md) |
| `retrospect-active-marker` | PreToolUse(Skill) + UserPromptSubmit | Record a session-scoped retrospect-active marker so the Stop gate detects Stage-3 fence omission (#666) | [hooks/preflight-gate/retrospect-active-marker/spec.md](hooks/preflight-gate/retrospect-active-marker/spec.md) |
| `pre-edit-protected-branch-guard` | PreToolUse | Block Edit/Write/NotebookEdit on protected branches (main/dev/prod/master) when dirty and target not already in dirty diff, or when clean tree and recent commits show `(#NNN)` PR-workflow signal (issue #231) | [hooks/preflight-gate/pre-edit-protected-branch-guard/spec.md](hooks/preflight-gate/pre-edit-protected-branch-guard/spec.md) |
| `worktree-edit-gate` | PreToolUse | Block Edit/Write on source files when the repo HEAD is on a base branch — opt-in via `PRAXIS_WORKTREE_ENFORCED_REPOS`; default no-op; blocks Edit AND Write (issue #437) | [hooks/preflight-gate/worktree-edit-gate/spec.md](hooks/preflight-gate/worktree-edit-gate/spec.md) |
| `pre-edit-md-escape-advisory` | PreToolUse(Edit) + PostToolUse(Read) | Advisory nudge when Edit on a `.md` file carries escape-sensitive tokens (`\|`, `\[`, `\]`, HTML entities) without a recorded Read in this session — Obsidian wikilink / HTML entity format mis-recall (issue #230) | [hooks/postuse-correction/pre-edit-md-escape-advisory/spec.md](hooks/postuse-correction/pre-edit-md-escape-advisory/spec.md) |
| `external-api-literal-trigger` | PreToolUse | Advisory nudge when ALL_CAPS enum candidates or 3-part SQL identifiers are written without prior retrieval verification (issue #202) | [hooks/advisory-nudge/external-api-literal-trigger/spec.md](hooks/advisory-nudge/external-api-literal-trigger/spec.md) |
| `block-manufactured-action-menu` | PreToolUse | Warn (advisory) or block (strict) when AskUserQuestion surfaces a manufactured menu — question-form ("shall we proceed?") or affirmative-form ("그대로 진행" / "execute now") — after the user already issued a command-intent signal (issue #377) | [hooks/preflight-gate/block-manufactured-action-menu/spec.md](hooks/preflight-gate/block-manufactured-action-menu/spec.md) |
| `output-block-falsify-advisory` | PreToolUse | Advisory nudge to run output-block falsification gate before surfacing `(Recommended)` options or bulk-action commands (issue #221) | [hooks/advisory-nudge/output-block-falsify-advisory/spec.md](hooks/advisory-nudge/output-block-falsify-advisory/spec.md) |
| `source-citation-probe-gate` | PreToolUse | Advisory when an external-write body cites source facts (file:line, inline-code call syntax, test-semantics claims) with no read-probe in the recent transcript and no in-body `Probe:` / `[verified]` basis (issue #830) | [hooks/advisory-nudge/source-citation-probe-gate/spec.md](hooks/advisory-nudge/source-citation-probe-gate/spec.md) |
| `count-assertion-verify` | PreToolUse | Advisory nudge when `grep -c` with alternation (`\|` BRE or ` \| ` ERE/PCRE) is run without per-arm verification; prevents citing inflated alternation counts (issue #277) | [hooks/advisory-nudge/count-assertion-verify/spec.md](hooks/advisory-nudge/count-assertion-verify/spec.md) |
| `pre-gh-pr-create-dedup-gate` | PreToolUse | Run `gh pr list --search` against the resolved target repo before `gh pr create`; surface artifact unconditionally to stderr, hard-block on repo-resolution / gh-call failure (issue #234) | [hooks/preflight-gate/pre-gh-pr-create-dedup-gate/spec.md](hooks/preflight-gate/pre-gh-pr-create-dedup-gate/spec.md) |
| `advisory-wrapper-signature-verify` | PreToolUse | Advisory nudge to verify wrapped function signatures before writing wrapper/client code with delegation patterns (issue #235) | [hooks/advisory-nudge/advisory-wrapper-signature-verify/spec.md](hooks/advisory-nudge/advisory-wrapper-signature-verify/spec.md) |
| `block-pr-without-caller-evidence` | PreToolUse | Block `gh pr create` / `gh pr new` unless the effective PR body contains a `Caller chain verified:` line (closes stdin / missing-file / TOCTOU bypasses; issue #158) | [hooks/preflight-gate/block-pr-without-caller-evidence/spec.md](hooks/preflight-gate/block-pr-without-caller-evidence/spec.md) |
| `block-pr-without-precommit-evidence` | PreToolUse | Block `gh pr create` / `gh pr new` unless the effective PR body contains one of three pre-commit marker lines (`Pre-commit verified:` / `Pre-commit: verified by CI (...)` / `Pre-commit: n/a (...)`); `--repo` does NOT bypass (issue #406) | [hooks/preflight-gate/block-pr-without-precommit-evidence/spec.md](hooks/preflight-gate/block-pr-without-precommit-evidence/spec.md) |
| `verify-commit-flag-override` | PreToolUse | Deny `git commit` invocations that override hooks / signing / hook path / template (`--no-verify`, `--no-gpg-sign`, `-S`, `-c commit.gpgsign=`, `-c core.hooksPath=`, `-c commit.template=`) without env verification; opt-out via `PRAXIS_SKIP_COMMIT_FLAG_CHECK=1` (issue #184) | [hooks/preflight-gate/verify-commit-flag-override/spec.md](hooks/preflight-gate/verify-commit-flag-override/spec.md) |
| `block-sciomc-finding-commit` | PreToolUse | Block content `git commit` after a sciomc/reviewer finding marker when no user-design consensus re-fetch happened in between; escape via `[user-approved]` token or `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` (issue #374) | [hooks/preflight-gate/block-sciomc-finding-commit/spec.md](hooks/preflight-gate/block-sciomc-finding-commit/spec.md) |
| `block-commit-without-codex-review` | PreToolUse | Block content `git commit` when `praxis:codex-review-wrap` has not been invoked this session (Skill tool_use or slash command); escape via `[skip-codex-review]` token or `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1`; claude-host only (issue #425) | [hooks/preflight-gate/block-commit-without-codex-review/spec.md](hooks/preflight-gate/block-commit-without-codex-review/spec.md) |
| `block-rename-sweep-survivors` | PreToolUse | Block `git commit` when staged diff contains ≥3 identical 1:1 identifier renames and the old token still exists in the tracked tree; escape via `# [rename-sweep-exempt]` marker or `PRAXIS_SKIP_RENAME_SWEEP_CHECK=1`; claude-host only (issue #556) | [hooks/preflight-gate/block-rename-sweep-survivors/spec.md](hooks/preflight-gate/block-rename-sweep-survivors/spec.md) |
| `block-gh-issue-create-without-dup-search` | PreToolUse | Block `gh issue create` when no prior `gh search issues` / `issue list` overlaps the new issue's title keywords; escape via `[dup-checked]` token, personal-repo carve-out, or `CLAUDE_HOOK_BYPASS_DUP_GATE=1` (issue #374) | [hooks/preflight-gate/block-gh-issue-create-without-dup-search/spec.md](hooks/preflight-gate/block-gh-issue-create-without-dup-search/spec.md) |
| `block-child-repo-issue-create` | PreToolUse | Block `gh issue create` on hub-mediated org child repos when `PRAXIS_HUB_MEDIATED_ORGS` is configured; NO-OP by default; bypass via `PRAXIS_HOOK_BYPASS_HUB_ENFORCE` (issue #436) | [hooks/preflight-gate/block-child-repo-issue-create/spec.md](hooks/preflight-gate/block-child-repo-issue-create/spec.md) |
| `skill-gate-commands` | PreToolUse | Block configured external-mutation commands (`gh pr create`, `gh pr merge`, `git push origin`) when required skill not invoked this session; NO-OP by default; opt-in via `PRAXIS_SKILL_GATED_COMMANDS`; bypass via `PRAXIS_HOOK_BYPASS_SKILL_GATE` (issue #438) | [hooks/preflight-gate/skill-gate-commands/spec.md](hooks/preflight-gate/skill-gate-commands/spec.md) |
| `strike-counter` | SessionStart + UserPromptSubmit + Stop | Session-scoped three-strike discipline — emits 1/2-strike reminder context, hard-blocks at 3, requires non-empty reflection file before reset; state under `${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/strikes/` (issue #126) | [hooks/completion-verify/strike-counter/spec.md](hooks/completion-verify/strike-counter/spec.md) |
| `external-write-path-existence-check` | PreToolUse | Advisory nudge when a `gh issue/pr` body file (via `--body-file`) contains markdown links referencing repo paths that do not exist on disk (phase 1: markdown links; phase 2 deferred: inline-code tokens; issue #324) | [hooks/advisory-nudge/external-write-path-existence-check/spec.md](hooks/advisory-nudge/external-write-path-existence-check/spec.md) |
| `block-personal-asset-leak` | PreToolUse | Advisory nudge (opt-in strict: block via `PRAXIS_PERSONAL_LEAK_STRICT=1`) on two personal-asset marker classes: (1) absolute home-dotfiles path (`/Users/<name>/.claude/...`, `/home/<name>/.config/...`) in a `gh issue/pr` write body — always active; (2) personal-repo reference (`<owner>/<repo>(#N)?`) toward a non-personal target, on `gh` bodies AND Write/Edit content — opt-in via `PRAXIS_PERSONAL_REPO_OWNERS`, target-discriminated by `--repo`/origin owner with gitignored-path exemption; tilde `~/` form and `/projects/` worktree paths excluded; semantic surfacing out of scope (issues #563, #658) | [hooks/advisory-nudge/block-personal-asset-leak/spec.md](hooks/advisory-nudge/block-personal-asset-leak/spec.md) |
| `path-probe-gate` | PreToolUse | Advisory nudge (opt-in strict: deny) when Write/Edit/NotebookEdit targets a nested worktree path whose immediate parent directory has not been enumerated this session — structural enforcement of the One-Probe-Before-Action Gate rule for the Write surface (issue #386) | [hooks/advisory-nudge/path-probe-gate/spec.md](hooks/advisory-nudge/path-probe-gate/spec.md) |
| `exclusion-probe-gate` | PreToolUse | Advisory nudge (opt-in strict: deny via `PRAXIS_EXCLUSION_PROBE_STRICT=1`) when Write/Edit content embeds a self-authored **exclusion directive** (Axis A: `do NOT add`, `deliberately excluded/omitted/skipped`, KO `의도적 제외`/`추가하지 말 것`/`포함하지 않음`) co-occurring within ±3 lines with an **uncited verification claim** (Axis B: `verified/confirmed/checked … via/with/by`, KO `확인함`/`검증됨`/`대조 완료`) and no `Probe:` citation nearby — structural backstop for the Author-exempt verification trap (Information Accuracy Layer 2). Directive-alone passes; normative-doc paths (`CLAUDE.md`/`AGENTS.md`/`SKILL.md`/`spec.md`), test/fixture paths, fenced/inline-code/blockquote regions, and negated claims excluded. Write/Edit content only; Bash heredoc/`gh --body` deferred (issue #807) | [hooks/advisory-nudge/exclusion-probe-gate/spec.md](hooks/advisory-nudge/exclusion-probe-gate/spec.md) |
| `jq-config-empty-dict-advisory` | PreToolUse | Advisory nudge when `jq` reads a config file (settings.json, hooks.json, ~/.claude/*.json, ~/.codex/*.json) that is empty or invalid JSON (issue #323) | [hooks/advisory-nudge/jq-config-empty-dict-advisory/spec.md](hooks/advisory-nudge/jq-config-empty-dict-advisory/spec.md) |
| `bash-worktree-existence-advisory` | PreToolUse | Advisory nudge when `cd <path>` targets a path that does not exist on disk (issue #322) | [hooks/advisory-nudge/bash-worktree-existence-advisory/spec.md](hooks/advisory-nudge/bash-worktree-existence-advisory/spec.md) |
| `pre-commit-staged-file-enumeration` | PreToolUse | Advisory nudge before `git commit` listing staged file additions not created via Write/Edit/MultiEdit/NotebookEdit this session — heredoc / `> file` / external-script output caught before it rides into the commit (issue #784) | [hooks/advisory-nudge/pre-commit-staged-file-enumeration/spec.md](hooks/advisory-nudge/pre-commit-staged-file-enumeration/spec.md) |
| `model-routing-advisory` | PreToolUse | Advisory nudge when a Bash delegation's `--model` names a bare Claude tier (`haiku`/`sonnet`/`opus`) mismatching the tier implied by task keywords (`find → haiku`, `implement → sonnet`, `architect/security → opus`) — the [Provider Routing](#provider-routing) complexity→model phase only; silent for `codex:`/`gemini:`/full model IDs. Full routing tree lives in this hook's spec; a companion ai-dotfiles change removes the always-loaded `Skill & Agent Routing` + `Model Routing Rules` blocks (issue #786) | [hooks/advisory-nudge/model-routing-advisory/spec.md](hooks/advisory-nudge/model-routing-advisory/spec.md) |
| `push-remote-ref-verify` | PostToolUse | Advisory after `git push` when the remote branch tip did not advance to the pushed SHA — guards the rotating-endpoint silent-divergence failure (issue #539) | [hooks/advisory-nudge/push-remote-ref-verify/spec.md](hooks/advisory-nudge/push-remote-ref-verify/spec.md) |
| `version-bump-evidence-check` | PreToolUse | Advisory nudge (opt-in strict) when `gh issue/pr` body describes an external version bump with no changelog URL, Fetched: line, or cross-reference matrix (issue #327) | [hooks/advisory-nudge/version-bump-evidence-check/spec.md](hooks/advisory-nudge/version-bump-evidence-check/spec.md) |
| `momentum-rule-retrieval-gate` | PreToolUse | Advisory nudge at high-momentum action points (`gh pr merge`, `cmux new-workspace`, `git push --force`) — surfaces relevant CLAUDE.md rules + memory entries to prevent "Loaded ≠ Retrieved" failures (issue #326) | [hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md](hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md) |
| `bulk-write-memory-checkpoint` | PreToolUse | Advisory nudge when bulk-writing to SOT-flagged paths (vault/, wiki/, .claude/, skills/, AGENTS.md/CLAUDE.md companions) — reminds to checkpoint memory before the write loop to prevent "Loaded ≠ Retrieved" failures (issue #443) | [hooks/advisory-nudge/bulk-write-memory-checkpoint/spec.md](hooks/advisory-nudge/bulk-write-memory-checkpoint/spec.md) |
| `pre-output-falsification-gate` | PreToolUse | Two advisory lanes: (Lane A) on `AskUserQuestion`, nudge when an evaluative / `(Recommended)` option is surfaced under recent negative transcript evidence without a disconfirming-probe phrase in the question body; (Lane B / B-i) on `Bash`, nudge when a read-only status command (status/get/list) repeats ≥3× in a session (issue #487) | [hooks/advisory-nudge/pre-output-falsification-gate/spec.md](hooks/advisory-nudge/pre-output-falsification-gate/spec.md) |
| `merge-menu-review-options-advisory` | PreToolUse | Advisory (opt-in strict via `PRAXIS_MERGE_MENU_REVIEW_STRICT=1`) on `AskUserQuestion` when a merge-decision menu (an option label names a merge/squash action) offers no review/debate option — nudges the agent to re-author the menu with codex-review-wrap / code-reviewer / critic levers before the merge gate (issue #560) | [hooks/advisory-nudge/merge-menu-review-options-advisory/spec.md](hooks/advisory-nudge/merge-menu-review-options-advisory/spec.md) |
| `bypass-telemetry` | PostToolUse(Bash) | Observe-only: log bypass-env usage (`CLAUDE_HOOK_BYPASS_*` / `PRAXIS_*BYPASS*`) to daily JSONL (`~/.praxis/telemetry/bypass-events-YYYY-MM-DD.jsonl`) — never blocks (issue #441 Phase 1; Phase 2 review CLI + Phase 3 HTTP deferred) | [hooks/postuse-correction/bypass-telemetry/spec.md](hooks/postuse-correction/bypass-telemetry/spec.md) |
| `askuserquestion-loop-signal` | PostToolUse(AskUserQuestion) | Observe-only: append one fire-ledger record per `AskUserQuestion` call — coarse per-session call-count proxy for the "re-clarification loop" outcome-proxy signal (issue #740, issue #737 signal 2 of 3), never blocks | [hooks/postuse-correction/askuserquestion-loop-signal/spec.md](hooks/postuse-correction/askuserquestion-loop-signal/spec.md) |

### Single-process dispatch (ADR-0002)

Every `Bash` tool call fires the whole `PreToolUse(Bash)` hook group. Under the
per-hook model each member is a `.sh` wrapper that `exec python3 .../impl.py`, so
one `Bash` call cold-started ~33 python3 interpreters — ~99% of the latency is
interpreter startup, not hook logic. ADR-0002 collapses that group into **one**
python3 process.

- **Declaration.** `hooks/manifest.json` carries a `dispatch_groups` array of
  `{event, matcher}` pairs. Only `(PreToolUse, Bash)` is collapsed today: the
  **33** hooks whose manifest `matcher` is exactly `Bash`. The two multi-tool
  hooks that also fire on Bash — `memory-hint`
  (`Bash|Edit|Write|NotebookEdit|AskUserQuestion`) and
  `external-api-literal-trigger` (`Write|Edit|Bash`) — keep standalone wrappers,
  because folding them into a Bash-only runner would drop their Edit/Write firing.
- **Build path.** For each platform, `build-plugin-manifests.py`
  (`filter_hooks_for_host`) emits, after host filtering, exactly **one** dispatcher
  node per group — `${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.sh <event> <matcher>
  <host>` — instead of one node per member. The platform `host_id` is baked into
  the command so the runtime applies the same `hosts` filter. The node `timeout`
  is the max of member timeouts (members run sequentially in one process, so the
  budget matches the slowest member's per-process budget, not the sum).
- **Runtime path.** `hooks/_lib/_dispatch.py` reads the payload from stdin once,
  resolves the ordered member list for `(event, matcher)` from the manifest
  (host-filtered to match the build), imports each member's `impl.py`
  in-process — the `if __name__ == "__main__"` guard means importing does **not**
  run `main()` — re-points `sys.stdin` at a fresh copy of the payload per member,
  and runs each member's `main()` through the existing `_hook_runtime.fail_open`
  decorator. Member `impl.py` files are unmodified; the dispatcher adapts around
  them.
- **Aggregation (most-restrictive wins).** Decisions are classified
  role-agnostically by exit code / `permissionDecision` marker: any member
  `deny` (exit 2 or `"permissionDecision": "deny"`) → propagate `deny`; else any
  `ask` → propagate `ask`; else allow. Every member's stderr (advisory nudges and
  deny reasons alike) is always forwarded. Role-agnostic detection is deliberate —
  some `advisory-nudge` hooks emit `ask`/`deny` under strict modes, so a role-gated
  split would silently drop their gate decisions.
- **Fail-open** ([`ETHOS.md`](ETHOS.md)). Each member runs under `fail_open`, and
  the dispatcher's own `main()` swallows exceptions to a `0` (allow), so a crash
  in one `impl.py` cannot block the tool call or abort the other members —
  restoring the isolation process separation gave for free. Import-time failures
  are forwarded to stderr (visible, not silent) before failing open.
- **Guard.** `scripts/check-plugin-manifests.py` Rule 13 ties the build and
  runtime paths together: for every `dispatch_groups` pair, per platform, the
  committed `hooks.json` must hold exactly one dispatcher node (no leaked member
  node, no second node, correct host args), and `_dispatch.group_members` must
  resolve the same member set the build collapsed, with every `impl.py` present
  on disk. A future manifest or schema edit that breaks the collapse fails CI.

**Measured latency** (`/usr/bin/time -p`, warm caches, no-op `ls -la` payload, 33
members, claude host):

| Path | Wall-clock |
| ------ | ----------- |
| Single-process dispatcher (wired runtime path) | **~0.13s** |
| Reconstructed per-process model (33 wrappers, spawned in parallel) | ~0.46s |

The dispatcher's measured ~0.13s matches the ADR-0002 §1.2 prototype estimate.
The per-process baseline above is the parallel wrapper-spawn cost on the same
bench; the ADR §1.1 figure of 1.87s was measured inside a live, CPU-saturated
Claude Code session (35 hooks) and reflects a harsher orchestration context.
Either way, the cost-growth shape is what changed: each hook added to the group
now costs one in-process `main()` call (~ms) instead of one more cold-started
process on every `Bash` call.

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
| ------ | ---------- |
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
