# Hook Index (categorized)

Praxis hooks grouped by enforcement role. For the full per-hook spec, follow
each link. For the flat tabular listing (with event column), see the Hook
section of the [project AGENTS.md](../../AGENTS.md#hook-index).

---

## preflight-gate

Pre-tool blocking / ask-user gates. Run **before** the tool executes; can
`deny`, `ask`, or `defer`. State-changing focus — these are the hard-stop or
confirmation-prompt layer.

| Hook | Trigger | Purpose |
|------|---------|---------|
| [block-gh-state-all](../../hooks/preflight-gate/block-gh-state-all/spec.md) | PreToolUse | Hard-block invalid `gh search ... --state all` flag combo |
| [gh-flag-verify](../../hooks/preflight-gate/gh-flag-verify/spec.md) | PreToolUse | Block `gh <subcmd>` calls with flags not in the subcommand's accepted set |
| [gh-json-validator](../../hooks/preflight-gate/gh-json-validator/spec.md) | PreToolUse | Block `gh <subcmd> --json <fields>` calls whose field names are not in the subcommand's valid JSON projection — issue #391 |
| [gh-label-verify](../../hooks/preflight-gate/gh-label-verify/spec.md) | PreToolUse | Block `gh (issue\|pr) (create\|edit)` calls whose `--label` values are absent from the target repo's label set — issue #385 |
| [block-ask-end-option](../../hooks/preflight-gate/block-ask-end-option/spec.md) | PreToolUse | Block `AskUserQuestion` options carrying end-option markers when no stop signal present |
| [block-manufactured-action-menu](../../hooks/preflight-gate/block-manufactured-action-menu/spec.md) | PreToolUse | Warn or block when `AskUserQuestion` surfaces a "shall we proceed?" menu after user already issued a command-intent signal |
| [block-pr-without-caller-evidence](../../hooks/preflight-gate/block-pr-without-caller-evidence/spec.md) | PreToolUse | Block `gh pr create` unless the PR body contains a `Caller chain verified:` line |
| [block-pr-without-precommit-evidence](../../hooks/preflight-gate/block-pr-without-precommit-evidence/spec.md) | PreToolUse | Block `gh pr create` unless the PR body declares pre-commit state (`Pre-commit verified:` / `verified by CI` / `n/a (reason)`); `--repo` is not a bypass |
| [side-effect-scan](../../hooks/preflight-gate/side-effect-scan/spec.md) | PreToolUse | Ask before commands with collateral side effects (`git commit/push`, `gh pr merge/create`, `kubectl apply`) |
| [commit-title-length-check](../../hooks/preflight-gate/commit-title-length-check/spec.md) | PreToolUse | Ask when `git commit` title exceeds 50 chars |
| [commit-title-format-check](../../hooks/preflight-gate/commit-title-format-check/spec.md) | PreToolUse | Block `git commit`, `gh pr create`, `gh issue create` when title does not match Conventional Commits format |
| [branch-name-check](../../hooks/preflight-gate/branch-name-check/spec.md) | PreToolUse | Block branch creation (`checkout -b`, `switch -c`, `worktree add -b`) when the new branch name does not match the configured regex |
| [pre-merge-approval-gate](../../hooks/preflight-gate/pre-merge-approval-gate/spec.md) | PreToolUse | Surface per-PR approval prompt for `gh pr merge` in direct sessions |
| [cross-boundary-preflight](../../hooks/preflight-gate/cross-boundary-preflight/spec.md) | PreToolUse | Block heredoc body in `gh pr/issue create`; ask with four-point checklist on cross-repo `--repo` writes |
| [pre-edit-protected-branch-guard](../../hooks/preflight-gate/pre-edit-protected-branch-guard/spec.md) | PreToolUse | Block Edit/Write/NotebookEdit on protected branches (main/dev/prod/master) outside the expected worktree workflow |
| [worktree-edit-gate](../../hooks/preflight-gate/worktree-edit-gate/spec.md) | PreToolUse | Block Edit/Write on source files when the repo HEAD is on a base branch — opt-in via `PRAXIS_WORKTREE_ENFORCED_REPOS`; default no-op (issue #437) |
| [pre-gh-pr-create-dedup-gate](../../hooks/preflight-gate/pre-gh-pr-create-dedup-gate/spec.md) | PreToolUse | Run `gh pr list --search` before `gh pr create`; hard-block on duplicate or repo-resolution failure |
| [verify-commit-flag-override](../../hooks/preflight-gate/verify-commit-flag-override/spec.md) | PreToolUse | Deny `git commit` invocations that override hooks / signing without env verification |
| [block-sciomc-finding-commit](../../hooks/preflight-gate/block-sciomc-finding-commit/spec.md) | PreToolUse | Block content `git commit` after a sciomc/reviewer finding when no user-design consensus re-fetch happened in between |
| [block-commit-without-codex-review](../../hooks/preflight-gate/block-commit-without-codex-review/spec.md) | PreToolUse | Block content `git commit` when `praxis:codex-review-wrap` has not been invoked this session |
| [block-gh-issue-create-without-dup-search](../../hooks/preflight-gate/block-gh-issue-create-without-dup-search/spec.md) | PreToolUse | Block `gh issue create` when no prior duplicate search overlaps the new issue's title keywords |
| [block-child-repo-issue-create](../../hooks/preflight-gate/block-child-repo-issue-create/spec.md) | PreToolUse | Block `gh issue create` on hub-mediated org child repos; redirects agent to the hub creation skill (opt-in via `PRAXIS_HUB_MEDIATED_ORGS`) |
| [session-intent](../../hooks/preflight-gate/session-intent/spec.md) | UserPromptSubmit + PreToolUse | Gate read-intent → mutation-pivot session drift on `gh` mutating commands |

---

## advisory-nudge

Pre-tool stderr hints. **Never block** — emit reminders for recurrent patterns
so the agent can self-correct. Fail-open on infrastructure errors by design.

| Hook | Trigger | Purpose |
|------|---------|---------|
| [momentum-rule-retrieval-gate](../../hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md) | PreToolUse | Advisory nudge at high-momentum action points (`gh pr merge`, `cmux new-workspace`, `git push --force`) — surfaces relevant CLAUDE.md rules + memory entries to prevent "Loaded ≠ Retrieved" failures |
| [cli-flag-incompat-advisory](../../hooks/advisory-nudge/cli-flag-incompat-advisory/spec.md) | PreToolUse | Advisory nudge for known mode-incompatible flag combos (`git merge-tree --name-only` 3-arg form, `kubectl --use-protocol-buffers`) |
| [memory-hint](../../hooks/advisory-nudge/memory-hint/spec.md) | PreToolUse | Surface hookable memory entries by keyword at decision-construction time |
| [external-write-falsify-check](../../hooks/advisory-nudge/external-write-falsify-check/spec.md) | PreToolUse (opt-in) | Warn before posting hypothesis-stage text to PR / issue / Slack / Notion |
| [external-api-literal-trigger](../../hooks/advisory-nudge/external-api-literal-trigger/spec.md) | PreToolUse | Advisory nudge when ALL_CAPS enum candidates or 3-part SQL identifiers are written without prior retrieval verification |
| [output-block-falsify-advisory](../../hooks/advisory-nudge/output-block-falsify-advisory/spec.md) | PreToolUse | Advisory nudge to run output-block falsification gate before surfacing `(Recommended)` options or bulk-action commands |
| [advisory-wrapper-signature-verify](../../hooks/advisory-nudge/advisory-wrapper-signature-verify/spec.md) | PreToolUse | Advisory nudge to verify wrapped function signatures before writing wrapper/client code |
| [jq-config-empty-dict-advisory](../../hooks/advisory-nudge/jq-config-empty-dict-advisory/spec.md) | PreToolUse | Advisory nudge when `jq` reads a config file (settings.json, hooks.json, ~/.claude/*.json, ~/.codex/*.json) that is empty or invalid JSON |
| [bash-worktree-existence-advisory](../../hooks/advisory-nudge/bash-worktree-existence-advisory/spec.md) | PreToolUse | Advisory nudge when `cd <path>` targets a path that does not exist on disk |
| [codex-review-route](../../hooks/advisory-nudge/codex-review-route/spec.md) | UserPromptSubmit | Warn when `/codex:review` runs in a multi-worktree repo (cwd mismatch risk) |
| [external-write-path-existence-check](../../hooks/advisory-nudge/external-write-path-existence-check/spec.md) | PreToolUse | Advisory nudge when a `gh issue/pr` body file contains markdown links to repo paths that do not exist |
| [path-probe-gate](../../hooks/advisory-nudge/path-probe-gate/spec.md) | PreToolUse | Advisory nudge (opt-in strict: deny) when Write/Edit/NotebookEdit targets a nested worktree path whose parent has not been enumerated this session |
| [version-bump-evidence-check](../../hooks/advisory-nudge/version-bump-evidence-check/spec.md) | PreToolUse | Advisory nudge (opt-in strict) when `gh issue/pr` body describes an external version bump with no changelog URL, Fetched: line, or cross-reference matrix |
| [count-assertion-verify](../../hooks/advisory-nudge/count-assertion-verify/spec.md) | PreToolUse | Advisory nudge when `grep -c` with alternation (`\|` BRE or `\|` ERE/PCRE) runs without per-arm verification; prevents citing inflated alternation counts — issue #277 |

---

## postuse-correction

After-tool-execution hooks. Fire **after** a tool completes; emit corrective
context, patch false positives, or record tracking state for paired gates.

| Hook | Trigger | Purpose |
|------|---------|---------|
| [builtin-task-postuse](../../hooks/postuse-correction/builtin-task-postuse/spec.md) | PostToolUse | Correct upstream "agent spawn" false positives on `TaskCreate` / `TaskUpdate` / etc. |
| [pre-edit-md-escape-advisory](../../hooks/postuse-correction/pre-edit-md-escape-advisory/spec.md) | PreToolUse(Edit) (`pre-edit-md-escape-advisory-pre`) + PostToolUse(Read) (`pre-edit-md-escape-advisory-post`) | Advisory nudge when Edit on a `.md` file carries escape-sensitive tokens without a recorded Read in the session |

---

## completion-verify

Stop hooks that gate **completion claims** before the assistant response is
finalized. Run sequentially: `completion-verify` → `retrospect-mix-check` →
`completion-signal-gate` → `strike-counter stop`. Also includes
session-lifecycle enforcement.

| Hook | Trigger | Purpose |
|------|---------|---------|
| [completion-verify](../../hooks/completion-verify/completion-verify/spec.md) | Stop | Block "done / 완료" claims without same-turn Bash verification evidence |
| [retrospect-mix-check](../../hooks/completion-verify/retrospect-mix-check/spec.md) | Stop | Block retrospect Stage 3 outputs that default findings to memory-only |
| [completion-signal-gate](../../hooks/completion-verify/completion-signal-gate/spec.md) | Stop | Advisory nudge when completion-signal phrase appears without evidence-block; also flags cross-plugin slash commands (Event 2) |
| [strike-counter](../../hooks/completion-verify/strike-counter/spec.md) | SessionStart + UserPromptSubmit + Stop | Session-scoped three-strike discipline — hard-blocks at strike 3, requires reflection before reset |
