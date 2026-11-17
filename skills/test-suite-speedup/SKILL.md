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
  "테스트 시간이 왜 다시 늘었나", "테스트가 왜 N분 증가했나", "한 주만에 CI 가
  느려졌다" — any "why did the tests/CI get slower" question, even a one-line
  one with no speedup asked. Load this before the first `gh run list`: the
  attribution recipe and its scripts (`ci_leg_series.sh`,
  `ci_durations_diff.py`) already exist, and hand-rolling them costs the turn.
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
| [`references/ci-verification.md`](references/ci-verification.md) | Step 6 in full: local-only failures, suite-wide stub safety, bot review waits, merge-run base, per-file metric when the suite grew |
| [`references/report.md`](references/report.md) | Step 7 in full: the before/after table, post-merge confirmation on push runs, net position, revising published text |
| [`references/next-bottleneck.md`](references/next-bottleneck.md) | Step 8 in full: accounting for a whole CI delta, the "keeps getting slower" answer order, equivalence-test premises |
| [`references/troubleshooting.md`](references/troubleshooting.md) | Symptom / cause / fix table |

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
`pytest -v` (a change-selection run does not cover every test). When CI logs
only dots plus a top-N `--durations`, rank from one serial local full-suite
`--junitxml` run instead (never parallel groups in one checkout) →
[`references/ci-measurement.md`](references/ci-measurement.md#ranking-test-files-when-ci-logs-only-dots). Validate the
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
   A test that scans every tracked file (manifest/guard sweeps) pays for the
   largest tracked files, not the test logic: rank them first with
   `git ls-tree -r -l <ref> | awk -v c=5 '$c ~ /\.(ya?ml|json)$/' | sort -k4 -rn | head`
   — one real-data fixture can be half the bytes. Shrink such a fixture to the
   minimum its own test's preconditions need (read the asserts: thresholds,
   counts), cut it so it stays referentially consistent (keep the newest N
   records and only the children that point at them), move it beside its test
   in the repo's `fixtures/` convention (first grep every reference to the
   fixture path, other tests, scripts and docs, and keep what they need),
   replace any ratio pinned to the old file with a floor, and prove it still discriminates with one mutant per
   behaviour the test guards.
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

Hold changed test code to the repo's coding guideline, run only the touched
files plus lint locally, push, and watch the PR's checks in the background.
A failure seen only in a local environment is checked on the unmodified base
first; a suite-wide stub is merge-safe only after its four bypass routes are
probed; a `pull_request` run tests the merge into the base tip; and when the
suite grew between runs, the touched file's CI seconds are the result. Full procedure: [references/ci-verification.md](references/ci-verification.md).

Done when: a CI run of the branch on the same path as the baseline has
finished, and every open review finding is answered.

### Step 7: Report the before/after

One table with both run IDs: job wall time (the slowest leg), the formerly slow
step and the unchanged half, plus the per-leg summary line; for a gain spread
over several PRs, baseline → each merged step → now. After the merge, confirm
the gain on the base branch's own push runs (per-leg seconds and their sum in
time order), lead with the net position against the pre-regression baseline,
and revise a published comment or body from its live text. Full procedure: [references/report.md](references/report.md).

Done when: the table carries both run IDs, the comparison is on the same
runner instance type, and every unverified path is named.

### Step 8: Next-bottleneck hunt (when asked what else is slow)

Account for the whole delta before proposing a fix (per-step table of both
runs, then same-tests-slower vs new tests vs collection), answer "we keep
cutting but it does not go down" in the five-part order, settle two premises
before offering to delete an equivalence test, re-rank from the post-change
run, and keep unexplained items in an explicit out-of-scope list. Full procedure: [references/next-bottleneck.md](references/next-bottleneck.md).

Done when: every top file is either in scope with a reproduction or in the
out-of-scope list with a reason.

## Troubleshooting

Symptoms seen while measuring or landing a speedup (a nonzero `gh run watch`
after a merge, a timeout only on a runner change, a speedup already merged by
another session, ...), each with its cause and fix, as a table. Full procedure: [references/troubleshooting.md](references/troubleshooting.md).

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
