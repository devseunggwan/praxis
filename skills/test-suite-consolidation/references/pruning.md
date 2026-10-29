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

For parametrized or per-caller cases whose necessity is unclear, decide with a
**mutation matrix**, not by reading: list the plausible regressions of the
diff (revert each changed source file alone; reintroduce the old pattern in a
changed template, e.g. read a value from runtime context again), run the
cases under each, and tabulate case × regression. A case whose failures are
always a subset of another kept case's is redundant — typically a second
caller of the shared code the PR did not change, or a variant axis (version,
context shape) the caller never reads. Keep a case that is the only one
failing on some row, and keep the one that runs the production runtime's
shape even when it adds no unique row, saying why. Show the matrix in the
answer; it is the evidence for each drop.

Build the mutants as whole replacement files of the changed source and run
[`scripts/mutation_matrix.sh`](../scripts/mutation_matrix.sh) (copy over → run
→ record failed names → restore from HEAD → `restored_clean=0`). Include, besides
reverting to the old logic: each sibling wrong approach the PR rejected (e.g.
three-dot vs two-dot vs commit-count), always-true / always-false, dropping the
core comparison, comparing against the wrong side, and each fail-safe branch
flipped (`set -e` added, error → the unsafe answer). Mutate config wiring too
(revert the workflow/config that calls the code) so the wiring test earns its
row. A mutant no test fails is an equivalent mutant when the observable result
cannot change (say why in one line); otherwise it is a missing test. Generate
mutants with `sed` from HEAD and `diff` each against HEAD before running — a
generation loop that silently writes an empty file produces a row where every
test fails and hides the real matrix. Delete any helper parameter the dropped
cases alone used.

Before running, assert every mutant's anchor string occurs exactly once in
the current source (`s.count(old) == 1`); an anchor that stopped matching
after an edit or a rebase produces a mutant identical to HEAD, which reads as
"no test catches it". Re-check anchors and re-run the matrix after anyone
(including a review bot) pushes new branches into the code. A mutant that
removes a *production* check and fails no test can also mean the check itself
is redundant (a later-added check covers every case it decided): then delete
the check and the test that existed only for it, rather than writing a new
test for it. Give the harness a per-mutant timeout and record a timeout as "caught (hung)": a mutant that removes a wait bound makes a wait test hang, and without the timeout the whole matrix dies on that row. When several review-bot guards each look dead in the matrix, propose replacing the stacked conditions with the one rule they approximate (and re-fit the tests to it) as a user choice, rather than deleting them one by one. While the matrix runs, the source file is a mutant — run lint
and shellcheck on it only after the matrix restores it, or the findings
belong to the mutant.

