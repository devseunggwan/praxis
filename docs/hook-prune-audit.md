# Hook Prune Audit (issue #713)

Evidence-based `keep` / `merge` / `drop` verdict for every hook in
`hooks/manifest.json`, scored against the fire-rate ledger `bypass-review
fire-rate` produces (issue #710). This applies ponytail's deletion-over-addition
lens to praxis's own hook accretion — see the P4 work item in
`.omc/specs/deep-dive-ponytail-vs-praxis-improvements.md` (local planning
artifact — `.omc/` is gitignored, so this is a plain-text pointer rather than
a repo-relative link).

## Data source

```bash
./skills/bypass-review/bypass-review fire-rate -d 30
```

Window: 2026-06-02 → 2026-07-01 (fire-event instrumentation itself only starts
2026-06-26 — the first 24 days of the requested 30-day lookback have no
fire-events at all, so the effective sample is **2026-06-26 → 2026-07-01, 49
sessions**). Test-fixture rows emitted by `tests/test_fire_ledger.py` (`adv`,
`ask`, `deny`, `crash`, `pass`, `p1`, `p2`, `boom*`, `real_block`, `allow`,
`_lib`) are excluded from every table below — cross-checked against
`hooks/manifest.json`'s 58 registered hook names.

## Axis 1 — never-fired

**Result: none.** Every hook that emits fire-events (54 of 58 — the other 4
are the uninstrumented shell hooks below) fired at least once in the window.
This is expected — the sample covers the tail end of this very session's
Wave 1 work, which exercised nearly every gate class (commit, PR create,
merge, AskUserQuestion, bulk write).

4 hooks are **shell (`.sh`) implementations with no `@fail_open` / dispatch
chokepoint** and emit no fire-events at all, so they cannot be scored on any
axis below: `codex-review-route`, `completion-verify`, `retrospect-mix-check`,
`strike-counter`. This is a measurement gap, not a verdict — **follow-up
recommendation**: port these to the `@fail_open` decorator (or an equivalent
coarse fire-event emit) so a future audit can actually score them.

## Axis 2 — advise-ignored-rate high

Only hooks with an `Observed` (non-right-censored) sample appear here; n is
small across the board (13–24), so treat percentages as directional, not
statistically solid.

| Hook | Ignored / Observed | Rate | Verdict |
| --- | --- | --- | --- |
| `pre-gh-pr-create-dedup-gate` | 7 / 23 | 30% | **Investigate** — the highest ignored-rate of any advisory hook. Worth checking whether the advisory message clearly states *what to do next* (vs. just flagging), since a 30% ignore rate on a duplicate-issue-search nudge suggests the message isn't actionable enough. Not a drop candidate — it protects against real duplicate-issue creation. |
| `destructive-bash-guard` | 2 / 13 | 15% | Keep as-is — small n, no action needed. |
| `momentum-rule-retrieval-gate`, `inspection-chain-advisory`, `count-assertion-verify`, `pre-output-falsification-gate`, `cli-flag-incompat-advisory`, `bash-worktree-existence-advisory`, `external-write-path-existence-check` | 0 / n | 0% | Keep — advisories are being heeded when observed. |

## Axis 3 — coverage-duplicate

Two known duplicate-*code* pairs from the 2026-06-04 quality audit
(`project_praxis_audit_spec` Cluster B) were cross-checked against fire-rate
behavior to see if the duplication also shows up as duplicate *coverage*:

| Pair | Fire-rate behavior | Verdict |
| --- | --- | --- |
| `pre-output-falsification-gate` (advise ×10 / 4625 fires) vs. `output-block-falsify-advisory` (advise ×0 / 5181 fires) | Both scan for the same "self-authored proposal without falsification" pattern (Cluster B3 — shared evaluative-marker/`Falsified:`-phrase matcher, not yet extracted to `hooks/_lib/`). `output-block-falsify-advisory` never actually advised in this window despite firing more often — either its matcher is narrower/stricter than `pre-output-falsification-gate`'s, or it is the AskUserQuestion-specific escalation path (ask/deny) documented in its own spec (`(Recommended)` + no `Falsified:` line → deny/ask) while `pre-output-falsification-gate` is the softer stderr-only nudge. | **Merge is premature** — they occupy different severities (deny-capable vs. advisory-only) on the same detection logic. Complete Cluster B3's code-dedup first (shared `_lib` matcher); revisit whether the *hook* pair should merge only after both consume the same extracted matcher and a further window shows continued full overlap. |
| `commit-title-length-check` (ask ×44 / 3835) vs. `commit-title-format-check` (block ×692 / 5315) | Different fire counts and different escalation levels (ask vs. block) — they gate different failure modes (length vs. format) that happen to share a git-title-parsing helper (Cluster B2). | **Not coverage-duplicate** — keep both hooks; only the shared parser code is a dedup target. |

**False-positive check (naming-only clustering, ruled out):** `pre-merge-approval-gate`,
`merge-state-claim-gate`, `merge-menu-review-options-advisory`, and
`pr-state-refetch-gate` all contain "merge" in their name and could look like
a 4-way duplicate cluster from naming alone. They are **not** — each gates a
distinct point in the merge lifecycle: pre-merge ask-surface (PreToolUse),
post-hoc claim verification (Stop), AskUserQuestion menu-option advisory
(PreToolUse), and live PR-state re-fetch (PreToolUse, issue #719, this Wave).
Defense-in-depth across different event types, not redundant coverage of the
same token. No action.

## Axis 4 — advisory-only-never-escalated

**Scope restriction (premise-verification):** the fire-rate report's own
caveat states that `Stop`/`UserPromptSubmit`-event hooks return a decision via
a stdout JSON field while exiting 0 — their blocks are invisible to the coarse
fire-event count and fold into "Pass". Scoring these on this axis would report
a false "never escalates" for hooks whose escalation is simply unmeasured.
**Excluded from this axis**: `merge-state-claim-gate`, `completion-signal-gate`,
`readonly-verify-deferral-gate`, `strike-counter` (Stop); `session-intent`,
`postcompact-context`, `retrospect-active-marker`, `codex-review-route` (UPS).
Hooks with a `strict_env` field also default to advisory-only pass-through
until a user opts into strict mode — a 0-block count for these is the designed
default, not evidence of dead code, so they're also excluded from a "drop"
read (though still listed below for completeness).

> **Update (issue #847):** the coarse Stop-lane blind spot this restriction
> works around is now closed for the five completion-verify Stop gates
> (`completion-signal-gate`, `merge-state-claim-gate`,
> `negative-existence-verdict-gate`, `runtime-state-claim-gate`,
> `readonly-verify-deferral-gate`). Each now calls `record_session_fire` at
> its emit point with the real decision (block/advise), so future audits can
> score them on this axis directly by filtering `granularity="rich"`. Before
> #847 the Stop lane recorded structurally zero non-pass fires — every
> block/advise collapsed to coarse "pass" — so any pre-#847 "never escalates"
> read on these hooks is measurement-absent, not evidence of dead code.

Remaining `PreToolUse`/`PostToolUse` hooks with **zero block AND zero advise**
across 49 sessions / 30 days (i.e., every fire resolved to a silent Pass):

| Hook | Fires | Purpose (verified via spec.md) | Verdict |
| --- | --- | --- | --- |
| `memory-hint` | 5053 | Emits a stderr hint when a tool call's text matches keywords from a memory file marked `hookable: true`. **Premise check**: an initial read suggested no hookable memories exist — **falsified** by `grep -rl "hookable: true" .../memory/*.md` → **13 files** in this project's own memory store declare `hookable: true` (including one, `feedback_worktree_context_pre_git_op.md`, whose own body documents a past session where the hook *should* have fired and didn't until frontmatter was added). The mechanism is live and in active use. | **KEEP** — but flag as **monitor**: 0 fires across 5053 evaluations with 13 hookable entries registered is worth checking (do the `hookKeywords` lists actually match tokens that appear in real `tool_input` text?) as a separate, narrower follow-up — not this issue's scope. |
| `jq-config-empty-dict-advisory` | 4819 | Warns before `jq` silently returns `empty` / crashes on a size-0 or malformed config JSON. | **KEEP** — narrow safety-net for a silent-failure mode (accidentally-emptied config file); rare-but-real, not obsolete. |
| `external-api-literal-trigger` | 4503 | Advisory to verify ALL_CAPS/SQL-identifier literals against real API/schema values before writing them. | **KEEP** — spec documents 3 real catches in 30 days elsewhere in the codebase; pattern-match is broad but the underlying failure (guessed enum/catalog values) is a recurring, real class per `feedback_external_cli_verify_first`. |
| `pre-edit-md-escape-advisory` | 1780 | Nudges a Read-before-Edit on `.md` files containing escape-sensitive tokens (`\|`, `\[`, HTML entities) to prevent exact-match Edit failures. | **KEEP** — documented root cause (issue #230, Obsidian-style escaped wikilinks); niche trigger, real recovery-cost avoidance when it fires. |
| `advisory-wrapper-signature-verify` | 1057 | Warns before writing wrapper/client code that delegates to underlying functions without having read their real signatures. | **KEEP** — spec documents 4 prior incidents (wrong exception attributes, factory signature mismatches). Narrow trigger (wrapper-shaped files + delegation pattern), real failure class. |
| `version-bump-evidence-check` | 3835 | Requires evidence of `VERSION`/manifest/`CHANGELOG.md` sync before a release PR. `strict_env`-gated. | **KEEP** — **not a dead-code signal**: the last actual `VERSION` bump (6.3.3, PR #706) landed 2026-06-25, one day *before* fire-event instrumentation began (2026-06-26). The trigger condition (a version-bump PR) simply did not occur inside the measurable window — falsifies the "never needed" read. Re-check after the next release PR (#707's checklist should make this easy to verify going forward). |
| `output-block-falsify-advisory` | 5181 | See Axis 3 — occupies the deny/ask-escalation half of the falsification-gate pair. | **KEEP** — see Axis 3 merge discussion; not scored as "unused", it's paired with a sibling that does carry the advisory load. |

No hook in this remaining set is recommended for **drop**. Every "inert in
this window" hook guards a documented, previously-real failure class whose
trigger condition is narrow by design (that's the point of a targeted
advisory) — a 30-day/49-session window without a match is consistent with
"working as intended, rare trigger" rather than "obsolete".

## Summary verdict table

| Verdict | Count | Hooks |
| --- | --- | --- |
| **Keep** (active or narrow-safety-net) | 50 | All measured PreToolUse/PostToolUse hooks not listed in the two rows below, including every hook with nonzero block/ask/advise activity |
| **Investigate** (non-drop follow-up) | 2 | `pre-gh-pr-create-dedup-gate` (advisory message clarity), `memory-hint` (keyword-match coverage) |
| **Merge candidate, blocked on prerequisite** | 2 | `pre-output-falsification-gate` + `output-block-falsify-advisory` — revisit only after Cluster B3's shared-matcher extraction lands and a further fire-rate window still shows full overlap |
| **Unmeasurable — instrument first** | 4 | `codex-review-route`, `completion-verify`, `retrospect-mix-check`, `strike-counter` (shell hooks, no fire-event emission) |
| **Drop** | 0 | — |
| **Total** | 58 | Matches `hooks/manifest.json`'s registered hook count (50+2+2+4) |

## Bottom line

No hook meets the bar for removal on the current evidence. The 58-hook count
itself (the ponytail-comparison spec's "~60 hooks" framing, rounded) is not,
on this data, an accretion of dead weight — it's mostly narrow,
non-overlapping safety nets whose low individual fire-rate is exactly what a
targeted advisory should look like. The actionable follow-ups are smaller and
different in kind: (1) finish Cluster B3's code dedup before reconsidering a
hook merge, (2) instrument the 4 shell hooks so they're scoreable, (3) look at
why `pre-gh-pr-create-dedup-gate`'s advisory is ignored 30% of the time, and
(4) confirm `memory-hint`'s 13 registered keyword sets actually match real
tool-call text.
