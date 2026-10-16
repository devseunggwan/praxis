---
name: test-suite-speedup
description: >
  Make a slow test suite or CI test job faster and prove it — rank where the
  time goes on CI across many runs, prefer structural fixes (shared setup,
  parallel files, matrix legs) over per-file rewrites, probe parallel safety,
  and accept the change only on a measured CI before/after comparison.
when_to_use: >
  Triggers on "test suite speedup", "slow test suite", "slow CI tests",
  "CI test time", "테스트 속도 개선", "CI 테스트 느림".
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
| [`references/pytest-slow-test-causes.md`](references/pytest-slow-test-causes.md) | Classifying a slow pytest file by phase and signature; escaped sleeps; narrowing a runtime `create_all` |
| [`references/pytest-shared-setup.md`](references/pytest-shared-setup.md) | Sharing an expensive pytest object safely: proofs before and after, minimal-churn wiring, change-selection interaction |
| [`references/vitest-isolation.md`](references/vitest-isolation.md) | JS/vitest suites: import-dominated cost, per-module import profiling, isolated vs shared pools, leak probes, shuffle checks |
| [`references/parallel-runner.md`](references/parallel-runner.md) | Parallel-safety probe, serial partition, runner implementation rules, CI matrix split |

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

Before proposing anything, search open issues and PRs for an existing speedup
issue: they often record measured and rejected alternatives.

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
while another suite runs.

Done when: each top-ranked file has a cause from the classification table, or
is explicitly marked "needs a look".

### Step 4: Pick the structural fix, in this order

0. Remove repeated expensive setup inside a hot file (a test that rebuilds the
   same heavy object per call — a DAG bag, an app, a schema). Reuse the repo's
   existing session fixture before inventing a cache, and prove sharing is safe
   first → [`references/pytest-shared-setup.md`](references/pytest-shared-setup.md).
1. Run independent test files in parallel inside the CI entrypoint.
2. Split the CI job into matrix legs so the halves run side by side.
3. Only then rewrite the slowest files' per-case spawns — wide churn, modest
   gain. Offer it; do not default to it.

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

### Step 8: Next-bottleneck hunt (when asked what else is slow)

Re-rank from the post-change full-suite run with the same method, classify
each top file, and reproduce the fixable ones locally (`--durations`,
cProfile) before filing. Put only reproduced causes in scope; list one-time
setup costs, intended waits, and unexplained items under an explicit
out-of-scope section with why, so the next session need not re-measure.

Done when: every top file is either in scope with a reproduction or in the
out-of-scope list with a reason.

## Troubleshooting

| Symptom | Cause | Fix |
| --------- | ------- | ----- |
| Turn stalls for many minutes on a local run | Waiting on a long foreground or chained wait | Run it in the background and end the turn, or push and let CI run |
| A background run of a shell script misbehaves | The script was edited while it ran — bash reads scripts incrementally | Never edit a script during its own run; edit other files meanwhile |
| Before/after differs by less than run-to-run swing | Mixed runner instance types, or contended local runs | Compare only same-instance-type runs; for sub-noise changes, time the touched files alone, base vs branch alternating, 3 reps, and say the full-suite gain is below noise |
| Matrix legs unbalanced after a speedup | A duration-based shard file still holds the old slow times | Refresh only the touched files' entries in the same PR → [`references/ci-measurement.md`](references/ci-measurement.md#refreshing-a-duration-shard-file) |
| Formatter rewrote dozens of unrelated files | A local formatter version differs from the pinned one, run on a directory | Format only touched paths with the pinned version; restore with `git checkout -- <path>` only the outside paths that `git status --porcelain` listed as clean before the formatter ran — on a path you had already edited, that checkout drops your edits too, so revert the formatter's hunks there with `git restore -p` instead |
| Tests fail locally that pass on CI | Newer local runtime major (globals differ) | Run on the CI's runtime major (`npx -y node@<major>`, the CI Python version) |
| pytest dies importing a plugin from a foreign `site-packages` | Shell `PYTHONPATH` points at another tool's venv | Run with `env -u PYTHONPATH` in the same call |
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