A fake world (fake `gh`, fake API, temp git origin) must also hold the state
the real system always produces as a side effect of the subject itself — e.g.
the push under test always has its own workflow run. Leave it out and any
stop condition that exists to skip that state ("stop when the walk reaches
HEAD") can be deleted with every test still green; register it in the
harness helper so every test gets it, and add the "include the subject"
mutant to the matrix.

When a test injects a failure (fake API error), scope the injection to the
call under test (match the URL or command), not every call: a later edit
that adds an earlier call makes the code exit there, and the test silently
stops reaching the branch it was written for.

After the drop, **mutate each remaining guard once** (delete or invert it) and
confirm a kept test fails — so pruning did not remove the only test pinning
it. Report a table of dropped test · reason and update the PR's verification
note with the new test count.

Run this pass before opening the PR — and before the first verification
anchor, so the anchor's test count and FAIL-before run are taken once on the
final set. A brief that lists N scenarios to test is a floor for what to
exercise, not a promise that N cases survive: keep a listed scenario only if
the matrix gives it a unique row, and name in the PR body what else covers a
dropped one (e.g. a real-history scan). If a PR this session wrote reaches its
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
For a read-only audit request ("점검", "check which are unnecessary"), skip
the question: state the default (first three axes, timing as reference, chat
report first) as an assumption in the first status line and proceed — the
audit deletes nothing, and the scope question comes at the end anyway.

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
- **Tests whose only target is dead code** — for each symbol a test imports,
  grep it across every source root with test files excluded
  (`grep -rnw <sym> src scripts <pkgs> --include=*.ts | grep -v '\.test\.'`);
  a definition-only hit means the test guards nothing that runs. Run one known
  live symbol through the same grep as a positive control, and check log
  strings that merely mention the name (they are hits, not callers). Also check
  the build's entry list and any path-string loaders (`'./src/…/x.ts'` maps)
  before calling a module orphaned. Report these as "delete test + code", not
  as a test-only drop — removing just the test leaves untested dead code.
- **Conditional skips** — grep `skipIf` / `runIf` / `.skip` / `.only` / `.todo`
  (vitest/jest) or `skipif` / `importorskip` (pytest), and judge each condition
  against the CI job's env (which env vars, creds, browsers, files it has). A
  block CI never runs is a `never-runs-in-CI` finding, not automatically a drop.
  - Before calling a main-CI skip `never-runs`, map the file against every
    other workflow's pytest targets, including target lists a script prints
    (`scripts/<x>.sh targets`) and grep-built lists — side workflows with the
    optional dependency often run it.
  - Enumerate never-runs files over the whole tests tree, not only the files
    a window audit touched: grep every file-level gate form (`importorskip("<dep>")`,
    `_HAS_<DEP>`, `skipif(<dep> is None …)`, and conftests that gate a directory),
    subtract every dep-installing workflow's targets, and fix the full list in one
    pass. A reviewer who runs that enumeration finds the files you missed, and each
    one costs another review round.
  - Then run the uncovered files once in a venv that has the optional
    dependencies, with `-rs`. A test CI always skips can be failing unseen;
    re-run it at the commit that added it (throwaway detached worktree) to say
    "failing since added" vs "regressed".
  - A pytest skip reading `got empty parameter set` is a parametrize over an
    empty glob or list: the test has never run anywhere. Check `git log` on the
    globbed directory. If it once held files and lost them, the test is
    vacuous. If it never held any (a per-spec contract for a feature with zero
    specs yet), it is a dormant contract that activates with the first file:
    report it as dormant, not as a defect, and change nothing unless the glob
    points at the wrong path. A loop over zero files (`for p in DIR.glob(...)`)
    passing vacuously is the same case.
  - A skip on a missing env var or ref (`CONTRACT_BASE_REF`, a merge-base with
    a branch a shallow checkout lacks) is fixed in the CI step's `env:`. With
    `fetch-depth: 2`, `HEAD^1` is the PR base tip or the previous push commit.
    It spans a whole push only when every allowed merge method lands one commit
    (squash) or a merge commit — read the base ruleset's `allowed_merge_methods`
    (`gh api repos/<o>/<r>/rules/branches/<base>`) before claiming so; a
    permitted rebase merge pushes N commits and `HEAD^1` covers only the last.
    State that caveat with the observed merge mix in any reply defending `HEAD^1`.
  - Fix a never-runs finding by adding the file to the side workflow's target
    list, run that list locally in the side job's environment with the job's
    marker filter (`-m "not dagbag"`; when replaying a CI line that uses
    `mapfile`, macOS `/bin/bash` 3.2 lacks it, so build the list with
    `targets=$(bash <script> targets)` instead), and add one line to the repo's agent
    guide saying where a new test of that kind must be registered. A
    hand-kept list rots, so the guide line is the non-CI half of the fix.
  - A test that has failed since it was added and pins behavior the
    production code never implemented (validation, canonicalization) is a
    latent production defect. A canonicalizing fix (strip leading zeros,
    lowercase) makes distinct inputs equal, so de-duplicate in input order
    (`list(dict.fromkeys(...))`) and pin that with a case like `['007','7'] → ['7']`;
    otherwise downstream acts on the same target twice. Fix production in its own `fix(...)` commit,
    run every test file that imports the module, and put the behavior change
    at the top of the PR body: it is the one commit in a test cleanup that
    changes what runs.
- **Timing** — run it in the background, timing each file separately. It is
  slow (minutes per ~50 shell suites); never let the report wait on it.
  - Run it in a detached worktree (`git worktree add --detach <scratch>
    origin/<base>`), not a `git archive` extract: tests that shell out to git
    (`git show HEAD:<path>`) fail with "not a git repository" in an extract.
    An extract is fine for the read-only lanes.
  - Replay every install step the CI test job runs, in its order, including
    sub-package installs (`npm ci --prefix <pkg>`, `pnpm --dir <pkg> install`),
    and use the CI-pinned package-manager version (`npx -y pnpm@<N> install
    --frozen-lockfile`) — a newer local pnpm can reject the lockfile's
    overrides, and a skipped sub-package install shows up as "Cannot find
    package" collection failures.
  - vitest: `npx vitest run --reporter=json --outputFile=<scratch>/vitest.json`,
    then parse `testResults[].assertionResults[].status` and per-file
    `endTime-startTime` in Python for skip counts, failures and slowest files.
    The exit code is nonzero if anything failed; the JSON is still written.
  - Sort audit-run failures into env (no `.git`, missing sub-package dep,
    timing asserts under parallel load) vs real before citing any; only the
    latter go in the report as findings.

## Auditing the tests added in a window

When the question is "are the N tests added since <date> worth it?":

1. **Scope by collected ids, not by file dates.** Collect at both refs in two
   detached worktrees with the same venv (`set -o pipefail; pytest dags/tests
   --collect-only -q -p no:cacheprovider | grep '::' | LC_ALL=C sort`), then
   `comm -13` = new ids, `comm -23` = gone ids. The net count and the new-id
   count differ by renames; report both.
2. **Join each new id with a CI run that logs `pytest -v`** for its status
   (PASSED / SKIPPED / not in run) and per-test seconds from result-line
   timestamp gaps. Push runs often log only dots; pick a recent PR run of the
   same path whose log has one line per test.
3. **Report cost concentration before any verdict**: tests and seconds above
   0.05 / 0.5 / 1 s, and how many files hold 70% of the time. A large count is
   usually mostly sub-50 ms tests; the CI-time question then lives in a few
   files and the effectiveness question in the rest — answer them separately.
4. Static AST scan over the new functions for no-assertion and
   reads-source-text candidates; treat it as a candidate list for a reading
   lane, never as a verdict.
5. Fan out the expensive files by cost (balance seconds, not file counts)
   with a `CHEAPEN` verdict next to KEEP / DROP / MERGE — the behavior is worth
   proving but a smaller call would prove it; the lane names what dominates
   the time. Cost lanes may profile one test at a time read-only.

## 3. Fan-out (three read-only subagents, no test runs)

Split by tree: top-level tests, hook/unit tests, and a duplicate-and-orphan
lane; on a large suite keep each lane near 150 files (split the biggest tree,
e.g. per-provider dirs, alphabetically). Write each lane's file list to
`<scratch>/l_<lane>.txt` and have the lane write a TSV
(`file  class  verdict  criterion  evidence  cases_affected`) to
`<scratch>/out_<lane>.tsv`, so you aggregate and count rows in code instead of
from summaries. Give each lane these classes:

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
  assertion into the survivor. Never drop them silently. Find *which*
  assertion earns the unique row before merging — it is often not the one
  the docstring names (a "step runs the script" test whose unique catch
  comes from its merge-commit check, not from its `"script" in body`
  text check). Move it, re-run the full matrix, confirm the survivor now
  fails on that row, then drop any moved assertion the matrix shows adds
  nothing (a text check a behavioral check now covers).
- **Inline re-implementation.** A test that imports only constants/types and
  recomputes the logic in its own body (pagination math, clamps, flag
  derivations) cannot fail when production changes — its copy has usually
  already drifted. Classify it vacuous; check whether deleting it leaves the
  production function with no real coverage and say so.
- **"Re-tests a shared helper" through a caller.** The helper's own test
  pins the helper, not which inputs each caller feeds it. Before dropping a
  caller-side case (e.g. "different source path → different directory"),
  drop one input from the caller's helper call and run the remaining tests;
  if nothing fails, keep one distinctness/determinism case per caller.
- **Randomized differential tests.** A test that runs the old and new
  production paths over N random seeds and asserts equal results is a
  property search, not N duplicates. Two seeds running the same lines
  (`sys.settrace`) differ in event ordering, and ordering is what the test
  checks. Treat cutting seeds as CHEAPEN: a search-power-for-time trade-off
  the user decides, never DROP.
- **"Forces every task to SUCCESS, so it cannot fail."** In a scheduler
  simulation, the trigger rules still decide what is scheduled and what is
  skipped. A clean-run case fails when a guard's trigger rule fires on a
  clean run, so it is a negative control: keep it.
- **A mock that answers every key the same way.** `Variable.get` patched to
  return `"[]"` for every name also switches the feature flag off, so a
  "tenant outside the list gets nothing" test passes whatever the scoping
  code does. It is vacuous. Rewrite it with the switch on and only the
  tenant outside the list, then mutation-check it against the scoping branch.
- **A skipped duplicate whose survivor runs in another workflow.** Compare the
  input cases first. Move any input only the skipped copy had into the
  survivor's parametrize list. Run the mutation in the survivor's own
  environment: build that venv from the side job's install step, including
  any private index token it fetches.
- **A differential test whose two sides are the same object.** "Shipped copy
  equals original" tests that exec a bundle in-process can compare the
  original with itself: a `try: from pkg.mod import f` inside the bundle
  rebinds `f` to the installed module. Block those imports in `sys.modules`
  during the exec (as they fail in the real runtime), and assert
  `shipped.__globals__ is not original.__globals__` before comparing outputs.
- **A collector over a tested stream** (`getX()` = collect `getXStream()`)
  is a duplicate only if the stream's tests carry every error-path case the
  collector's had; compare the case lists before dropping.
- When re-verification shrinks the approved scope, list each kept item with
  its reason in the PR body's "Kept deliberately" section and in the approval
  ask. Never narrow silently, and correct the issue body when it overstated
  the scope.

## 4. Implement

1. **Issue, before the worktree.** Search for an existing issue that covers
   the cleanup and attach to it; if none, open one in the repo's issue
   convention (read the repo's `CLAUDE.md`/`AGENTS.md` for which tracker holds
   the issue, which number goes in `Closes`, and the branch-name pattern). The
   user's scope approval is the approval
   for this issue — do not cut a branch first and ask "issue or no issue?"
   before the PR; that skips the convention and costs a round. A "don't file
   new issues" preference covers follow-ups found mid-task (they go into this
   issue as comments), not the issue for the approved work itself. On a
   public repo the issue and the PR each need their own approval: write the
   body to a scratch file, show it, then `gh issue create --body-file <file>`.
   Record the out-of-scope tier (3) items as a comment on that issue and cite
   the comment URL as `Carried:` in the verification anchor.
