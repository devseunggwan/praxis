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

Issue #798. CLAUDE.md's "Pre-merge Worktree Precondition" section documents
the required manual sequence (remove the head-branch worktree, then merge)
after this exact failure mode recurred across PRs #147, #170-173, #175, and
#796 in this project — six documented occurrences of the same deterministic
git constraint being rediscovered at merge time instead of checked before it.

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
   every command segment for `gh pr merge` (mirrors
   `pre-merge-approval-gate`'s `is_gh_pr_merge` — `gh` global flags
   `-R/--repo`/`--hostname`/`--color` are walked past before the subcommand
   check).
3. Within that segment, `-d`/`--delete-branch` must be present — without it,
   `gh` never attempts to delete the local branch, so no worktree conflict
   can occur and the segment is skipped.
4. Extract the positional PR identifier (number / URL / branch name), if
   any, walking past `gh pr merge`'s own value-taking flags
   (`-A/--author-email`, `-b/--body`, `-F/--body-file`, `-t/--subject`,
   `--match-head-commit`).
5. Resolve the live head branch:
   `gh pr view <identifier> --json headRefName -q .headRefName`
   (`cwd` from the hook payload). When no identifier was parsed, `gh pr view`
   is called with **no positional argument** — gh then infers the PR from the
   current branch, exactly mirroring what `gh pr merge` itself would do with
   no identifier.
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
| `gh -R owner/repo pr merge N --delete-branch` | detected — global flags are walked past |
| `gh pr view N ...` / other non-`merge` gh subcommands | pass — subcommand check requires exactly `pr merge` |

## Scope decision — compound commands not special-cased

CLAUDE.md's own "Unified post-merge cleanup sequence" explicitly instructs
agents NOT to collapse `git worktree remove` + `gh pr merge` into a single
`&&`-chain (a git hard error mid-chain silently short-circuits later steps
and trailing output from an earlier step is easily misread as success — the
exact failure mode `Bulk Operation Pre-Enumeration` and this project's own
worktree-cleanup guidance warn about). This hook therefore does not scan
earlier segments of the same compound command for a preceding
`git worktree remove` that would have already resolved the conflict — adding
that would require path-order-sensitive reasoning in service of a pattern the
project's own convention already discourages. Run the two steps as separate
Bash calls, per the documented "Unified post-merge cleanup sequence."

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
| `pre-merge-approval-gate` | Direct session vs `CMUX_DELEGATE=1` background agent | `ask` (never blocks outright) |
| `gh-merge-worktree-precondition` (this hook) | Whether `--delete-branch`'s target branch is checked out in another worktree | `deny` (exit 2) on a confirmed conflict |

## Tests

```bash
bash tests/hooks/preflight-gate/test_gh_merge_worktree_precondition.sh
```

13 cases: block on confirmed conflict (long and short delete-branch flags,
with and without an explicit PR identifier, with a leading `gh` global
flag), pass on no-delete-branch / no-conflict / non-Bash / unrelated-command
segments, fail-open on `gh pr view` error / empty output / malformed stdin
JSON, and the `PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE` opt-out. Worktree
conflict cases run against a real temporary git repo + `git worktree add`
(worktree state cannot be faked without a real `.git`); `gh` calls are
short-circuited via a per-case fake-bin shim prepended to `PATH`.
