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
- (d) re-test a shared parser/helper — when N call sites delegate a check to
  one helper, test the rejection once on the helper; call-site tests only
  need to prove each site accepts the new input;
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
test for it. Run the matrix as a background process, never inside a foreground tool call with a timeout: a killed harness skips its restore step and leaves the last mutant applied (plus a backup file, if the harness writes one). After any interrupted run, compare each target with `git show HEAD:<f>` and restore it from HEAD before testing or committing anything; delete any backup files the harness left. A test that compares a value derived from the real clock (a floor of `now - N days`) with a fixed fixture date is a time bomb that passes today and fails on a fixed future date: pin `now` inside the test (monkeypatch the clock call the code uses) and assert the exact derived value, then prove it with a clock-freezing plugin at a future date: the old file fails there, the fixed one passes. Give the harness a per-mutant timeout and record a timeout as "caught (hung)": a mutant that removes a wait bound makes a wait test hang, and without the timeout the whole matrix dies on that row. A hung row names no test, so before it justifies a drop, rerun that mutant against each candidate test alone and record which one hangs. When several review-bot guards each look dead in the matrix, propose replacing the stacked conditions with the one rule they approximate (and re-fit the tests to it) as a user choice, rather than deleting them one by one. While the matrix runs, the source file is a mutant — run lint
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
it. A drop justified by "the integration test already covers it" needs this
mutation, not a reading: integration fixtures often never build the input
combination the unit case did (two accounts on one key, one of them
unchecked), so the guard goes unpinned and a reviewer's surviving mutant
forces the test back with a correction reply. Report a table of dropped test · reason and update the PR's verification
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
  After deleting such a module, re-run the grep for every type and helper it
  imported from its own package (models, parsers): one whose only remaining
  hits are the package `__init__` re-export is now dead too, unless the
  package is published or imported from outside the repo (check its public
  API and dependents first) — delete it and trim the re-export in the same
  commit, or the next audit finds it.
