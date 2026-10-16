# Pruning unnecessary tests

The prune shape of `test-suite-consolidation`: audit → approved scope → one
issue and one PR. The deliverable comes in two stages — a chat report with a
per-file keep / drop / merge / investigate table, then the issue and PR for the
scope the user picks. **Never delete before the user picks the scope.**

## Own-PR quick pass

When the question is "any unnecessary tests?" about tests this session added
in its own open PR, skip the criteria question, probes and fan-out below: the
scope is the diff's added tests. Drop, in one `test(...)` commit, cases that:

- (a) exercise only the language or a library (e.g. string sort order);
- (b) parametrize the same generator/factory over inputs that take one code
  path;
- (c) re-list rules whose owning module's test already pins them — keep one
  case proving the caller invokes the check;
- (d) re-test a shared parser/helper;
- (e) duplicate a repo-wide contract test — grep that test's selection
  predicate (glob + text match) to confirm it covers your file first;
- (f) restate in a mocked-session unit test what the same PR's real-DB
  integration test already asserts (rows created, state fixed,
  secret-before-commit, lookup scoping). The real DB is the stronger oracle.
  Before dropping, move any assertion only the unit test had into the
  integration test (e.g. "no secret write on the conflict path"), keep one
  representative case per code branch in parametrize lists, and keep unit
  tests for branches the integration test does not drive (vendor response
  classification, error mapping).

Also drop asserts that only re-test a library guarantee (a secret type's
`repr`), and a case whose assertions another case's exact-equality assert
already implies (a "no `s3:List*`" case next to one that asserts equality on
the whole action list).

After the drop, **mutate each remaining guard once** (delete or invert it) and
confirm a kept test fails — so pruning did not remove the only test pinning
it. Report a table of dropped test · reason and update the PR's verification
note with the new test count.

Run this pass before opening the PR. If a PR this session wrote reaches its
merge briefing without it, run it first and hold the briefing: "are the
unneeded tests cleaned up?" is a merge-time question an unaudited PR cannot
answer. For a large added set (100+ cases), give each PR one read-only
subagent with these criteria and a fixed row format (one row per test:
verdict KEEP / DROP / MERGE, criterion, evidence = surviving test name or
grep), then re-check every DROP / MERGE yourself (see
[Re-verify](#re-verify-before-you-report-or-delete)). When the request spans
several open PRs of one work stream, run the pass once per PR with the same
criteria; a PR still being written gets it after its author finishes.

## 1. Ask the criteria once

"Unnecessary" has four separable axes. Offer them; the usual pick is the first
three, with timing for reference only:

- **structural orphans** — fixtures no test reads; tests for deleted code;
  test files the runner never discovers;
- **phrase-pin** — asserts that a doc or SKILL.md sentence exists and executes
  nothing;
- **duplicate coverage** — same entrypoint, same input class, same asserted
  outcome;
- **slow and low-value tests**.

Also ask the output form; "chat report first, then issue/PR" is the default.

## 2. Deterministic probes (yourself, before any fan-out)

- **Runner discovery** — read the CI entrypoint's globs (e.g.
  `tests/hooks/*/test_*.sh`) and list `test_*` files no glob matches.
- **Tests whose target is gone** — map each test file to the code directory it
  exercises by name and confirm the directory exists. Scanning test bodies for
  path strings is useless: tests are full of synthetic paths.
- **Orphan fixtures** — grep each fixture basename outside the fixtures dir.
  Before calling one an orphan, check glob loaders
  (`FIXTURE_DIR/<prefix>-*.jsonl`) and directory loaders. A secret-scan test
  that walks the fixtures dir reads every file but is not a consumer.
- **Timing** — run it in the background, timing each file separately. It is
  slow (minutes per ~50 shell suites); never let the report wait on it.

## 3. Fan-out (three read-only subagents, no test runs)

Split by tree: top-level tests, hook/unit tests, and a duplicate-and-orphan
lane. Give each lane these classes:

- `behavior` — executes code;
- `contract` — pins a structure code consumes at runtime: manifest fields, a
  catalog JSON, a fenced block a hook parses, a hook's own output text, or a
  SKILL.md code branch the test extracts and *runs*;
- `phrase-pin` — doc text nothing reads;
- `mixed`.

Before marking any `drop`, a lane must grep the pinned phrase across code
directories and state the grep it ran. Ask for one row per file plus an
`enumerated_count`, and check that count against your own `git ls-files`
total. Parse the lanes' final returned results, not partial or truncated
output.

## Re-verify before you report or delete

Lane verdicts are claims. Re-read the code path behind every `drop` /
`duplicate` yourself. The traps:

- **"Duplicate" claims that rely on an early return in today's code.** An
  opt-in env var read only after a fire decision makes arm-on equal arm-off on
  silent input. When the test is labelled a negative control ("must not fire
  on…"), it pins a *future* regression — keep it. A case is a duplicate only
  when the env, the input class and the assertion all match.
- **Mirror suites (sh vs py).** Delete the mirror only where the survivor
  asserts the *full* token stream or output. Where the survivor checks a
  weaker predicate, the mirror is the stronger test — keep it.
- **"Prose-only" wrapper tests.** Some assertions pin *code inside a SKILL.md
  template* (guards, traps, rc branches) the executable twin never reaches.
  Drop only the rationale-sentence assertions; keep the shape assertions.
- **Vacuous tests.** An "oversized line refused before allocation" test where
  a size early-out fires before the per-read cap is ever exercised. Confirm by
  reading the bounded reader; classify it as vacuous, not duplicate.
- **Near-duplicates with one unique assertion.** Keep them, or merge the
  assertion into the survivor. Never drop them silently.
- When re-verification shrinks the approved scope, list each kept item with
  its reason in the PR body's "Kept deliberately" section and in the approval
  ask. Never narrow silently, and correct the issue body when it overstated
  the scope.

## 4. Implement

1. **Issue first.** On a public repo the issue and the PR each need their own
   approval: write the body to a scratch file, show it, then
   `gh issue create --body-file <file>`.
2. **Worktree** from a fresh `origin/<base>`.
3. **Before `git rm`**, grep each deleted file's name across the repo
   (excluding the changelog). Rewrite comments that cite it with the
   current-state reason — no "dropped in #N" history. Check whether a shared
   helper (e.g. an assert library) still has consumers.
4. **A check that greps a SKILL.md for a script name** passes even when the
   script is gone. Rewrite it to resolve the `${PLUGIN_ROOT}/…` path from the
   skill body and assert `-x` on that file. Use `if …; then ok=0; fi`, not
   `A && B || C` (shellcheck SC2015).
5. **Commits** — one per class (orphans / phrase-pin / tightened check /
   duplicates), each with `Refs #N`; the PR uses `Closes #N`.
6. **Run the CI entrypoint in the background.** The agent's foreground
   `python3` may lack pytest even though the runner's interpreter has it, so
   judge from the runner's log, not an ad-hoc `python3 -m pytest`. Lint and
   the changed shell suites can run in the foreground first.

## Report shape

In the user's language, in chat:

1. Unverified items first — timing still pending, and which lane claims you
   re-checked yourself.
2. Counts per axis, with the enumerated file total.
3. One table per axis: file · verdict · one-line ground.
4. One recommended scope, with the approval question on the last line.
