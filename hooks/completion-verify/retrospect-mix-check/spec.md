# Stop Hook Retrospect Mix Check

Supported hosts: all

`hooks/retrospect-mix-check.sh` fires on every `Stop` event and blocks the
retrospect skill's Stage 3 output from defaulting to memory-only when
findings are tagged `tool` / `workflow` / `spec-gap`, or when memory-only
findings ship without a structured 5-line rationale.

### Why this exists

Predecessor work (`retrospect-tool-friction`) added Stage 2 step 4b (Tool
Friction Pass) and an upstream-feedback action type, but in practice the
retrospect skill kept resolving most findings as memory-only — even tool
and workflow friction got memo'd instead of escalated. A spec-only fix
(stronger Red Flags + selection matrix) was insufficient because the LLM
would acknowledge the rule and still skew memory; the same pattern that
caused this hook's existence is the one that proved memory-based feedback
alone fails. So the gate moved out-of-band: a Stop hook that parses the
structural distribution-card fence emitted by Stage 3 and rejects outputs
that violate the T3 double gate.

This is the second praxis hook to follow the "spec defines the contract,
hook enforces it" pattern (after `completion-verify.sh`).

### What is blocked

When the last assistant message contains:

1. A line matching `^## Retrospect Report` (em-dash or hyphen tail)
2. The HTML-fenced distribution card `<!-- retrospect:distribution begin -->`
3. The most recent `## Retrospect Report` block does NOT contain
   `## Actions Executed` (i.e., we're in Stage 3 awaiting approval)

…the hook parses the card and the unified findings table, then blocks if any
of the following hold:

| Trigger | Why blocked |
|---------|------------|
| `gate_1_verdict: FAIL` in the distribution card | Stage 2.5 Gate-1 (categorical) was violated |
| `gate_2_verdict: FAIL` in the distribution card | Stage 2.5 Gate-2 (procedural rationale) was violated |
| `gate_3_verdict: FAIL` in the distribution card | Stage 2.5 Gate-3 (evidence robustness) was violated — a 2-action compound finding lacked independent evidence per action, or had decision-coupled actions |
| `gate_4_verdict: FAIL` in the distribution card | Stage 2.5 Gate-4 (external-repo authorization) emitted FAIL |
| `gate_4_verdict` absent AND Rationale contains `⚠ EXTERNAL:` prefix | Gate-4 ran and marked findings external, but `gate_4_verdict` was not written to the distribution card — Stage 2.5 was partially skipped |
| `gate_1_verdict` or `gate_2_verdict` key missing | Distribution card is malformed or Stage 2.5 was skipped |
| Any row with `Category` ∈ {tool, workflow, spec-gap} AND `Proposed Actions = memory` (single) | Gate-1 violation detected via independent table parse |
| Any row with `Proposed Actions = memory` (single) whose `Rationale` lacks exactly 5 lines `^not (issue\|claude_md_draft\|skill_idea\|hook_code\|upstream_feedback): .+$` | Gate-2 violation detected via independent table parse |
| Any row with `Proposed Actions` containing `upstream_feedback` or `issue` whose `Rationale` lacks a `backing_repo: <owner/repo>` declaration | Gate-3 (backing_repo) violation — Stage 2 step 8 requires this declaration for routing; Stage 4 Action 4 step 0 aborts on absence |

### Issue #666 — retrospect-active Stage-3 fence-omission gate

The three identifier conditions above key on the agent's **own output format**
(`## Retrospect Report` header + distribution fence). A free-form / localized
Stage 3 report that omits the fence fails identifier check 2, so the hook
`exit 0`s and **every gate (Gate-1..7) silently no-ops** — "the gate exists but
does not fire", one level deeper than "rule exists ≠ retrieval". This is the
exact bypass that let a post-compaction salient-window report survive Gate-7.

The fix anchors on a signal the bypassing report cannot avoid: a session-scoped
**retrospect-active marker** written by
[`hooks/preflight-gate/retrospect-active-marker`](../../preflight-gate/retrospect-active-marker/spec.md)
at retrospect skill-invocation time (resolved here from
`${PRAXIS_RETROSPECT_ACTIVE_FILE:-${TMPDIR:-/tmp}/praxis-retrospect-active-${session_id}.json}`).
When the marker is present:

| Condition | Action |
|-----------|--------|
| `## Actions Executed` present (Stage 4 complete) | clear the marker, pass through |
| presenting a findings table (markdown separator row) AND fence absent AND not Stage 4 | **block** — re-emit the canonical Stage 3 schema so the gates can evaluate |
| distribution fence present but no `## Retrospect Report` header | run the gates **header-independently** (a localized header cannot skip them) |
| no findings table, no fence (a pre-Stage-3 prose clarification stop) | pass through (not a report) |

When the marker is **absent**, this gate is dormant and the hook behaves exactly
as before (the identifier checks pass through). This keeps the change additive:
the full pre-#666 test matrix passes unchanged.

The block's *report-shaped* trigger is a markdown table separator row
(`|---|---|`), which is **language-independent** — a localized Stage 3 findings
table still uses markdown pipe syntax. So the gate catches the localized-report
bypass without keying on a localizable English header/column the violator could
avoid. It deliberately does NOT fire on a retrospect-active stop that presents
only prose, because SKILL.md prescribes legitimate pre-Stage-3 STOP-to-user
surfaces (self-conflict detection; ambiguous `backing_repo`) that are prose
clarifications, not reports.

The marker's lifecycle (set on skill-invoke, cleared on any non-invocation
`UserPromptSubmit` and on Stage 4) bounds the armed window to the active,
incomplete retrospect turn, so an abandoned retrospect / topic change does not
cause a later unrelated Stop to be blocked.

