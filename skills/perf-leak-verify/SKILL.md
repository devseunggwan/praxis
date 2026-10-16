---
name: perf-leak-verify
description: >
  Prove or refute perf/leak candidates from a read-only review by running
  code — drive each candidate's real lifecycle N times in an isolated probe,
  measure the container or call count that would grow, and pair every row with
  a positive control. Reports one row per candidate; a row that was not run is
  never reported as verified.
when_to_use: >
  Triggers on "verify leak candidate", "prove leak", "leak repro",
  "perf candidate verify", "누수 실증". Do NOT activate on "perf review" or "leak
  review" (reading a diff for candidates is `perf-leak-review`) or on general
  benchmarking with no candidate list.
verified-against-runtime: true
runtime-verified-at: 2026-10-16
runtime-verified-note: "git 2.50.1 — `rev-parse HEAD` / `status --porcelain` snapshots and the `diff` against them ran on a scratch repo. Python 3.9.6 — `python3 -I -B -c` with a trailing arg showed `sys.argv == ['-c', '50']` (N read from `argv[-1]`), and `runpy.run_path(..., run_name='__main__')` ran the probe as `__main__`. vitest 5.0.5 on node 26 — a config outside the project importing `vitest/config` failed with `UNRESOLVED_IMPORT`; a plain object spreading the project's `vite.config` with `root`, `dir`, `include`, `globals`, `pool: 'forks'`, `execArgv: ['--expose-gc']` ran a scratch probe test that saw `gc` as a function. No desktop-process probe was run"
---

# perf-leak-verify

## Overview

[`perf-leak-review`](../perf-leak-review/SKILL.md) reads code and points at
candidates; by its own contract it never runs anything, so every finding it
emits is graded `candidate`. This skill is the execution half: it takes those
candidates — from a single diff review or a
[repo-wide sweep](../perf-leak-review/references/repo-wide-sweep.md) — and
drives each one's real lifecycle in a probe that lives outside the repository,
measuring whether the thing the reviewer said grows actually grows.

The deliverable is a table, one row per candidate: the lifecycle that was
driven, N, the measured before → after, and a control. Candidates you did not
run stay in the table as `BLOCKED` or `not run`.

**Core principle:** a candidate becomes a defect only when its real lifecycle,
driven N times with its cleanup, grows — and a control on the same path
shows the release mechanism itself works.

## When to Use

- A `perf-leak-review` envelope (or a sweep's merged envelope) exists and the
  user asks to prove, reproduce, or refute its candidates.
- A leak report needs a reproduction before it goes upstream
  ([`upstream-bug-report`](../upstream-bug-report/SKILL.md)).
- Not for open-ended profiling with no candidate list — this skill verifies
  named `file:line` claims, it does not hunt.

## The Iron Law

- **No write to the repository.** Every probe, config, and output file lives
  in a scratch directory outside the tree; Step 9 proves it.
- **Never relaunch or attach to the user's running application.** Use an
  isolated instance (separate profile/data directory, config home, debug port)
  or mark the row `BLOCKED`.
- **A row not run is not verified.** It is `BLOCKED` (with the reason) or
  `not run` — never folded into the reproduced count.

## Process

### Step 1: Pick the candidates you can drive in isolation

Prefer candidates on long-lived processes (servers, daemons, UI stores), ranked
by the reviewer's confidence. Before writing a probe, read three sites per
candidate: the insert or acquire site, the release function, and the teardown
path the candidate claims never calls it.

Done when: every candidate in the envelope is either selected (with its three
sites cited by `file:line`) or listed as `not run` with a reason.

### Step 2: Snapshot for the no-write check

```bash
git -C <repo> rev-parse HEAD > <scratch>/HEAD.before
git -C <repo> status --porcelain > <scratch>/status.before
```

Done when: both snapshot files exist in the scratch directory.

### Step 3: Run probes with the project's own runtime

System interpreters lack the project's dependencies and its bootstrap. Run
every probe under the project's own runtime, from outside the tree:

- **Python.** Use the project's interpreter (its virtualenv, or the one its
  launcher reports). Run the probe file from scratch via a short wrapper that
  puts the checkout on `sys.path`, points the app's config/home directory at a
  scratch path, runs any bootstrap the entry point normally runs, then
  `runpy.run_path(<probe.py>, run_name="__main__")`. Run with `-I -B` so no
  user site-packages or `.pyc` files leak in. A `-c` wrapper shifts `argv`, so
  read N from `sys.argv[-1]`.
- **Frontend stores (TypeScript, vite + jsdom).** Use the repo's own test
  runner with a config kept **outside** the repo: a plain object that spreads
  the imported `vite.config.ts` (importing `vitest/config` from outside the
  repo fails to resolve), with `root: <app dir>`, `dir: <scratch probe dir>`,
  `include: ['**/*.probe.test.ts']`, `setupFiles: [<repo test setup file>]`,
  `pool: 'forks'`, and `execArgv: ['--expose-gc']`. Run
  `npx vitest run --config <scratch config>` from the app directory; use
  `describe` / `it` as globals. This setup swallows console output, so write
  results to a JSON file. A bare `node --import tsx` fails on
  `import.meta.env` and DOM globals — do not use it.
