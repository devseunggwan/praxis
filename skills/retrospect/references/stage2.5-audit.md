# Stage 2.5 action-distribution audit (retrospect)

Detailed gate procedures for [`../SKILL.md`](../SKILL.md) Stage 2.5.
`SKILL.md` names the gates; this file defines how each gate passes, fails, and
hands off to Stage 3.

Deterministic checks are owned by the audit script; only semantic judgment
stays in-context (issue #774).

## Run the deterministic audit first

Write the Stage 3 draft (unified findings table + `memory_scan` evidence
blocks) to a scratch file, then run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/retrospect/audit-distribution-gates.py" \
  --draft <stage3-draft.md> \
  [--signals <pre-scan-signal-text.txt>] \
  [--gate3 PASS|FAIL|NA] [--gate6 PASS|FAIL|NA] \
  [--reinforce-memory N]
```

The script mechanizes, mirroring the Stop hook's parsing semantics
(`hooks/completion-verify/retrospect-mix-check/impl.sh`):

- **table schema** — 10-cell rows, valid `category[]` / `Tool Layer` /
  `Proposed Actions` enums, 1-2 actions after dedupe
- **Gate-1 (mechanical half)** — tool/workflow/spec-gap labeled finding with
  `Proposed Actions = memory` (single); per-finding behavioral-label
  falsification keyword scan (requires the honest second category or a
  `behavioral-label-justify:` line); Behavioral-only safeguard (all findings)
  keyword scan over `--signals` + Pattern/Root Cause text (emitted as an
  advisory — surface it to the user and require explicit confirmation before
  Stage 3)
- **Gate-2** — memory-only rationale Schema A (exactly 5
  `not <action>: <reason>` lines) or Schema B (1-2 `not-others: <dim-tags>`
  lines, no Schema A lines)
- **backing_repo** — any row routing `upstream_feedback` or `issue` must
  declare `backing_repo: <owner>/<repo>` in Rationale. Either row type may add
  `repo_visibility: public|private|internal` on its own Rationale line; Gate-4
  reads it, and its absence means public (issue #993). Gate-4 audits `issue`
  rows too since issue #1038, so an own-org *private* backing repo needs the
  line on an `issue` row as well — without it the row escalates as public
- **Gate-4** — cross-boundary write classification and the literal external
  warning prefix `⚠ EXTERNAL: per-action approval required at Stage 4` on
  every escalated row (Stage 4 scans that exact string; a paraphrase disables
  the per-action approval gate). Since issue #993 the criterion is
  **visibility, not ownership**: a write to a *public* backing repo is
  escalated to per-action prior approval even when the owner is your own
  handle/org. A row is unescalated only when **both** halves hold — the owner
  resolves inside the own-org allowlist (`PRAXIS_OWN_ORGS` →
  `gh api user --jq .login` → conservative all-external fallback) **and** the
  Rationale declares `repo_visibility: private` or `repo_visibility:
  internal`. An undeclared repo counts as public (an advisory says so), and a
  third-party repo stays escalated whatever visibility it declares. Verdict
  `WARN` means at least one external finding exists, or the conservative
  fallback was required — Stage 4 must then require per-action approval for
  those rows
- **Gate-5** — every memory-action finding needs a complete
  `<!-- memory_scan finding #N: ... -->` block (`scanned: true`,
  `candidates_reviewed`, `repeat`, `repeat_count`)
- **distribution card** — rendered on stdout in the canonical fence format;
  paste it into Stage 3 verbatim

Exit codes: `0` clean, `1` violations (fix the draft and re-run), `2`
usage/input error. `--reinforce-memory N` adds `successful_patterns` rows
whose `reinforce_action` is `memory` to the memory count.

Scope note: the script is a pre-flight for the Stop hook's per-finding checks
only. Session-level hook gates (Gate-7 transcript receipt, Gate-8/8b/8c
suppression ledger, Gate-9 silent-pass coverage, Gate-10 critic roots) are not
mirrored here — a script-clean draft can still be hook-blocked on those.

Do NOT hand-compute anything the script owns; do NOT hand-edit the card the
script rendered.

## Semantic gates (agent judgment, before trusting the card)

### Gate-1 (semantic half): category correctness

The script validates enum membership and keyword signals; it cannot judge
whether the chosen categories match the finding's actual root cause. Before
accepting the card, confirm per finding that the label reflects the root-cause
evidence — action choice must never drive category narrowing. If the
behavioral-only safeguard advisory fired, surface the keyword set to the user
and log the confirmation.

