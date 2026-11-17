# Step 7: Commit in order

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

The merge commit first (resolution plus test rewrites), then one commit per
review nit.

- **Before you commit a test a reviewer suggested to kill a surviving
  mutant, run that mutant against the new test.** Use a scratch script that
  applies each one-line source mutation by exact-string replace (it must
  assert the anchor matches exactly once), runs the target test file, records
  CAUGHT or SURVIVED, and restores the source in `finally`. Run the unmutated
  baseline under the same env so you know a CAUGHT is a test failure and not
  an import error. If the mutant still survives, look for a later line that
  repeats the same filter. A repeat alone does not make the mutant
  equivalent: compare the original and the mutant across every observable
  step between the two lines (a call, a write, a log line, an exception). Only
  when none differs is it equivalent: leave the fixture out and answer the
  thread `Not fixed —` with the probe output. When one differs, write the test
  against that intermediate effect instead.
  A test that kills no mutant only adds noise for later readers.

- Stage only the files you resolved. An in-progress merge commits only the
  index, so unrelated edits can wait in the worktree.
- **Do not `git stash` during a merge.** It fails with `could not write
  index`, and the following `stash pop` targets whatever sits at
  `stash@{0}` — which may belong to another session.
- A pre-commit formatter may reformat a line that had already drifted on the
  base. Re-add the file, commit again, and mention it in the change comment.
- **Never run a formatter over a whole resolved file by hand** (`uv run black
  <file>` or similar) — a formatter version that differs from the one the
  base was formatted with rewrites untouched base lines and bloats the merge
  diff. Check `git diff --stat <base-remote>/<base> -- <file>` after any
  format step; if base lines moved, rebuild the file as the base version plus
  only the PR's hunks as they stand in the resolved file (index or worktree),
  written via a scratch script. `git show HEAD:<file>` is the pre-merge PR
  side and lacks the conflict fixes, so take only a block the resolution did
  not touch from it: for a conflicted test file where the PR only appended a
  class, `git show <base-remote>/<base>:<file>` + that appended block is also
  the cleanest resolution.
- **Split a file that mixes two commits' changes by writing each commit's
  content into the index, not by hand-picking hunks.** Build the
  intermediate version in scratch (base plus only this commit's change, from
  `git show HEAD:<path>`), then `git update-index --cacheinfo
  <mode>,$(git hash-object -w <scratch file>),<path>` with the mode the index
  already holds (`git ls-files -s <path>`: `100755` for an executable hook
  or script, which a hard-coded `100644` would strip). `git add -p` needs a
  TTY, and `git apply --cached --unidiff-zero` of a `-U0` diff with some
  hunks dropped places later pure insertions at shifted lines. Before each
  commit, prove the index builds: `git checkout-index -a -f
  --prefix=<scratch>/idxN/` and run the tests there.
- A pre-commit check that fails on the merge commit may be failing on the base
  itself (a repo-wide guard the base already violates). Prove it by running
  that hook against the base tree first; only then skip that single hook
  (`SKIP=<hook-id> git commit`) for the merge commit, keep every hook on your
  own follow-up commits, and name the skip in the change comment.

Done when: `git log <head-remote>/<head>..HEAD` shows the merge commit followed by one
commit per addressed review finding.
