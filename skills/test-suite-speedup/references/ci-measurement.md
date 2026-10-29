# Measuring test time on CI

Used by SKILL.md Steps 1, 3, 7 and 8. All commands are read-only against
GitHub Actions; `<o>/<r>` is the repository, `ci.yml` the workflow file.

## Baseline run and its step timings

```bash
gh run list --workflow ci.yml --branch main --status success --limit 1 --json databaseId,event
gh api repos/<o>/<r>/actions/runs/<id>/jobs \
  -q '.jobs[]|select(.name=="test" or (.name|startswith("test (")))|.name as $j|.steps[]|"\($j)\t\(.name)\t\(.started_at)\t\(.completed_at)"'
```

The job filter matches a single `test` job and matrix legs named
`test (<leg>)` alike (`.name=="test"` alone returns nothing once the job is
split, see [`parallel-runner.md`](parallel-runner.md)). A `main` run is a
`push` run. When the PR base selects the CI path, take the baseline from the
latest successful `pull_request` run into that same base instead:

```bash
gh api "repos/<o>/<r>/actions/workflows/ci.yml/runs?event=pull_request&status=success&per_page=50" \
  -q '.workflow_runs[]|select(any(.pull_requests[]?; .base.ref=="<base>"))|"\(.id)\t\(.head_branch)\t\(.pull_requests[0].number)"' | head -1
```

A run from a fork has an empty `pull_requests` list, so this filter skips it;
when the base only gets fork PRs, resolve the run through the PR instead
(`gh pr list --base <base> --state merged --json headRefOid`, then
`gh run list --workflow ci.yml --commit <sha> --event pull_request --status success`).
Then confirm the passed/skipped/deselected counts match the after-run before
comparing.

Inside one big step, split by the runner's section banners: save
`gh run view <id> --log --job <job_id>` to a scratch file, then
`grep -E '=== .* ==='` gives each stage's timestamp (pytest / shell suites /
static checks).

## Ranking steps across many runs

One run is noise. Rank job and step durations over the last ~60 completed
runs, and separate queue wait from execution:

1. List run IDs, keeping only `success` and `failure` — cancelled runs truncate
   their steps. For a time window, pass `-X GET -f created='>=YYYY-MM-DD'`
   with `--paginate` (`gh api` sends POST once a `-f` field is added; an unfiltered `--paginate` on a busy repo walks the whole
   history and times out; the filtered query caps at 1,000 runs, so read the
   actual min/max `created_at` back as the window):

   ```bash
   gh api "repos/<o>/<r>/actions/workflows/ci.yml/runs?per_page=60&status=completed" \
     -q '.workflow_runs[]|select(.conclusion=="success" or .conclusion=="failure")|"\(.id)\t\(.event)"' > runs.tsv
   ```

2. For each run, emit rows prefixed with the run id and event: one per job for
   queue (`started_at − created_at`) and duration (`completed_at −
   started_at`), and one per executed step. jq's `fromdate` parses the API
   timestamps. A failed jobs query stops the loop with a non-zero status
   instead of leaving a partial `steps.tsv`:

   ```bash
   (
     set -o pipefail
     while IFS=$'\t' read -r id event; do
       gh api "repos/<o>/<r>/actions/runs/$id/jobs" -q '.jobs[] | select(.completed_at) | . as $j
         | (["queue", $j.name, "", ((.started_at|fromdate) - (.created_at|fromdate))],
            ["job", $j.name, "", ((.completed_at|fromdate) - (.started_at|fromdate))]),
           (.steps[] | select(.completed_at and .conclusion != "skipped")
            | ["step", $j.name, .name, ((.completed_at|fromdate) - (.started_at|fromdate))])
         | @tsv' | awk -v id="$id" -v ev="$event" 'BEGIN { OFS = "\t" } { print id, ev, $0 }' \
         || { echo "jobs query failed for run $id" >&2; exit 1; }
     done < runs.tsv
   ) > steps.tsv
   ```

   Columns: `run_id  event  kind  job  step  seconds`, where `kind` is
   `queue`, `job` or `step` (`step` is empty on the two job rows).

