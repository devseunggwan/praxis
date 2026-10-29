---
name: test-suite-consolidation
description: >
  Clean up a test suite without changing what it proves — move fixtures and
  helpers that were copied across test files into shared locations behind a
  copy guard, or audit for unnecessary tests (orphans, phrase-pins, duplicate
  coverage) and delete only the scope the user approves. Every change is
  checked against a collected-test-ID diff.
when_to_use: >
  Triggers on "test consolidation", "copied fixtures", "duplicate test
  helpers", "prune tests", "unnecessary tests", "테스트 정리",
  "불필요한 테스트". Do NOT activate on a slow suite with no duplication or
  pruning ask (that is `test-suite-speedup`).
verified-against-runtime: true
runtime-verified-at: 2026-10-16
runtime-verified-note: "pytest 9.0.3 — `--collect-only -q -p no:cacheprovider | grep '::' | sort` gave one sorted id per test on a scratch project; pytest 9.1.1 with a file that fails to collect — the pipeline exited 0 without `set -o pipefail` and 2 with it. git 2.50.1 — `git archive origin/main tests/hooks | tar -x` extracted 30 files, `git log --diff-filter=A --format='%h %s'` named the adding commit. pre-commit 4.7.0 — with `.pre-commit-config.yaml` edited but unstaged, `git commit` exited 1 with `Your pre-commit configuration is unstaged`. `uvx ruff@0.15.8` resolved the pinned formatter. `gh issue create --body-file` checked via `--help` only; nothing was filed"
---

# test-suite-consolidation

## Overview

Test suites rot in two ways that look alike from a distance. Fixtures and
helpers get copied from the nearest file instead of reused, so the same body
lives in a dozen places and drifts; and tests accumulate that no longer earn
their keep — orphans, assertions that pin doc sentences nothing reads, and
duplicate coverage. Both are cleanups with zero intended behavior change, both
end in one issue and one PR, and both fail the same way: something that
mattered is silently removed. This skill runs either shape with a
collected-test-ID oracle and, for copies, a guard so the next change stops
re-copying.

**Core principle:** nothing a test proves may disappear unannounced — every
move is diffed against the collected test IDs, and every deletion is approved
by the user and re-verified by you.

Reference map — load only when the step runs:

| File | Holds |
| ------ | ------- |
| [`references/duplicate-audit.md`](references/duplicate-audit.md) | How to find copied fixtures/helpers on the base ref with a normalized-AST grouping, and the three buckets to report |
| [`references/copy-guard.md`](references/copy-guard.md) | Copy-guard design: binding-aware equality, diff-scoped vs baseline, thresholds, checker mutation testing, CI placement |
| [`references/pruning.md`](references/pruning.md) | Pruning shape: own-PR quick pass, criteria, deterministic probes (never-runs / empty-parametrize / failing-since-added), window audit of newly added tests, read-only fan-out, re-verification traps (randomized differential, forced-state simulations, uniform mocks, self-comparing differentials), parallel implementation lanes (private DBs, HEAD-checked mutations), removal-vs-approved check, report |

## When to Use

- The same fixture/helper body appears in several test files, or
  per-directory conftests repeat the same overrides.
- The user asks whether the test structure is reusable, or wants other work
  to stop patching tests locally.
