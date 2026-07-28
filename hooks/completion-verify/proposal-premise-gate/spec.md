# Stop Prose-Proposal Premise Gate

Supported hosts: all

`hooks/completion-verify/proposal-premise-gate/impl.py` runs on the `Stop`
event. It scans the final assistant message for a **prose proposal block**
(no `AskUserQuestion`, no `Recommended` marker) carrying a **code-checkable
premise** with **no probe evidence** in the current turn, and emits a
non-blocking advisory.

## Why this exists

The two existing falsification gates are PreToolUse-only and keyed on
`AskUserQuestion` evaluative markers:

- `advisory-nudge/output-block-falsify-advisory` — fires on `AskUserQuestion`
  / `Bash`
- `advisory-nudge/pre-output-falsification-gate` — Lane A requires an
  evaluative marker (`Recommended` / `권장` / `safer`) **and** negative
  evidence in the recent transcript

`output-block-falsify-advisory/spec.md` already states the gap:

> The answer-trailer proposal, issue body, PR body, and mid-answer design
> table are surfaced as assistant prose — no PreToolUse surface exists to
> intercept them, so they remain agent-retrieval discipline.

That "agent-retrieval discipline" is the part that failed.

**Motivating case.** An agent surfaced a 7-item prioritized improvement
table as plain prose — no `AskUserQuestion`, no `Recommended` marker, so
neither existing hook could fire. Zero premise probes were run before
surfacing. When the user explicitly asked for an adversarial pass, 2 of the
7 items collapsed within minutes:

| Item | Claimed premise | Falsifying probe | Cost |
| --- | --- | --- | --- |
| "introduce tiered model routing" | "no tier routing exists" | one `grep` for the flag | ~30s |
| "suppress auto-generated artifact PRs" | "they burn the review pipeline and quota" | one CLI read of a sample PR | ~30s |

The first item was worse than merely wrong: it proposed reintroducing a
mechanism that had been deliberately reverted, and the rationale was sitting
in a source comment reachable by the same single grep. The collapsed items'
premises were code-checkable factual claims wearing the costume of an
opinion — a negative existence claim and a cost claim, both falsifiable in
~30s. The agent classified the whole block as "a proposal, therefore
opinion, therefore not subject to verification" — a classification error,
not a cost decision.

Reference: issue [#846](https://github.com/devseunggwan/praxis/issues/846)
(Refs #227, closed — retrospect-lane recommendation premise falsification on
a different surface).

## Design — presence enforcement, mirrors `negative-existence-verdict-gate`

A premise-correctness judgment is infeasible at this layer (the hook cannot
re-run the agent's reasoning). Following the sibling's precedent, this hook
enforces PRESENCE of probe evidence rather than adequacy:

### (1) Proposal-block shape — cheap early exit

The text must contain, at column 0 or as a bold-label line, a
heading/table-header carrying one of: `우선순위` / `제안` / `개선` (Korean,
plain substring) or `Priority` / `Recommendation` (English, case-insensitive).
A markdown table qualifies when its header row carries the keyword AND the
next line is a separator row (`|---|---|` shape). Absent shape → silent
immediately — this is the false-positive guard the issue's own "Open
questions" section calls out as the main design risk: ordinary prose
mentioning "priority" once in passing must not enter the scan.

### (2) Item-line scoping

Only two structural units are scanned for a premise marker, not the whole
message:

- Numbered-list-item lines (`^\s*\d+[.)]\s+\S`)
- Table-data-row lines (contains `|` at least twice, is not a separator row)

This deliberately narrower than "any paragraph" targets exactly the unit the
motivating case exposed — each table row / list item WAS a discrete claim.

### (3) Premise marker

- Negative-existence: KO substrings `없습니다` / `없음` / `미구현` / `안 되어
  있` / `연결되어 있지 않`; EN substrings (case-insensitive) `does not exist` /
  `is missing` / `not wired`; regex `no ... path` allowing 1-3 intervening
  words (case-insensitive), e.g. "no such routing_tier_flag path exists".
- Cost/impact: KO substrings `태웁니다` / `비용이` / `비용을`; EN substrings
  (case-insensitive) `burns` / `costs`; any `N%` pattern.

### (4) Probe-evidence check

An item line carrying a premise marker is "probed" when the CURRENT turn
(per `get_current_turn` — same turn-scoping every sibling completion-verify
hook uses) contains a `Grep`/`Read`/`Bash`/`Glob` tool call whose input
(`pattern`/`path`/`glob`/`file_path`/`command`) shares an identifier-like
token (`[A-Za-z0-9_/.-]{4,}`, common stopwords excluded) with that line.

No overlap — or no extractable ASCII-identifier token at all (an all-Korean
claim with no distinguishing name/flag/path) — counts as unprobed. This is a
deliberately crude, substring-only match; see "Honest limitation" below.

## Tier — advisory only, never blocks

Unlike `negative-existence-verdict-gate` (hard block by default), this hook
is advisory-only per the issue's own proposal — the false-positive risk from
crude token-overlap matching is explicitly acknowledged as the main design
problem, so a block tier is not justified yet.

```json
{"systemMessage": "[proposal-premise-gate] Prose proposal contains N code-checkable premise(s) with no probe in the current turn. Probe before locking. Bypass: PRAXIS_PROPOSAL_PREMISE_BYPASS=1"}
```

**Exit code:** `0` in every case.

## Honest limitation

Token-overlap matching is crude by design (issue's own words): a probe that
covers the claim's TOPIC without sharing an ASCII identifier (e.g. a
Korean-only claim, or a probe phrased with different vocabulary) is not
recognized as evidence, biasing the gate toward MORE advisories rather than
fewer. That bias is accepted because the tier is advisory-only — an
unnecessary nudge costs a re-read; a missed one reproduces the motivating
case. The turn-boundary scope (current turn only, not full session) is
likewise inherited from every sibling completion-verify hook, not a
deliberate narrowing specific to this gate — a probe run in an EARLIER turn
of the same session and not repeated in the final turn will not suppress
the advisory.

## Fail-open contract

| Condition | Behavior |
| --- | --- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| Missing / unreadable / empty transcript | exit 0 (silent pass) |
| No last assistant text | exit 0 (silent pass) |
| `stop_hook_active=true` | exit 0 (re-entrancy guard) |
| `PRAXIS_PROPOSAL_PREMISE_BYPASS=1` | exit 0 (full bypass) |
| Any uncaught exception | exit 0 (`@fail_open` on `main()`) |

This is a standalone Stop hook (not a `(PreToolUse, Bash)` dispatch-group
member), so `main()` carries the `@fail_open` decorator directly per
DESIGN.md (issue #645, enforced by `check-plugin-manifests.py` Rule 16). No
external dependencies — standard library only.

## Tests

```bash
bash tests/hooks/completion-verify/test_proposal_premise_gate.sh
```
