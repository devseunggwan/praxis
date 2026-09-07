---
name: spec-drift
description: >
  Report which requirements in the `~/.praxis/docs/specs/` store the current
  tree does not yet satisfy — runs each requirement's `Verify:` command and
  classifies it `implemented` / `missing` / `UNKNOWN`. Report-only: no writes,
  no commits, no issues.
when_to_use: >
  Triggers on "spec drift", "spec-drift", "스펙 드리프트", "미구현 요구",
  "unmet requirement", "what does this spec still need", "requirement status".
  Do NOT activate on SKILL.md frontmatter drift (that is `codex-review-wrap`'s
  live-runtime gate) or on deferred-decision trailers (that is `debt`).
verified-against-runtime: true
runtime-verified-at: 2026-08-14
runtime-verified-note: "python3 3.x — ran the report against the real store resolved from praxis_specs_dir() (<PRAXIS_HOME>/docs/specs, no --spec-dir passed): 2 specs, 18 implemented, 0 missing, 6 UNKNOWN. tests/test_spec_drift.sh 36/36 against synthetic fixture spec dirs — case 6 is the parse boundary this design rests on (a fixture requirement whose PROSE quotes `false`; the report neither runs it nor leaves the requirement anything but UNKNOWN), case 14 finds the store via a throwaway PRAXIS_HOME with no --spec-dir, 15 has a caller --spec-dir override it, 16-18 pin where a Verify line binds (later-section rebinding, duplicate ids, a second line in one block), 19 a timed-out command keeping its partial output, 20 a Verify command that reads stdin reported `implemented` in 0.04s where the pre-fix code reported `missing (exit 124)` after the full timeout (#1008; fed an open stdin through a FIFO, since a runner whose own stdin is already /dev/null would pass the case vacuously) — positive-controlled on a fixture whose Verify exits 3 (reported `missing` with its command and stderr marker)."
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
requirement quotes commands it wants *rejected* as oracles — the worked example
in [`docs/spec-store.md`](../../docs/spec-store.md) → *Verification lines* holds
three backticked spans, of which exactly one is the oracle — so a tool that
guessed between prose backticks would run the one command the spec says not to
trust.

## When to Use

- Asked what a spec still leaves unmet, or whether a feature is fully built.
- Before picking up work on a feature that has a spec — the report names what
  is already satisfied, so the work does not redo it.
- Reviewing a PR against a spec: `missing` rows are the diff's unfinished half.
- After a refactor, to find requirements the change silently invalidated.

## Not for

- **Enforcing** a spec. Nothing here blocks a commit, a PR, or a merge.
  The spec-store convention sets that boundary: this reports, and the remedy
  if that is not enough is a hook, decided then.
- **Skill spec drift** — SKILL.md frontmatter against live runtime is
  [`codex-review-wrap`](../codex-review-wrap/SKILL.md)'s live-runtime gate
  (#208), a different oracle on a different artifact.
- **Deferred decisions** — commit trailers and tree comments are
  [`debt`](../debt/SKILL.md)'s. `debt` answers *what did we put off*; this
  answers *what is not done*.

## Process

### Step 1: Run the report

```bash
"${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/skills/spec-drift/spec-drift"
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
| `--timeout <seconds>` | Per-command timeout (default 120). A command that exceeds it is reported `missing` with exit 124, carrying whatever it printed before it hung. |
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
eligible command; [`docs/spec-store.md`](../../docs/spec-store.md) lets such a
requirement carry no `Verify:` line for exactly that reason. What a
rising `UNKNOWN` count does mean is that specs are being written without
checkable requirements, which is the feedback loop this design is built on.

## Writing a Verify line

The convention and its rules live in
[`docs/spec-store.md`](../../docs/spec-store.md) → *Verification lines*. In
short: a nested `- Verify: \`<command>\`` item under the requirement, whose
exit code reports **the requirement** and not the environment it ran in, and
which terminates without input. Commands run with stdin at `/dev/null` (#1008),
so one that reads stdin gets EOF rather than hanging to the timeout and being
reported `missing`.

The oracle must also **fail when its subject is gone** (#1011). A comparison
whose *both* sides are command substitutions cannot do that: with the subject
absent both collapse to the empty string and `test "" = ""` exits 0, so the
report prints `implemented` for a requirement nothing satisfies. Pin one side
to a literal.

A requirement with no eligible command keeps no `Verify:` line and says why in
its own prose — that line is what separates "nobody got to it" from "nothing
can check this".

**Where a line binds** is stated in the same section of that document: the
requirement's block, ending at the next line in column 0. A `- Verify:` under
a later heading belongs to nothing and is not run, and a second one in one
block prints as a `warning` line naming the command that did **not** run.

## Error Handling

| Situation | Handling |
| --------- | -------- |
| `CLAUDE_PLUGIN_ROOT` unset (skill run outside plugin context) — the `:?` guard aborts with `praxis plugin root not set` instead of silently trying `/skills/spec-drift/spec-drift` | Resolve the plugin root via the installed-plugins manifest: `jq -r '.plugins["praxis@praxis"][0].installPath // empty' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"`, export it as `CLAUDE_PLUGIN_ROOT`, and re-run; if still unresolved (manifest missing or no praxis entry), report the failure verbatim and stop — do not hand-classify requirements without the oracle |

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
