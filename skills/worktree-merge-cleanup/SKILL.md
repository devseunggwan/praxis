---
name: worktree-merge-cleanup
description: >
  Merge a PR from the right worktree and clean up after it: run
  `gh pr merge --squash` from the base-branch worktree, remove the head
  worktree only after `MERGED`, use the submodule `--force` path (never
  `git submodule deinit` a linked worktree: it strips the shared submodule
  url), guard against the squash-ancestry stale HEAD, and keep each cleanup
  step its own command.
when_to_use: >
  Triggers on "merge cleanup", "post-merge cleanup", "worktree cleanup",
  "delete-branch merge", "squash-ancestry", "pre-merge worktree", "머지 후 정리",
  "worktree 정리", "PR merged", "머지했습니다" (the user reporting a merge),
  and on removing ANY worktree (load it BEFORE the first removal command) or
  cleaning up after a merge — including the worktree of a PR that was
  closed without merging ("닫았습니다" → "정리해주세요") — or when about to
  run `git submodule deinit` to get a worktree removed, and on the error
  `fatal: working trees containing submodules cannot be moved or removed`
  (load it before reacting: the reflexive fixes, `deinit` or moving the
  directory aside and pruning, are both the wrong path).
  Do NOT activate on "merge conflict resolution" or a plain
  feature-branch "git merge".