3. Group `step` rows by `(job, step)` and `queue` / `job` rows by job, and
   print median, p90 and max, sorted by median; drop steps whose median is
   under ~3 s. Keep the event (`push` vs `pull_request`) as a grouping key
   when the two take different paths.
4. Read the runner's instance type from a job log (`Instance type:` line or the
   runner label) and compare its vCPU count to the configured worker count.

## Ranking test files from a `pytest -v` log

Use a full-suite run (every matrix leg), not a change-selection run. Save each
leg's log: `gh run view <run> --job <job_id> --log > legN.log`, and check its
last line is the pytest summary — a truncated log ranks only the first part of
the suite; fall back to the raw curl fetch in [the before/after report](#the-beforeafter-report).

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

## Attributing a slowdown between two dates

For "CI is getting slower again, is it the new tests?":

1. **Find the change point with a daily sample.** On a busy repo the
   1,000-run cap covers only days, so fetch one page per day instead
   (`-X GET -f per_page=100 -f created=YYYY-MM-DD`, loop the dates in a script) and
   print per-path daily p50 of attempt-1 successes. A step drop followed by a
   steady daily creep means suite growth; a one-day jump means one change.
2. **Pick two comparable runs**: the median run of the baseline day and of
   today, same event/branch (full-split path, not change-selection). Record
   both `head_sha`.
3. **Compare totals from raw logs**: `collected N items` count, collection
   time (step start → the `collected` line timestamp), pytest summary seconds
   per leg. Report test-count growth % next to time growth %; time growing
   faster than count means either the new tests are individually slow or the
   existing ones got slower — step 5b tells the two apart, so do not name the
   new tests as the cause from totals alone.
4. **Per-file delta**: run the per-file ranking above on both runs, bucket by
   the first two path segments under the test root, and print baseline,
   now, delta. Start attribution at the `collected` line, not the step start:
   otherwise collection time lands on whichever file runs first and shows up
   as a fake "new slow file". Positive control: the per-leg sum must match the
   pytest summary seconds minus that leg's collection time (step 3; pytest's
   summary clock starts before collection) within a few seconds.
4b. **Step change on the base branch, decomposed**: when the push-run series
   (both-legs sum, SKILL.md Step 7) jumps at one or two commits, (a) diff the
   CI `--durations=30` lists of the run just before and just after each jump —
   new node ids at 5–12 s each usually name the file outright; (b) list the
   test files added/changed in that range (`git diff --name-only --diff-filter=AM
   <before> <after> -- 'tests/**/test_*.py'`); (c) time exactly those files
   locally on two detached worktrees (`git worktree add --detach`, one per sha,
   each with its own sqlite metadata DB) with `--junitxml`, and sum testcase
   time per module on both sides — the per-module delta is the attribution, and
   the CI top-30 entries for the same tests are its cross-check (CI is often
   2–4× local per test). Files that error on both sides (missing service) are
   unmeasured: list them, do not count them as zero. When the error is
   `DockerException: Error while fetching server API version` while `docker`
   itself works, the Python client is reading `/var/run/docker.sock` but the
   active daemon lives elsewhere: re-run just those files (collect them from
   the junit `error` cases) with `DOCKER_HOST` set to
   `docker context inspect --format '{{.Endpoints.docker.Host}}'`.
   Do the window, not one jump: when the user wants "everything since the
   regression" in one PR, run 4b over the whole range from the last good
   commit to now, then hand each top module to the classification table —
   a module that is not slower locally (CI-only growth) points at the runner
   or the environment, not at the test code.
   Collect the series with `scripts/ci_leg_series.sh` (background; one jobs
   call per run) and diff the runs either side of each jump with
   `scripts/ci_durations_diff.py [--step <name>] <repo> <before> <after> ...` —
   it windows each log to the main pytest step and prints per-leg
   collected/summary plus the `--durations` entries that grew or are absent
   from the before-run's top-N (absent is not proof of new: confirm with (b)). When
   CI runs pytest without `-v` (dots only), this diff is the only per-test
   evidence; say the attribution covers just the top-N share of the leg time.
   A jump at a release merge (dev→prod) bundles many PRs: locate it again in
   the dev push series to name the PR. Also compare node ids present in both
   top-N lists: the same test growing (e.g. a DAG-simulation test whose DAG
   gained tasks) is "same tests got slower", not new tests.
