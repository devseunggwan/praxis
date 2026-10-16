# Repo-wide sweep — perf/leak candidates beyond one diff

`perf-leak-review` is diff-scoped by contract: Step 1 requires `--diff`, and
the reviewer brief's read budget starts from the changed hunks. A request like
"check the whole repository for leaks" has no diff, so running the skill on it
changes its contract. This file is the procedure for that request — fan out
read-only reviewers per area, validate, and report **candidates** — without
pretending the result is the same thing as a diff review.

Nothing here is executed. To prove the candidates this produces, hand the
merged envelope to [`perf-leak-verify`](../../perf-leak-verify/SKILL.md).

## Step 1: Size the repository, then ask once

```bash
git ls-files | wc -l
git ls-files <area> | xargs wc -l | tail -1     # per candidate area
```

Then one `AskUserQuestion`:

- "Area hotspot sweep" — sampling per area, not exhaustive
- "Uncommitted diff only" — the skill's own contract (`git diff` to a file
  outside the repo, then `SKILL.md` Step 1)
- "Both"
- "취소"

Done when: the user picked a mode, and the area list with line counts is
written down.

## Step 2: Snapshot for the no-write check

```bash
git rev-parse HEAD > <scratch>/<run>/HEAD.before
git status --porcelain > <scratch>/<run>/status.before
```

Done when: both files exist in the run's scratch directory.

## Step 3: Write the sweep brief to a file

Copy [`reviewer-brief.md`](reviewer-brief.md) verbatim into
`<scratch>/<run>/brief_full.md`, then append an adaptation block:

- There is no diff. Review **area X** (a directory list).
- Grep the area for hotspot patterns of the five classes (loops around
  remote calls, acquisitions stored on `self`, module-level containers,
  unfiltered `load_all`-style fetches, `subscribe` / `on` / `setInterval`
  registrations).
- Before reporting, open the lifecycle sites the brief already requires —
  release, evict, unregister, the batched sibling.
- Name any **sibling container with a cap or TTL** in the same codebase: it is
  the strongest evidence that a missing bound is a bug.
- Surface a code comment saying a retention is deliberate (for example
  descriptors held to preserve POSIX locks) as such, not as a plain defect.
- Tools: Read, Grep, Glob only. Exclude tests, `node_modules`, and build
  output.
- Cap at about 12 findings per area; a false positive is worse than an
  omission.
- Echo the exact `repo_root`; the final message is one JSON object in the
  brief's envelope schema.

Each delegate's prompt is short: "First read `<brief path>` and follow it",
plus its area and the repo root. A long brief pasted into every prompt drifts
between copies; one file does not.

Done when: `brief_full.md` holds the verbatim brief plus the adaptation block,
and the per-area prompts reference it by path.

## Step 4: Fan out

Build the full area list first, then issue all `Agent` calls in one message.
Never dispatch a placeholder entry to fill it in later — dispatch is
immediate. Prioritize long-lived processes (servers, daemons, background
workers, UI stores) over one-shot CLI code. Areas that partition a large
mixed-language repository well: core runtime, servers/gateways, tools or
plugins, state/scheduling/daemons, UI renderer, desktop main process.

praxis's `fan-out-scope-gate` asks from the second delegation in a turn; the
Step 1 answer is the scope those asks confirm.

Done when: one delegation per area is running, and the count matches the
Step 1 area list.

## Step 5: Collect from the returned results

Rebuild each envelope from the delegate's **returned final message**, not from
progress or log streams — those may truncate long lines, and JSON parsed from
a truncated line fails. Strip a ```` ```json ```` fence if present (normal,
per `SKILL.md` Error Handling).

Done when: one parsed envelope exists per dispatched area, or the missing
areas are named.

## Step 6: Validate and spot-check

1. Merge the envelopes into one (`repo_root` is the same for all) and run the
   skill's validator: `"${CLAUDE_SKILL_DIR}/validate_findings.py" < merged.json`.
   Report rejections verbatim; do not repair them.
2. For each finding, check its `evidence` string appears within ±3 lines of
   its `file:line`.
3. For the top candidates, re-grep the container or handle repo-wide
   (excluding tests) for `delete` / `pop` / `clear` / `close` / unregister
   yourself, and record the grep scope.
4. Diff `git status --porcelain` against the Step 2 snapshot.

Done when: the validator result, the evidence check, the re-grep scope for
each top candidate, and the no-write diff are all recorded.

## Step 7: Report

- **Top:** the scope ("area sampling, not exhaustive"), "candidates, not
  executed", and any dispatch slip (a missing or re-run area).
- **The skill's summary line:**
  `perf-leak-review: <n> candidate(s) — C1:<n> C2:<n> C3:<n> C4:<n> C5:<n> (candidates, not verified by execution)`.
- **Priority table:** candidates on a long-lived process **and** whose absence
  of release you re-confirmed (state the grep scope).
- **The rest**, grouped by class and marked reviewer-only.
- **A class with 0 findings gets a scope caveat** — sampling is not absence.
- Attach the validated merged envelope, and offer `perf-leak-verify` for the
  priority rows.

Done when: every finding in the merged envelope appears in the priority table
or in the reviewer-only groups.
