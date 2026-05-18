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
| [block-gh-state-all](block-gh-state-all.md) | PreToolUse | Hard-block invalid `gh search ... --state all` flag combo |
| [gh-flag-verify](gh-flag-verify.md) | PreToolUse | Block `gh <subcmd>` calls with flags not in the subcommand's accepted set |
| [block-ask-end-option](block-ask-end-option.md) | PreToolUse | Block `AskUserQuestion` options carrying end-option markers when no stop signal present |
| [block-manufactured-action-menu](block-manufactured-action-menu.md) | PreToolUse | Warn or block when `AskUserQuestion` surfaces a "shall we proceed?" menu after user already issued a command-intent signal |
| [block-pr-without-caller-evidence](block-pr-without-caller-evidence.md) | PreToolUse | Block `gh pr create` unless the PR body contains a `Caller chain verified:` line |
| [side-effect-scan](side-effect-scan.md) | PreToolUse | Ask before commands with collateral side effects (`git commit/push`, `gh pr merge/create`, `kubectl apply`) |
| [commit-title-length-check](commit-title-length-check.md) | PreToolUse | Ask when `git commit` title exceeds 50 chars |
| [pre-merge-approval-gate](pre-merge-approval-gate.md) | PreToolUse | Surface per-PR approval prompt for `gh pr merge` in direct sessions |
| [cross-boundary-preflight](cross-boundary-preflight.md) | PreToolUse | Block heredoc body in `gh pr/issue create`; ask with four-point checklist on cross-repo `--repo` writes |
| [cross-repo-worktree-preflight](cross-repo-worktree-preflight.md) | PreToolUse | Ask before `git worktree remove <abs-path>` when the path is not registered in cwd repo's worktree list |
| [pre-edit-protected-branch-guard](pre-edit-protected-branch-guard.md) | PreToolUse | Block Edit/Write/NotebookEdit on protected branches (main/dev/prod/master) outside the expected worktree workflow |
| [pre-gh-pr-create-dedup-gate](pre-gh-pr-create-dedup-gate.md) | PreToolUse | Run `gh pr list --search` before `gh pr create`; hard-block on duplicate or repo-resolution failure |
| [verify-commit-flag-override](verify-commit-flag-override.md) | PreToolUse | Deny `git commit` invocations that override hooks / signing without env verification |
| [session-intent](session-intent.md) | UserPromptSubmit + PreToolUse | Gate read-intent → mutation-pivot session drift on `gh` mutating commands |
| [trino-describe-first](trino-describe-first.md) | PreToolUse (`trino-describe-first-pre`) + PostToolUse (`trino-describe-first-post`) | Require `DESCRIBE <table>` before Trino MCP query references that table |
| [trino-catalog-gate](trino-catalog-gate.md) | PreToolUse | Block Trino MCP queries with 3-part catalog references when `SHOW CATALOGS` has not run in this session |

---

## advisory-nudge

Pre-tool stderr hints. **Never block** — emit reminders for recurrent patterns
so the agent can self-correct. Fail-open on infrastructure errors by design.

| Hook | Trigger | Purpose |
|------|---------|---------|
| [cli-flag-incompat-advisory](cli-flag-incompat-advisory.md) | PreToolUse | Advisory nudge for known mode-incompatible flag combos (`git merge-tree --name-only` 3-arg form, `kubectl --use-protocol-buffers`) |
| [memory-hint](memory-hint.md) | PreToolUse | Surface hookable memory entries by keyword at decision-construction time |
| [external-write-falsify-check](external-write-falsify-check.md) | PreToolUse (opt-in) | Warn before posting hypothesis-stage text to PR / issue / Slack / Notion |
| [external-api-literal-trigger](external-api-literal-trigger.md) | PreToolUse | Advisory nudge when ALL_CAPS enum candidates or 3-part SQL identifiers are written without prior retrieval verification |
| [output-block-falsify-advisory](output-block-falsify-advisory.md) | PreToolUse | Advisory nudge to run output-block falsification gate before surfacing `(Recommended)` options or bulk-action commands |
| [advisory-wrapper-signature-verify](advisory-wrapper-signature-verify.md) | PreToolUse | Advisory nudge to verify wrapped function signatures before writing wrapper/client code |
| [jq-config-empty-dict-advisory](jq-config-empty-dict-advisory.md) | PreToolUse | Advisory nudge when `jq` reads a config file (settings.json, hooks.json, ~/.claude/*.json, ~/.codex/*.json) that is empty or invalid JSON |
| [codex-review-route](codex-review-route.md) | UserPromptSubmit | Warn when `/codex:review` runs in a multi-worktree repo (cwd mismatch risk) |
| [external-write-path-existence-check](external-write-path-existence-check.md) | PreToolUse | Advisory nudge when a `gh issue/pr` body file contains markdown links to repo paths that do not exist |

---

## postuse-correction

After-tool-execution hooks. Fire **after** a tool completes; emit corrective
context, patch false positives, or record tracking state for paired gates.

| Hook | Trigger | Purpose |
|------|---------|---------|
| [builtin-task-postuse](builtin-task-postuse.md) | PostToolUse | Correct upstream "agent spawn" false positives on `TaskCreate` / `TaskUpdate` / etc. |
| [pre-edit-md-escape-advisory](pre-edit-md-escape-advisory.md) | PreToolUse(Edit) (`pre-edit-md-escape-advisory-pre`) + PostToolUse(Read) (`pre-edit-md-escape-advisory-post`) | Advisory nudge when Edit on a `.md` file carries escape-sensitive tokens without a recorded Read in the session |

---

## completion-verify

Stop hooks that gate **completion claims** before the assistant response is
finalized. Run sequentially: `completion-verify` → `retrospect-mix-check` →
`strike-counter stop`. Also includes session-lifecycle enforcement.

| Hook | Trigger | Purpose |
|------|---------|---------|
| [completion-verify](completion-verify.md) | Stop | Block "done / 완료" claims without same-turn Bash verification evidence |
| [retrospect-mix-check](retrospect-mix-check.md) | Stop | Block retrospect Stage 3 outputs that default findings to memory-only |
| [strike-counter](strike-counter.md) | SessionStart + UserPromptSubmit + Stop | Session-scoped three-strike discipline — hard-blocks at strike 3, requires reflection before reset |