2. **Worktree** from a fresh `origin/<base>`. If the base moved since the
   audit, `git diff --stat <audit-sha> origin/<base>` and re-check any audited
   file it touches. Take the collect-ID oracle now, before the first edit.
3. **Before `git rm`**, grep each deleted file's name across the repo
   (excluding the changelog). Rewrite comments that cite it with the
   current-state reason — no "dropped in #N" history. Check whether a shared
   helper (e.g. an assert library) still has consumers.
4. **A check that greps a SKILL.md for a script name** passes even when the
   script is gone. Rewrite it to resolve the `${PLUGIN_ROOT}/…` path from the
   skill body and assert `-x` on that file. Use `if …; then ok=0; fi`, not
   `A && B || C` (shellcheck SC2015).
5. **Commits** — one per class (orphans + their dead code / phrase-pin /
   tightened check / duplicates), each with `Refs #N` when an issue exists;
   the PR uses `Closes #N`. Do the small orphan/dead-code commit yourself
   first, then fan the case pruning out, so no file carries two classes.
   - Case-pruning lanes: one subagent per audit lane, disjoint test files in
     the same worktree, no git commands. Each lane re-reads every claimed
     survivor, merges unique assertions instead of dropping them, and runs a
     mutation per removed group (save the production file to scratch, break
     the     guard, run the survivor, restore, `diff` the copy). Lanes mutate
     production files concurrently, so another lane's run can fail
     transiently — run the full suite yourself after every lane returns.
   - Forbid lane edits to `conftest.py` and shared helper modules when two
     lanes' files share a directory; a lane puts its module-scope fixture in
     its own test file. Split the heaviest file into its own lane so lanes
     balance by action rows, not by file count.
   - Parallel lanes share one local test DB container. A setup error such as
     a dropped table or an FK violation during `drop_all` can be another lane's
     teardown. Re-run the file alone once before you diagnose it.
   - Give each lane a private Airflow metadata DB
     (`AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:///<scratch>/<lane>.db`).
     Every pytest session runs `resetdb` on the shared worktree `airflow.db`,
     so lanes otherwise fail each other with `no such table` / unique errors.
   - Lanes mutate production concurrently. A lane must apply a mutation only
     when the file equals `git show HEAD:<f>`, and check the restore against
     HEAD, not its own backup. Another lane's backup can capture a file
     mid-mutation and "restore" the broken line. After all lanes return,
     `git diff --stat -- <production dirs>` must show only intended changes.
   - A lane that rewrites a test to execute SQL or code can make it newly
     depend on an optional package. Ask lanes to name such files, then add
     them to the side workflow's target list as in the never-runs fix.
   - When the cleanup moves gated tests into another CI job, run them locally under that
     job's own pins (the image's duckdb/sqlglot versions, not the dev venv's): a newer
     parser can reach a file the main CI's version skipped and expose an old failure.
     Tests that reach into a library's internal node fields break the same way
     (sqlglot 30 renamed `args["from"]` to `args["from_"]`): read both keys or use the
     public API, so the test passes under the dev venv's pin and the side job's.
   - When `origin/<base>` conflicts with a file you cheapened, keep the base's newly
     added tests as written (their own per-test setup) and keep your shared-fixture
     form only for the tests you changed; then run every test file both sides touched
     (`comm -12` of the two `git diff --name-only <merge-base>` lists) in the main-CI
     venv and the side job's pinned venv before pushing the merge commit.
   - Scripted block removal (a brace-matching remover for top-level
     `describe`/`export` blocks) leaves section-header comments that sat
     above a blank line, and a TS repo without `noUnusedLocals` will not flag
     imports the removal orphaned. After removal, grep for `^// <name>`
     headers and for each removed symbol's remaining imports.
   - Deleting dead production code: grep every open PR's added lines for
     the removed symbols and paths (`gh pr diff <N> --repo <o>/<r> | grep '^+'
     | grep -E '<sym1>|<sym2>|<path>'`) and list open PRs that touch the same
     files in the PR body (merge-order conflicts). A compat shim or module
     that design docs or an open doc PR cite as a recorded decision goes in
     the report as a merge-time question, even with zero importers.
   - Verification builds can regenerate tracked files (generated API specs,
     `next-env.d.ts`). Before `git add -A`, run
     `git diff --cached --name-status | grep -v '\.test\.'` and restore any
     build output you did not mean to change.
   - Format only what you dirtied: check a file's base copy with
     `git show HEAD:<f> | npx prettier --stdin-filepath <f> --check`; leave
     pre-existing format debt alone.
