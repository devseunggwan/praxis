# Squash merge under bulk cleanup: the merged-ness oracle

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

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

## Bulk branch-delete procedure

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