#### Known residual limitations

- **Multi-turn retrospect with a pre-Stage-3 user interaction.** If a retrospect
  STOP-surfaces a pre-Stage-3 question and the user *answers it in a new prompt*,
  that prompt clears the marker (it is indistinguishable from a topic change by
  the `UserPromptSubmit` handler). A subsequent fence-omitting Stage 3 in the
  next turn would then not be gated. This is the aggressive-clear ↔ false-positive
  trade-off: aggressive clear was chosen because it gives zero false positives in
  the **dominant single-turn flow** (invoke → Stage 3 in one turn, where the
  marker is still set) and closes the common bypass; the multi-turn reopen
  requires the *optional* pre-Stage-3 surfaces and is the accepted residual.
- **Localized Stage-4 header.** Stage-4 detection keys on the literal
  `## Actions Executed`. An agent that both localizes the Stage-4 header AND omits
  the fence on a findings-table-bearing message would be blocked post-execution
  with a re-emit nudge. This is a narrow compound deviation; the consequence is a
  recoverable nudge (actions already ran; no data effect), not a wrong write.

### What is NOT blocked (pass-through)

- Non-retrospect Stop events (most assistant messages)
- Retrospect outputs at Stage 4 (`## Actions Executed` present in most-recent block)
- `behavioral`-only findings with valid 5-line rationales — legitimately memory-only
- Compound actions like `memory, skill_idea` — Gate-2 only checks single `memory`
- Rows whose `Proposed Actions` contain neither `upstream_feedback` nor `issue` — Gate-3 does not apply
- `gate_4_verdict: PASS` or `gate_4_verdict: WARN` in the distribution card — WARN means external findings exist but per-action approval is the enforcement at Stage 4 (not here)
- `gate_4_verdict: NA` — no `upstream_feedback` findings exist; Gate-4 did not apply

### Trigger condition summary

Hook fires only when ALL three conditions hold; this scoping is what
makes Stage 3 the gate point and prevents a previously-successful Stage 4
from creating a permanent same-session bypass.

### Fail-safe paths

The hook exits 0 (passes) when any of:

- `stop_hook_active` is true (re-entry guard)
- `transcript_path` is missing or unreadable
- The transcript is empty or contains no parseable assistant text
- The last assistant message is not a retrospect Stage 3 output (any of
  the 3 identifier conditions fails)
- `jq` is not installed
- The distribution-card fence is malformed (parse error)

### No bypass marker

Like `completion-verify.sh`, this hook intentionally has **no escape
hatch**. False positives must be reported as a new issue, not papered
over with a marker — the pattern this hook catches is the same pattern
the marker would re-enable.

### Stop hook ordering