verified-against-runtime: true
runtime-verified-at: 2026-10-14
runtime-verified-note: "gh 2.102.0 + git 2.50.1 — out-of-band path run live: `git worktree remove --force` on a clean submodule worktree, `git branch -D`, `git push origin --delete`, then `git ls-remote origin refs/heads/<branch>` empty; earlier: `gh pr merge -s/-d` confirmed via --help."
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
additionally enforced structurally by the `merge-gate` (worktree check)
PreToolUse hook (see [Relationship to enforcement](#relationship-to-enforcement)).

**Default order:** merge without `--delete-branch`, confirm `MERGED`, then
remove the head worktree and branches. Removing the worktree first (which
`--delete-branch` requires) loses it when the merge is then blocked or
interrupted, and cuts off any other session working in it. Run `gh pr merge` as
its own command, with nothing chained before or after it: a merge gate scores
the whole command line, so a `git worktree remove ... && gh pr merge` chain gets
blocked as a unit and neither half runs.

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
- Removing the worktree and head branch of a PR the user closed without
  merging. The removal mechanics are the same as below (clean-check, submodule
  `--force` without `deinit`, explicit workdir afterwards, remote tip equals
  the PR's `headRefOid` before `push origin --delete`), but a post-merge
  standing approval does not cover it: list the worktree and branches and ask
  once. Load this skill before the first removal command even for a one-off
  closed PR — skipping it is how the shared-config `deinit` damage repeats.
- Cleaning up many stale worktrees across repos at once → follow
  `references/bulk-stale-worktree-cleanup.md` (inventory, classification order,
  squash-safe content oracle, regenerable-artifact list, checkpointed background run,
  and the stash/keep-branch pass for removing holds when the user asks for all).

## Pre-call checklist

`gh pr merge --squash --delete-branch` must be **called from the worktree that
owns the base branch**. Calling from an issue-branch worktree triggers the git
exclusive-ref guard:

```text
fatal: 'main' is already used by worktree at /Users/.../<main-worktree>
```

1. Confirm the worktree occupying the base branch with `git worktree list`.
2. **Merge without `--delete-branch`; remove the head worktree only after
   `gh pr view <N> --json state` reads `MERGED`** (`git worktree remove
   <issue-worktree-path>`, then the branches, per the unified sequence
   below). With `--delete-branch` gh deletes the local head branch itself,
   so the worktree has to go first and is lost when the merge is then
   blocked; pass the flag only for a worktree no other session uses and a
   merge nothing can block. If the shell's working
   directory is that worktree, every later shell call fails at its implicit
   `cd` once the directory is gone — run the remove from (or with `-C`) the base
   worktree, and give every following call an explicit working directory
   (`workdir`, `git -C`, `--repo`) instead of retrying. On Hermes the
   terminal tool's persistent cwd is applied before your command runs, so a
   `cd <base> && …` inside the command still fails with `cd: <removed path>:
   No such file or directory` — set the tool's `workdir` parameter.
   Likewise, step 5's scratch deletion removes only files you created for
   this PR: list them with mtimes and leave older files other sessions wrote. The post-merge local
   branch delete fails if the branch is still occupied — `cannot delete branch
   'X' used by worktree at ...` — even when invoked from the base (main)
   worktree, because the failing branch is the *head*, not the base. After
   removal, re-run `git worktree list` to confirm.
   - ⚠️ **The worktree is the agent session's own working folder** (Hermes desktop freezes a chat's cwd at creation; check the runtime `Current working directory` / the turn's workdir): removing it kills the session — every later message fails with "working folder no longer exists" before any model call. Still remove it if the cleanup is approved, but say in the same report that this chat must be moved (desktop **Move to project**) to a live folder before the next message, and name the folder (base worktree or the next issue's worktree).
   - ⚠️ **Something live may still run from the worktree**: before removing it, check that no symlink points into it (an agent's plugin or skills directory linked to the worktree loads from it: `ls -la <load dir>`; a sibling worktree of the same repo whose `node_modules` or `.venv` is a symlink to this one breaks on removal, so check every registered worktree, wherever it lives: for each path in `git worktree list --porcelain`, run `find <path> -maxdepth 2 -lname '*<worktree dir name>*'`, and give each hit its own install first) and that no process has its cwd there (`lsof -d cwd 2>/dev/null | grep -F "<worktree path>"`, which also catches a subdirectory without the recursive walk of `+D`; `pgrep -fl <worktree path>` matches command lines only and misses a process that merely `cd`'d there). Stop or repoint those first and name them in the report.
   - ⚠️ **The `codex-review-wrap` companion leaves its app-server broker running with the reviewed worktree as cwd.** Find it (`lsof -a -p <pid> -d cwd` on the `app-server-broker.mjs serve --cwd <worktree>` process) and stop only that pid before removing the worktree — never a broad `pkill`.
   - ⚠️ **Submodule-containing worktree**: plain `git worktree remove` is
     rejected with `fatal: working trees containing submodules cannot be moved
     or removed`. Use `git worktree remove --force <path>` **only after
     confirming the worktree is clean** (`git -C <path> status --porcelain`
     empty, and no `!!` line from `--porcelain --ignored` other than a
     regenerable dependency or build cache — `remove` deletes ignored files too). `--force` destroys uncommitted changes in a dirty worktree, so
     never make it the default. Never run `git submodule deinit` in a linked worktree as a
     cleanup step: it does not lift the rejection (the check looks at the
     registered submodule, not its checkout), and it deletes
     `submodule.<name>.url` from the repository config that every worktree
     shares, so sibling worktrees lose their submodule registration. Go
     straight to the clean-check + `--force`. If a deinit already ran, restore
     the entry from the main checkout with `git submodule init <path>` and
     confirm with `git config --get submodule.<name>.url`.
     When the dispatch or user forbids `--force` ("remove without `--force`,
     stop if refused"), the refusal is the stop point: run the clean-check,
     report the worktree path, the branch it holds and its `!!` list, and touch
     nothing else — a `deinit` there is the same shared-config damage, not a
     softer alternative.
     Moving the directory aside and running `git worktree prune` is not a
     workaround either: it skips the clean-check `--force` relies on and runs a
     repository-wide prune; the clean-check + `--force` is the only path.
   - A lone `M <submodule path>` line (with a leading space) in the clean-check is often only the
     submodule checkout lagging the pointer a base merge recorded, not your
     edit. Confirm the submodule itself is clean
     (`git -C <path>/<submodule> status --porcelain --ignored` empty, or
     only regenerable `!!` lines) and that its current commit survives the
     update and the removal (`git -C <path>/<submodule> for-each-ref
     --contains HEAD refs/remotes` non-empty, so a remote holds it: a local
     branch does not count, because a linked worktree's submodule repository
     can live under the worktree's git dir and `worktree remove --force`
     deletes its refs and objects too; `branch -a --contains` is not enough
     either, it lists a detached HEAD as `* (no branch)`; otherwise stop and
     report), then run
     `git -C <path> submodule update --checkout <submodule path>` (no
     `--init`, never `deinit`) and re-read `status --porcelain`. When it comes back empty, the
     worktree is clean and the `--force` remove may proceed. A dirty submodule
     is a stop condition like any other dirty file.
3. `cd <base-worktree-path>` or chain `cd <path> && gh pr merge ...` in the same
   Bash call (avoid cwd-reset trap between Bash calls).
   - **Fork clones: pass `--repo <fork-owner>/<repo>` on every `gh pr` call,
     the merge included.** In a clone whose GitHub repo is a fork, gh
     resolves a bare `gh pr merge <N>` (and `view` / `comment` / `edit`)
     against the parent repo: it fails with `Could not resolve to a
     PullRequest` at best and acts on the upstream PR of the same number at
     worst.
4. Even if the call fails on a worktree conflict, the PR may already be merged
   on remote (gh order: remote merge → local sync). Manual cleanup — run each as
   a **separate** Bash call, never chained (a failure in one step must not
   short-circuit the rest): `git worktree remove <path>`, then
   `git branch -D <branch>`, then `git worktree prune` (snapshot and dry-run
   it first — see [prune is repository-wide](references/prune.md)),
   then `git pull origin <base>`.
5. **Squash-ancestry stale HEAD guard (post-merge sync mandatory)**: immediately
   after `gh pr merge --squash` (with or without `--delete-branch`), run
   `git pull origin <base-branch>` as a **separate step**. gh merges remote then deletes local
   branch by reachability against local HEAD — but does not fetch the local
   base, so if local base is still at pre-merge SHA the delete silently skips
   (`warning: deleting branch 'X' that has been merged to 'refs/remotes/origin/X',
   but not yet merged to HEAD`). Distinct from item 4's worktree case — only
   `git pull` fixes it, not `git worktree remove`.

## When the user merged out-of-band

When the user merged the PR themselves (a `/merge` comment, the web UI) or a
repository bot merged it (your own `gh pr merge` prints `already merged`), a
merge report is not an instruction to delete: run the read-only checks and ask
once for the whole set, unless the user's own instructions record a standing
approval for post-merge cleanup. Confirm `MERGED` and that the remote head tip
equals the PR's `headRefOid`, re-read the branch right before each delete (an
empty read means someone else already deleted it), and check sibling
worktrees' symlinks into the worktree being removed. Full procedure: [references/out-of-band-merge.md](references/out-of-band-merge.md).

## When the base worktree has uncommitted edits, or no worktree holds the base

When no worktree holds the base, or the base worktree holds other sessions'
uncommitted edits (`git pull` refuses), do not switch, stash, discard or commit
anything there: merge from a directory outside any repository with
`gh pr merge <N> --repo <owner>/<repo> --squash --match-head-commit <sha>`
(`--admin` only when the merge briefing named it under Risk and the user
approved that). Then confirm the local base has no commits of its own and move
it with `git reset --keep origin/<base>`, never `--mixed` or `--hard`; on a
refusal, stop and report the file it named. Full procedure: [references/base-worktree-busy.md](references/base-worktree-busy.md).

## Unified post-merge cleanup sequence

Covers both sub-cases (worktree conflict + squash-ancestry gap):

```bash
cd <base-worktree-path>
git switch <base-branch>
gh pr merge <N> --squash                                   # no --delete-branch
gh pr view <N> --json state,headRefOid                     # MERGED before any removal
git pull origin <base-branch>                              # resolve squash-ancestry gap
git worktree remove <head-worktree-path>                   # checklist item 2 caveats
git branch -D <feature-branch>                             # after its tip equals headRefOid
git push origin --delete <feature-branch>                  # only when delete_branch_on_merge is off
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
worktree left the registry earlier in this sequence, so it is already absent
from the snapshot: when the dry-run printed nothing, the `diff` must print
nothing either — assuming no one added or removed a worktree in this
repository while the sequence was running. Any entry it reports as removed
belonged to someone else.

### `prune` is repository-wide — snapshot before running it

`git worktree prune` takes no target: it drops every unlocked registration
whose directory is gone, not only yours. Capture `git worktree list
--porcelain` before and after it, and report the entries it removed that were
not yours. Full procedure: [references/prune.md](references/prune.md).

## Squash merge under bulk cleanup — the merged-ness oracle

For N already-merged local branches in a squash-merge repo, `git branch
--merged` and `git diff base...branch` both report a merged branch as not
merged. Delete a branch (with `-D`) only when its local tip equals the PR's
`headRefOid`, the PR `state` is `MERGED`, and `baseRefName` is the base you are
cleaning up, read from `gh pr list --search "head:<branch>" --state merged`
(without `--state merged` every branch comes back as no PR found). Full procedure: [references/bulk-squash-oracle.md](references/bulk-squash-oracle.md).

## Relationship to enforcement

| Layer | Home | Covers |
| ------- | ------ | -------- |
| Structural enforcement | `merge-gate` (worktree check) PreToolUse hook | Blocks `gh pr merge ... --delete-branch` when the head branch is still checked out in another worktree, the remove-first order the flag needs; the default path in checklist item 2 does not pass the flag |
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
