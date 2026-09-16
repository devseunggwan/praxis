---
name: perf-leak-review
description: >
  Point at performance-defect and memory-leak candidates in a diff — a
  read-only LLM reviewer reads the change plus the repository around it and
  reports candidates in a closed list of five classes. Report-only: nothing is
  executed, nothing is written, no finding is a proven defect.
when_to_use: >
  Triggers on "perf review", "leak review", "performance defect", "memory
  leak", "성능 결함 점검", "메모리 누수 점검". Do NOT activate on
  "프로파일링", "벤치마크", "성능 측정", or any request to measure runtime —
  this skill never runs the code it reviews.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(git diff *)
  - Bash(git rev-parse *)
verified-against-runtime: true
runtime-verified-at: 2026-09-16
runtime-verified-note: "Claude Code Agent(general-purpose) — 7 read-only delegations over the fixture corpus returned schema-valid envelopes, 5/5 planted classes recalled, both clean controls empty, every prepared tree still porcelain-clean; a third of envelopes arrived inside a ```json fence"
---

# perf-leak-review

## Overview

A session writes code and the next thing that reads it is a reviewer looking for
correctness. Performance defects and leaks survive that pass: they are correct
code, they pass the tests, and the cost shows up in production instead. praxis
has no axis that looks for them — [`retrospect`](../retrospect/SKILL.md) grades
workflow friction, and its one "performance" word means a slow *tool call*, not
slow code.

This skill hands a unified diff and the repository around it to a **read-only
LLM reviewer** and asks one question: does this change introduce a candidate in
one of five defect classes? It is **report-only** — it runs none of the
project's code, writes nothing into the repository, and grades every finding
`candidate` rather than defect, the same read-oracle lineage
[`spec-drift`](../spec-drift/SKILL.md) and
[`codex-review-wrap`](../codex-review-wrap/SKILL.md) sit in.

**Core principle:** a reader can point at a candidate; only an execution can
prove a defect — so the output says `candidate` and never more.

## When to Use

- A change touches loops, I/O, handles, subscriptions, caches, or query paths
  and you want the leak axis read before it merges.
- You have a diff (any repository, any language) and want its perf/leak
  candidates listed with file and line.
- Do **not** use it to measure anything. No profiler, no benchmark, no
  before/after comparison — those need execution, which this skill refuses.

## The closed list

Five classes, and nothing outside them is reported. The list is the spec:
widening it is a separate change to
[`references/reviewer-brief.md`](references/reviewer-brief.md) and
`validate_findings.py` together.

| # | Class |
|---|-------|
| C1 | N+1 / remote call inside a loop |
| C2 | Unreleased resource (file, socket, connection, subprocess) |
| C3 | Unbounded global container or cache |
| C4 | Load-everything-then-filter-in-memory |
| C5 | Unreleased listener, callback, or timer |

## Caller-agnostic by contract

The skill takes `--diff <path> --repo <root>` and returns a finding envelope. It
does not decide **when** a review runs or **where** the findings go — those are
the caller's, and they are deliberately unsettled. So no step here reads a
session transcript, posts a comment, opens an issue, or blocks anything; a
trigger surface added later attaches by passing the two arguments.

## Process

### Step 1: Resolve the two inputs

```bash
git rev-parse --show-toplevel
git diff
```

`--diff <path>` is a unified diff file and `--repo <root>` is the root the diff
applies to. With neither argument, take the diff from `git diff` in the current
repository and the root from `git rev-parse --show-toplevel`. Both paths are
read-only for the rest of the run.

Stop if the diff is empty: there is nothing to review, and an empty envelope
from an empty diff says nothing about the reviewer.

### Step 2: Check the host can delegate

This step needs the `Agent` tool. On a host that does not provide it — the Codex
host is **UNKNOWN** here, never verified — **stop** and report
`perf-leak-review: this host cannot delegate a read-only reviewer — unverified
here`. Do not review the diff inline instead: the session that wrote the diff is
the one context that cannot read it independently (CLAUDE.md, *the author-exempt
trap*), and an inline fallback would silently trade that away.

### Step 3: Delegate the review

Read [`references/reviewer-brief.md`](references/reviewer-brief.md) and pass it
as the prompt, verbatim, followed by the two resolved paths:

```text
Agent(
  subagent_type="general-purpose",
  prompt=<the full text of references/reviewer-brief.md>
         + "\n\n--diff <resolved diff path>\n--repo <resolved repo root>\n"
)
```

One delegation per diff. The brief, not this file, is the source of truth for
what the reviewer is told — the class definitions, the read budget, the tool
restriction, and the output schema all live there, so they cannot drift from
what the validator enforces.

The reviewer is told to use Read, Grep, and Glob only. That instruction is the
whole enforcement: `allowed-tools` above is an allow list for *this* skill's
steps and does not bind a subagent, so "it did not write" is a claim the
caller's own after-the-fact check has to settle (see Limitations).

### Step 4: Validate the envelope

Pipe the reviewer's JSON through the validator; it exits non-zero and prints
every violation:

```bash
printf '%s' "$envelope" | "${CLAUDE_SKILL_DIR}/validate_findings.py"
```

A rejected envelope is **not** repaired and re-emitted. Report the violations as
they are: a class outside C1..C5 is a false positive that the count has to see,
and quietly rewriting it into a compliant finding is what the closed list exists
to prevent.

### Step 5: Report

Print the validated envelope, then one summary line:

```text
perf-leak-review: <n> candidate(s) — C1:<n> C2:<n> C3:<n> C4:<n> C5:<n> (candidates, not verified by execution)
```

Zero findings is a result, not a failure — say `0 candidates` plainly. Every
finding carries `file`, `line`, and the quoted `evidence`, so the reader can go
to the line without re-deriving anything.

## Error Handling

| Error | Recovery |
|-------|----------|
| `--diff` path missing or empty | Abort — report the path; do not fall back to `git diff` when a path was given explicitly |
| `--repo` is not a directory | Abort with the path |
| `Agent` tool unavailable on this host | Stop with the unverified-host message (Step 2); never review inline |
| Reviewer wrapped the envelope in a ```` ```json ```` fence, or put prose around it | Strip the fence and extract the single JSON object; if there is not exactly one, report the raw output and stop. Observed on a third of live envelopes, so treat it as normal rather than as a failure |
| Validator exits 1 | Report its violation lines verbatim; do not repair the envelope |
| Reviewer reports a class outside C1..C5 | The validator rejects it — report it as an out-of-list finding, which is a false positive |

## Limitations

- **Candidates, not defects.** Nothing is executed, so a finding is a reading of
  the code, and a false positive is expected rather than exceptional.
- **Read-only is an instruction, not a structure.** `allowed-tools` does not
  constrain the delegated reviewer. A harness that needs proof runs the review
  against a write-protected copy and checks `git status --porcelain` afterwards
  — which is what `scripts/perf-leak-review-eval.sh` does, and even that
  observes only traces left *inside* the prepared tree.
- **Non-deterministic.** Two runs over one diff can differ. `scripts/run-tests.sh`
  never executes a skill, so CI gates the schema, the fixture inventory, and the
  scorer; recall and the clean controls are measured live and transcribed into
  the PR verification anchor.
- **Five classes only.** Algorithmic complexity, concurrency, and security are
  out of scope by construction.
- **Claude host only.** The `Agent` delegation is verified on the Claude host;
  the Codex host is UNKNOWN and the skill stops there rather than guessing.
