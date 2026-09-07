# PreToolUse gh pr merge Worktree-Precondition Gate

Supported hosts: all

`hooks/preflight-gate/gh-merge-worktree-precondition/impl.py` fires on every
PreToolUse(Bash) event. When the command is `gh pr merge ... --delete-branch`
(or `-d`), it resolves the PR's live head branch via `gh pr view` and checks
whether that branch is still checked out in another `git worktree`. If so,
the merge is guaranteed to fail with `cannot delete branch '<branch>' used by
worktree at '<path>'` — the hook blocks (exit 2) before the doomed command
runs, instead of letting it fail and requiring manual recovery.

## Why this exists

`gh pr merge --delete-branch` fails deterministically when the head branch is
still checked out in a worktree, and the check is cheap to run first — yet the
constraint kept being rediscovered at merge time instead of checked before it.
The praxis `worktree-merge-cleanup` skill is the on-demand home for the
required manual sequence (remove the head-branch worktree, then merge) after
this exact failure mode recurred across PRs #147, #170-173, #175, and #796 in
this project — six documented occurrences (issue #798).

A retrospect session on the PR #796 merge (2026-07-16) root-caused why the
existing `feedback_worktree_context_pre_git_op.md` memory never prevented the
6th recurrence: that memory's `hookable`/`hookKeywords` frontmatter was meant
to surface the rule via the `memory-hint` PreToolUse hook, but `memory-hint`'s
`resolve_memory_dir()` fallback path had an independent bug (see issue #799)
that silently disabled the entire hookable-memory mechanism for this user's
environment. `memory-hint` is a soft textual nudge even when working — this
hook is the structural enforcement layer for the specific, deterministic
`--delete-branch` failure mode, so the check does not depend on any memory
retrieval path at all.

## Detection

1. `tool_name == "Bash"` — non-Bash tools exit 0 silently.
2. Tokenize with `_hook_utils.safe_tokenize` + `iter_command_starts` and scan
   every command segment for `gh pr merge`. `gh` global flags
   (`-R/--repo`/`--hostname`/`--color`) are inherited cobra flags that gh
   accepts in any position — before `pr`, between `pr` and `merge`, and after
   `merge` — so the walker skips them (capturing a repo selection) wherever
   they appear.
3. Within that segment, `-d`/`--delete-branch` must be present — without it,
   `gh` never attempts to delete the local branch, so no worktree conflict
   can occur and the segment is skipped. The scan walks past values consumed
   by value-taking merge flags (`--body -d` is a body of `-d`, not a
   delete-branch request) and stops at `--`.
4. Extract the positional PR identifier (number / URL / branch name), if
   any, walking past value-taking flags — `gh pr merge`'s own
   (`-A/--author-email`, `-b/--body`, `-F/--body-file`, `-t/--subject`,
   `--match-head-commit`) and the inherited global ones, so a post-`merge`
   `-R owner/repo` is never misread as the identifier.
5. Resolve the live head branch:
   `gh pr view <identifier> --json headRefName -q .headRefName`
   (`cwd` from the hook payload). When no identifier was parsed, `gh pr view`
   is called with **no positional argument** — gh then infers the PR from the
   current branch, exactly mirroring what `gh pr merge` itself would do with
   no identifier. A `-R/--repo` value on the merge command (separate or
   `--repo=` inline form) is forwarded to `gh pr view` so the PR resolves in
   the repository the merge actually targets, not the payload `cwd`'s repo.
6. List worktrees: `git worktree list --porcelain`, parsed into
   `{branch_name: worktree_path}`.
7. If the resolved head branch is a key in that map → **block**, citing the
   conflicting worktree path. Otherwise → pass.

## What is blocked

| Scenario | Action |
| ---------- | -------- |
| `gh pr merge N --squash --delete-branch`, head branch checked out in another worktree | block (exit 2) |
| `gh pr merge N --squash -d`, same conflict (short flag) | block (exit 2) |
| `gh pr merge N --squash` (no delete-branch flag) | pass — no local-branch deletion is attempted |
| `gh pr merge N --squash --delete-branch`, head branch not checked out anywhere | pass — no conflict |
| `gh pr merge --squash --delete-branch` (no identifier) | resolved via `gh pr view`'s own current-branch inference, then checked the same way |
| `gh -R owner/repo pr merge N --delete-branch` | detected — global flags are walked past, and the repo selection is forwarded to `gh pr view` |
| `gh pr -R owner/repo merge N -d` / `gh pr merge -R owner/repo -d` | detected — inherited flags are accepted in any position, repo forwarded, and the repo value is never misread as the PR identifier |
| `gh pr merge N --body -d` | pass — `-d` here is the value of `--body`, not a delete-branch flag |
| `gh pr view N ...` / other non-`merge` gh subcommands | pass — subcommand check requires exactly `pr merge` |

## Scope decision — compound commands not special-cased

The `worktree-merge-cleanup` skill's "Unified post-merge cleanup sequence"
explicitly instructs agents NOT to collapse `git worktree remove` +
`gh pr merge` into a single
`&&`-chain (a git hard error mid-chain silently short-circuits later steps
and trailing output from an earlier step is easily misread as success — the
exact failure mode `Bulk Operation Pre-Enumeration` and this project's own
worktree-cleanup guidance warn about). This hook therefore does not scan
earlier segments of the same compound command for a preceding
`git worktree remove` that would have already resolved the conflict — adding
that would require path-order-sensitive reasoning in service of a pattern the
project's own convention already discourages. Run the two steps as separate
Bash calls, per the `worktree-merge-cleanup` skill's "Unified post-merge
cleanup sequence."

## Fail-open

Any infrastructure error exits 0 (allow) — this hook only blocks on a
**positively confirmed** worktree conflict, never on an inability to
determine one:

- `gh` binary missing, `gh pr view` non-zero exit, timeout (5s), or empty
  `headRefName` output → cannot resolve the head branch → pass
- `git` binary missing, `git worktree list` non-zero exit, or timeout (5s) →
  cannot enumerate worktrees → pass
- Malformed stdin JSON → pass
- `PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE` set to any non-empty value → pass
  unconditionally (checked first, before any subprocess call)

Wrapped in `@fail_open` (`_hook_runtime`) as a second layer — any uncaught
exception also exits 0 rather than blocking a legitimate merge.

## Relationship to `pre-merge-approval-gate`

Both hooks fire on `gh pr merge` PreToolUse(Bash) events and share the same
`gh` global-flag-walking / subcommand-detection shape, but check unrelated
preconditions and can both fire on the same command (Claude Code runs
PreToolUse hooks in parallel per Anthropic's docs — `deny`/block from either
hook wins):

| Hook | Checks | Decision |
| ------ | -------- | ---------- |
| `pre-merge-approval-gate` | Whether the command is a `gh pr merge` at all — every session is gated | `ask` (never blocks outright) |
| `gh-merge-worktree-precondition` (this hook) | Whether `--delete-branch`'s target branch is checked out in another worktree | `deny` (exit 2) on a confirmed conflict |

## Tests

```bash
bash tests/hooks/preflight-gate/test_gh_merge_worktree_precondition.sh
```

18 cases: block on confirmed conflict (long and short delete-branch flags,
with and without an explicit PR identifier, with `-R`/`--repo=` in every
placement gh accepts — before `pr`, between `pr` and `merge`, after `merge`,
trailing — whose value must be forwarded to `gh pr view` and never misparsed
as the PR identifier, asserted by a repo-required fake `gh`), pass on
no-delete-branch (including `--body -d`, where `-d` is an option value) /
no-conflict / non-Bash / unrelated-command segments, fail-open on
`gh pr view` error / empty output / malformed stdin JSON, and the
`PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE` opt-out. Block
assertions require the stderr message to name the conflicting worktree path,
so a crash cannot masquerade as a gate decision. Worktree
conflict cases run against a real temporary git repo + `git worktree add`
(worktree state cannot be faked without a real `.git`); `gh` calls are
short-circuited via a per-case fake-bin shim prepended to `PATH`.