- **Desktop main process.** Launch an isolated instance only (Iron Law), or
  mark the row `BLOCKED`.

Done when: a trivial probe (import the target module, print one value) runs
under the project's runtime with zero files created inside the repo.

### Step 4: Drive the real lifecycle, including its cleanup

Call the production functions themselves — the real `create()` → `cleanup()`
pair, the real append path through its actual branch. Stub only the I/O or
database boundary; use `types.SimpleNamespace` (or a plain object) for
collaborators the code only reads attributes from. Silence or redirect
stderr: heal and escalation paths often log one line per iteration.

Done when: the probe calls the production acquire and release/teardown
functions (named in the probe), with only the I/O boundary stubbed.

### Step 5: Measure the verdict quantity

Pick the number the candidate says grows, and read it before and after N
iterations:

- **C2 / C3 / C5 (unreleased resource, unbounded container, unreleased
  listener):** `len()` of the module-level container or registry is the
  verdict. `tracemalloc` deltas include first-call imports inside the loop,
  so report them only as a secondary number.
- **Frontend retention:** measure by identity. Get item X, create N distinct
  ones, subscribe and unsubscribe each (as mount → unmount does), then check
  that `getter(X) === X` still holds. Report heap after `gc()` and bytes per
  item as secondary numbers.
- **C1 (query inside a loop):** count statements. With SQLite, use
  `conn.set_trace_callback` on a real database in the scratch home; normalize
  each statement (strip digits and literals) and `Counter` them. Run at 2–3
  sizes to show the count scales linearly. Then check whether the realistic
  caller reaches that path at all (for example a persisted marker that skips
  it), and say so.
- **Event-loop blocking (asyncio):** run a 1 ms `asyncio.sleep` ticker beside
  the operation and report the longest gap between ticks. Stub the network
  call so only local blocking work is measured.
- **Latency on a long-lived process:** measure warm. Run the operation once
  before timing it — the first call pays imports and credential-store warm-up
  and can overstate a stall by an order of magnitude. Report the warm number
  as the steady-state cost and the cold one only as a once-per-start cost.

Done when: every selected row has a before → after value at a stated N.

### Step 6: Run a positive control on every row

Show the release mechanism itself works: call the release function directly
and show it removes the key, or show that a sibling container on the same path
drains. Together with Step 5 this shows the defect is the **missing call**, not
a broken mechanism. For identity measurements, the control is the same key
called N times, which should show near-zero growth.

Done when: every reproduced row has a control result beside it.

### Step 7: Compare base and branch when a fix exists

Run the same probe script against both checkouts, passing the checkout path as
an argument, so the two numbers come from one measurement.

Done when: each row with a candidate fix shows base and branch values from
the same script, or the step is marked not applicable.

### Step 8: Size the real impact read-only

Open the application's real state read-only (for SQLite,
`sqlite3 "file:<state.db>?mode=ro"`) to get the actual volume: object counts,
child counts, the time window. Say plainly when a reproduced leak is small at
the user's real volume.

Done when: each reproduced row carries a real-volume estimate, or an explicit
"volume unknown" with the reason.

### Step 9: No-write check

```bash
git -C <repo> status --porcelain | diff <scratch>/status.before -
```

An empty diff (and an unchanged `HEAD`) is the pass.

Done when: the diff is empty, or every difference is reported as a
violation.

### Step 10: Report

Lead with: the number reproduced out of the number run, which rows were not
run and why, and whether the reproduced growth matters at the real volume.
Then one table:

| Candidate `file:line` | Lifecycle driven (N) | Before → after | Control |
| ------- | ------- | ------- | ------- |

Order priorities by measured unit size × process lifetime, and name the exact
missing call site when there is one. Offer upstream issue drafting as a
separate, approval-gated step ([`upstream-bug-report`](../upstream-bug-report/SKILL.md));
for that, rerun the probe as a failing test in a detached worktree at the
upstream default branch with the repository's own test runner, so the body
quotes red output from upstream, not from the live checkout.

Done when: every candidate from the input envelope appears exactly once in the
table or in the not-run list.

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| Probe fails on missing dependencies | Wrong interpreter — switch to the project's own runtime (Step 3) |
| Test-runner config cannot resolve `vitest/config` | Spread the imported `vite.config.ts` as a plain object instead |
| Results never appear in the console | The runner swallowed it; write results to a JSON file |
| `exit=0` reported after a pipe (`\| tail`, `\| grep`) | That is the pipe's status. Write logs to a file and read `$?` from the unpiped command |
| Candidate needs the user's running app | Isolated instance or `BLOCKED` — never the live one |
| Step 9 diff is non-empty | Report each changed path as a violation; do not clean it up silently |

## Limitations

- **A probe is a model of the caller.** Driving the lifecycle N times in a
  loop shows the mechanism grows; it does not show that production reaches N.
  Step 8 sizes that gap but does not close it.
- **Stubs can hide the defect.** Stubbing the I/O boundary removes whatever
  the boundary itself retains; a row reproduced only behind a stub says so.
- **A shared surface quoting a cold number is wrong.** If an issue or PR body
  already quoted a cold latency figure, correct it once the warm number exists.