### Gate-3: evidence robustness for compound actions

Applies to each finding with exactly two `Proposed Actions` (single-action
findings skip Gate-3). Evaluate all three sub-conditions, then pass the
verdict via `--gate3`. The script refuses to default Gate-3 when compound
findings exist.

- **(a) per-action evidence pointer** — each action must cite at least one
  explicit friction-event observation in the Rationale cell (sub-bullet or
  inline `(observed: <event-id or one-line>)`). An action present only because
  a category default filled the table returns to Stage 2 step 7.
- **(b) sibling decision-coupling** — if action B's outcome decides a question
  action A presupposes, keep the stronger-evidence action and demote the
  weaker one to a Stage 3 trigger line:
  `Finding #N: file <action> when <observation predicate>`.
- **(c) single-observation downgrade** — for each action backed by exactly one
  observation with `repeat=false`, downgrade one tier:

| Original action | Downgraded to | Rationale |
| ----------------- | --------------- | ----------- |
| `upstream_feedback` | `memory` | One observation against an external party does not justify upstream-write cost |
| `issue` | `memory` | One observation without repeat does not justify systemic tracking |
| `hook_code` | `skill_idea` | One observation does not justify enforcement code |
| `claude_md_draft` | `memory` | One-observation rule changes risk over-fitting |
| `skill_idea` | `memory` | One observation does not justify a skill artifact |
| `memory` | no downgrade | Already the lowest tier |

If a downgrade makes the action set memory-only while the finding still
carries `tool` / `workflow` / `spec-gap`, re-run the audit script (its Gate-1
check re-fires). The downgrade itself does not consume a re-entry; the
resulting Gate-1 re-evaluation does.

### Gate-6: oracle-match completeness

For every stored-value correction finding (an action that would invalidate or
overwrite a stored numeric value), the `oracle_match` field must be present in
the finding's Rationale — with `oracle_match: true` when the action would
invalidate or overwrite the stored value. Applicability is not mechanically
detectable, so the script trusts the `--gate6` verdict; run the producer
procedure honestly before passing it:

1. Read the originating entry's stored oracle: matching basis, cohort, unit.
2. Re-probe with the same matching basis / cohort / unit before treating the
   stored value as stale or wrong.
3. `oracle_match: true` only when the falsification probe used that same
   oracle.
4. A different-oracle probe is a cohort-shift observation, not falsification
   of the stored value.

If the originating entry did not record its oracle, defer falsification and
route the work to an annotation update rather than fabricating a correction.
No stored-value corrections in the finding set → `--gate6 NA` (or omit).

## Re-entry and override

Each finding gets at most 2 gate-driven re-entries (fix draft → re-run
script, or re-evaluate a semantic gate). When a gate still fails after the
cap, stop the loop and surface the gate-specific 3-way prompt — do not invent
a fourth "continue looping" path:

- Gate-1 / Gate-2 / Gate-3:
  `"Finding #N의 Gate-X가 2회 재진입 후에도 통과되지 않습니다. 어떻게 진행할까요? [a] rationale 직접 입력 / [b] action 직접 지정 / [c] note only 강등."`
- Gate-5:
  `"Finding #N의 Gate-5 (memory_scan 필드 누락)가 통과되지 않습니다. 어떻게 진행할까요? [a] MEMORY.md index를 직접 읽고 memory_scan 필드를 입력 / [b] action을 직접 지정 / [c] 이 finding은 note only로 강등."`
- Gate-6:
  `"Finding #N의 Gate-6 (oracle_match 누락 또는 불일치)가 통과되지 않습니다. 어떻게 진행할까요? [a] 같은 oracle로 재측정 / [b] 별도 cohort-shift finding으로 전환하고 stored value 유지 / [c] 이 finding은 note only로 강등."`

Record the selected option and the user's supplied rationale in the Stage 3
trail before proceeding.

## Distribution card semantics

The script renders the canonical card (all six action counts, the
`memory_hygiene` / `output_quality` category counts, and
`gate_1..6_verdict`). `gate_1_verdict`, `gate_2_verdict`, and
`gate_4_verdict` are structurally relevant to the Stop-hook path;
`gate_3_verdict`, `gate_5_verdict`, and `gate_6_verdict` are emitted for
procedural audit even when the Stop hook does not parse them.