The Stop array in `hooks/hooks.json` runs in order:
`completion-verify` → `retrospect-mix-check` → `strike-counter stop`.

`completion-verify` checks evidence-of-completion claims; `retrospect-mix-
check` checks retrospect Stage 3 mix. The two gates are independent — they
match on different signals — and both must pass. If both block, only the
first one's reason reaches the user (Claude Code Stop hooks short-circuit
on the first `decision: block`); fix the upstream issue and re-run.

### Rollback

If a hook bug produces false blocks in production:

```bash
# Option 1: revert the hooks.json registration entry
git -C ~/.claude/plugins/.../praxis apply --reverse <patch>

# Option 2: edit hooks/hooks.json, remove the retrospect-mix-check entry
#          from the "Stop" array, save.

# Option 3: temporary kill switch — edit ${CLAUDE_PLUGIN_ROOT}/hooks/
#           retrospect-mix-check.sh and add `exit 0` at the top.
```

### Tests

`tests/test_retrospect_mix_check.sh` covers 38 cases plus 8 synthetic
regression fixtures:

- 4 pass scenarios (behavior-only with rationale, escalated tool, escalated
  workflow, compound action)
- 7 block scenarios (Gate-1 across 3 categories, Gate-2 across 4 forms,
  combined)
- 2 pass-through (non-retrospect, post-Stage-4)
- 5 fail-safe (`stop_hook_active`, missing/empty/malformed transcript, no
  `jq`)
- 3 regression (T19 same-session rerun, T20 hyphen header, T21 interaction
  with `completion-verify`)
- 5 hardening (T22 escaped pipe in cell, T23 short row schema violation,
  T24 degenerate `memory, memory`, T25 dual-card last-wins, T26 retrospect
  inside fenced code block)
- 3 Gate-3 backing_repo (T27 upstream_feedback with backing_repo → pass,
  T28 issue row missing backing_repo → block, T29 non-routed action no
  backing_repo needed → pass)
- 2 Gate-3 verdict (T30 gate_3_verdict: FAIL in card → block, T31
  gate_3_verdict: PASS in card → pass)
- 3 Gate-4 verdict (T36 gate_4_verdict: PASS → pass, T37 external marker +
  absent gate_4_verdict → block, T38 gate_4_verdict: NA + no upstream_feedback
  → pass)
- 3 Category-count carve-out (T-NEW1 memory_hygiene category count in card →
  pass — parser ignores; T-NEW2 audit_skipped trail line outside fence → pass
  — trail does not interfere with parsing; T-NEW3 output_quality category
  count in card with cli Tool Layer → pass)

### Category counts (memory_hygiene, output_quality)

`memory_hygiene` and `output_quality` are CATEGORY counts emitted by Stage 1.5
and Stage 2.7 respectively. They are sibling lines to the 6 action-type counts
inside the distribution-card fence but are **informational-only**:

- The Stop hook's awk parser keys on `gate_1_verdict` / `gate_2_verdict` /
  `gate_4_verdict` only; any other line (including these category counts) is
  silently ignored.
- Adding/removing/renaming `memory_hygiene` or `output_quality` alone does NOT
  require fence-marker or action-key changes in this hook or the test suite —
  the parser is forgiving by design.
- Stage 1.5/2.7 findings still emit their underlying action under one of the
  6 action-type keys (`memory`, `issue`, `claude_md_draft`, `skill_idea`,
  `hook_code`, `upstream_feedback`); the category-count lines surface how
  much of the report originated from the new stages without changing the
  gate-enforcement surface.

`<!-- retrospect:audit_skipped: no artifacts -->` is a trail line emitted by
Stage 2.7 on 0-trigger silent-skip. It lives OUTSIDE the distribution-card
fence (typically before or after the fence) and is informational-only — the
hook ignores it.

Fixtures live in `tests/fixtures/retrospect-synth-{tool,workflow,behavior,
mixed,gate3-fail}.jsonl` with `.expected.json` sidecars (`{expected_decision,
must_contain, must_not_contain}`). All pass fixtures include `gate_3_verdict`
in `must_contain` to verify the key is present in the distribution card.

```bash
./tests/test_retrospect_mix_check.sh
```