6. **Check removals against the approved set.** `comm -23 before after` must
   equal the approved DROP/MERGE ids plus the renames and unparametrizations
   the lanes reported; compute the residual in code and explain each line.
   When the full run errors, group errors by their innermost exception first.
   Hundreds of setup errors with one message (`database exists`, a stuck
   `DROP` in the fixture DB) come from leftover local fixture state, not from
   the change. Clear that state, then re-run only the errored files.
7. **Run the CI entrypoint in the background.** The agent's foreground
   `python3` may lack pytest even though the runner's interpreter has it, so
   judge from the runner's log, not an ad-hoc `python3 -m pytest`. Lint and
   the changed shell suites can run in the foreground first.

## Report shape

In the user's language, in chat:

1. Unverified items first — timing still pending, and which lane claims you
   re-checked yourself.
2. Counts per axis, with the enumerated file total.
3. One table per axis: file · verdict · one-line ground.
4. One recommended scope, with the approval question on the last line. Tier
   it: (1) whole-file drops + case pruning you re-verified, (2) the same with
   the dead production code they guarded, (3) decisions that need the user —
   unwired modules that may be planned, `never-runs-in-CI` blocks (add a CI
   job or drop). Recommend 1+2; leave 3 out until the user decides.
5. Say whether the slowest files are in the drop set; usually they are KEEP,
   so pruning will not shorten CI — state that rather than imply it.
