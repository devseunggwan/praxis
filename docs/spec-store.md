# Feature specs

Design documents for praxis features. One file per feature, in a store that
sits outside every checkout.

These are **feature specs** — what a change must satisfy. They are not the
same thing as the *skill spec drift* gate in
[`CONTRIBUTING.md`](../CONTRIBUTING.md), which is about a `SKILL.md` matching
live runtime behavior.

## Where specs live

```text
~/.praxis/docs/specs/NNNN-slug.md
```

The root resolves through `PRAXIS_HOME` exactly as every other praxis runtime
root does — see [`runtime-state-layout.md`](runtime-state-layout.md), and
`praxis_specs_dir()` in [`hooks/_lib/_paths.py`](../hooks/_lib/_paths.py) and
its shell twin. One store serves every repository you work in, and no
repository has to adopt anything for it to work.

**It is not version-controlled, and that is the trade.** A reader with only a
clone cannot see the specs; a requirement change does not appear in any
`gh pr diff`; a lost machine loses the store, since no remote holds it. In
exchange the convention costs a repository nothing and follows you across all
of them. Backing the store up is yours, not praxis's. Anything a *reviewer*
needs to know has to be restated in the PR body or a commit trailer — the spec
will not carry it there.

Numbering is global across repositories, because the store is.

## Why this exists

Design intent otherwise lives in two places nothing local can read:

- `.omc/plans/` — excluded by `.gitignore` (`.omc/`), and per-tool rather than
  per-person
- GitHub issue bodies — remote only

So the question "what was this hook built to satisfy?" has no local answer.
A spec file gives it one.

## When to write one

Write the spec **before implementation starts** — after the issue exists, at
whatever point the requirements are known. Unlike a tracked file it does not
wait for a worktree; the store is always there.

What matters is that it lands *before* the implementation: writing it after
work has started turns it into post-hoc justification — the same reason the
`Judgement` axes of an issue review are only valid before work begins.

Write a spec when the change:

- adds or removes a skill, hook, or CLI surface
- changes a rule's enforcement tier or its decision predicate
- spans more than one PR, or more than a handful of files

**Skip** it for a single-file bug fix, a documentation typo, a mechanical
rename, or anything whose revert is self-evident. A spec that restates a
one-line diff costs nothing to review and teaches nothing.

The skip list applies **only when no trigger above fires**. The two overlap on
purpose — a one-file change to a rule's decision predicate is both a
single-file fix and a predicate change — and when they do, the trigger wins.
Size is never the reason to skip: what earns a spec is the change being hard to
reconstruct later, not the diff being large.

## Naming

| Part | Rule |
| --- | --- |
| `NNNN` | Highest existing number + 1, zero-padded to four digits |
| `slug` | Lowercase kebab-case, derived from the issue title |

The slug does not have to match the branch name. Branch
`issue-1001-docs-spec-convention` and spec `0001-spec-store.md` are the same
work under two names, and that is fine — each is named for what its own reader
is looking for. The link between them is the `**Issue**: #N` line, not a shared
string; a slug rule strict enough to be greppable is one the very first spec
here already broke.

The numbers are independent too: `NNNN` is a spec counter, not an issue number.

## Required header

Every spec starts with a back-reference line directly under the title:

```markdown
# Feature Specification: <name>

**Issue**: devseunggwan/praxis#1001
```

Without it a spec has nothing pointing back at the thread it came from — and
since the store carries no history, that line is the only provenance there is.

**The repository is part of the reference, not optional context.** One store
serves every repository you work in, and issue numbers are per-repository, so a
bare `#1001` names a different thread depending on where the reader happens to
be standing — and there is no surrounding repository to disambiguate it, which
is exactly what the move to a shared store gave up. Write `owner/repo#N`, or
the full issue URL.

**The template does not carry this field** — it is upstream's file, and the
upstream template has no notion of an issue. Add the line by hand when you
copy, directly above `**Feature Branch**`. Keeping the omission rather than
patching the template is deliberate: it leaves the copy byte-identical to
upstream, so re-syncing a newer spec-kit version stays a diff instead of a
merge.

## Template

