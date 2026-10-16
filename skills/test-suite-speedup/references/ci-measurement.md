# Measuring test time on CI

Used by SKILL.md Steps 1, 3, 7 and 8. All commands are read-only against
GitHub Actions; `<o>/<r>` is the repository, `ci.yml` the workflow file.

## Baseline run and its step timings

```bash
gh run list --workflow ci.yml --branch main --limit 1
gh api repos/<o>/<r>/actions/runs/<id>/jobs \
  -q '.jobs[]|select(.name=="test")|.steps[]|"\(.name)\t\(.started_at)\t\(.completed_at)"'
```

When the PR base selects the CI path, list runs of PRs into that base instead
of `main`, and confirm the passed/skipped/deselected counts match the after-run
before comparing.

Inside one big step, split by the runner's section banners: save
`gh run view <id> --log --job <job_id>` to a scratch file, then
`grep -E '=== .* ==='` gives each stage's timestamp (pytest / shell suites /
static checks).

## Ranking steps across many runs

One run is noise. Rank job and step durations over the last ~60 completed
runs, and separate queue wait from execution:

1. List run IDs, keeping only `success` and `failure` — cancelled runs truncate
   their steps:

   ```bash
   gh api "repos/<o>/<r>/actions/workflows/ci.yml/runs?per_page=60&status=completed" \
     -q '.workflow_runs[]|select(.conclusion=="success" or .conclusion=="failure")|"\(.id)\t\(.event)"' > runs.tsv
   ```

2. For each run, emit one row per executed step — `job`, `step`, seconds — and
   one row per job for queue (`started_at − created_at`) and duration
   (`completed_at − started_at`). jq's `fromdate` parses the API timestamps:

   ```bash
   cut -f1 runs.tsv | while read -r id; do
     gh api "repos/<o>/<r>/actions/runs/$id/jobs" -q '.jobs[] | select(.completed_at) | . as $j
       | .steps[] | select(.completed_at and .conclusion != "skipped")
       | [$j.name, .name, ((.completed_at|fromdate) - (.started_at|fromdate))] | @tsv'
   done > steps.tsv
   ```

3. Group by `(job, step)` and print median, p90 and max, sorted by median; drop
   steps whose median is under ~3 s. Keep the event (`push` vs
   `pull_request`) as a grouping key when the two take different paths.
4. Read the runner's instance type from a job log (`Instance type:` line or the
   runner label) and compare its vCPU count to the configured worker count.

## Ranking test files from a `pytest -v` log

Use a full-suite run (every matrix leg), not a change-selection run. Save each
leg's log: `gh run view <run> --job <job_id> --log > legN.log`.

Every line of that log is `<job>\t<step>\t<ISO timestamp>Z <text>`, but recent
`gh` releases fill the step field with `UNKNOWN STEP` on every line of a
`--job` log, so do not filter on it. Attribute time to files like this:

1. Window the log to the main pytest step by time: take that step's
   `started_at` / `completed_at` from the jobs API (the baseline query above)
   and skip lines outside it, so an earlier, separate pytest step (an
   import-validation step, for instance) and the gap between steps do not leak
   into the ranking.
2. Walk the remaining lines in order. When a line contains a node id
   (`<path>.py::<test>`), remember that file as current.
3. When a line also carries a result word (`PASSED`, `FAILED`, `SKIPPED`,
   `ERROR`, `XFAIL`, `XPASS`), add *this timestamp − the previous result's
   timestamp* to the current file and count one test.
4. Sum across legs; print seconds, share of total, test count and path,
   largest first.

It is an estimate, not `--durations`: fixture setup/teardown and log noise land
on the next result. **Validate it on a run with a known answer** — the pre-fix
run must rank the file you later fixed first — before trusting a new ranking.
State the method and that positive control in the report.

Adding `--durations=30` to the CI's main pytest invocation leaves exact
evidence for the next hunt at no cost.

## Runner-type noise

On a self-hosted pool with mixed instance types, compare CI runs only on the
same type. A suite can run ~20% slower on the previous instance generation,
which swamps a 1% change. When the expected gain is below that noise, also
time the touched files alone, base vs branch alternating, 3 reps each, and
state that the full-suite gain is below CI noise. Single local full-suite runs
swing as much (the same tree measured 113 s and 196 s back to back) —
alternate trees, at least 2 reps, idle machine.

## The before/after report

Per leg, pull the pytest summary line:

```bash
gh run view <run> --job <job_id> --log \
  | grep -oE '[0-9]+ passed[^=]* in [0-9.]+s[^=]*' | tail -1
```

(`gh api .../jobs/<id>/logs` refuses output that contains terminal escape
sequences unless given `--allow-escape-sequences`; `gh run view --log` does
not.)

| Row | Baseline (run `<id>`) | After (run `<id>`) |
| ----- | ---------------------- | ------------------- |
| Job wall time (slowest leg) | | |
| Formerly slow step | | |
| Unchanged half | | |
| Per-leg pytest summary | | |

For a gain across several PRs: per-leg main-pytest seconds for baseline → each
merged step → now, plus slowest-leg wall time and the both-legs sum, with run
links and each PR's share. Then name the CI path not exercised, say "one run
each", and say so when pruning tests contributed little.

## Refreshing a duration shard file

After a speedup, a duration-based shard file (`.test_durations` for
pytest-split's `least_duration`, or a similar timing cache) still records the
old slow times, so the splitter overweights the sped-up tests and unbalances
the legs.

1. Sum the touched files' share of the file (`json.load`, filter entries by
   path prefix, total) to show it matters.
2. Refresh only those entries: `pytest --store-durations <touched files>`.
   pytest-split merges per test id into the existing file unless
   `--clean-durations` is given, so do not run the whole suite.
3. Diff old vs new JSON in code and confirm only the touched files' ids
   changed.
4. Say the new entries are local-machine timings and leg balance is unverified
   until CI runs; legs that still differ noticeably are the symptom.
