# UserPromptSubmit Codex Review Worktree Disambiguation

Supported hosts: claude (excludes codex — false-positive on Codex /codex:review command)
Requires: codex-plugin (the openai-codex plugin defines the /codex:review prompt this matcher keys on)

`hooks/codex-review-route.sh` (generated launcher for
`hooks/advisory-nudge/codex-review-route/impl.py`) fires on every
`UserPromptSubmit` event and emits an `additionalContext` warning when the
user invokes `/codex:review` in a multi-worktree repository.

The body was a shell script until issue #1304 ported it to Python (the
first of the four shell hooks, smallest first). Stdout is byte-identical to
the shell version — same trigger regexes, same worktree block parsing, same
message text, same pretty-printed JSON shape — so nothing downstream had to
change.

### Why this exists

`/codex:review` (owned by the external `openai-codex` plugin) executes its
companion script through Claude Code's Bash tool, whose cwd resets to the
session root between calls. In a multi-worktree session — common when a
parent worktree holds `main` and a sibling worktree holds an issue branch —
the companion's `git diff` runs from the parent cwd, not the issue
worktree. The result is an empty or wrong-target review.

`praxis:codex-review-wrap` solves this by enumerating worktrees, prompting
for explicit selection, and delegating to `/codex:review` with the correct
cwd. But users routinely forget to use it and reach for `/codex:review`
directly. This hook detects that pattern and primes Claude to redirect.

### What is warned

The hook emits up to two independent advisories per trigger. Neither blocks the prompt.

**Advisory 1 — Multi-worktree** (bare `/codex:review` only):

Emits when **all** of the following hold:

| Gate | Condition |
| ------ | ----------- |
| Prompt prefix | `/codex:review` or `/codex-review` (whitespace-separated args allowed) |
| Worktree count | `git worktree list --porcelain` reports `>= 2` non-bare, non-prunable worktrees |

Suppressed for `codex-review-wrap` prompts — the skill handles disambiguation itself.

**Advisory 2 — PR state** (all triggers: `/codex:review`, `/codex-review`, `codex-review-wrap`):

Emits when the current branch's PR is `CLOSED` or `MERGED`. Requires `gh` CLI; fail-opens silently when `gh` is absent, when no PR exists for the branch, or when not in a git repo.

**Fail-safe.** Malformed JSON on stdin, a prompt that is missing or not a
string, a cwd outside any git repo, `gh` absent from `PATH`, a subprocess
that fails or times out, and any unexpected exception (caught by
`@fail_open`) all exit 0 with empty stdout. The hook never blocks a prompt.
The three subprocesses share one deadline derived from the manifest
`timeout` (`shared_probe_deadline`), so their combined wall-clock stays
inside the hook's budget; below the spawn floor a probe is skipped rather
than started.

**Fire ledger.** An emitted advisory is recorded as one RICH `advise` fire
via `_fire_ledger.record_session_fire` (hook `codex-review-route`, role
`advisory-nudge`, tool `""`), with the coarse duplicate suppressed. Silent
runs are recorded only by `@fail_open`'s coarse `pass` — the same split every
other advisory-nudge Python hook uses.

False-positive guards:

| Input | Action |
| ------- | -------- |
| `/codex:reviews` (trailing char) | silent — regex requires whitespace or end-of-line after `review` |
| `/codex:review-thing` (hyphenated suffix) | silent — same guard |
| `please /codex:review later` (mid-sentence) | silent — regex anchored to start-of-prompt |
| `/codex:status` (different command) | silent |
| Single-worktree repo, OPEN PR | silent — neither advisory fires |
| Bare repo + 1 linked worktree | silent — `bare` blocks excluded from the count, only the linked worktree is active |
| Not a git repo | silent — `git worktree list` and `gh pr view` both return nothing |
| Empty prompt | silent |
| Malformed JSON stdin | silent — payload parse failure is fail-open |
| No PR for current branch | silent — `gh pr view` exits non-zero, PR-state advisory skipped |

### Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "⚠️ Multi-worktree detected ... ask the user to run /praxis:codex-review-wrap ..."
  }
}
```

Claude reads the `additionalContext` alongside the user prompt and is
expected to redirect the user to the wrapper rather than dispatch the
codex companion against the wrong cwd. The hook does **not** block the
prompt — Claude can still proceed if the user has explicitly confirmed
the target worktree in the same turn.

### Why warn instead of block

Blocking would cause false positives in legitimate single-target reviews
where the user has already run `cd <target>` mentally / explicitly. The
warning gives Claude the discretion to redirect or proceed, which matches
how the rest of the praxis hook suite handles similar discretionary
escalations (memory-hint emits hints, side-effect-scan asks rather than
denies).

### Tests

```bash
bash tests/hooks/advisory-nudge/test_codex_review_route.sh
```

Covers 21 cases: 4 warn paths (bare, with flag, with `--model`,
hyphenated form), 8 silent paths (single-worktree, plain text, different
slash command, false-positive trailing chars, empty prompt, hyphenated
suffix, mid-sentence mention, bare-repo + 1 linked worktree), 2 fail-safe
paths (malformed JSON, non-git cwd), 7 PR-state guard paths (OPEN/CLOSED/MERGED/no-PR
for `/codex:review`, `codex-review-wrap` CLOSED and OPEN, plus mention-only
mid-sentence regression). Worktree state is fixtured via temporary `git init`
(and `git init --bare` for the bare-repo case); PR state is fixtured via mock
`gh` binaries injected into PATH — no real GitHub remote required. The test
pipes payloads straight into `impl.py`; the fire-ledger `advise` record is
covered end-to-end by `tests/hooks/_lib/test_record_fire.sh`.