- Adding or changing a CI/pre-commit guard against copied test code.
- The user asks which tests are unnecessary, or asks to clean them up —
  including tests you wrote in your own open PR (→ the quick pass in
  [`references/pruning.md`](references/pruning.md#own-pr-quick-pass)).
- **Unasked, before opening any PR that adds tests.** Run the own-PR quick
  pass (mutation matrix via
  [`scripts/mutation_matrix.sh`](scripts/mutation_matrix.sh)) before
  `gh pr create`; otherwise the user asks for it after review and the anchor,
  PR body and CI all need another revision.

Pick the shape first: **consolidate** (Steps 1–8) or **prune**
([`references/pruning.md`](references/pruning.md)). A request for both runs
pruning first, so you do not consolidate helpers for tests about to be
deleted.

## Process — consolidating copied fixtures/helpers

### Step 1: Measure duplication on the base ref

Audit an extracted copy of `origin/<base>`, not the working tree, and group
module-level fixtures/helpers by normalized body. Report three buckets
separately: (a) copies of a definition a shared conftest/helper already has —
safe deletes; (b) identical copies with no shared home — move; (c) same name,
different bodies — leave, and say why. Grep docstrings for "same pattern as
<file>" and run `git log --diff-filter=A` on the copies: that chain is the
clearest evidence for the issue. Procedure →
[`references/duplicate-audit.md`](references/duplicate-audit.md).

Done when: every copy group is in exactly one bucket, with the copy-chain
evidence quoted.

### Step 2: Choose the shared-location model and state it

- **Fixtures stay in the conftest hierarchy.** Their reach and autouse
  overrides are decided by directory; moving them into a `pytest_plugins`
  module can change override order. Say this up front — the user may read
  "one package" as including fixtures.
- **Plain helpers go into ONE package** under the tests root (e.g.
  `<tests>/tests_common/{sql,import_stubs,servers}.py`, the Apache Airflow
  `tests_common` shape). Scattered per-area helper modules are a transitional
  step at most.
- **Call sites use the shared name directly.** An alias import
  (`read_text as _read`) is acceptable only as an intermediate commit; finish
  by renaming call sites in the same PR.
- **Moving per-directory autouse overrides into a parent conftest widens them
  to sibling directories.** Grep the newly covered directories' tests for what
  the overridden fixture provided (DB, connections) and state the result.

Done when: the PR body names where fixtures and where helpers live, and every
widened autouse override has its grep result stated.

### Step 3: Take the oracle before touching anything

```bash
set -o pipefail
pytest <tests> --collect-only -q -p no:cacheprovider | grep '::' | sort > <scratch>/before.txt
```

`pipefail` makes a collection error fail the command instead of leaving a
partial `before.txt` behind `sort`'s exit 0; the file lives outside the
checkout so it never shows up in the diff. vitest/jest equivalent:
`npx vitest list | LC_ALL=C sort > <scratch>/before.txt` (one
`file > describe > case` line per test; it imports every test module, so a
broken import fails it). Vitest 5+ parses files statically by default and
then skips the imports — add `--no-static-parse` there. Sort both sides with `LC_ALL=C` and compare with
`LC_ALL=C comm`: shells with different locales collate non-ASCII test names
differently, and `comm` then reports thousands of identical lines as
added/removed. Group the removed lines by file (`sed 's/ > .*//' | sort -u`)
and check every file is one the change touched; IDs behind env-dependent
`skipIf` can appear or vanish between runs — name them, don't count them as
losses.

After every step, regenerate and diff: only additions for new tests, zero
deletions. This catches what lint does not — a re-export removed by
`ruff check --fix` (F401), or a broken import from a sibling test module.

Done when: `before.txt` exists and is taken from the unmodified branch.

### Step 4: Apply mechanically, with refusal guards

- Delete a local def only when its normalized body equals the reference
  exactly; report the skipped files as "differs". Fixtures that differ
  slightly (an extra env patch, another schema) are not copies.
- Rename call sites with Python's `tokenize` (NAME tokens not preceded by
  `.`), refusing any file where the shared name is already bound — a regex
  `\b_old\b` also hits `obj._old`.
- Before renaming, grep for test modules importing the old private name from a
  sibling test module (`from .test_x import _old`, including parenthesized
  multi-line imports); the rename breaks those and only collection catches it.
- Lint and format only touched files, with the repo's pinned formatter version
  (`uvx ruff@<pre-commit rev> format <files>`), not the venv's.
- Delete helpers left with zero callers (`git grep <name>` shows only the
  definition) in their own commit.

Done when: the collect-ID diff after each move shows zero deletions.

### Step 5: Add the copy guard

Prevent recurrence with a check, not only docs. Default design: diff-scoped
against a base revision (no baseline file), free-name-binding-aware equality,
fail on a redefinition of a shared definition or a 3rd test-module copy, run
as the last step of the existing test job plus a pre-commit hook. Mutation-test
every decision branch of the checker. Design, thresholds, the baseline
alternative and CI placement → [`references/copy-guard.md`](references/copy-guard.md).

Done when: each checker decision point has a mutation that fails at least one
test, and a historical replay includes one positive control that reports a
real copy.

### Step 6: Document the map

Add a "shared locations" table to the repo's agent guide (`AGENTS.md` /
`CLAUDE.md` testing section): what lives where, the import form, and how the
guard works. Agents read that file; it is the non-CI half of "stop local
fixes".

Done when: every shared module created or used by this PR appears in the table.

### Step 7: Verify

