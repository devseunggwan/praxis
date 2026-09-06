---
name: worktree-merge-cleanup
description: >
  On-demand home for the pre-merge worktree precondition and the unified
  post-merge cleanup sequence — how to run `gh pr merge --squash --delete-branch`
  from the base-branch worktree, the submodule `--force` caveat, the
  squash-ancestry stale-HEAD guard, and the no-`&&`-chain rule.
when_to_use: >
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
   on remote (gh order: remote merge → local sync). Manual cleanup — run each as
   a **separate** Bash call, never chained (a failure in one step must not
   short-circuit the rest): `git worktree remove <path>`, then
   `git branch -D <branch>`, then `git worktree prune` (snapshot and dry-run
   it first — see [prune is repository-wide](#prune-is-repository-wide--snapshot-before-running-it)),
   then `git pull origin <base>`.
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
git worktree list --porcelain \
  > "$(git rev-parse --path-format=absolute --git-common-dir)/wt-before-<N>.txt"
git worktree prune --dry-run -v                             # what would go — read it
```

**STOP here and read the dry-run output.** `--dry-run` reports candidates; it
is not a confirmation barrier, so the two commands below must not be pasted
together with the block above. If anything other than your own worktree
appears, surface it to the user before continuing. Only then:

```bash
git worktree prune
git worktree list --porcelain | grep '^worktree ' \
  | diff <(grep '^worktree ' \
      "$(git rev-parse --path-format=absolute --git-common-dir)/wt-before-<N>.txt") -
```

Even if stdout shows only "deleted remote branch" or is empty, verify local
cleanup separately: confirm with `git branch | grep <feature-branch>` **and**
diff the post-prune registry against the snapshot. Both sides must be filtered
the same way — the plain `git worktree list` output is a different format from
`--porcelain`, so comparing one against the other never matches. Your own
worktree left the registry back at checklist item 2, so it is already absent
from the snapshot: when the dry-run printed nothing, the `diff` must print
nothing either — assuming no one added or removed a worktree in this
repository while the sequence was running. Any entry it reports as removed
belonged to someone else.

### `prune` is repository-wide — snapshot before running it

`git worktree prune` takes no target. It drops the registration of **every**
unlocked worktree whose directory is gone, including worktrees belonging to
other issues that you never touched. Two bounds on that blast radius: it only
removes registrations for directories that are already missing — it never
deletes a live worktree — and a registration held by `git worktree lock`
survives even when its directory is gone. Neither bound is visible after the
fact unless you captured evidence first.

- **Before**: `git worktree list --porcelain` records every registration and
  marks the already-missing, unlocked ones `prunable` (a locked entry shows
  `locked` instead and is not a prune target). Park it at
  `$(git rev-parse --path-format=absolute --git-common-dir)/wt-before-<N>.txt`,
  substituting the PR number you are cleaning up for `<N>`. Not a shell
  variable and not a bare `wt-before.txt`: the steps run as separate Bash calls
  so a `snap=$(mktemp)` variable is gone by the next call, while the common dir
  recomputes to the same path every time — and the `<N>` suffix keeps two
  concurrent cleanups in the same repository from overwriting each other's
  baseline. `--path-format=absolute` is not optional: a bare
  `git rev-parse --git-common-dir` answers `.git` in the main worktree and
  `../.git` one directory down, so without it the snapshot and the diff can
  resolve to different files. This snapshot is
  the only thing that later answers "was that sibling worktree already gone, or
  did I break it?" — without it the question is unanswerable, which is worse
  than the removal itself. Write the **unfiltered** porcelain output: the
  `prunable` and `locked` lines are precisely the forensic evidence, and a
  snapshot filtered at write time cannot answer the question it exists for.
- **Dry run**: `git worktree prune --dry-run -v` (`-n` / `-v`, confirmed via
  `git worktree prune -h`) prints what it would remove without touching
  anything. If anything other than your own worktree appears, stop and surface
  it to the user before running the real prune.
- **After**: diff `git worktree list --porcelain` against the snapshot — the
  same format on both sides, or the comparison is meaningless. Filter both
  through `grep '^worktree '` **at compare time**: the question is only which
  *registrations* vanished, and an unfiltered diff also reports the `HEAD` /
  `branch` lines that any parallel worktree changes by committing or switching,
  which reads as a removal that never happened. The existing `git branch | grep` check only
  proves *your* branch is gone; a sibling registration can disappear and still
  pass it.

Recovering a registration removed this way is out of scope here — see
[Limitations](#limitations) (`git worktree add`, never `git clone`).

⚠️ Do NOT collapse the sequence into a single `&&`-chain. A git hard error
mid-sequence (submodule worktree missing `--force`, divergent pull)
short-circuits `&&`, silently skips remaining steps, and trailing output from an
earlier step is easily misread as "cleanup done". Run steps separately; after a
fragile step (worktree remove / pull) fails, re-verify primitive state with
`git worktree list` / `git branch | grep`.

## Squash merge under bulk cleanup — the merged-ness oracle

The unified sequence above cleans up **one** worktree right after its own PR
merge, where `gh pr merge` itself is the merged-ness proof. Bulk cleanup of N
already-merged local branches (no `gh pr merge` call in the loop) needs its
own oracle, and under a squash-merge repo the obvious ones are wrong.

- **`git branch --merged` is invalid.** It answers "is this branch's tip an
  ancestor of `<base>`?" — true for a fast-forward or merge-commit, but a
  squash merge creates a brand-new commit on `<base>` that is never an
  ancestor of the original branch tip. A genuinely merged squash branch shows
  up as **not** merged by this check.
- **`git diff base...branch` (or `--stat`) is equally invalid**, for the
  mirror reason: 3-dot diff shows the branch's unique commits relative to
  their merge-base with `<base>`, and squash does not rewrite or remove those
  commits from the branch ref — it only adds a new, unrelated commit to
  `<base>`. A squash-merged branch still reports the same non-empty diff a
  never-merged branch would, so an empty-diff check can never fire true and a
  non-empty one can never rule the branch out.
- **Valid oracle — tip SHA vs `headRefOid`, plus PR `state` and `baseRefName`.**
  Compare the branch's local tip against the PR's recorded head SHA, then check
  the PR closed as merged *into the base you are cleaning up*:

  ```bash
  git rev-parse <branch>
  gh pr list --search "head:<branch>" --state merged \
    --json number,headRefOid,state,mergedAt,baseRefName
  ```

  `--state merged` is **not optional**. `gh pr list` defaults to `--state open`
  (`gh pr list --help`), so without it a squash-merged branch — the only kind
  this procedure ever looks at — comes back as `[]` and the `state == MERGED`
  check can never be reached. The failure is silent and looks exactly like "no
  PR found", which routes every merged branch into the manual fallback path.

  (`gh pr view <N> --json headRefOid,state,mergedAt,baseRefName` works the same
  way when the PR number is already known, and needs no `--state` because it
  addresses one PR directly.)

  The branch is safe to delete only when **all three** hold:

  1. the local tip SHA equals `headRefOid`;
  2. `state` is `MERGED` (not `OPEN`/`CLOSED`);
  3. `baseRefName` equals the `<base>` you are cleaning up.

  `state` alone is insufficient — a PR can be merged and the local branch since
  amended with unpushed commits, in which case the tip SHA no longer matches the
  merged head and the extra commits would be lost by deleting it. `baseRefName`
  matters because this skill covers `main`/`dev`/`prod` bases: a branch merged
  into `dev` but not yet into `prod` satisfies conditions 1 and 2 while cleaning
  up `prod`, and deleting it there discards work `prod` has never seen.
- Do not write verdict language ("safe to delete", "confirmed merged") before
  this two-step check has actually run against live `gh` output — a plausible
  guess from branch name or commit message is not evidence.

### Bulk branch-delete procedure

1. **Enumerate candidates**: list local branches to review (e.g.
   `git branch --format='%(refname:short)'`, excluding the base branch).
2. **Look up each branch's PR** with
   `gh pr list --search "head:<branch>" --state merged --json number,headRefOid,state,mergedAt,baseRefName`.
   - `--state merged` is required; the default is `open`, which returns `[]`
     for exactly the branches this procedure exists to clean up.
   - **No PR found** (never pushed, or pushed to a fork/deleted remote): this
     branch is not tracked by the oracle above — fall back to a separate,
     manual path (inspect the branch's own commits / confirm with the user)
     rather than assuming it is safe or unsafe. If *every* branch reports "no
     PR found", suspect the query before suspecting the branches — a dropped
     `--state merged` produces precisely that pattern.
3. **Confirm `state == "MERGED"`.** `OPEN` or `CLOSED` (without `mergedAt`)
   means do not delete.
4. **Compare tip SHA**: `git rev-parse <branch>` must equal the PR's
   `headRefOid`. A mismatch means the local branch has commits the merged PR
   never saw — do not delete without inspecting the difference first.
5. **Confirm `baseRefName == <base>`** — the branch must have merged into the
   base you are cleaning up, not a sibling base.
6. Only after all three checks pass, `git branch -D <branch>` (`-D` because the
   squash commit is not an ancestor of the branch tip, so `-d` refuses with
   "not fully merged" even though the PR is in fact merged).

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
- The bulk branch-delete oracle covers branches with a discoverable PR only
  (`gh pr list --search "head:<branch>"` returns a result). A branch pushed to
  a fork, or whose PR/remote was deleted, has no `headRefOid` to compare
  against — that case is out of scope here and needs a separate, manual
  review path.