5. **Name the source**: for each top file, `git cat-file -e <base_sha>:<path>`
   (new vs grown) and `git log --diff-filter=A` / `git log <A>..<B> -- <path>`
   for the PRs that added it, so the report names the work stream.
5b. **Same tests vs new tests** (do this before naming causes): build a
   per-test map (node id → seconds) from both runs' `-v` logs with the
   timestamp-gap method, dropping the first result of the main run (it absorbs
   collection), then split into *common* (in both), *new* and *removed* ids.
   Print the common set's total in both runs, and the new set's total and count.
   Common unchanged ⇒ the runner and shared fixtures are not the cause and the
   growth is new tests; common grown ⇒ look at runner type, conftest, shared
   fixtures first. Then bucket the new tests by work stream (directory) and by
   per-test time (≥ 1 s, ≥ 0.5 s, < 0.1 s): a few dozen slow tests usually
   carry most of the added seconds while thousands of fast ones add little,
   which tells you the lever is per-file, not suite-wide.
5c. **Queue and setup steps**: compare every step of the test legs, not only
   the test step, and read job queue (`started_at − created_at`) on one median
   run per day; a queue jump in a single run is incidental unless the daily
   sample repeats it.
6. **Shard balance**: compare the duration file's entry count with the
   collected count, and the legs' pytest seconds. Unknown tests get the
   average, so a stale file leaves the legs unequal; wall time is set by the
   slowest leg, so the cost is about half the gap. Measure the gap over ~20
   runs (jobs API, `Run tests` step per leg), not one: report p50 gap / 2 as
   the saving, and when the slower leg flips between runs, part of the gap is
   runner noise a refresh will not remove. Under `least_duration` a stale file
   costs tens of seconds, not minutes — size it before proposing a refresh
   workflow.
7. **Verbose logs**: in `-v` output a long node id wraps its status onto the
   next line, so treat the line ending in `[ NN%]` as the test's completion
   timestamp and keep the most recent node id as the current file.

A single test taking an exact round time (`30.00s`, `10.00s`) is almost
always a real timeout or unpatched backoff wait; list it as the first fix
candidate, marked unverified until the code is read.

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

`gh run view --log` / `--log-failed` silently truncate a long job log (a
`log_cli=true` or `-v` pytest job of ~19k lines came back as ~2.9k), so the
summary line and `FAILED`/`ERROR` lines can be missing while the command exits
0. When the grep comes back empty, fetch the raw log instead —
`gh api --allow-escape-sequences repos/<o>/<r>/actions/jobs/<id>/logs` (without
the flag gh refuses the escape sequences and writes nothing), then strip ANSI
codes (`sed 's/\x1b\[[0-9;]*m//g'`); or curl with the gh token:

```bash
curl -sSL -H "Authorization: Bearer $(gh auth token)" \
  https://api.github.com/repos/<o>/<r>/actions/jobs/<job_id>/logs > job.log
grep -aE 'Z (FAILED|ERROR) |=+ .*(passed|failed).* in [0-9.]+s' job.log
```

(Many jobs at once: `xargs -P8` over job ids, grepping each stream so the
3 MB logs never land on disk.)

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

When the user asks "how has CI speed changed?" mid-way (some fixes merged, some
open), answer with the daily-p50 table per path (baseline day → today, with n)
first, then per PR: merged ones from post-merge runs' slowest-leg step seconds,
open ones from their own PR run against same-day runs, labelled as a forecast.
Say when a merged fix sits on the non-slowest leg (it then barely moves wall
time), and end with the remaining gap and the levers that could close it.

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