6. When asked "is it faster?", measure both sides before answering:
   - Local: a throwaway `git worktree add --detach <scratch> origin/<base>`
     whose dependency dirs are symlinks to the branch worktree's (only after
     `git diff --quiet origin/<base> HEAD -- <every lockfile>` exits 0), then
     a script that alternates base/branch full runs for 3 rounds and records
     wall time per run to a TSV. Alternating cancels warm-cache drift.
   - CI: the step duration (`Test` step `completed_at - started_at` from
     `gh api repos/<o>/<r>/actions/runs/<id>/jobs`) for ~25 recent successful
     runs of the same workflow, giving min/median/max. One PR run inside that
     band is "no measurable change" — say so with the band, not a delta.
   - Answer with the table, the reason (which files dominate), and the existing
     issue that owns CI speed if there is one; then remove the bench worktree.
7. Mutation results a lane reported are self-reports: put them in the
   verification anchor's `Unverified` list unless you re-ran them. Before the
   PR, re-run the ones behind the highest-stakes changes yourself (a seed or
   case cut, a rewritten vacuous test, any production fix) in one scratch
   script: apply each mutation only when the file equals HEAD, run the
   survivor, restore, and confirm `git diff --quiet -- <f>`. Grade those rows
   PASS and leave the rest Unverified.
8. "Apply all of it" after an audit that listed a trade-off item (seed
   count, sample size) is approval to apply it. Choose the parameter yourself,
   state it in the PR body, and mutation-check that the reduced set still
   catches the regressions the full set caught.
