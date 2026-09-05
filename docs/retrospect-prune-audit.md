# Retrospect Prune Audit (issue #776)

Evidence-based `keep` / `merge` / `drop` / `investigate` verdict for every
enforcement device in the retrospect skill corpus (SKILL.md + references +
`retrospect-mix-check` Stop hook), scored from retrospective transcript
mining. This extends the [hook prune audit](hook-prune-audit.md)
(issue #713) methodology to the one hook that audit could not score —
`retrospect-mix-check` is fire-ledger-uninstrumented, so its device
fire-rates are reconstructed from recorded session transcripts instead.

**Report-only.** No device is removed by this document; each `drop` /
`merge` verdict is a proposal requiring its own issue + PR.

## Data source and method

Local Claude Code session transcripts (`~/.claude-4/projects/**/*.jsonl` +
`~/.claude/projects/**/*.jsonl`), scanned 2026-07-13:

- **3,120 files scanned, 64 files with retrospect reports, 68 Stage 3
  reports** (all dated 2026-06 → 2026-07; 54 in June, 14 in July).
- A "report" = an assistant text block containing BOTH the
  `## Retrospect Report` header AND the `retrospect:distribution` fence —
  the Stop hook's own identification logic. Fixture cards inside
  `tool_result` payloads (praxis dev/test sessions) are structurally
  excluded by this dual-signal rule. Cohort boundary: a fence-omitting
  Stage 3 draft is blocked by the hook and re-emitted with the fence,
  so it enters the cohort in its fenced form (the one recorded block
  below confirms this path); only a draft abandoned without re-emit is
  invisible, so the fence-omission gate's exposure denominator is a
  lower bound.
- Per-report tallies: `gate_1..6_verdict` values, fence occurrence,
  `critic_diff:` values, `memory_hygiene` / `output_quality` category
  counts.
- Genuine Stop-hook blocks are persisted as `type: user` events whose
  string content starts `Stop hook feedback:` — **130 such events exist**
  across the corpus (workflow-drift ×70, completion-verify ×21, strike ×15,
  …), so Stop-block persistence is proven working and an absence of
  retrospect blocks is evidence of non-firing, not a recording gap.

Reproduce: the miner script is a ~120-line stdlib-Python scan (session
scratchpad artifact; aggregate-only output, no session content). Core
counting rules are stated above; exact report counts require the
dual-signal rule (header + fence in the same assistant text block),
which grep alone cannot express. Coarse file-level spot-checks across
both roots:
`grep -rl 'gate_1_verdict' ~/.claude-4/projects ~/.claude/projects | wc -l`
(files containing at least one keyed card — upper bound on sessions) and
`grep -rh 'Stop hook feedback' ~/.claude-4/projects ~/.claude/projects | head`
(genuine Stop-block feedback events).

### Known measurement caveats

1. **Deterrence is invisible.** A gate that never fires may be deterring
   the violation (the producer follows the spec *because* the gate
   exists). Retrospective data cannot distinguish this — all `drop`
   verdicts below are directional, mirroring #713's caveat.
2. **Deployment lag.** Sessions run the *installed plugin release*, not
   repo HEAD. A device's repo-merge date precedes its real-world exposure
   by up to one release cycle, so exposure counts below are upper bounds.
3. **Schema evolution.** 14 of 68 reports predate the full 6-verdict card
   (`ABSENT` rows) — rates are computed against the reports where the key
   could appear.

## Device exposure (introduction date → reports since)

From `git log --reverse -- skills/retrospect
hooks/completion-verify/retrospect-mix-check`:

| Device | Landed | Reports since (of 68) |
| --- | --- | --- |
| Gate-3 evidence robustness (#172/#291) | 2026-05-11 | 68 |
| Gate-4 external-repo (#302) | 2026-05-17 | 68 |
| Gate-5 memory-scan (#—, 05-18) | 2026-05-18 | 68 |
| Gate-6 oracle-match (#501) | 2026-05-30 | 68 |
| Stage 1.5 hygiene + Stage 2.7 audit (#366) | 2026-05-21 | 68 |
| Gate-7 receipt gate (#639) | 2026-06-08 | 68 |
| Gate-7 value check (#692/#693) | 2026-06-19 | 44 |
| Fence-omission gate (#666/#667) | 2026-06-16 | 56 |
| Gate-8 suppression ledger (#700), 8b (#703), critic tier (#704) | 2026-06-25 | 26 |
| Gate-8c critic-skip floor (#717, narrowed #723) | 2026-06-27 | 18 |
| Gate-9 silent-pass + Gate-10 critic-roots (#773) | 2026-07-13 | 0 |

## Axis 1 — never-applicable producer gates

Verdict distribution over the 68 recorded cards. `ABSENT` = the card
predates the key (caveat 3) — absent cards are excluded from the keyed
denominator, so missing schema data is never counted as `NA`:

| Gate | PASS | FAIL | NA | ABSENT | Applicability (keyed) | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Gate-1 categorical | 24 | 0 | 30 | 14 | 44% (24/54) | **Keep** — the anchor gate; hook + script (#775) both enforce it. |
| Gate-2 rationale schema | 40 | 0 | 14 | 14 | 74% (40/54) | **Keep** — highest-applicability gate. |
| Gate-3 evidence robustness | 12 | 0 | 42 | 14 | 22% (12/54) | **Keep** — applies to 1-in-5 reports; semantic half is cheap post-#775 (verdict flag). |
| Gate-5 memory-scan | 46 | 0 | 8 | 14 | 85% (46/54) | **Keep** — near-universal applicability. |
| Gate-4 external-repo | 2 | 0 | 50 | 16 | **4%** (2/52) | **Keep (severity floor)** — applicable only twice across its 52 keyed cards, but it guards external-repo writes (high blast radius, reputational). #775 already collapsed its cost to a script check; prose residue is minimal. Note: the `gate_4` key is absent from 2 more cards than the other gates (16 vs 14 — 2 producers omitted just this key), so its keyed denominator is 52. |
| Gate-6 oracle-match | 0 | 0 | 52 + 2 `N/A` | 14 | **0%** (0/54) | **Merge/demote** — never once applicable in any of its 54 keyed cards. It also has no defined recording surface (found during #775 review — `oracle_match` lives nowhere until the agent invents a Rationale line), and 2 cards emitted a non-canonical `N/A` spelling the hook silently tolerates. Proposal: fold the producer procedure into the Stage 4 memory-action procedure (where stored-value corrections actually execute) and drop `gate_6_verdict` from the card. Blast radius: prose ~35 lines + one script flag; hook does not parse gate_6. |

**Schema-laxity finding (no device verdict):** the card verdict values are
free text to every consumer except the hook's `gate_1/2/3/4` keys — the
`N/A` variant above proves drift is already occurring. The #775 script now
canonicalizes production; the hook remains tolerant by design.

## Axis 2 — hook gates that never block

Across every recorded session, `retrospect-mix-check` produced exactly
**1 genuine Stop block** — the #666 fence-omission re-emit nudge
(`Retrospect Stage 3 distribution fence missing`). Zero genuine blocks
from the content gates (Gate-1/2/3-backing/4 table checks, Gate-7 value
check, Gate-8/8b/8c ledger checks) in their entire recorded lifetime. All
other occurrences of the block-reason string in transcripts are
`tool_result` quotes of the hook's own source/tests (praxis dev sessions).

| Device | Genuine blocks | Exposure | Verdict |
| --- | --- | --- | --- |
| Fence-omission gate (#666) | 1 | 56 (lower bound — see cohort boundary note in the method section) | **Keep** — the only device with a recorded save. |
| Gate-1/2/3-backing/4 table parse | 0 | 68 | **Keep, cost-shifted** — never blocked, but these are the checks the #775 producer script now pre-flights; the hook side is the 2nd defense line and its cost is already paid (code + 121 tests). Do not extend further. |
| Gate-7 value check (#692/#693) | 0 | 44 | **Investigate** — receipt fences appear in 40/68 report messages (59%), so the *producer* practice is alive; the enforcement (stale-count detection, live only since 2026-06-19) has never fired in its 44-report exposure. Candidate for simplification if the next instrumented window (see Appendix) still shows 0. |
| Gate-8 structural + 8b laundering | 0 | 26 | **Keep (young)** — 26-report exposure is below decision threshold. |
| Gate-8c critic-skip floor | 0 | 18 | **Investigate** — the #722/#723 narrowing chose a near-zero-FP token set (3 phrases), which plausibly also means near-zero TP. 18-report exposure is too small to conclude; flag for the instrumented re-audit. |
| Gate-9/10 (#773) | — | 0 | **No verdict** — shipped 2026-07-13, zero exposure. Explicitly excluded. |

**Deterrence caveat applies to this whole axis** (caveat 1): the
producer-side compliance that keeps these at zero may exist because the
gates exist. That is why never-blocked ≠ drop here, and why the axis
verdicts are keep/investigate rather than drop.

## Axis 3 — stage productivity

| Device | Productive reports | Rate | Verdict |
| --- | --- | --- | --- |
| Stage 1.5 MEMORY.md hygiene | `memory_hygiene > 0` in 24/68 | 35% | **Keep** — regularly yields findings. |
| Stage 2.7 artifact audit | `output_quality > 0` in **0/68** | **0%** | **Merge/demote** — in its entire 68-report lifetime the artifact audit has never contributed a finding to a card. Proposal: demote from an unconditional stage section (SKILL.md + stage1-2-analysis.md trigger list) to a short trigger checklist inside Stage 2, keeping the `audit_skipped` trail line. Directional (deterrence caveat), but 0-for-68 is the weakest productivity signal in the corpus. |
| Externalized critic tier (#704) | `critic_diff:` recorded values: `not-run` ×2, ran ×0 | 0 runs / 26 exposure | **Investigate** — the tier has never actually executed. Its enforcement (Gate-8c) is also unfired (Axis 2). Young device; re-score after instrumentation. Note: only 6/26 exposed reports carry a ledger fence at all — see Axis 4. |

## Axis 4 — compliance gap (not a prune signal)

`suppression_ledger` fences appear in **6 of the 26** reports emitted
after the mandate landed (23%). Under caveat 2 (deployment lag) some of
those 26 ran pre-mandate plugin releases, so the true compliance rate is
unknown but bounded below 100%. This is an adoption/enforcement question,
not a prune candidate — Gate-8 blocks a missing ledger only when the
*hook* release carrying it is installed. The instrumented re-audit
(Appendix) resolves this by recording hook-version alongside fires.

## Verdict summary

| Verdict | Devices |
| --- | --- |
| Keep | Gate-1, Gate-2, Gate-3, Gate-4 (severity floor), Gate-5, fence-omission gate, Gate-8/8b (young), Stage 1.5 |
| Merge/demote (proposal) | **Gate-6 oracle-match** (0/54 keyed-applicable → fold into Stage 4 memory-action procedure), **Stage 2.7 artifact audit** (0/68 productive → demote to Stage 2 trigger checklist) |
| Investigate at next window | Gate-7 value check, Gate-8c token set, critic tier |
| No verdict (zero exposure) | Gate-9, Gate-10 |

Estimated prose reduction if both merge/demote proposals land: ~90-120
lines across SKILL.md / stage1-2-analysis.md / stage2.5-audit.md /
stage4-execution.md, plus one flag and its tests in the #775 script —
each proposal needs its own issue with a regression check for the
original motivating incident (#501 for Gate-6, #366 for Stage 2.7).

## Appendix — instrumentation follow-up (pre-req for the next audit)

Issue #713 already recommends porting the 4 uninstrumented shell hooks
to fire-event emission (2026-09-05: issue #892 has since instrumented all
four in place, and issue #1304 ported `codex-review-route` to Python, so
three shell hooks remain). For this corpus specifically:

1. Port `retrospect-mix-check` to emit fire-events under the existing
   `hooks/_lib/_fire_ledger.py` contract (`decision: block | ask |
   advise | pass`, `granularity: rich | coarse`, `hook`, `role`), so
   Axis 2 gets live denominators instead of transcript mining. The
   coarse tier is NOT sufficient here: this hook blocks by emitting
   `{"decision": "block"}` JSON with exit 0 (impl.sh:1013), the exact
   pattern the ledger docstring says the coarse path records as
   `pass`. Rich recording (or parsing the emitted JSON) is required,
   plus a reason/gate field extension to attribute which gate blocked.
2. Record the plugin release version in the fire event (a schema
   extension — the current contract has no release field) to close the
   deployment-lag caveat (Axis 4).
3. Re-run this audit once Gate-9/10 and the #775 producer script have a
   full release cycle of exposure; the `investigate` rows above become
   decidable then.
