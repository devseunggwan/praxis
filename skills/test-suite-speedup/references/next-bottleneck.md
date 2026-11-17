# Step 8: Next-bottleneck hunt

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

For a bare "why are the tests so slow" with no window, first read one slow and
one fast recent run's legs side by side (instance type + summary seconds per
leg, timing-artifact file sums): runner mix, suite size, and the top new file
are three separate causes, and the report ranks them by wall-time impact.

When the question is "CI went from X to Y minutes — why?", the answer must
account for the whole delta before any fix is proposed: a per-step table of
both runs (setup steps, queue, the test step), then the test step split into
*same tests got slower* vs *new tests* vs *collection* (recipe →
[`references/ci-measurement.md`](ci-measurement.md#attributing-a-slowdown-between-two-dates)).
A fix that covers part of it is reported as that part ("44 s of ~2.5 min"),
never as the answer — the user reads a single fixed file as "done" and has to
come back to ask about the rest.

When the question is "we keep cutting but it doesn't go down", answer in this
order: (1) net — leg sum and slowest leg, baseline run → now, with test-count
growth % next to time growth %; (2) the daily p50 series of the both-legs sum,
which shows the step changes and the creep between them; (3) one table row per
step change — commit/PR, seconds added, the tests that caused it (from the
`--durations` diff), and whether a later PR recovered it (with its drop in the
series) or it is still there; (4) same-tests-slower and long-standing costs
the earlier speedups never touched; (5) next levers ranked by recoverable
seconds. Merged speedups that did land show up as drops in the series — name
them so the user sees the cuts worked and were outpaced, not that they failed.
Scripts: `scripts/ci_leg_series.sh`, `scripts/ci_durations_diff.py`.

When the report ends in a choice, put the options in the `clarify` call, not as a lettered list under a numbered candidate table: a bare "1" reply then has two antecedents (candidate #1 or option 1) and costs a round to disambiguate. If a lever in the approved bundle measures well below its estimate during implementation, say its new size and ask before dropping it; record the drop with the numbers in the issue body. When the approval itself was conditional on that measurement ("include it if the hypothesis holds"), a failed CI check is the answer: revert the lever with a new commit on the branch (never force-push), retitle the PR and rewrite its body around what remains, post a correction on the tracking issue with both runs' per-test numbers, and lead the next report with the revert.

Before a menu offers to delete or shrink an equivalence test (on/off switch
pair, before/after transform comparison, "every task ends in the same state"),
settle two premises with probes, not from the file name: (1) is the switch
still partially rolled out — read its live value per tenant (prod Airflow
Variable / flag store); `on:<a few ids>` or a rollback switch left `on` means
the off path is live; (2) is the "before" side a reconstructed reference for a
transform the code still applies unconditionally — then it is a differential
oracle guarding that transform against future edits, not a dead path, and
deleting it drops regression coverage. Only a comparison whose both premises
fail is removable; otherwise the levers are semantics-free ones (dedupe
identical simulations, fold re-reads into the parametrized case) or the
owner's call on the case matrix. Offering removal first and retracting it
after the user picks it costs two approval rounds.

Re-rank from the post-change full-suite run with the same method, classify
each top file, and reproduce the fixable ones locally (`--durations`,
cProfile) before filing.

When the remaining delta is "new tests" and the user asks whether they are
worth it, run the window audit in `test-suite-consolidation` (its pruning
reference, section "Auditing the tests added in a window"). Lead with
cost concentration: a count in the thousands is usually mostly sub-50 ms tests.
The CI-time answer then lives in a few files and the coverage-gap answer in
the skipped ones. Report both, and say what a full cleanup can save in total
next to what only more shards can. Put only reproduced causes in scope; list one-time
setup costs, intended waits, and unexplained items under an explicit
out-of-scope section with why, so the next session need not re-measure.

Done when: every top file is either in scope with a reproduction or in the
out-of-scope list with a reason.
