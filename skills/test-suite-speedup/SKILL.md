---
name: test-suite-speedup
description: >
  Make a slow test suite or CI test job faster and prove it — rank where the
  time goes on CI across many runs, prefer structural fixes (shared setup,
  parallel files, matrix legs) over per-file rewrites, probe parallel safety,
  and accept the change only on a measured CI before/after comparison.
when_to_use: >
  Triggers on "test suite speedup", "slow test suite", "slow CI tests",
  "CI test time", "테스트 속도 개선", "CI 테스트 느림", "줄였는데 안 줄어",
  "테스트 시간이 왜 다시 늘었나".
  Do NOT activate on copied test fixtures with no timing complaint (that is
  `test-suite-consolidation`) or on slow production code paths (that is
  `perf-leak-review`).
verified-against-runtime: true
runtime-verified-at: 2026-10-16
runtime-verified-note: "gh 2.102.0 — on a public repo, `gh run list --workflow ci.yml --branch main --status success` (a `push` run), the `pull_request` runs query filtered to `pull_requests[].base.ref`, the `--commit --event pull_request` fallback, and the jobs API step query returned live rows — `.name==\"test\"` matched 0 steps on the matrix legs, the `test (` prefix filter matched them; the per-run loop (4-run page) wrote run id · event · kind rows with job-level queue/duration rows, and a 404 run id made the loop exit 1; `gh run view --job <id> --log` saved 7,761 lines whose step field read `UNKNOWN STEP` on every line (ci-measurement now windows by the jobs API step times), and `gh api .../jobs/<id>/logs` exited 1 with `the response contains terminal escape sequences; pass --allow-escape-sequences`. pytest 9.0.3 — `--collect-only -q` ids ran in explicit argument order reversed, `--durations-min=0.5` kept only the 0.6 s test; GNU `tail` has no `-r` (BSD only), so the reference now gives `tac`. pytest-split / pytest-xdist / pytest-testmon (scratch venv, pytest 8.4.2) — `--store-durations`, `-n`, `--testmon` listed in `--help`. vitest 5.0.5 on node 26 — summary prints phase shares as percentages, not summed times (reference updated); `list --project shared --filesOnly`, `--sequence.shuffle --sequence.seed`, and a scratch `onTestModuleEnd` reporter read `diagnostic().importDurations` (0 rows at the default limit, rows with `--experimental.importDurations.limit=100000`); `npx -y node@22` ran v22.23.3"
---

# test-suite-speedup

## Overview

A slow suite tempts two wrong moves: rewriting the slowest-looking file by
hand, and reporting a local stopwatch as the gain. Both miss. The time usually
sits in a few structural places — repeated heavy setup, files run serially that
could run side by side, one CI job doing two jobs' work — and a local timing
taken while anything else runs is noise. This skill ranks the cost on CI first,
picks the lever that moves the most files, and treats a CI before/after table
as the acceptance test.

**Core principle:** the deliverable is a measured CI comparison on the same CI
path, not a local timing and not green checks.

Reference map — load only when the step runs:

| File | Holds |
| ------ | ------- |
| [`references/ci-measurement.md`](references/ci-measurement.md) | Ranking steps across many runs, per-file ranking from a `pytest -v` log, runner-type noise, the before/after report, duration-shard refresh |
| [`references/pytest-slow-test-causes.md`](references/pytest-slow-test-causes.md) | Classifying a slow pytest file by phase and signature; escaped sleeps; unreachable product-code waits; suite-wide hermetic patches (cloud clients, library idle sleeps) with guard tests; one-process profiling of a work stream; narrowing a runtime `create_all` |
| [`references/pytest-shared-setup.md`](references/pytest-shared-setup.md) | Sizing the shareable part of a hot factory from its callers; sharing an expensive pytest object safely: proofs before and after, minimal-churn wiring, change-selection interaction |
| [`references/vitest-isolation.md`](references/vitest-isolation.md) | JS/vitest suites: wait-vs-compute file classification, CI-only slow files, import-dominated cost, per-module import profiling, barrel-library prebundling, isolated vs shared pools, leak probes, shuffle checks |
| [`references/parallel-runner.md`](references/parallel-runner.md) | Parallel-safety probe, serial partition, runner implementation rules, CI matrix split |
| [`references/ci-redundant-runs.md`](references/ci-redundant-runs.md) | Runs that should not happen: classifying backmerge/push history by tree, reusing an identical tree's result, superseding by a real newer run (Actions API, not message parsing), per-job concurrency, testing workflow `if:` gates as code, keeping skip markers out of commit text |
| [`scripts/ci_leg_series.sh`](scripts/ci_leg_series.sh) | Per-leg test-step seconds for every push run since a date (TSV) — the series for locating step changes; run in the background |
| [`scripts/ci_durations_diff.py`](scripts/ci_durations_diff.py) | For before/after run pairs around each step change: collected counts, pytest summaries, new-or-grown `--durations` entries |
| [`scripts/boto_call_counter.py`](scripts/boto_call_counter.py) | pytest plugin counting real botocore calls per test — run it when cProfile shows SSL/socket waits under botocore |

## When to Use

- A test suite, or the test job of a CI workflow, is too slow and the user
  wants it faster.
- The user asks "what else is slow?" after an earlier speedup.
- Not for copying/duplication cleanup without a timing goal — restructuring
  tests for reuse is not a speedup (see `test-suite-consolidation`).

## Process

### Step 1: Baseline on the same CI path

Take the baseline from CI, never from a local run. The baseline must exercise
the same CI path as the after-run: when the PR base selects the path (for
example `prod` runs the full suite split into matrix legs while `dev` runs one
leg with change-selection), take the latest successful run of a PR into the
same base, and check that passed/skipped/deselected counts match before
comparing times. Pull per-step timings for that run, split the one big step by
the runner's section banners, and read the workflow file's comments — the
maintainers often already wrote down the intended fix. Then rank steps across
many runs, not one, and check the runner's real vCPU count against the
configured worker count. Commands → [`references/ci-measurement.md`](references/ci-measurement.md).

Before proposing anything, search issues and PRs for an existing speedup
issue, closed ones and throwaway experiment PRs included (`gh pr list --state
all --search "xdist"`, `--search "test_durations"`): they record measured and
rejected alternatives, and the conditions under which they were rejected — a
lever refuted when setup cost dominated may pay off once CPU-bound tests do.

Done when: a baseline run ID on the matching CI path is named, its test counts
are recorded, and every step over a few seconds has a median and p90 across
many runs.

### Step 2: Size the expected gain before picking a lever

Gain ≈ (share of suite time the batch covers) × (per-file saving measured on
that subset). A batch that is 2% of the suite gives about 1–2% even at −60% per
file — below CI noise, and the user will read it as "barely moved". Prefer
levers that move hundreds of files at once (a shared test helper, a pool
classifier, the runner type) and present per-file migrations as the long tail.
Say the expected gain up front.

Done when: each candidate lever has a written estimate with its inputs, and the
estimates below CI noise are labelled as such.

### Step 3: Rank files and classify each top file

Rank files across the whole suite from a full-suite CI run that logs
`pytest -v` (a change-selection run does not cover every test). Validate the
ranking on a run with a known answer first — the pre-fix run must rank the
file you fixed first. Then classify each top file by phase (`setup` / `call` /
`teardown`) and shape before proposing a fix:

- pytest → [`references/pytest-slow-test-causes.md`](references/pytest-slow-test-causes.md)
- vitest / JS → [`references/vitest-isolation.md`](references/vitest-isolation.md)

