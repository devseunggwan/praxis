# Step 6: Verify on CI, not by sitting on local runs

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

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
