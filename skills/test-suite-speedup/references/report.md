# Step 7: Report the before/after

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

A table with both run IDs: job wall time (now = the slowest leg), the formerly
slow step, and the unchanged half, plus the per-leg test summary line. For a
gain spread over several PRs, one table: baseline → each merged step → now,
with the share each PR contributed. Name the CI path the PR did not exercise
as unverified, say it is one run each (runner variance unmeasured), and say so
when pruning tests contributed little. Format →
[`references/ci-measurement.md`](ci-measurement.md#the-beforeafter-report).

Done when: the table carries both run IDs, the comparison is on the same
runner instance type, and every unverified path is named.

After the merge, confirm the gain on the base branch's own push runs, not the
PR run: list every successful push run's per-leg test-step seconds **and their
sum** in time order (a few days back) — the slowest leg swings with how the
splitter balanced that run, the both-legs sum does not, so locate step changes
on the sum, take the "before" window as the runs since the last
level shift, and report the after-runs with their count (two runs is a hint, not
a number). Lead that report with the net position against the pre-regression baseline
— CI wall time and leg sum, baseline run → now, in minutes — and only then
what this PR recovered and what is still unrecovered (each remaining step with
its commit). A PR delta read alone ("−24 s") hides that the regression was not
recovered, and the user has to work out the net themselves.
Read the series for a step change that is not yours — a jump of tens
of seconds at one commit is a new regression to name (commit, before/after band)
as a finding with its cause marked "not yet split", and it is usually why the
gain looks smaller than the PR run promised. Put the series and windows in the
tracking issue first, then link that comment from any chat/Slack summary.

When the base rewrites a file you sped up (moves its loop onto a shared helper, changes the return shape), resolve the conflict by merging the base (never rebasing a pushed branch), taking the base's version of the file whole, and re-applying only your speedup on its new API — hand-merging hunks keeps calls to helpers the base deleted. Check that a cached value is still plain data (a dataclass, not a live session object) before caching it again. Then re-run the base-vs-branch mutation with the *new* base copy as the "before" side: the old proof compared against a file that no longer exists. Re-take the CI baseline from the merged base commit's push run too.

To revise a published verification comment or PR body after a re-measure, fetch
its live text first (`gh api repos/<o>/<r>/issues/comments/<id> --jq .body`,
`gh pr view <N> --json body --jq .body`) and edit that, never a scratch copy:
scratch files get pruned, and a local copy can predate corrections already
published, so editing it silently reverts them. Never issue the file write and
the `gh pr edit --body-file` in the same parallel batch: a refused write leaves
the old file, the edit publishes it anyway, and the title/body disagree. Write,
confirm the write succeeded, edit, then grep the live body for a phrase the new
text removed.
