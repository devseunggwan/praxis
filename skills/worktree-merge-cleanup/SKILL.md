---
name: worktree-merge-cleanup
description: >
  On-demand home for the pre-merge worktree precondition and the unified
  post-merge cleanup sequence — how to run `gh pr merge --squash --delete-branch`
  from the base-branch worktree, the submodule `--force` caveat, the
  squash-ancestry stale-HEAD guard, and the no-`&&`-chain rule.
  Triggers on "merge cleanup", "post-merge cleanup", "worktree cleanup",
  "delete-branch merge", "squash-ancestry", "pre-merge worktree", "머지 후 정리",
  "worktree 정리". Do NOT activate on "merge conflict resolution" or a plain
  feature-branch "git merge".
verified-against-runtime: true
runtime-verified-at: 2026-07-17
runtime-verified-note: "gh 2.93.0 + git 2.50.1 — `gh pr merge -s/--squash`, `-d/--delete-branch`, and `git worktree remove -f/--force` confirmed present via --help."
---

# worktree-merge-cleanup

## Overview

`gh pr merge --squash --delete-branch` does a local checkout of the base branch
(main / dev / prod) in the post-squash stage, and it deletes the local head
branch by reachability against the local base HEAD. Both facts make the command
sensitive to *which worktree* it runs from and *whether the base is synced* —
get either wrong and the merge fails at the git layer or silently skips the
local-branch delete, forcing manual recovery.

This skill is the on-demand home for that procedure. The always-loaded rule set
keeps only a pointer here; the deterministic `--delete-branch` failure mode is
additionally enforced structurally by the `gh-merge-worktree-precondition`
PreToolUse hook (see [Relationship to enforcement](#relationship-to-enforcement)).

**Core principle:** run the post-merge cleanup steps as *separate* Bash calls,
never collapsed into one `&&`-chain — a git hard error mid-sequence
short-circuits the chain and its trailing output is easily misread as success.

## When to Use

- About to merge a PR with `gh pr merge --squash --delete-branch` from an
  issue-branch worktree.
- The merge just failed with `fatal: '<base>' is already used by worktree at ...`
  or `cannot delete branch 'X' used by worktree at ...`.
- After a squash-merge the local feature branch was not deleted
  (`warning: deleting branch 'X' ... not yet merged to HEAD`).
- Cleaning up a worktree that contains a submodule.

## Pre-call checklist

`gh pr merge --squash --delete-branch` must be **called from the worktree that
owns the base branch**. Calling from an issue-branch worktree triggers the git
exclusive-ref guard:

```text
fatal: 'main' is already used by worktree at /Users/.../<main-worktree>
```

1. Confirm the worktree occupying the base branch with `git worktree list`.
2. **If `--delete-branch` is included**: remove the PR head branch's worktree
   first (`git worktree remove <issue-worktree-path>`). The post-merge local
   branch delete fails if the branch is still occupied — `cannot delete branch
   'X' used by worktree at ...` — even when invoked from the base (main)
   worktree, because the failing branch is the *head*, not the base. After
   removal, re-run `git worktree list` to confirm.
   - ⚠️ **Submodule-containing worktree**: plain `git worktree remove` is
     rejected with `fatal: working trees containing submodules cannot be moved
     or removed`. Use `git worktree remove --force <path>` **only after
     confirming the worktree is clean** (`git -C <path> status --porcelain`
     empty). `--force` destroys uncommitted changes in a dirty worktree, so
     never make it the default.
3. `cd <base-worktree-path>` or chain `cd <path> && gh pr merge ...` in the same
   Bash call (avoid cwd-reset trap between Bash calls).
4. Even if the call fails on a worktree conflict, the PR may already be merged
   on remote (gh order: remote merge → local sync). Manual cleanup:
   `git worktree remove <path> && git branch -D <branch> && git worktree prune &&
   git pull origin <base>`.
5. **Squash-ancestry stale HEAD guard (post-merge sync mandatory)**: immediately
   after `gh pr merge --squash --delete-branch`, run `git pull origin
   <base-branch>` as a **separate step**. gh merges remote then deletes local
   branch by reachability against local HEAD — but does not fetch the local
   base, so if local base is still at pre-merge SHA the delete silently skips
   (`warning: deleting branch 'X' that has been merged to 'refs/remotes/origin/X',
   but not yet merged to HEAD`). Distinct from item 4's worktree case — only
   `git pull` fixes it, not `git worktree remove`.

## Unified post-merge cleanup sequence

Covers both sub-cases (worktree conflict + squash-ancestry gap):

```bash
cd <base-worktree-path>
git switch <base-branch>
gh pr merge <N> --squash --delete-branch
git pull origin <base-branch>                              # resolve squash-ancestry gap
git branch -d <feature-branch> 2>/dev/null \
  || git branch -D <feature-branch>                        # fallback (may already be deleted)
git worktree prune
```

Even if stdout shows only "deleted remote branch" or is empty, verify local
cleanup separately: confirm with `git branch | grep <feature-branch>`.

⚠️ Do NOT collapse the sequence into a single `&&`-chain. A git hard error
mid-sequence (submodule worktree missing `--force`, divergent pull)
short-circuits `&&`, silently skips remaining steps, and trailing output from an
earlier step is easily misread as "cleanup done". Run steps separately; after a
fragile step (worktree remove / pull) fails, re-verify primitive state with
`git worktree list` / `git branch | grep`.

## Relationship to enforcement

| Layer | Home | Covers |
| ------- | ------ | -------- |
| Structural enforcement | `gh-merge-worktree-precondition` PreToolUse hook | Blocks `gh pr merge ... --delete-branch` when the head branch is still checked out in another worktree — one deterministic slice of checklist item 2 |
| On-demand procedure (this skill) | `worktree-merge-cleanup` | The full manual sequence: base-worktree call site, submodule `--force`, squash-ancestry stale-HEAD guard, unified cleanup, no-`&&`-chain |
| Always-loaded | user-level rule set | A pointer to this skill only |

The hook is the automated guard for the single failure mode that can be checked
before the command runs; this skill is the reference for everything the hook
does not (and structurally cannot) enforce.

## Limitations

- The sequence assumes the squash-merge workflow (`--squash`). A merge-commit or
  rebase-merge strategy has different local-branch reachability semantics and is
  out of scope here.
- Worktree-recovery after an accidental removal is a separate concern (recover
  via `git worktree add`, never `git clone`).
