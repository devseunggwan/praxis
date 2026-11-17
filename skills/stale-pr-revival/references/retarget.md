# Step 9: Retarget the base (only when asked)

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

Check that the new base already contains everything the old one does:

```bash
git diff --quiet <base-remote>/<new> <base-remote>/<old>; echo $?     # 0 = trees equal
gh pr edit <N> --base <new>
gh pr view <N> --json files,additions,deletions          # must be unchanged
```

If the trees differ, retargeting drags unreleased base commits into the PR.
When the user asked for the new base anyway (and force-push is off the table),
port the PR's **net change** onto a fresh branch instead:

```bash
# net change = diff from the base commit the head last merged in (its 2nd parent) to the head
OLD=$(git -C <wt> show -s --format=%P <merge-commit> | cut -d' ' -f2)
git -C <wt> diff --binary $OLD <head> > <scratch>/net.patch
git worktree add ../<repo>-<slug>-<new> -b <head>-<new> <base-remote>/<new>
git -C ../<repo>-<slug>-<new> apply --3way --index <scratch>/net.patch
```

- Generated files that conflict (test-duration maps, lockfiles) are rebuilt,
  not hand-merged: load the new base's file, insert only the PR's keys, and
  write it back in the base's exact formatting (key order, indent). Sorting
  or re-serialising differently turns a 20-line change into a whole-file diff.
  Check `git diff --cached --stat <base-remote>/<new>` matches the old PR's
  net stat file by file. The same file conflicts again when `<new>` moves
  before the merge: merge `<new>` in and rebuild it the same way in the
  merge commit.
- Prove the port is the same code before reusing any live proof:
  `git diff --cached --quiet <old-head> -- <PR paths>` (index vs old head) and
  `git diff --quiet $OLD <base-remote>/<new> -- <PR paths>` (the code the PR
  sits on is the same on both bases). Both 0 lets the old head's live rows
  carry over, cited as such; otherwise re-measure.
- Re-run red/green and the type gate on the new tree, commit once with a
  `Co-authored-by:` for the original author, push, open the new PR to
  `<new>` with `--assignee @me`, and state in its body that the repo's own
  docs name a different base and what back-merge that implies.
- Comment on the old PR with the new URL and why (commits it would drag), and
  update sibling PRs' bodies that reference it. Closing the old PR is the
  user's call: ask, recommending close (two open PRs for one fix split the
  review); never delete its branch. When closing, close with a reason
  comment (`gh pr close <N> --comment "$(cat <scratch>/close.md)"`; `close`
  has no `--body-file`): the replacing PR URL,
  how many unreleased commits a plain retarget would drag, the net-change
  range ported and the equality probe result, the back-merge that realigns
  the old base, and that the branch is kept for its author. A bare close
  leaves reviewers and the author guessing why and gets sent back.

Done when: either the PR is on `<new>` with unchanged `files` / `additions` /
`deletions`, or a port PR on `<new>` exists with the equality probes recorded
and the old PR carries a pointer comment.