- **Never-run map** — before judging any skip, map every test file to the CI
  steps that execute it: parse each workflow's `run:` steps (with
  `working-directory`) for `vitest run <paths> --exclude`, `pytest <paths>`,
  `unittest discover -s`, `node --test <glob>`, `python3 <test_file>` and
  `pnpm --filter <pkg> test` (resolve the package's `test` script dirs), and
  write `{owners: {file: [workflow:job]}, never: [...]}` to scratch JSON. Hand
  the JSON to every lane so each row carries a `ci` column. If an approved
  open PR is about to change how tests run (a monorepo task runner fallback),
  say which never-run files it will pick up, and re-read the map on the new
  base before acting on it.
- **Conditional skips** — grep `skipIf` / `runIf` / `.skip` / `.only` / `.todo`
  (vitest/jest) or `skipif` / `importorskip` (pytest), and judge each condition
  against the CI job's env (which env vars, creds, browsers, files it has). A
  block CI never runs is a `never-runs-in-CI` finding, not automatically a drop.
  - Also grep in-body env guards — `if (!process.env.X) return;`,
    `if (!bundlePath) return;`, `if not os.environ.get(...): return` — which no
    skip grep finds and which report PASS. For each, find the CI step that runs
    that file and check whether the step (or the task-runner target) exports
    the variable; one step exporting it does not cover a sibling step that runs
    the same directory. Fix by resolving the value in the test (an existing
    resolver helper) or exporting it in that step, never by leaving the guard.
  - An `importorskip("<pkg>.<module>")` whose module imports a dependency
    missing from the only venv that collects the file always skips: grep that
    venv's lockfile (`grep -c '^name = "<dep>"' <dir>/uv.lock`) and every other
    job's collected paths before calling it live.
  - Before calling a main-CI skip `never-runs`, map the file against every
    other workflow's pytest targets, including target lists a script prints
    (`scripts/<x>.sh targets`) and grep-built lists — side workflows with the
    optional dependency often run it.
  - Enumerate never-runs files over the whole tests tree, not only the files
    a window audit touched: grep every file-level gate form (`importorskip("<dep>")`,
    `_HAS_<DEP>`, `skipif(<dep> is None …)`, and conftests that gate a directory),
    subtract every dep-installing workflow's targets, and report the full list in
    one pass (fix it in one pass once fixes are in scope; a read-only audit
    changes no file). A reviewer who runs that enumeration finds the files you missed, and each
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
    a branch a shallow checkout lacks) is fixed in the CI step: set the var in
    `env:`, and fetch the ref with enough history for `merge-base` to resolve
    (`fetch-depth`, or `git fetch origin <base>` plus `--deepen`) — the var
    alone leaves the ref missing and the test still skips. With
    `fetch-depth: 2`, `HEAD^1` is the PR base tip or the previous push commit.
    It spans a whole push only when every allowed merge method lands one commit
    (squash) or a merge commit — read the base ruleset's `allowed_merge_methods`
    (`gh api repos/<o>/<r>/rules/branches/<base>`) before claiming so; a
    permitted rebase merge pushes N commits and `HEAD^1` covers only the last.
    State that caveat with the observed merge mix in any reply defending `HEAD^1`.
  - Once fixes are in scope (a read-only audit reports the next two items
    instead), fix a never-runs finding by adding the file to the side workflow's target
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
from summaries. When the ask includes "needs improvement" (not only unnecessary/invalid),
add an `IMPROVE` verdict whose lane must name what dominates the cost and the
cheaper proof: whole-repo scans (`git ls-files` / glob / `os.walk` parsing
files unrelated to the assertion — pre-filter with a cheap regex, keep the
all-files guarantee), fixtures of hundreds of KB re-read per case (share one
load or synthesize a small one; keep a recorded original when its owner says
it is the source of truth), the same synth/stack build repeated per case
(cache by input), bundling inside synth (`bundling-stacks: []`), fixed
sleeps and real-time waits (inject the clock or interval), and network or
host-binary dependencies (docker, kubectl, PyPI, GitHub downloads). Give each
lane these classes:

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
  production function with no real coverage and say so. A positive control
  that re-states a guard's predicate inline protects nothing for the same
  reason: extract the predicate into one function the guard and the control
  both call, then show a mutant of it fails the control.
  When the re-implemented part is a harness standing in for code that cannot
  load in the test runtime (a browser patch hunk), drop only the assertions
  about the harness's own behavior (ordering), keep the ones that drive the
  real loaded code, comment which test pins the real code, and mutate the real
  source (the patch line) to show that test fails.
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
- **A copied production command judged by a different oracle.** A test that
  pastes a probe or shell command from a manifest into its own body and
  decides pass/fail by stdout instead of the exit code the runtime uses can
  hide that the real command never fails (`find` exits 0 with no match, so a
  `find ... -mmin -2` liveness probe passes on a stale file). Run the real
  command's exit code on the failure inputs; if it cannot fail, report a
  production defect, not a test verdict, and replace the test with one that
  executes the rendered command.
- **Lane citations and environment claims.** A lane that says "`<file>:<N>`
  already does X" or "breaks on runners without `<binary>`" can be wrong:
  open the cited line, and read the job's `runs-on` image before relaying a
  missing-tool risk (hosted `ubuntu-latest` ships common CLIs). Drop or
  correct the claim before it reaches a comment or issue.
- **A phrase-pin over a runbook operators paste.** A test asserting a README
  command block's shape (no `openssl rand` in a seed step, `--context` on every
  `kubectl`) looks doc-only, but people execute that text by hand. Inject the
  unsafe line into the README: if only that test fails, it is a contract —
  keep it (tighten it if it is loose), and correct the audit verdict.
- **A source-grep test rewritten as a real build gets slower.** Replacing
  "the call string appears in the file" with a synth/render assertion can cost
  5x per file. Show the mutation that proves the old test vacuous (comment out
  the call → old green, new red) next to the before/after seconds, and report
  it as an accepted trade-off rather than an IMPROVE win.
