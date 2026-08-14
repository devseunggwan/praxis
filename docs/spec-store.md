# Feature specs

Tracked design documents for praxis features. One file per feature:
`.praxis/specs/NNN-slug.md`.

These are **feature specs** — what a change must satisfy. They are not the
same thing as the *skill spec drift* gate in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md), which is about a `SKILL.md`
matching live runtime behavior.

## Why this exists

Before this convention, praxis design intent lived in two places that the
repository itself cannot see:

- `.omc/plans/` — excluded by `.gitignore` (`.omc/`)
- GitHub issue bodies — remote only

So someone reading a checkout could not answer "what was this hook built to
satisfy?" A spec file closes that gap by putting the requirements next to the
code, under review, in version control.

## When to write one

Write the spec **after the worktree is created, before implementation starts**.

A spec is a tracked file, so it is written inside the worktree like any other
change — the mandatory issue-driven workflow has the worktree in place before
tracked files are touched, and writing the spec earlier would mean editing the
base checkout.

What matters is that it lands *before* the implementation: writing it after
work has started turns it into post-hoc justification — the same reason the
`Judgement` axes of an issue review are only valid before work begins.

Write a spec when the change:

- adds or removes a skill, hook, or CLI surface
- changes a rule's enforcement tier or its decision predicate
- spans more than one PR, or more than a handful of files

**Skip** it for a single-file bug fix, a documentation typo, a mechanical
rename, or anything whose revert is self-evident. A spec that restates a
one-line diff costs a review round and teaches nothing.

The skip list applies **only when no trigger above fires**. The two overlap on
purpose — a one-file change to a rule's decision predicate is both a
single-file fix and a predicate change — and when they do, the trigger wins.
Size is never the reason to skip: what earns a spec is the change being hard to
reconstruct later, not the diff being large.

## Naming

| Part | Rule |
| --- | --- |
| `NNN` | Highest existing number + 1, zero-padded to three digits |
| `slug` | Lowercase kebab-case, derived from the issue title |

The slug does not have to match the branch name. Branch `issue-1001-docs-spec-convention`
and spec `001-spec-artifact-convention.md` are the same work under two names,
and that is fine — each is named for what its own reader is looking for. The
link between them is the `**Issue**: #N` line, not a shared string; a slug rule
strict enough to be greppable is one the very first spec here already broke.

The numbers are independent too: `NNN` is a spec counter, not an issue number.

## Required header

Every spec starts with a back-reference line directly under the title:

```markdown
# Feature Specification: <name>

**Issue**: #1001
```

Without it a spec orphans as soon as its branch is deleted.

**`TEMPLATE.md` does not carry this field** — it is upstream's file, and the
upstream template has no notion of an issue. Add the line by hand when you copy,
directly above `**Feature Branch**`. Keeping the omission rather than patching
the template is deliberate: it leaves the copy byte-identical to upstream, so
re-syncing a newer spec-kit version stays a diff instead of a merge.

## Template

Copy [`TEMPLATE.md`](TEMPLATE.md) and fill it in. It is
[github/spec-kit](https://github.com/github/spec-kit)'s `spec-template.md`
v0.16.3 verbatim (MIT), plus an attribution block.

Two conventions on top of the template:

- Delete the sections that do not apply. `Key Entities` is marked
  *include if feature involves data* and rarely applies to a hook or skill —
  an empty section reads as an unanswered question.
- Leave `[NEEDS CLARIFICATION: ...]` markers in place until they are actually
  resolved. An unresolved marker is the point of the format; replacing it
  with a guess defeats it.
- Delete the `**Status**: Draft` line. praxis does not track spec status in the
  document: a spec on `main` is accepted and one on a branch is not, which git
  already answers without anyone remembering to edit a field. The line stays in
  `TEMPLATE.md` only because that file is upstream's, kept byte-identical.

## What praxis did not adopt

praxis takes this one template and nothing else from spec-kit. Not adopted:

| spec-kit component | Reason |
| --- | --- |
| `.specify/memory/constitution.md` | Duplicate rule source of truth; `CLAUDE.md` and [`ETHOS.md`](../../ETHOS.md) already hold that role |
| `.specify/extensions.yml` hooks | They instruct the model to invoke its own gates — the prompt-only form [`ETHOS.md`](../../ETHOS.md) replaced with real hooks |
| `.specify/workflows/*.yml` gates | Only fire when driven through `specify workflow run`; an agent working directly bypasses them |
| `speckit-*` agent skills | Namespace pressure on an already-large skill surface |
| `specify` CLI at runtime | One template needs no external CLI dependency |
| `plan.md` / `tasks.md` / `checklist.md` | Plan mode and issue checklists already own these |
