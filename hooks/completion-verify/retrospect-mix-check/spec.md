# Stop Hook Retrospect Mix Check

Supported hosts: all

`hooks/completion-verify/retrospect-mix-check/impl.sh` (run by the `Stop` dispatch
group, `hooks/_dispatch.sh Stop -`, since issue #1281) fires on every `Stop` event and blocks the
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

| Trigger                                                                                                                                                                                                                                             | Why blocked                                                                                                                                                                                                                                                                                                                                       |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gate_1_verdict: FAIL` in the distribution card                                                                                                                                                                                                     | Stage 2.5 Gate-1 (categorical) was violated                                                                                                                                                                                                                                                                                                       |
| `gate_2_verdict: FAIL` in the distribution card                                                                                                                                                                                                     | Stage 2.5 Gate-2 (procedural rationale) was violated                                                                                                                                                                                                                                                                                              |
| `gate_3_verdict: FAIL` in the distribution card                                                                                                                                                                                                     | Stage 2.5 Gate-3 (evidence robustness) was violated — a 2-action compound finding lacked independent evidence per action, or had decision-coupled actions                                                                                                                                                                                         |
| `gate_4_verdict: FAIL` in the distribution card                                                                                                                                                                                                     | Stage 2.5 Gate-4 (external-repo authorization) emitted FAIL                                                                                                                                                                                                                                                                                       |
| Gate-4b (#1244): a routed row is a cross-boundary write but its Rationale carries no `⚠ EXTERNAL: per-action approval required at Stage 4` marker, or the card declares `gate_4_verdict: PASS` while such a row exists, or `NA` while one does      | The card's verdict is written by the agent this gate constrains, so Gate-4b recomputes it from the row's declarations plus `gh api repos/<owner>/<repo>` (own-org only — the exemption needs both halves). `NA` means "no routed row at all", so one such row refutes it. An unresolved lookup demotes instead of blocking — see below            |
| `gate_4_verdict` absent AND Rationale contains `⚠ EXTERNAL:` prefix                                                                                                                                                                                 | Gate-4 ran and marked findings external, but `gate_4_verdict` was not written to the distribution card — Stage 2.5 was partially skipped                                                                                                                                                                                                          |
| `gate_1_verdict` or `gate_2_verdict` key missing                                                                                                                                                                                                    | Distribution card is malformed or Stage 2.5 was skipped                                                                                                                                                                                                                                                                                           |
| Any row with `Category` ∈ {tool, workflow, spec-gap} AND `Proposed Actions = memory` (single)                                                                                                                                                       | Gate-1 violation detected via independent table parse                                                                                                                                                                                                                                                                                             |
| Any row with `Proposed Actions = memory` (single) whose `Rationale` lacks exactly 5 lines `^not (issue\|claude_md_draft\|skill_idea\|hook_code\|upstream_feedback): .+$`                                                                            | Gate-2 violation detected via independent table parse                                                                                                                                                                                                                                                                                             |
| Any row with `Proposed Actions` containing `upstream_feedback` or `issue` whose `Rationale` lacks a `backing_repo: <owner/repo>` declaration                                                                                                        | Gate-3 (backing_repo) violation — Stage 2 step 8 requires this declaration for routing; Stage 4 Action 4 step 0 aborts on absence                                                                                                                                                                                                                 |
| Gate-7 value mismatch: `transcript_receipt` fence declares `is_error_count` or `user_turn_count` that diverges from a live grep of the transcript by more than 1                                                                                    | Receipt was transcribed verbatim from the compaction summary rather than re-derived this turn (presence ≠ freshness)                                                                                                                                                                                                                              |
| Gate-8 (issues #699, #702): the Stage 3 report has no `retrospect:suppression_ledger` fence, has more than one, has a malformed (unterminated/nested) one, or the fence lacks a `worst_agent_failure:`, `self_adversarial:`, or `critic_diff:` line | The Stage 2 self-incrimination pass / conditional externalized critic re-scan record is missing — the painful agent-caused friction the analyzing context is most motivated to bury was never surfaced for audit. Mandatory on every path incl. the clean one. Skipped only once Stage 4 (`## Actions Executed`) is reached for the latest report |
| Gate-11 (issue #917): a findings row proposes a remedy-layer action but the report has no `retrospect:remedy_reach` fence, has more than one, has a malformed one, or carries no complete row for that finding                                      | The remedy's reach was never put on the record — a correct diagnosis whose prescription lives on a layer that cannot fire where the finding was uttered, with the shortfall then described as legitimate. Coverage is per finding. Shares Gate-8's Stage-4 carve-out                                                                              |
| Gate-8b (issue #701): `suppression_ledger` claims a clean/no-failure path while a live transcript scan finds more than one deterministic adverse signal                                                                                             | The ledger exists but launders away visible evidence. The hook re-derives `is_error:true`, content-error syntax, and documented `user_correction` markers on user turns before trusting clean ledger language                                                                                                                                     |
| Gate-8c (issue #715): `critic_diff: not-run` while a live transcript scan finds more than one explicit user-correction marker (tighter `sl_strong_correction_re`, not Gate-8b's broad regex)                                                        | The externalized critic tier — the only anti-concealment mechanism that survives the self-correction literature — was self-skipped in exactly the case its predicate ("a friction_event required user correction") was satisfied. Converts the run-the-critic guidance from unenforced self-feedback into a deterministic format gate             |

| Gate-12 (issue #1013): the live transcript carries at least one structurally-rejected tool call and the report has no `retrospect:denied_actions` fence, has more than one, has a malformed one, or carries no schema-valid disposed row (the whole row is matched — `denied` / `tool` / `source: user_rejection` / `confessed: yes\|no` / `disposition`, anchored at the list marker — so a lone `disposition:` line cannot buy the gate off) | Every other lane keys on something that happened; a refused action has no outcome, so it leaves no error, no correction and no confession, and selection by ease of recall never reaches it. Shares Gate-8's Stage-4 carve-out |

### Gate-11 remedy-reach receipt (issue #917)

A remedy only works on the surface it lives on. The recurring failure this gate
targets is diagnosing a gap correctly, placing the remedy on a layer that cannot
reach where the gap is uttered, and then describing the shortfall as legitimate:
the canonical instance shipped a `PreToolUse` hook for the tool-call axis while
the axis the user named — a **prose** proposal, which emits no tool call — was
routed to a memory layer whose adjacent rule already carried
`recurrence 5 / enforcement none`.

Whenever the distribution card records a non-zero count for a remedy-layer
action (`memory`, `claude_md_draft`, `skill_idea`, `hook_code`), the report MUST
carry exactly one well-formed `retrospect:remedy_reach` fence with at least one
row of the shape:

```markdown
- finding #N: reach=full|partial|none | surface: <layer> | unreached: <axis or none> | worse_axis: yes|no|na
```

One row is owed per remedy-layer finding, and each row must carry all four
fields. Every field label is matched at its delimiter — immediately after
`finding #N:` or after the `|` closing the previous field — so `unreach=full`,
`nonsurface:`, and `not_unreached:` do not stand in for the real labels.

**Two oracles, because neither is sound alone.** The trigger reads the
distribution card's remedy counts; coverage reads the finding IDs collected
while the findings table was parsed. The card carries counts, not identities, so
it cannot say *which* findings owe a row — a report with two remedy findings
would satisfy a count-based check with one row naming whichever was easiest to
answer for. The ID list cannot say *whether* a row is owed — a card declaring a
remedy total while no findings-table row parses as a remedy action leaves the
list empty and the gate silent, which is exactly the self-inconsistent report
that most needs asking. In that card-only case the gate demands one well-formed
row rather than naming a finding it cannot identify.

**Why neither is a text scan.** The distribution card enumerates every action
type by name, so grepping the report body for `memory` fires even on the
0-friction path where every count is 0 and no remedy exists.

**What it does and does not prove.** Structure only: the reach question was
answered on the record. `reach=partial` with an honest `unreached:` axis is a
valid outcome — the gate blocks the *absence* of an answer, not an
uncomfortable one. Requiring both a `reach=` verdict and a non-empty
`unreached:` is what keeps `reach=full` from standing in for an axis that was
never named.

Shares Gate-8's Stage-4 carve-out: a positive-presence gate must not
retroactively block a cycle that already reached `## Actions Executed`.

### Gate-12 denied-action coverage (issue #1013)

The retrospect pipeline enforces a full-corpus scan but does not constrain what
gets **selected** from it, so selection sorts by ease of recall rather than by
damage. In the motivating session the scan really was exhaustive — 13,324
records, all 56 `is_error` bodies read individually, tool census reconciled —
and all five friction slots still went to incidents the agent had already
confessed in conversation. The three failures the external critic recovered
appeared in no lane, and the common factor was that **it never ran**: a machine
guard or the user stopped it, so no outcome existed and nothing was written
down. The less recovery signal an item carries, the larger its damage can be.

Pre-scan lane 6 (`denied_actions`) supplies those candidates; Gate-12 makes the
supply non-optional.

**The oracle is the live transcript, not the fence.** Like Gate-10 (which reads
the critic's subagent return rather than a pasted block), Gate-12 re-derives the
denied set itself, via the shared enumerator
`hooks/_lib/_transcript.py::scan_user_rejections`. A record counts only when
three independent structural markers agree:

| Marker | Field |
| --- | --- |
| Denial kind | top-level `toolDenialKind == "user-rejected"` |
| Error flag | the `tool_result` block's `is_error: true` |
| Fixed sentence | the runtime's `"The user doesn't want to proceed with this tool use…"` |

No natural-language judgement is made anywhere: an option label reading "No" is
never classified as a refusal. The same scanner backs the `#1007`
`rejected-mutation-reconsent-gate`, so the lane, the gate and the preflight gate
cannot drift into three definitions of "rejected". The gate is **tool-agnostic**
— a rejected `Bash` call counts as much as a rejected `AskUserQuestion`; only
the #1007 preflight gate narrows to approval questions.

When at least one rejection exists, the report must carry exactly one
well-formed fence with at least one disposed row:

```markdown
- denied: "<verbatim question>" | tool: <name> | source: user_rejection | confessed: yes|no | disposition: promoted (finding #N)|noted|dismissed (<reason>)
```

**Over the byte bound: acknowledgement, not a receipt (issue #1231).** The
scan is resumable — it continues from the offset a per-session cursor recorded
at the previous Stop — but each call reads at most `REJECTION_SCAN_MAX_BYTES`
(20 MiB) of new bytes. When more than that was appended since the cursor,
`scan_user_rejections` answers `None` — the oracle has not caught up with the
session. That used to fold into `0`, which is also what a clean session
returns, and the budget is exceeded only by long sessions, where refusals
accumulate.

The gate now blocks on it, but asks for less than it does for a known count:
having no count, it cannot demand a row per rejection. One fence is still owed,
carrying **either** the rows an unbounded re-scan recovered **or**:

```markdown
- scan: indeterminate | rescan: done|skipped (<reason>)
```

An empty fence does not clear it — that is precisely the shape a silently-zero
lane produces. A missing, unreadable, or python3-less environment is unchanged:
still `0`, still silent, since none of those is evidence that a session history
exists.

**Supply gate, deliberately weak — stated, not hidden.** One disposed row clears
it. It forces the unconfessed candidates onto the record; it cannot judge which
one deserved a finding slot, and a single schema-valid row carrying
`disposition: dismissed` satisfies the letter of it. What it no longer accepts
is a *bare* `disposition:` line — the row must name what was denied, on which
tool, and whether it was confessed, so the weak gate still costs an enumeration
rather than one word. That is issue #1013's own known limit (`confessed: yes/no` is a
proxy, and the unreached axis is the larger-damage one). The real lever remains
the externalized critic (Gate-8c / Gate-10); Gate-12 is the cheap backstop for
when the critic does not run. A passing Gate-12 is not evidence of a clean
session.

**Half-coverage, stated.** Issue #1013 names a second unconfessed source —
mutations stopped by a hook or command classifier. That half is **not
mechanizable today**, verified rather than assumed:

- `hooks/_lib/_fire_ledger.py` records `hook`, `role`, `decision`, `session_id`
  and `tool`, and never the command text — a ledger-driven lane could say "a
  preflight gate blocked something" but not what;
- its COARSE standalone path (`record_standalone_fire`) writes
  `session_id: ""`, so those records cannot even be attributed to the session
  under analysis;
- in the transcript a hook/classifier block arrives as an ordinary `is_error`
  tool_result with **no** `toolDenialKind` field (probe on a real 659-event
  session: 2 `toolDenialKind` records, both `user-rejected`; 5 `is_error`
  tool_results, the hook-block one carrying only `Exit code 2` and command
  output) — indistinguishable from a failed command without parsing
  block-message prose, i.e. exactly the judgement this lane refuses to make.

Blocked-mutation candidates therefore remain the `is_error` enumeration's job
and the critic's.

Shares Gate-8's Stage-4 carve-out, and fails open at every step: no `python3`,
an unreadable or oversize transcript, or any scanner error yields a count of 0
and a silent gate.

### Gate-7 value check (issue #671)

Previous Gate-7 logic verified that the `transcript_receipt` fence was **present** and
**structurally valid** (begin/end markers, parseable `is_error_count`, enum block when
count > 0). It did **not** verify that the declared counts were derived from the live
transcript this turn. A fence with counts transcribed verbatim from the compaction
summary's pre-formatted numbers is structurally identical to a fresh one and passed.

The value check re-derives `is_error_count` and `user_turn_count` from the transcript
independently and compares them against the declared values:

| Field                              | Canonical derivation command                                                            | Semantics          |
| ---------------------------------- | --------------------------------------------------------------------------------------- | ------------------ |
| `is_error_count`                   | `grep -c '"is_error":true' {transcript_path}`                                           | exact (±tolerance) |
| `user_turn_count`                  | `grep -c '"role":"user"' {transcript_path}`                                             | exact (±tolerance) |
| `content_error_count` (issue #670) | `grep '"type":"tool_result"' {transcript_path} \| grep -cE '<calibrated error syntax>'` | floor only         |

`interrupt_count` is not re-derived (no canonical grep command in the spec).

**`content_error_count` floor (issue #670):** the structural block accepts a
declared `content_error_count: 0` without requiring an enum. The floor re-derives
the count from **tool_result content only** (the grep is scoped to lines bearing
`"type":"tool_result"` so the calibrated error-syntax regex never matches the
agent's own analysis prose or the Stage-3 report, which live in assistant/text
lines). When the receipt declares `0` but the live tool_result scan finds more
than `tolerance` signals, Stage 3 is blocked — a declared `0` that launders away
real exit-0 errors is the same gap #671 closed for `is_error_count`. The check is
a floor, not exact equality: the agent may dismiss matches via the
`content_error_enum` disposition rows, but cannot claim zero signals existed.

**Tolerance:** a delta of ≤ 1 line is accepted to account for live-append races (the
transcript is appended while the Stop hook runs; the final line may be partially
written at scan time). A delta > 1 indicates the count was not derived from a fresh
scan this turn and blocks Stage 3.

**Non-firing conditions (same as existing Gate-7):**

- `_skipped` variant present and no `begin` fence: value check is skipped (no fence to verify)
- No compaction marker in transcript: Gate-7 is dormant
- A structural violation was already set: value check is skipped (more specific error wins)
- `transcript_path` is missing or unreadable: value check is skipped

**Note:** `interrupt_count` value-checking remains deferred because no canonical
grep command is specified in the skill's stage reference files. Issue #670 added
the content-error-signal scan and its floor re-derivation (above); `interrupt_count`
re-derivation is still open.

### Gate-8b ledger-laundering floor (issue #701)

Gate-8's original contract proved only that a `suppression_ledger` fence existed
and carried the required `worst_agent_failure:`, `self_adversarial:`, and
`critic_diff:` lines. It did not verify that a clean-looking ledger was honest.
A report could claim
`disposition: none-found` while the transcript itself contained multiple
deterministic adverse signals.

Gate-8b runs after the structural Gate-8 checks succeed. It re-derives three
cheap signal counts from the same transcript using top-level JSONL events, not
raw text inside assistant prose:

| Signal                   | Canonical derivation                                                                                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `is_error:true`          | `jq` count of top-level tool-result objects or nested `message.content[]` tool-result blocks where `.is_error == true`                                                |
| content-error syntax     | `jq` count of top-level or nested tool-result blocks whose text payload matches the content-error regex                                                               |
| `user_correction` marker | `jq` count of non-tool-result user turns/events where the text payload matches the documented negation / redirect / mismatch marker regex from `stage1-2-analysis.md` |

If the ledger uses clean/no-failure language on either `worst_agent_failure:`
(`disposition: none-found`, `no painful agent failure`, `nothing painful`,
`no real session to scan`, etc.) or pairs a clean-path `worst_agent_failure:`
line with a clean `self_adversarial:` result (`concurred` paired with
`nothing`/`no`, `nothing omitted`, `nothing softened`, etc.) and the combined
live signal count is greater than the tolerance (`1`), Stage 3 is blocked.
Tool-result signals are deduped by top-level JSONL record before applying the
tolerance, so one record that carries multiple matching nested `tool_result`
blocks still counts as one adverse tool event. This is a floor, not full
semantic judgment: the hook does not decide whether every marker is a true
correction, but it prevents the ledger from claiming that no signal existed.

### Gate-8c critic self-skip floor (issue #715)

Gate-8b closes the case where the ledger claims a *clean* path. It does **not**
cover the subtler concealment where the ledger names a (possibly minor)
`worst_agent_failure:` — so `sl_clean_like` is false and Gate-8b stays quiet —
while marking `critic_diff: not-run` to skip the externalized critic tier.
The self-correction literature that motivates issue #715 (intrinsic
self-correction without an external signal does not reliably work; the
bottleneck is error *detection*) makes that tier the one mechanism with teeth.
Leaving the decision to run it as prose guidance re-creates the exact
self-feedback dependency the literature says fails.

Gate-8c runs after Gate-8b inside the well-formed-fence branch. It blocks when
**both** hold:

| Condition                                              | Derivation                                                                                 |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `critic_diff:` value starts with `not-run`             | last `critic_diff:` line inside the ledger fence matches `critic_diff:[[:space:]]*not-run` |
| live explicit-correction count exceeds tolerance (`1`) | `jq` count of user turns matching `sl_strong_correction_re` (`live_sl_strong_uc_count`)    |

The critic tier predicate (`stage1-2-analysis.md`) fires when a
`friction_event` required user correction, so an explicit-correction count over
tolerance means the predicate was satisfied and `not-run` is a self-skip rather
than a legitimate skip.

**Why a tighter regex than Gate-8b.** Gate-8b's `sl_user_correction_re` is broad —
it matches bare `no`/`stop`/`다시`, so benign user turns (`no problem`,
`다시 설명해줘`, `stop the dev server`) also count (a code-reviewer probe matched
7/7 benign phrasings). That breadth is safe in Gate-8b because Gate-8b is
double-guarded by `sl_clean_like`; Gate-8c is a single condition, so the broad
regex — combined with Gate-8c scanning the whole session — would force the critic
on any busy session regardless of real correction (a session-length proxy, not a
correction proxy). Gate-8c therefore uses a dedicated `sl_strong_correction_re`.

**Low-FP narrowing — false-negative bias (issue #722).** A hard block must fire
only on a low-false-positive signal. The first iteration of this regex still
carried everyday-Korean conversational tokens (`그거 말고`, `그게 아니라`,
`그거 아니야`, `내 말은`, `하라니까`, `왜 .*안 하고`, the anchored `I said` token),
and a measurement found **7 of 10** benign non-correcting phrases matched them
(e.g. `그거 말고도 더 있어요`, `그게 아니라 그냥 궁금했어요`,
`하라니까 바로 했어요`). With `>1` tolerance, a quiet retrospect carrying two
such phrases plus a correctly self-reported `not-run` would false-block. Because
the **consequence asymmetry** favours a false-negative (a missed self-skip is
still nudged by the prose self-incrimination layer) over a false-positive
(blocking a clean retrospect erodes trust in the gate), the set is deliberately
narrowed to near-unambiguous redirects: `하라고 했잖아`, `that's not what I asked`,
`그렇게 하지 말라고`. This is **not** an exhaustive correction detector — the
dropped conversational markers remain the prose layer's job. The kept set is
empirically verified low-FP: **benign set 13/13 no-match, genuine corrections
3/3 match**. A greedy `내가 말한 건 .*아니` candidate was rejected during review
(code-reviewer #722) for matching reportive benign forms like
`내가 말한 건 아니지만 참고해주세요`. Re-run the jq probe before adding any new
marker — a candidate that matches a benign phrase is not added.

The `>1` tolerance still
absorbs a single stray strong marker, so
a session with exactly one genuine correction may still legitimately mark
`not-run` (an accepted recall gap that avoids single-marker false blocks). The
check is guarded by an empty-`GATE8_VIOLATION` test so a more specific Gate-8b
laundering message keeps precedence. Like Gate-8b this is a floor, not semantic
judgment: it cannot tell which named failure is the worst, but it removes the
agent's discretion to skip the external auditor on a session the transcript shows
carried genuine user corrections.

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

| Condition                                                                             | Action                                                                       |
| ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `## Actions Executed` present (Stage 4 complete)                                      | clear the marker, pass through                                               |
| presenting a findings table (markdown separator row) AND fence absent AND not Stage 4 | **block** — re-emit the canonical Stage 3 schema so the gates can evaluate   |
| distribution fence present but no `## Retrospect Report` header                       | run the gates **header-independently** (a localized header cannot skip them) |
| no findings table, no fence (a pre-Stage-3 prose clarification stop)                  | pass through (not a report)                                                  |

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

The marker's lifecycle (set on skill-invoke, decayed one turn per
non-invocation `UserPromptSubmit` and cleared on Stage 4) bounds the armed
window to the active, incomplete retrospect, so an abandoned retrospect / topic
change does not cause a later unrelated Stop to be blocked. The decay replaced
an unconditional clear that disarmed this gate on the clarification round-trip
the retrospect skill documents
([#1098](https://github.com/devseunggwan/praxis/issues/1098)); the marker hook's
spec owns the budget.

#### Known residual limitations

- **Multi-turn retrospect with more than one pre-Stage-3 user interaction.** The
  decay spends one turn of the budget per non-invocation prompt, so the marker
  survives the *first* such prompt — the clarification round-trip the retrospect
  skill documents — and is cleared by the **second**, which the
  `UserPromptSubmit` handler cannot tell apart from a topic change. A
  fence-omitting Stage 3 after that second prompt is not gated. The budget is
  where the false-negative ↔ false-positive trade-off is set: a larger one would
  close this residual, and a replay of both hooks over the local retrospect
  corpus showed every value above 2 blocking strictly more table-bearing Stops
  that were not Stage 3 reports while catching no additional bypass, so the
  residual is cheaper than the headroom that would remove it. The marker hook's
  spec owns that measurement.
- **Localized Stage-4 header.** Stage-4 detection keys on the literal
  `## Actions Executed`. An agent that both localizes the Stage-4 header AND omits
  the fence on a findings-table-bearing message would be blocked post-execution
  with a re-emit nudge. This is a narrow compound deviation; the consequence is a
  recoverable nudge (actions already ran; no data effect), not a wrong write.

### Gate-4b independent visibility resolution (issue #1244)

Every Gate-4 check above reads `gate_4_verdict` out of the distribution card,
and that card is written by the agent the gate constrains: the enforcement
layer's input is authored by its own target, so `gate_4_verdict: PASS` passes
the gate. PR #1242 closed the same hole one layer up (the audit script no
longer trusts the self-declared `repo_visibility` when it resolves the own-org
exemption, issue #1150) and left this one open.

Gate-4b recomputes the classification instead of reading the verdict. Its
criterion is the SoT's, `skills/retrospect/references/stage2.5-audit.md`
Gate-4: a routed row is unescalated only when **both** halves hold — the owner
is own-org **and** the repo is `private`/`internal` by declaration **and** by
API. Since issue #993 the criterion is visibility, not ownership.

| Row (routed, `backing_repo` declared) | Resolution | Outcome |
| --- | --- | --- |
| declares `repo_visibility: public`, or declares nothing | none needed — public (#993) | `escalate` |
| declares `private`/`internal`, owner outside the own-org allowlist | none needed — `NOT_OWN_ORG` | `escalate` |
| declares `private`/`internal`, own-org, API says `public` | one `gh api repos/<r>` | `escalate` |
| declares `private`/`internal`, own-org, API says `private`/`internal` | one `gh api repos/<r>` | `exempt` |
| declares `private`/`internal`, lookup returned no answer | attempted, unresolved | `unresolved` |
| declares `private`/`internal`, own-org allowlist itself unresolved | none needed — `UNRESOLVED` | `unresolved` |

Two thirds of that table needs no network at all, which is what keeps the
common offline retrospect deterministic.

The two rows marked *none needed* are the own-org filter: the exemption needs
own-org **and** private/internal, so once the first half fails no visibility
answer can change the verdict, and the resolver spends nothing on it. It
answers `NOT_OWN_ORG` for a refuted owner and `UNRESOLVED` when the allowlist
itself could not be read — ownness unknown is not ownness refuted, and the two
route to different outcomes. Neither consumes the lookup cap, so a batch of
third-party rows cannot starve a genuine own-org row of the budget it needs.
`PRAXIS_REPO_VISIBILITY` is therefore not consulted for a non-own-org repo
either; the hook never reads that value for such a row, so the outcome is
unchanged.

**The own-org half does not depend on `python3`.** The resolver owns the
visibility half, but the allowlist has a purely local answer whenever
`PRAXIS_OWN_ORGS` is set, so the hook reads that variable itself — splitting on
comma, trimming, dropping empties and lowercasing, exactly as
`gate4_visibility.resolve_own_orgs()`'s env leg does. Without this the whole
resolver sat behind `command -v python3`, and an interpreter-less environment
demoted a row whose owner the allowlist already **refutes** — a knowably
external repo passing the Stop, which is the hole Gate-4b exists to close. The
same shell leg covers the adjacent failures: the helper exiting non-zero, or
printing nothing.

Only the env leg is local. `gh api user` genuinely needs a subprocess, so an
allowlist that exists nowhere on the machine still resolves `UNRESOLVED` and
still demotes — the offline tradeoff above is deliberate and unchanged. The
demotion message names the input that was actually missing (`python3
unavailable` when the interpreter is gone, `gh unavailable` when it is present
and the lookup failed) rather than always blaming the env var.

`backing_repo` values reaching Gate-4b are always `<owner>/<repo>`: Gate-3's
parse admits nothing else, so a bare handle is rejected one gate earlier and
never enters the Gate-4b array. That is why the hook's owner comparison carries
no `not slash` guard while the resolver, which reads an unfiltered stdin list,
does.

**Blocks** when a row resolves `escalate` and its Rationale carries no literal
`⚠ EXTERNAL: per-action approval required at Stage 4` marker (Stage 4 Step 0a
trigger 1 is disarmed for that row), when the card declares
`gate_4_verdict: PASS` while at least one row resolves `escalate`, or when it
declares `gate_4_verdict: NA` while **any** routed row with a declared backing
repo exists. `NA` is emitted by `audit-distribution-gates.py` only when no
finding proposed `upstream_feedback` or `issue` at all, so one such row refutes
it whatever the per-row resolution came back as — including rows that resolve
`exempt`, which the escalation check above cannot see.

**Demotes, never blocks, on `unresolved`.** Fail-open would reproduce the
behaviour this gate replaces; fail-closed would block every offline retrospect,
and no other Stop hook in this repo makes a network call. So the Stop proceeds
and the row's ground is demoted from "exempt" to "visibility unresolved",
emitted as a `{"systemMessage": ...}` on stdout (and folded into the reason
string when some other gate blocks anyway, so it is not lost).

That demotion reaches Stage 4 without anything being written into the report,
which the hook could not do in any case:
`skills/retrospect/references/stage4-execution.md` Step 0a fires on **either**
of two independent triggers, and trigger 2 (public-visibility recheck) is
itself fail-closed — *"If the visibility query errors, times out, or returns
anything other than `PRIVATE` / `INTERNAL`, treat the repo as public and fire
the gate."* A row the hook could not resolve is exactly a row Step 0a
re-resolves and fails closed on, so it stays in the per-action approval set.
The literal marker is trigger 1 only.

**Lookup budget.** The resolver
(`hooks/completion-verify/retrospect-mix-check/gate4_visibility.py`) mirrors
`resolve_own_orgs` / `resolve_repo_visibility` in
`skills/retrospect/audit-distribution-gates.py` (`PRAXIS_OWN_ORGS` → `gh api
user`; live API → `PRAXIS_REPO_VISIBILITY`, the API deliberately outranking the
env var per #1150), looks up own-org repos only (`actual =
resolve_repo_visibility(repo) if own else None` in the SoT), dedupes to one call
per repo per run, caps the run at **8 lookups actually spent** (past ~15 repos
the manifest's 10s budget breaks), and adopts the
`MIN_SUBPROC_BUDGET_SEC = 0.5` floor from `hooks/_lib/_hook_runtime.py` — below
it nothing is spawned, because 12 more hooks queue behind this one in the Stop
group. Every failure cause — `gh` missing, unauthenticated, 404, rate-limited,
offline, timed out, over the cap, below the floor, `python3` unavailable —
collapses to the single value `UNRESOLVED`, which demotes. That covers the
**visibility** half only: the own-org half is resolved in the hook from
`PRAXIS_OWN_ORGS` when it is set, so a refuted owner still escalates with the
resolver entirely out of reach.

Measured on this repo (5 runs each, `gate4_visibility.py` on the real API):
the full gate-parsing path costs 1.41s with no lookup and 2.45s with one on a
small transcript, 2.16s / 3.08s on a 21 MB transcript — against a 10s manifest
timeout and the 8s internal deadline the resolver is handed.

The own-org filter is what keeps that budget reachable. Measured on 8 repos
outside the allowlist, real `gh`, the pre-filter and post-filter resolvers run
alternately in one loop so both see the same machine load: 3.62 / 3.81 / 4.60s
before, 0.65 / 0.87 / 0.84s after. Both figures include the one live `gh api
user` call that resolves the allowlist, so the delta is exactly the eight repo
lookups — every one of them spent on an answer the owner check had already made
irrelevant. Run them at separate moments and the comparison is worthless: on a
loaded host the same post-filter fixture measured 0.75s and 2.09s minutes
apart.

### What is NOT blocked (pass-through)

- Non-retrospect Stop events (most assistant messages)
- Retrospect outputs at Stage 4 (`## Actions Executed` present in most-recent block)
- `behavioral`-only findings with valid 5-line rationales — legitimately memory-only
- Compound actions like `memory, skill_idea` — Gate-2 only checks single `memory`
- Rows whose `Proposed Actions` contain neither `upstream_feedback` nor `issue` — Gate-3 and Gate-4b do not apply
- `gate_4_verdict: WARN` in the distribution card — WARN means external findings exist but per-action approval is the enforcement at Stage 4 (not here)
- `gate_4_verdict: PASS` — passes only when Gate-4b's own resolution escalates no row; the card's verdict is no longer sufficient on its own (#1244)
- `gate_4_verdict: NA` — passes only when no routed row declared a backing repo at all; with any such row present it blocks, whatever the resolution said (#1244)
- Rows Gate-4b could not resolve — demoted, not blocked (see above)

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
- (Gate-12 only) `python3` is unavailable, or the shared rejection scanner
  cannot read the transcript / hits its 20 MB bound — the denied count is 0 and
  the gate stays silent
- (Gate-4b only) `python3` or `gh` is unavailable, or the lookup otherwise
  returns no answer — rows that needed it are demoted rather than blocked. Rows
  that needed no lookup still escalate: that path never touches the network.
  A third-party owner is one of them **only while the allowlist is readable** —
  with `PRAXIS_OWN_ORGS` set the hook classifies the owner itself, so a missing
  `python3` no longer excuses a knowably external repo; with no allowlist
  reachable at all, ownness is unknown rather than refuted and the row demotes

### Block log

Every block appends one line to
`${PRAXIS_HOME:-$HOME/.praxis}/logs/retrospect-mix-blocked.log` (best-effort —
a failed write never changes the hook's decision or exit status). Before
#1182 this log lived at the undocumented
`~/.praxis/scope-confirm/retrospect-mix-blocked.log`; old files are not
migrated and a legacy `scope-confirm/` directory may linger harmlessly.

### No bypass marker

Like `completion-verify.sh`, this hook intentionally has **no escape
hatch**. False positives must be reported as a new issue, not papered
over with a marker — the pattern this hook catches is the same pattern
the marker would re-enable.

### Stop hook ordering

The Stop array in `hooks/manifest.json` runs in order:
`completion-verify` → `retrospect-mix-check` → the other completion-verify
gates → `strike-counter stop`. Since issue #1281 every entry except
`strike-counter` runs inside the `Stop` dispatch group (one
`hooks/_dispatch.sh Stop -` node), in that same order; `strike-counter`
follows as its own node.

`completion-verify` checks evidence-of-completion claims; `retrospect-mix-
check` checks retrospect Stage 3 mix. The two gates are independent — they
match on different signals — and both must pass. If both block, the
dispatcher merges every blocking member's reason into one `decision: block`,
each reason prefixed with its `[praxis:<hook>]` tag, so both reasons reach
the user in a single object (before #1281 the standalone nodes
short-circuited on the first `decision: block` and only the first reason
was shown); fix every listed issue and re-run.

### Rollback

If a hook bug produces false blocks in production:

```bash
# Option 1: revert the hooks.json registration entry
git -C ~/.claude/plugins/.../praxis apply --reverse <patch>

# Option 2: edit hooks/manifest.json, remove the retrospect-mix-check entry
#          from the "Stop" array, then regenerate and reload. The manifest is
#          the source; the runtime reads the generated per-platform
#          hooks.json, so editing the manifest alone changes nothing.
./scripts/build-plugin-manifests.py   # rewrites .claude-plugin/hooks/hooks.json et al.
#          then reload the plugin (restart the session) to pick it up.

# Option 3: temporary kill switch — edit
#           ${CLAUDE_PLUGIN_ROOT}/hooks/completion-verify/retrospect-mix-check/impl.sh
#           and add `exit 0` at the top (the dispatcher execs the impl
#           directly; the per-hook wrapper that used to sit beside
#           _dispatch.sh was removed in #1281).
```

### Tests

`tests/hooks/completion-verify/test_retrospect_mix_check.sh` covers 121 cases
plus 11 synthetic regression fixtures:

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
- 5 Gate-4b resolution (issue #1244): T36b forged `private` over a public repo
  → block; T36c undeclared visibility + PASS → block; T36d offline lookup →
  demote; T36e allowlist unresolved → demote; T36f `NA` over a routed row that
  resolves exempt → block (negative polarity is T38).
  Every case that spawns a lookup pins `GH_HOST` at an unresolvable domain, so
  `PRAXIS_REPO_VISIBILITY` is reached as the documented fallback rather than
  because the fixture repo happens to be unregistered on the live API — that
  accident would flip the expectation on an authenticated host, or the day
  someone registers the name
- 15 own-org classification without the resolver: the shadow's own premise
  (`T36_nopy_shadow_hides_python3` plus its `jq`-still-resolvable positive
  control — an unreachable binary and a PATH that was never applied look
  identical), and that premise asserted end to end
  (`T36_nopy_shadow_pipeline_reaches_gate4b` — a python3-independent fixture
  must still reach Gate-4b under the shim, with
  `..._control_card_gate_still_reachable` proving a hook that printed nothing
  could not satisfy it); T36g external owner + `python3` absent → block, and
  T36g2 that the block names the own-org refutation, since T36g is the one case
  an earlier gate can satisfy by coincidence; T36h own-org
  owner, same absence → still demote (the visibility half is what is missing);
  T36i and T36i2 no allowlist at all, set-empty and unset → demote survives;
  T36i3 the demotion names `python3 unavailable`, with T36i3b as the control
  naming `gh unavailable` in the opposite condition; T36j helper exits
  non-zero → block; T36k helper prints nothing → block; T36l the allowlist
  parse — several handles, stray spaces, an empty field, a case mismatch —
  still matches the owner → demote; T36m/T36m2 a bare handle is rejected by
  Gate-3 and never reaches Gate-4b. `python3` is shadowed by mirroring every
  `PATH` entry and deleting that one link, so nothing on the machine is
  touched. The shim is a mirror rather than a hand-listed set of binaries
  because a list cannot be audited against a command no source line contains:
  bare `xargs` (impl.sh:334-337) defaults to `echo`, GNU findutils execs
  `/bin/echo` for it where BSD xargs resolves it internally, and the missing
  link emptied `GATE_1`/`GATE_2` on Linux only
- 11 Gate-4b resolver, asserted against `gate4_visibility.py` directly (issue
  #1244): G4H1/G4H1b below the budget floor nothing is spawned, G4H2/G4H2b the
  same call with budget does spawn it (positive control), G4H3 the 9th repo is
  past the cap, G4H4 the 8th is inside it, G4H5 a repeated repo costs one call,
  G4H6/G4H6b a third-party repo answers `NOT_OWN_ORG` with no call,
  G4H7 an own-org repo in the SAME run is still looked up, G4H8 eight
  third-party rows do not starve a following own-org row of the cap,
  G4H9/G4H9b an unresolved allowlist answers `UNRESOLVED` and spends no lookup,
  G4H10 the live API outranks `PRAXIS_REPO_VISIBILITY` (#1150) against an
  explicit `gh` stub that answers `public` where the env map says `private`,
  with G4H11 as its control — same map, same repo, API taken away, now
  `private`
- 3 Category-count carve-out (T-NEW1 memory_hygiene category count in card →
  pass — parser ignores; T-NEW2 audit_skipped trail line outside fence → pass
  — trail does not interfere with parsing; T-NEW3 output_quality category
  count in card with cli Tool Layer → pass)
- 6 Gate-7 value check (issue #671): V1 stale is_error_count blocked; V2
  counts match live transcript → pass; V3 off-by-one within tolerance → pass;
  V4 stale user_turn_count blocked; V5 _skipped variant bypasses value check →
  pass; V6 no compaction Gate-7 dormant → pass
- 9 Gate-8 suppression ledger (issues #699, #702): SL1 missing fence → block; SL2
   missing self_adversarial → block; SL3 missing worst_agent_failure → block;
   SL4 unterminated fence → block; SL5 double fence → block; SL6 valid adverse
   ledger → pass; SL7 inline mention (no real fence) → block; SL8 Stage-4
   Actions Executed without ledger → pass (carve-out); SL27 missing
   critic_diff → block
- 10 Gate-8c critic self-skip floor (issue #715, narrowed #722): SL28 critic_diff
   not-run + 2 kept-token corrections → block; SL29 not-run + no correction → pass;
   SL29b not-run + exactly 1 kept-token correction → pass (>1 tolerance boundary);
   SL29c not-run + 2 benign EN markers (no/stop) → pass (tighter regex excludes);
   SL29d not-run + 2 benign KO imperatives (걱정하지 마세요) → pass (codex #717);
   SL29e not-run + 2 'AI said …' (anchored I-said no match) → pass (codex #717);
   SL29f not-run + 7 dropped-token benign phrases ×2 each → pass (low-FP narrowing #722);
   SL29g not-run + 2 '그렇게 하지 말라고' (third kept token) → block (CodeRabbit #723);
   SL29h not-run + exactly 1 '그렇게 하지 말라고' → pass (>1 tolerance boundary);
   SL30 critic ran (none|checked) + 2 corrections → pass
- 11 Gate-11 remedy-reach receipt (issue #917): RR1 remedy action + no fence →
   block; RR2 row without an `unreached:` axis → block; RR3 duplicate fences →
   block; RR4 unterminated fence → block; RR5 0-friction card (all counts 0) +
   no fence → pass; RR6 `reach=partial` with the axis named → pass;
   RR7 two remedy findings + one row → block; RR8 row without `surface:` →
   block; RR9 row without `worse_axis:` → block; RR10 empty `unreached:` value
   → block; RR11 prefixed field labels (`unreach=`, `nonsurface:`) → block;
   RR12 card declares a remedy but no findings row parses as one → block
- 13 Gate-12 denied-action coverage (issue #1013): DA1 transcript rejection + no
   fence → block; DA2 rejection + fence with a disposed row → pass; DA3 fence
   with an undisposed row → block; DA4 empty fence → block; DA5 unterminated
   fence → block; DA6 duplicate fences → block; DA7 rejected `Bash` call +
   disposed row → pass (the lane is tool-agnostic); DA8 rejected `Bash` call +
   no fence → block; DA9 ×3 one structural marker dropped (`is_error`,
   fixed sentence, `toolDenialKind`) → pass; DA12 ordinary `is_error` tool
   failure → pass; DA13 Stage-4 Actions Executed → pass (carve-out). Every
   other case in the suite is an additional silent control: none of them carry
   a rejection record, so a Gate-12 that fired on the wrong signal would break
   them rather than pass quietly.

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
tests/hooks/completion-verify/test_retrospect_mix_check.sh
```
