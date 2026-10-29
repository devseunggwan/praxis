# Skipping redundant CI runs (reuse, superseded, skip markers)

Use this when the slow part is not one run's speed but runs that should not happen at all: bot backmerges, pushes that tested an identical tree, or pushes a newer push is about to overtake. The lever moves whole runs, so size it by counting run classes in the history first, not by timing a run.

## 1. Classify the history before designing

Walk the branch's first-parent history and sort each merge (bot backmerges especially) into these classes. Compare git trees only (`git rev-parse <c>^{tree}`), never diffs:

| Class | Test | Lever |
| --- | --- | --- |
| Result tree == first parent's tree | nothing changed on this branch | record the merge, push with a skip marker |
| Result tree == second parent's tree, and that parent's own push run passed | the tree was already tested elsewhere | reuse that run's result instead of re-running tests |
| Next push follows within N minutes | the run will be overtaken | supersede: skip tests and deploy when a newer run exists |
| Both sides differ | a new tree nobody has tested | **always test it.** Merged-tree-only failures land here |

Pull the failed runs of the last weeks per class (`gh run list --branch <b> --status failure`), and read each failed job's log for the real cause. A runner `shutdown signal` or a cancellation is not a test failure, so count those separately. Two people can count the "same" failures differently for exactly this reason. The history keeps moving, so report class counts as a range across measurements, with the date.

## 2. Decide "did a commit start a run" by asking Actions, never by reading the message

Several things leave a commit without a run of its own. Reading the commit message misses each one:

- the five bracket markers, anywhere in the message;
- a `skip-checks: true` trailer, which counts only as the last trailer;
- a multi-commit push, where GitHub decides on the pushed head only, so the inner commits get no run even with clean messages;
- the workflow's `paths` filter.

Query `GET repos/<o>/<r>/actions/runs?head_sha=<sha>&event=push&branch=<b>` and filter `workflow_runs[].name` to the suite's workflow. To find a newer run, walk the branch tip back along first parents (`commits/<sha>` → `parents[0].sha`) until you reach your own push, with a step cap. Read commits through the API inside the runner: a `git fetch --depth` there, or in a developer's full clone when the script runs locally, turns the clone shallow.

In the fake `gh` used by tests, register the planning push's own run too. Without it, a mutant that lets the walk count the push itself passes every test.

## 3. Every uncertain answer runs the tests

Plan-step outputs default to "do not skip":

- an API error, a missing env var, a pending upstream run past the wait cap;
- any event other than the one the scheme targets: guard inside the script on `GITHUB_EVENT_NAME`/`GITHUB_REF_NAME`, not only in the workflow `if:`, so a later workflow edit cannot make the script skip a release branch's tests and deploy.

When a degraded answer can still cost something (an API error after a long wait reopens a cancellation window), print `::warning::` so it shows in the run summary. Size any wait to the runner's job cap (e.g. a slim runner's 15 minutes), and test that the wait constant fits under `timeout-minutes`.

## 4. Cancel superseded tests per job, not per run

Put `concurrency: {group: <name>-<branch>-<matrix leg>, cancel-in-progress: true}` on the test jobs only, and only for the branch push the scheme targets. Keep bundle and deploy jobs outside the group. A cancelled test then blocks publishing through `needs.test.result == 'success'`, and a run-level group would also cancel deploys.

Before shipping it, size the deferral it causes and put it in the PR body: every push that lands within one test duration of the previous one cancels that run's tests, bundle and deploy, so a burst defers the branch's deploy until the burst ends. From the branch history, count push gaps shorter than the test job's duration and the longest consecutive chain of such gaps (`git log --first-parent --format=%ct` → sorted gaps). The last push of a burst still tests and deploys, so no stale tree is left, but the user should hear "deploys can lag by up to <chain × gap>" before merge, not after.

## 5. Test the workflow gates as code

Load the workflow YAML in pytest, translate each gated job's `if:` and `concurrency` expression into a Python predicate, and assert run/skip per scenario: normal, tests failed, tests cancelled, reuse, superseded, plan job failed. GitHub still evaluates a job whose `if:` has a status function (`!cancelled()`, `always()`) after its `needs` job was skipped, with that job's outputs empty. Evaluate such a job independently, not as `bundle and deploy`. Prove the tests with workflow mutants: drop a `result == 'success'` term, move the group to run scope, run the plan job on any ref.

## 6. Keep skip markers out of your own commit text

A commit body that mentions a skip marker in prose stops the PR's CI from running at all. Before each commit run `grep -inE '\[(skip ci|ci skip|no ci|skip actions|actions skip)\]|^skip-checks' <msg>`. When it slipped through, push an empty commit (`git commit --allow-empty`) instead of amending. Never put a marker in a PR title in a squash-merge repo: the title becomes the merge commit.

## 7. Verify after the merge

PR events skip the branch-push path entirely, so the first live evidence is the first real push after merge. Classify that push (§1), read job states from `actions/runs/<id>/jobs`, and read the `reuse=… superseded=…` log line as soon as the plan job completes: `gh api repos/<o>/<r>/actions/jobs/<job id>/logs --allow-escape-sequences` serves a finished job's log while the rest of the run is still going (`gh run view --log` refuses until the whole run completes).

A cancelled test job is evidence of §4 only with its cause: read `check-runs/<job id>/annotations` and expect `Canceling since a higher priority waiting request for <group> exists`, plus a newer push's run on the same branch. A runner shutdown or manual cancel reads differently.

The interesting outcomes (reuse hit, superseded at plan time, skip-marked no-op) occur only when the history produces them, so one post-merge push rarely covers them. Run a `.py` poller in the background that, per new first-parent push: waits for its runs to complete (or records "no runs" after a few minutes, the expected no-op result), classifies the tree, and appends the plan lines and job conclusions to a JSONL file. Stop it when each target outcome was seen once, with a ceiling of a few hours, and report what was and was not observed when it stops. Do not create a branch push yourself to trigger an outcome: it writes to the shared branch.