- Collect-ID diff, then the full suite in the CI's environment, plus any gated
  subset in the environment that actually executes it (a separate venv or job
  when main CI skips those tests).
- Path-filtered workflows that are the only place some tests execute must list
  every new dependency of those tests in `paths:` (both `pull_request` and
  `push`) — the new conftest, and the shared helper package once a test they
  run imports from it. Otherwise a change to the shared file never triggers
  the only job that runs it.
- When CI fails on the guard, reproduce the merge ref in a throwaway worktree
  (`git worktree add --detach <scratch> HEAD`, merge `origin/<base>` there,
  run the checker) — a base-branch merge can bring a new copy. Fix by merging
  `origin/<base>` into the branch (a merge commit, never a force-push), moving
  the copy to the nearest common conftest, and collect-ID-diffing that
  directory.
- If automated reviewers push to the PR, `git fetch` the head first and lint
  their diff with the repo config: CI linters often lint only changed files,
  so a docstring past the line limit fails there and nowhere locally.
- Blast radius: the guard applies to every open PR's next CI run. Count open
  PRs touching the tests directory and say new copies there will fail.

Done when: the collect-ID diff is additions-only, the gated subsets ran in
their own environment, and the blast-radius count is in the PR body.

### Step 8: Commit and report

Split pre-existing format debt (files not clean under the pinned formatter on
the base) into a first `style(...)` commit, then one commit per logical move,
the guard, and docs. Report whether CI speed changed — consolidation usually
does not, because session-scoped setup already ran once. If asked, measure the
touched subset base vs branch in a throwaway worktree (2 rounds, drop the cold
first), and note that editing any `conftest.py` cold-starts a change-selection
cache keyed on `**/conftest.py` once after merge.

When asked whether the result is "top-tier", answer in a table with a source
per row: the pytest conftest hierarchy / `pytest_plugins` docs, the
PHPStan/detekt baseline model ("the life goal of a baseline is to not exist"),
and Airflow's single `tests_common` package. Name the remaining gaps (aliases,
scattered helper modules, a custom detector vs pylint/jscpd) and offer to
close them in the same PR.

Done when: `git log` shows the style commit first and no commit mixes a move
with unrelated format changes.

## Troubleshooting

| Symptom | Cause | Fix |
| --------- | ------- | ----- |
| Consumers fail collection after a clean lint | `ruff --fix` F401 deleted an import a module only re-exported | Import from the shared module at each consumer instead of re-exporting; the collect-ID diff catches it |
| Another test module breaks after a rename/move | It imported the helper or fixture from its old module | Grep `import .*\b<name>\b` and multi-line import lists across the tests tree; drop the fixture from those imports once a conftest provides it |
| F401 / F811 on a shared fixture | The fixture was imported into another test module | Share fixtures through a conftest; import the module-local classes the body uses into that conftest — same module object, unchanged behavior |
| Pre-commit copy check fails on an unrelated commit | The tree holds a copy (e.g. from a base merge); pre-commit sees HEAD + staged | Commit the copy fix first; check `git show --stat HEAD` and split with `git reset --soft HEAD~1` if a failed commit left files staged |
| A "format-only" commit contains deletions | `git rm` stages immediately | Check `git diff --cached --name-status` before every split commit; redo with `git reset --soft HEAD~1` (local, unpushed) |
| pre-commit refuses to commit | `.pre-commit-config.yaml` has unstaged edits | Commit the config with the hook it adds, or restore it from HEAD temporarily for earlier commits |
| An edit did not happen | A chained command was blocked or denied, so none of its parts ran | Re-run the state-changing step alone before re-posting |

Answer the question as asked: when the user asks whether "the method in
<file>" is right, evaluate that file's mechanism (CI placement, runner cost),
not adjacent logic.

## Integration

- `test-suite-speedup` — when the goal is time. Neither consolidation nor
  pruning is a speedup by itself; measure before claiming one.
- `surface-enumeration` — when a pruned test was the only case for an input
  variant, it is not a duplicate.

## Limitations

- The duplicate audit compares normalized text; two bodies that read the same
  names but bind them to different definitions look equal until the copy
  guard's binding-aware key is applied.
- Pruning verdicts from a read-only fan-out are claims; this skill makes you
  re-verify them, but the re-verification is still reading, not execution —
  the mutation check in the pruning pass is what proves a kept guard is pinned.