- **Race tests over a fake async client that never yields.** If the fake
  store/boto client completes every call without awaiting, "concurrent"
  coroutines run one after another and a removed conditional-write guard stays
  green. Add an interleaving fake (`await asyncio.sleep(0)` before each call)
  in the store tests' conftest only, and mutation-check the guard. Widening it
  to other suites can surface real production contention — record that as a
  finding on the work issue instead of fixing it in the cleanup.
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
2. **Worktree** from a fresh `origin/<base>`. Take the collect-ID oracle now,
   before the first edit. If the base moved since the audit, re-baseline every
   checklist row before touching it:
   - `git log -M --name-status <audit-sha>..origin/<base> -- <row files>` —
     a row file that looks deleted was often renamed or moved into another
     package (`*.integration.test.ts`, a new project dir); judge the new path,
     and drop rows a merged PR already fixed (a removed `it.skip`).
   - Scan open PRs for the row files (`gh pr list --state open --json
     number,author,files`, intersect in code). A row an in-flight PR already
     reworks (often by the file's owner) leaves this PR — name the PR in the
     report instead of producing a conflicting edit; ignore long-stale
     bot/revert PRs.
   - Re-run each dead-code grep on the new base before quoting it to anyone.
   - When the user picks one PR with production-code deletion gated on the
     owner, start the test-only rows at once and put the dead-code and
     INVESTIGATE rows in ONE owner comment on the work issue: a table of
     code · usage grep (with its scope and base sha) · tests deleted with it,
     plus one question per INVESTIGATE row. Show the draft before posting
     (it mentions a person); land the answers on the same branch. If the
     user says they will notify the owner themselves, post without the
     @mention. If the user later says to delete the dead code before the owner
     answers, do it in the same PR and state that decision in the PR body,
     since the posted comment still reads as a question.
3. **Before `git rm`**, grep each deleted file's name across the repo
   (excluding the changelog) — after the lanes return too, since lanes delete
   files you did not. Docs that list commands to run (quickstart evidence
   tables, runbooks) must drop the removed paths; history lines get the
   repo's deletion annotation. Rewrite comments that cite it with the
   current-state reason — no "dropped in #N" history. Check whether a shared
   helper (e.g. an assert library) still has consumers.
4. **A check that greps a SKILL.md for a script name** passes even when the
   script is gone. Rewrite it to resolve the `${PLUGIN_ROOT}/…` path from the
   skill body and assert `-x` on that file. Use `if …; then ok=0; fi`, not
   `A && B || C` (shellcheck SC2015).
5. **Commits** — one per class (orphans + their dead code / phrase-pin /
   tightened check / duplicates), each with `Refs #N` when an issue exists;
   the PR uses `Closes #N` even while owner-pending rows remain (repo link
   gates reject `Refs`): add one line saying those rows land on this branch
   before merge, and move them there or to the open umbrella issue before
   asking to merge. Do the small orphan/dead-code commit yourself
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
     The HEAD check is check-then-write, not a lock: when two lanes can mutate
     the same file, give each lane its own detached worktree, or hold a
     per-file lock (`mkdir <scratch>/locks/<f>`) from mutation through restore.
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
   - Fan-out mechanics: write ONE shared instructions file (worktree, lane
     JSON path, verdict semantics, hard rules: test files only, no git state
     changes, no fixture edits, re-verify STALE, mutation per DROP/MERGE,
     before/after timing ×2, lint) and give each lane only its lane name plus
     lane-specific conflicts (open PRs on its files). Each lane writes
     `<scratch>/<issue>-result-<lane>.json`; aggregate those, not summaries.
   - Timing and flakes measured while other lanes run are invalid (load
     average 15–30). Re-time after every lane returns, alone. A test that fails
     once in a loaded full run: run it alone ~20× on the branch and on a clean
     base worktree before attributing it to the change. Resolve the node id
     with `git grep -ln <test name>` first and print each run's last line: a
     wrong path exits non-zero with `no tests ran`, which a loop counting
     only exit codes reports as 20/20 failures on both sides.
   - Lint lanes with the CI linter's own config, not the local default: read
     the MegaLinter/CI lint config for the file it passes
     (`TYPESCRIPT_ES_CONFIG_FILE: eslint.config.tsc.js`) and run
     `eslint -c <that file> $(git diff --name-only --diff-filter=AM origin/<base> -- '*.ts')`.
     oxlint and a plain `eslint` pass code the type-aware config rejects —
     e.g. a synth cache key `` `${stage}/${flag}` `` with a boolean trips
     `restrict-template-expressions`; write `String(flag)`. Confirm the
     config reproduces the CI errors on the pre-fix file before trusting its
     clean result.
   - A lint bot may push auto-fix commits to the PR head between your
     pushes; on a rejected push, `git fetch` the head, read its diff, and
     merge it (never force-push).
   - Write commit trailers only with facts known at commit time: a
     `Not-tested: <X> pending` in a pushed commit cannot be corrected once
     X is measured. Leave pending measurements to the verification anchor.
   - Infra diff bots on a test-only PR (cdk diff "Destructive Changes",
     plan summaries) can report base-vs-deployed drift. Before explaining
     or alarming, confirm the PR's `cdk/lib`/`cdktf/lib` diff and find the
     same warning on another open PR's diff comment; then record it as
     unrelated in the anchor.
   - Split commits from the lanes' result JSON, not from memory: collect the
     dead-code lane's deleted/edited paths (plus any you deleted yourself)
     into a file, `git add --pathspec-from-file=<file>` for the
     `refactor(...)` commit, then `git add -A` for the test commit, and read
     `git diff --cached --diff-filter=A --name-only` before each.
   - Deleting production code a still-running lane's test imports breaks
     that lane mid-run: defer the deletion until the lane returns, then remove
     the remaining import site yourself. A dead module that a pending
     INVESTIGATE row's test imports waits for that row's decision — delete
     them together or not at all.
   - Deleting the last test file of a task-runner project: remove its `test`
     target too (an nx `vitest run src` over zero files fails the affected
     run) and the CI-mapping doc row that names it.
   - Before dropping a `pythonpath`/path entry whose comment cites only the
     deleted module, grep for other consumers (`files("<pkg>.tests")`,
     resource imports); keep the entry with a corrected comment if any remain.
   - Doc edits for deleted code: current-state docs (README) state the
     present design with no date/issue tag; history docs (specs, task logs)
     follow the repo's own annotation precedent or stay untouched.
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
     pre-existing format debt alone. When the repo has no linter config or CI
     lint step, run the default linter on the touched files in the branch and
     in a clean base worktree, strip line numbers, and `comm -13` the two
     lists: report only the new findings, and keep one that mirrors a
     production idiom (a blind `except` copied from the task runner) with
     that reason instead of diverging from production.
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
5. A finding that is a production defect (a check that cannot fail, a
   probe that always passes) leaves the cleanup scope: propose it as its own
   fix issue and PR first, ahead of the test cleanup issue. Before editing,
   find the file the live system is actually built from — for a Kubernetes
   object, read the owning GitOps Application's `source.path`
   (`kubectl get deploy -A | grep <name>`, then the Application YAML); a
   code generator next to the app may be a dead pre-adoption path. Measure
   on live data that the fixed check will not fire immediately (e.g. the
   max interval between health-file writes in `kubectl logs --timestamps`)
   and state the rollout it triggers on merge.
   - Prove the fix on the cluster, not only in a container (cluster writes
     need the user's approval, or a standing one recorded for it): apply
     throwaway pods in dev with the live image digest and probe settings, replace the
     app with a scripted writer (`: > file; sleep 50; while …; do echo
     Traceback > file; sleep 20; done`), watch `ready/restartCount` every
     15 s plus `kubectl get events`, then delete them by a `purpose=` label.
     Run old vs new side by side; it is the evidence reviewers cannot get
     from a docker exit code.
   - When a heartbeat-file probe starts being able to fail, re-check two
     things the always-passing probe hid: the first write only lands after
     the first run, so add a `startupProbe` covering that window; and a
     failed run that the app retries on its own loop is not a reason to
     restart (a restart rebuilds nothing when clients are created per run).
     Default split: startup/liveness = file still refreshed
     (`test -n "$(find F -mmin -N)"`), readiness = refreshed and empty
     (`-size 0`). Make the CI check assert a separate expected exit per probe
     per input (missing / fresh-empty / fresh-error / stale), not one shared
     verdict.
   - After merge, verify the rollout yourself rather than stopping at
     "ArgoCD will sync": per cluster, the Application `Synced/Healthy`, the
     live Deployment's probe commands equal the chart's, `rollout status`,
     new pods Ready with restarts 0, and pod events filtered to
     `Unhealthy|Killing|BackOff`. One `Startup probe failed … No such file`
     per pod before the first write is the designed wait; a pod replaced by
     `Evicted pod: Underutilized` is node consolidation, not the probe —
     read the event reason before counting a replacement as a restart.
   - The freshness window now couples to the app's own sleep: if the loop
     sleeps up to `jitter × SLEEP` between writes, a larger configured sleep
     turns a healthy pod into a restart loop. Guard the exposed value in the
     template (`{{- if gt (float64 .Values.<sleep>) <max> }}{{- fail … }}`,
     max = window / jitter minus run time) and add a render-must-fail case to
     the chart validation script.
6. Say whether the slowest files are in the drop set; usually they are KEEP,
   so pruning will not shorten CI — state that rather than imply it.
7. When asked "is it faster?", measure both sides before answering:
   - Local: a throwaway `git worktree add --detach <scratch> origin/<base>`
     whose dependency dirs are symlinks to the branch worktree's (only after
     `git diff --quiet origin/<base> HEAD -- <every lockfile>` exits 0), then
     a script that alternates base/branch full runs for 3 rounds and records
     wall time per run to a TSV. Alternating cancels warm-cache drift.
   - CI: the step duration (`Test` step `completed_at - started_at` from
     `gh api repos/<o>/<r>/actions/runs/<id>/jobs`) for ~25 recent successful
     runs of the same workflow, giving min/median/max. One PR run inside that
     band is "no measurable change" — say so with the band, not a delta.
     When the workflow runs only on PRs (no base-branch runs), take the band
     from other branches' successful runs. An affected-only job (nx
     `affected`) spans 70 s–10 min by how many projects a PR touches; a
     cleanup touching every project is a full run, so compare it only with
     other full-run PRs. Job time is mostly install/setup, so per-file
     savings of seconds rarely show — report the cleanup's value as removed
     vacuous tests and determinism, not speed.
   - Answer with the table, the reason (which files dominate), and the existing
     issue that owns CI speed if there is one; then remove the bench worktree.
   - When asked "how does CI change after this?", lead with the time answer in
     one line, then a table of target · today · after for what newly runs or
     can now fail (env-guarded tests that executed nothing, skipped contract
     tests, race tests that never interleaved, grep tests that pass with the
     call removed), moved CI targets, and per-suite test counts; mark
     unmeasured claims (fewer flakes) as unmeasured.
8. Mutation results a lane reported are self-reports: put them in the
   verification anchor's `Unverified` list unless you re-ran them. Before the
   PR, re-run the ones behind the highest-stakes changes yourself (a seed or
   case cut, a rewritten vacuous test, any production fix) in one scratch
   script: apply each mutation only when the file equals HEAD, run the
   survivor, restore, and confirm `git diff --quiet -- <f>`. Grade those rows
   PASS and leave the rest Unverified.
9. "Apply all of it" after an audit that listed a trade-off item (seed
   count, sample size) is approval to apply it. Choose the parameter yourself,
   state it in the PR body, and mutation-check that the reduced set still
   catches the regressions the full set caught.
