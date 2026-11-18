# Step 5: Resolve conflicts by integrating, not by picking a side

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

- **The base often grew its own workaround for the same defect** while the PR
  waited. Make it build on the PR's abstraction (change its base class, keep
  its extra checks) instead of shipping two mechanisms for one problem.
- **Rewrite tests whose premise the PR changes.** A test that relied on the
  old fall-through path now has to drive the new event sequence.
- **Taking the base side of a file and re-applying the PR's small additions**
  (`git -C <worktree> checkout --theirs <file>`, then re-add) is usually cleaner than
  hand-merging hunks — and it drops the PR's unrelated formatting churn,
  which reviewers tend to flag. `--theirs` replaces the whole file, including
  the PR's non-conflicting hunks in it, so first list what the PR changed
  there (`git -C <worktree> diff $(git -C <worktree> merge-base HEAD <base-remote>/<base>) HEAD -- <file>`)
  and confirm each hunk is moot or re-applied.
- **Re-read the reviewer's merge-order advice against the current base.** A
  review that said "only file X conflicts" was measured against an older base;
  more commits may have landed since. Run `git -C <worktree> merge-tree --write-tree
  --name-only <base-remote>/<base> HEAD` again and work from that list.
- **When the base refactored the code the PR fixes**, the PR's hunk no longer
  has a home: take the base side, find where the logic lives now
  (`git -C <worktree> log <old-base>..<base-remote>/<base> -- <file>`, grep
  the raised symbol), and re-apply the fix there — keeping the old hunk
  resurrects code the refactor deleted. Re-check the blast radius at the new
  location: a new shared helper can have more callers than the old site.

- **Hunt semantic conflicts the text merge cannot show.** When the PR removes
  or renames a parameter, symbol, or default, callers that landed on the base
  while it waited still use the old shape and merge cleanly into a broken tree.
  `git -C <worktree> grep -n <removed symbol>` on the merged tree, and run the repository's
  import/load test (e.g. DagBag import errors) before committing; fold the
  caller fixes into the merge commit so every commit stays buildable.
  The reverse case matters as much: when the **base deleted** a function the
  PR modified or ported, take the base side and also grep the merged tree for
  that function, because the PR may have moved its tests to a non-conflicting
  file that still imports it. When the base retired that behaviour, delete
  those tests in the merge commit and drop the matching row from the PR body
  and anchor; when the base moved it to another implementation, rewire the
  tests to that implementation instead.
- **Carry the base's new guards onto the PR's parallel paths.** When the PR
  adds a bulk/alternate entry point beside an existing single-item one
  (bulk pause next to a single PUT, a batch trigger next to a single trigger),
  diff the base's changes to that sibling since the merge-base
  (`git -C <worktree> diff <merge-base> <base-remote>/<base> -- <sibling file>`)
  and list every guard, precondition, or fallback it gained (rollout-stage
  checks, provider-specific lookups). Each must also run on the PR's new path —
  the text merge is clean because the PR's file never had the call, and only a
  reviewer catches it after push. Grepping for status/enum literals alone does
  not find these.
- For a read-only conflict preview while only reporting status (no worktree
  needed): `git merge-tree --write-tree --name-only <base-remote>/<base>
  <head-sha>` lists the conflicted files.

Done when: no conflict markers remain, every conflicted file has a
one-line note saying which side's mechanism survived and why, and a grep of
the merged tree for each symbol the PR removes/renames returns only intended
hits.
