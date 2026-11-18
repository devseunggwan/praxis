# `prune` is repository-wide: snapshot before running it

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

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
[Limitations](../SKILL.md#limitations) (`git worktree add`, never `git clone`).

⚠️ Do NOT collapse the sequence into a single `&&`-chain. A git hard error
mid-sequence (submodule worktree missing `--force`, divergent pull)
short-circuits `&&`, silently skips remaining steps, and trailing output from an
earlier step is easily misread as "cleanup done". Run steps separately; after a
fragile step (worktree remove / pull) fails, re-verify primitive state with
`git worktree list` / `git branch | grep`.