Copy [`spec-template.md`](spec-template.md) and fill it in. It is
[github/spec-kit](https://github.com/github/spec-kit)'s `spec-template.md`
v0.16.3 verbatim (MIT), plus an attribution block.

The template and this document stay **in the praxis repository**, not in the
store: they are source, and a redistributed MIT file needs the version control
its notice assumes. Only spec instances live under `PRAXIS_HOME`.

Conventions on top of the template — the count is deliberately not stated, so
that adding one cannot leave a stale number behind:

- Delete the attribution block. The notice lives in
  [`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md) and does not need to
  travel into every spec.
- Delete the sections that do not apply. `Key Entities` is marked
  *include if feature involves data* and rarely applies to a hook or skill —
  an empty section reads as an unanswered question.
- Leave `[NEEDS CLARIFICATION: ...]` markers in place until they are actually
  resolved. An unresolved marker is the point of the format; replacing it
  with a guess defeats it.
- Delete the `**Status**: Draft` line. praxis does not track spec status in a
  field nobody remembers to edit. The line stays in `spec-template.md` only
  because that file is upstream's, kept byte-identical.

## Verification lines

A requirement may carry the command that checks it, as a nested `Verify:` item
directly under it:

```markdown
- **FR-001**: Specs MUST live at `<praxis_specs_dir()>/NNNN-slug.md`, resolving
  through `PRAXIS_HOME`. `test -d ~/.praxis/docs/specs` is not the oracle here:
  it passes on any machine that once created the directory, …
  - Verify: `test "$(sh -c '. hooks/_lib/_paths.sh; praxis_specs_dir')" = "$HOME/.praxis/docs/specs"`
```

`praxis:spec-drift` runs these lines and nothing else. **Backticks in the
surrounding prose are never executed** — the example above is exactly why: one
of its three backticked spans is the oracle, one is a counter-example the
requirement exists to reject, and one is a path pattern. A convention that
guessed between them would run the command the spec says not to trust.

**A `Verify:` line belongs to the requirement whose block it sits in**, and
that block ends at the next line starting in column 0. The requirement's own
nested items and indented continuation prose stay inside it; a `- Verify:`
further down the document, under a later heading, belongs to nothing and is
not run. Writing a second `Verify:` in one block is an authoring error — the
first binds, and the report prints the rest as `warning` lines naming the
command it did not run.

Rules on what may appear there — the count is deliberately not stated, so that
adding one cannot leave a stale number behind:

- **The exit code must report the requirement, not the environment.** A command
  that can exit non-zero for a reason outside the repository is not eligible —
  `scripts/run-tests.sh` is the standing example, since it reads a per-user
  store that lives outside the checkout ([#1003](https://github.com/devseunggwan/praxis/issues/1003)).
  A requirement with no eligible command keeps no `Verify:` line.
- **The command must terminate without input.** It runs unattended, with stdin
  at `/dev/null` ([#1008](https://github.com/devseunggwan/praxis/issues/1008)),
  so a command that reads stdin gets immediate EOF. Before that it inherited the
  report's own stdin, hung until the timeout, and was reported `missing` — a
  rule stated here but not enforced showed up only as a wrong verdict.
- **The command must fail when its inputs are gone.** An oracle whose subject
  has been deleted must exit non-zero, not collapse into a vacuous success. The
  shape that breaks this is a comparison whose *both* sides are command
  substitutions: when the subject is absent both substitutions fail, both
  collapse to the empty string, and `test "" = ""` exits 0 — so the report
  prints `implemented` for a requirement nothing in the tree satisfies
  ([#1011](https://github.com/devseunggwan/praxis/issues/1011)). Pin at least
  one side to a literal, so an absent subject compares against something and
  the mismatch is visible. This does not conflict with the first rule: that one
  is about per-user state *outside* the checkout, this one is about the
  repo-relative inputs the requirement is actually asserting.

Commands run from the repository root of wherever the report was invoked, so a
`Verify:` line may use repo-relative paths — and a spec written for repository
A will report `missing` if run from repository B. That is the store being
shared while the checks are not.

A requirement with no `Verify:` line is reported as `UNKNOWN`, which is a
readable state rather than a failure — some requirements are about prose, and
the report says so instead of guessing. What it must never be is a requirement
that *had* an eligible command and did not carry it.

## What praxis did not adopt

praxis takes this one template and nothing else from spec-kit. Not adopted:

| spec-kit component | Reason |
| --- | --- |
| `.specify/memory/constitution.md` | Duplicate rule source of truth; `CLAUDE.md` and [`ETHOS.md`](../ETHOS.md) already hold that role |
| `.specify/extensions.yml` hooks | They instruct the model to invoke its own gates — the prompt-only form [`ETHOS.md`](../ETHOS.md) replaced with real hooks |
| `.specify/workflows/*.yml` gates | Only fire when driven through `specify workflow run`; an agent working directly bypasses them |
| `speckit-*` agent skills | Namespace pressure on an already-large skill surface |
| `specify` CLI at runtime | One template needs no external CLI dependency |
| `plan.md` / `tasks.md` / `checklist.md` | Plan mode and issue checklists already own these |
