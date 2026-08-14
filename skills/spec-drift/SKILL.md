---
name: spec-drift
description: >
  Report which requirements in the `~/.praxis/docs/specs/` store the current
  tree does not yet satisfy — runs each requirement's `Verify:` command and
  classifies it `implemented` / `missing` / `UNKNOWN`. Report-only: no writes,
  no commits, no issues.
  Triggers on "spec drift", "spec-drift", "스펙 드리프트", "미구현 요구",
  "unmet requirement", "what does this spec still need", "requirement status".
  Do NOT activate on SKILL.md frontmatter drift (that is `codex-review-wrap`'s
  live-runtime gate) or on deferred-decision trailers (that is `debt`).
verified-against-runtime: true
runtime-verified-at: 2026-08-14
runtime-verified-note: "python3 3.x — ran the report against the real store resolved from praxis_specs_dir() (/Users/<user>/.praxis/docs/specs, no --spec-dir passed): 2 specs, 18 implemented, 0 missing, 6 UNKNOWN, and 0001's prose-quoted counter-example command was absent from the printed run list (the parse boundary this design rests on). tests/test_spec_drift.sh 23/23 against synthetic fixture spec dirs — including case 14 (store found via a throwaway PRAXIS_HOME with no --spec-dir) and case 15 (caller --spec-dir overrides the resolved default) — positive-controlled on a fixture whose Verify exits 3 (reported `missing` with its command and stderr marker)."
---

# spec-drift

## Overview

`~/.praxis/docs/specs/NNNN-slug.md` states what a feature must do. Nothing
reads those statements back, so a requirement that quietly stops holding looks exactly like
one that never stopped — the spec's own prose is the last place that would say
so.

This skill runs each requirement's `Verify:` command and reports the verdict.
It is **report-only**: no writes, no commits, no issues, the same invariant
[`debt`](../debt/SKILL.md) carries.

**Core principle:** only a `Verify:` line is ever executed. A well-written
requirement quotes commands it wants *rejected* as oracles — `0001`'s FR-001
cites a counter-example command precisely to disqualify it — so a tool that guessed
between prose backticks would run the one command the spec says not to trust.

## When to Use

- Asked what a spec still leaves unmet, or whether a feature is fully built.
- Before picking up work on a feature that has a spec — the report names what
  is already satisfied, so the work does not redo it.
- Reviewing a PR against a spec: `missing` rows are the diff's unfinished half.
- After a refactor, to find requirements the change silently invalidated.

## Not for

- **Enforcing** a spec. Nothing here blocks a commit, a PR, or a merge.
  `0001`'s Assumptions set that boundary: this reports, and the remedy if
  that is not enough is a hook, decided then.
- **Skill spec drift** — SKILL.md frontmatter against live runtime is
  [`codex-review-wrap`](../codex-review-wrap/SKILL.md)'s live-runtime gate
  (#208), a different oracle on a different artifact.
- **Deferred decisions** — commit trailers and tree comments are
  [`debt`](../debt/SKILL.md)'s. `debt` answers *what did we put off*; this
  answers *what is not done*.

## Process

### Step 1: Run the report

```bash
"$CLAUDE_PLUGIN_ROOT/skills/spec-drift/spec-drift"
```

Two paths are resolved, and they come from different places. The **store** is
`praxis_specs_dir()` — under `PRAXIS_HOME`, outside every checkout — resolved by
the `spec-drift` shell entry, which sources `hooks/_lib/_paths.sh` and passes
the result to `spec_drift.py`; the skill layer never imports the Python resolver
(#981). The **working directory** is the repository root of wherever you invoke
it, via `git rev-parse --show-toplevel`, and every `Verify:` command runs from
there, so those lines may use repo-relative paths.

The store being shared while the checks are not has one consequence worth
stating: run the report from repository B and a spec written for repository A
reports `missing`. The verdicts belong to the tree you are standing in.

Flags:

| Flag | Effect |
| --- | --- |
| `--timeout <seconds>` | Per-command timeout (default 120). A command that exceeds it is reported `missing` with exit 124. |
| `--spec-dir <path>` | Scan somewhere other than the resolved store (used by the tests). |

Exit code is 0 whenever the report itself ran, **including when requirements
are `missing`** — the verdicts are the output, not the exit status. Do not
wire this into a gate expecting otherwise.

### Step 2: Read the verdicts

| Verdict | Meaning | What closes it |
| --- | --- | --- |
| `implemented` | The `Verify:` command exited 0 | Nothing |
| `missing` | It exited non-zero — command and output are printed | Implement the requirement, or correct the command if it is the wrong oracle |
| `UNKNOWN` | The requirement carries no `Verify:` line | Add one, or state in the spec why none is eligible |

There is no `partial`. An exit code is binary, and a middle value would put
the judgement back on a reader — which is the thing this report replaces.

### Step 3: Report to the user

Lead with `missing`, then the `UNKNOWN` count, then the totals. A `missing` row
is a defect claim, so carry its command and output verbatim rather than
paraphrasing — the reader needs to see whether the requirement failed or the
oracle did.

`UNKNOWN` rows are not failures. Some requirements are about prose and have no
eligible command; `0001`'s FR-003 and SC-003 say so in the spec itself. What a
rising `UNKNOWN` count does mean is that specs are being written without
checkable requirements, which is the feedback loop this design is built on.

## Writing a Verify line

The convention and its two rules live in
[`docs/spec-store.md`](../../docs/spec-store.md) → *Verification lines*. In
short: a nested `- Verify: \`<command>\`` item under the requirement, whose
exit code reports **the requirement** and not the environment it ran in, and
which terminates without input.

A requirement with no eligible command keeps no `Verify:` line and says why in
its own prose — that line is what separates "nobody got to it" from "nothing
can check this".

**A `Verify:` line binds to the requirement whose block it sits in**, and a
block ends at the next line starting in column 0. Nested items and indented
continuation prose stay inside; a `- Verify:` further down the document, under
a later heading, belongs to nothing and is not run. A second `Verify:` in one
block is an authoring error: the first binds, and the rest are printed as
`warning` lines naming the command that did **not** run.

## Limitations

- **The report executes strings read from files under `PRAXIS_HOME`, which no
  review ever sees.** The store is not version-controlled, so unlike a tracked
  spec there is no diff, no reviewer, and no history behind the command that is
  about to run — the trust level is that of anything else in the author's home
  directory. Every command is printed before it runs, and that printing is now
  the only thing keeping the executed set inspectable. Do not point
  `--spec-dir` at a directory you did not write.
- Requirements must be one per top-level list item under a requirements
  heading, which is what `docs/spec-template.md` produces. A spec that states
  requirements as prose paragraphs reports nothing for them.
- Nested backticks inside a `Verify:` command are not supported.
- The scan is one flat directory; there is no recursion into subdirectories.
- A `Verify:` line whose command re-enters this report (for example a test that
  scans the real spec directory) recurses. Point such requirements at a
  fixture-based test instead — `tests/test_spec_drift.sh` documents the case.