Per-file local timings are for shape only: run each file in the background,
look at *why* it is slow (a process spawn per case, real `sleep`s — a `sleep`
inside a test's *input string* is not a delay), and never report a number taken
while another suite runs. Record CPU (`user+sys`) next to wall time for each
file. A file that uses little CPU for its wall time is waiting on real time
(limiters, backoff, polling), and the fix is test-side fake time; more workers
or import cuts will not help it. A file that is slow on CI but fast locally
under CI's worker count and env needs a reproduction on CI's OS and runtime
major (a `node:<major>` container capped at CI's vCPU), not a guess; read the
slow-test lines the CI log already prints under that file first →
[`references/vitest-isolation.md`](references/vitest-isolation.md#ci-only-slow-files).
For pytest, compare the CI/local ratio per file against the suite's usual
ratio. A file several times above it has a CI-only cost, but its cause is a
hypothesis until the PR's own CI run moves that file: a Linux-container A/B
(e.g. sqlite `synchronous=OFF` cutting scheduler simulations ~35%) did not
carry over to the GitHub runner, where the same tests kept their
`--durations`. Ship such a lever only together with a measurable target in
the first PR run →
[`references/pytest-slow-test-causes.md`](references/pytest-slow-test-causes.md#reproducing-a-linux-only-cost).

The cause of a slow suite is usually a combination: serial workers, waiting
files, and heavy per-file imports. Rank the levers in one table: cause ·
evidence (measured vs estimated) · CI seconds · fix · expected saving.

A cause guessed from `--durations` names ("probably re-parses the DAG") is a
hypothesis until a cProfile `cumtime` run of 2–3 of the slow tests shows it;
profile before the cause goes into a plan or an issue, and retract it in the
report when the profile says otherwise.

Done when: each top-ranked file has a cause from the classification table, or
is explicitly marked "needs a look".

### Step 4: Pick the structural fix, in this order

0. Remove repeated expensive setup inside a hot file (a test that rebuilds the
   same heavy object per call — a DAG bag, an app, a schema). Reuse the repo's
   existing session fixture before inventing a cache, and prove sharing is safe
   first, then prove the base and branch copies of the file fail on the same
   production mutants. A module/session cache only dedupes inside one pytest
   process: on a sharded CI, check which leg each consumer runs in before
   counting the saving. When a separate test only re-reads one parametrized
   case's expensive result, fold its assertions into that case instead of
   caching (shard-independent), and break one moved assertion to prove it
   still executes → [`references/pytest-shared-setup.md`](references/pytest-shared-setup.md).
1. Run independent test files in parallel inside the CI entrypoint.
2. Split the CI job into matrix legs so the halves run side by side.
3. Only then rewrite the slowest files' per-case spawns — wide churn, modest
   gain. Offer it; do not default to it.

When most of the cost is whole runs that need not happen (bot backmerges, an
identical tree already tested on another branch, pushes about to be
overtaken), that lever comes before all of the above: classify the run
history by tree and skip or reuse whole runs →
[`references/ci-redundant-runs.md`](references/ci-redundant-runs.md).

Done when: the chosen lever is named with its Step 2 estimate, and any skipped
higher-order lever has a one-line reason.

### Step 5: Prove parallel safety and implement the runner change

Run every file in parallel under one throwaway state dir, each to its own
output and exit-code file, list the failures, and re-run each failure alone to
separate a concurrency bug from environment. Partition the files that edit the
working tree into a serial group keyed on a marker they already carry. Then
implement the runner and, if chosen, the CI matrix split. Procedure and rules
→ [`references/parallel-runner.md`](references/parallel-runner.md).

Done when: every parallel-probe failure is classified (concurrency /
environment / pre-existing), the serial group is derived from a grep rather
than a hand list, and required status checks have been checked before any job
rename.

### Step 6: Verify on CI, not by sitting on local runs

Hold changed test code to the repo's own coding guideline like product code —
no pass-through wrappers, duplication extracted, a mutation proof for any new
helper. Locally run only the touched files plus lint, push, and watch the PR's
checks in the background instead of holding the turn. Read the PR's review
threads before writing any verification note: bot reviewers post within
minutes of a push.

When a test fails only in a local environment (an optional-dependency venv, a
local fake service), run that one test on the unmodified base in a throwaway
detached worktree before touching the change: the same failure there is
environment, not regression. Name it in the commit's `Not-tested:` trailer and
the PR body, and close it from the CI job that installs that dependency (its
raw log: the summary line plus `grep -c FAILED` = 0), not from the local run.

A local full-suite base-vs-branch comparison supports "no new failures" only as
far as it was reproduced: when the branch-only failures share one env error,
say "N branch-only failures, all <error>; <file> reproduced on base" and let
the CI run close the rest — "0 new failures" overstates it and has to be
corrected on every surface it reached.

Before calling a suite-wide stub merge-safe, enumerate how it could change results, each with a probe: (1) module-level binds that bypass it (`git grep -nE "from <mod> import <fn>"` — a name bound at import keeps the real function); (2) production callers that do not catch the stub's exception, and whether any test runs them unpatched (the per-test call counts from the before-run show it); (3) tests that assert the real path — they must patch it themselves, nested inside the session stub; (4) an in-flight library upgrade that moves the patched name (see the version-tolerant rule in `pytest-slow-test-causes.md`). Report each as checked or as an inference with what would confirm it.

Bot reviewers can run longer than a short poll: the Codex connector marks a PR 👀 while reviewing and swaps it for 👍 when it finishes with no findings, sometimes past 20 minutes. Bound the wait, then re-read reactions and threads once before the merge briefing instead of extending the poll. To tell whether a standing 👍 belongs to the latest push, read its timestamp (`gh api graphql` → `pullRequest{reactions(first:20){nodes{content createdAt user{login}}}}`) against the push time — a 👍 older than the push is the previous round's verdict.

A `pull_request` run tests the merge of the head into the base tip at that
moment, not the PR's own base commit: read the base sha from the job log
(`HEAD is now at <m> Merge <head> into <base>`) and compare with that base
commit's push run.

The per-file metric is the after-measure when the suite grew between the
baseline and the PR run: whole-leg seconds then mix in other commits' tests
(compare the `collected N items` counts first). Report the touched file's CI
seconds from both runs as the result and the leg times as context. After
merging the base into the PR, rebuild the comparison band from other PRs' runs
in the same period, not the earlier band: the base's own suite keeps growing,
so the old band can make a real gain look like a regression. Say in the report
when the band itself moved.

Done when: a CI run of the branch on the same path as the baseline has
finished, and every open review finding is answered.

### Step 7: Report the before/after

A table with both run IDs: job wall time (now = the slowest leg), the formerly
slow step, and the unchanged half, plus the per-leg test summary line. For a
gain spread over several PRs, one table: baseline → each merged step → now,
with the share each PR contributed. Name the CI path the PR did not exercise
as unverified, say it is one run each (runner variance unmeasured), and say so
when pruning tests contributed little. Format →
[`references/ci-measurement.md`](references/ci-measurement.md#the-beforeafter-report).

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

### Step 8: Next-bottleneck hunt (when asked what else is slow)

When the question is "CI went from X to Y minutes — why?", the answer must
account for the whole delta before any fix is proposed: a per-step table of
both runs (setup steps, queue, the test step), then the test step split into
*same tests got slower* vs *new tests* vs *collection* (recipe →
[`references/ci-measurement.md`](references/ci-measurement.md#attributing-a-slowdown-between-two-dates)).
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

## Troubleshooting

| Symptom | Cause | Fix |
| --------- | ------- | ----- |
| A background `gh run watch <id> --exit-status` loop reports a nonzero for one run after a merge | The run was `cancelled` by the workflow's concurrency group when a newer push to the same branch started it again — `--exit-status` treats cancelled as failure | Read `gh run view <id> --json conclusion` and the next run of that workflow on the branch before reporting a failure; report it as "cancelled, superseded by <run> (success)" |
| Turn stalls for many minutes on a local run | Waiting on a long foreground or chained wait | Run it in the background and end the turn, or push and let CI run |
| A background run of a shell script misbehaves | The script was edited while it ran — bash reads scripts incrementally | Never edit a script during its own run; edit other files meanwhile |
| Before/after differs by less than run-to-run swing | Mixed runner instance types, or contended local runs | Compare only same-instance-type runs; for sub-noise changes, time the touched files alone, base vs branch alternating, 3 reps, and say the full-suite gain is below noise. Run the base side from the same worktree: `git show origin/<base>:<file> > <stem>_prodab.py` next to the branch file (same conftest, same venv), pass `--junitxml` per run, sum `testcase` times per file, delete the copies after. The copy's name must stay a plain module name ending in `.py` (`<stem>_prodab.py`, never `<file>.prodab.py`) — an extra dot makes pytest import it under a broken module name. Lead the user report with this per-file A/B and give the CI leg sums as context with their run count |
| Matrix legs unbalanced after a speedup | A duration-based shard file still holds the old slow times | Refresh only the touched files' entries in the same PR → [`references/ci-measurement.md`](references/ci-measurement.md#refreshing-a-duration-shard-file) |
| Formatter rewrote dozens of unrelated files | A local formatter version differs from the pinned one, run on a directory | Format only touched paths with the pinned version; restore with `git checkout -- <path>` only the outside paths that `git status --porcelain` listed as clean before the formatter ran — on a path you had already edited, that checkout drops your edits too, so revert the formatter's hunks there with `git restore -p` instead |
| Tests fail locally that pass on CI, or are fast locally but slow on CI | Newer local runtime major: globals and internals such as `AsyncLocalStorage` differ | Run on the CI's runtime major (`npx -y node@<major>`, the CI Python version). For timing, use a `node:<major>` Linux container with CI's vCPU cap |
| One test case takes almost exactly its `spawnSync`/`execFileSync` `timeout` on Linux CI although the child exits with the expected code | The child launched grandchildren via `npx <bin>`; SIGTERM stops at npx, the orphans keep the parent's stdout/stderr pipes open, and the sync call waits until its timeout | Compare the CI per-test time with the spawn timeout; in a `node:<major>` container run the entrypoint and `ps` for orphans (ppid 1). Fix the spawn to start the bin with `process.execPath` + `require.resolve('<pkg>/cli')` instead of `npx` |
| pytest dies importing a plugin from a foreign `site-packages` | Shell `PYTHONPATH` points at another tool's venv | Run with `env -u PYTHONPATH` in the same call |
| A profiling/driver script dies at import with `module 'queue' has no attribute ...` (or similar for another stdlib name) | A scratch script in the same directory is named after a stdlib module (`queue.py`, `json.py`, `profile.py`); Python puts the running script's directory first on `sys.path` | Name scratch scripts after what they measure (`queue_times.py`), never after a stdlib module |
| Tests move between CI steps after an edit | Change-selection steps (testmon, marker reruns, template-driven steps) key off file contents or fixture names | Read the comments around every test step before editing the workflow |

Read another ref with `git show <ref>:<path>` or `git grep <pattern> <ref>`,
never by checking it out in a checkout you are not working in — it may hold
someone's branch or uncommitted state. To run tests at another ref, use a
throwaway worktree.

## Integration

- `test-suite-consolidation` — moving copied fixtures/helpers into shared
  locations and pruning unneeded tests. Neither is a speedup by itself:
  measure base vs branch on the same subset before claiming one, and note that
  editing any `conftest.py` cold-starts a change-selection cache keyed on
  `**/conftest.py` once after merge.
- `perf-leak-review` — perf defects in product code, not test runtime.

## Limitations

- CI timing comparisons here are usually one run per side; runner variance is
  not measured unless you run more, and the report must say so.
- The per-file ranking from a `-v` log is an estimate: fixture setup and
  teardown land on the next result line. Validate it on a known answer before
  trusting a new ranking.
