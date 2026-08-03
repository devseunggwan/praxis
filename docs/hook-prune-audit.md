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

Window: **2026-07-05 → 2026-08-03, 743 sessions** — a full 30 days with
fire-events throughout, superseding the first audit's effective 5-day /
49-session sample (2026-06-26 → 2026-07-01). Recalibrated for issue #874.

Fire counts below are a snapshot taken 2026-08-03T07:5xZ; the ledger is
append-only and live, so re-running the command minutes later moves every
`Fires` figure up by tens. The block/ask/advise counts the verdicts actually
rest on are stable at this resolution — where a number matters to a verdict,
it is an escalation count, not a fire count.

The ledger carries 96 hook names; `hooks/manifest.json` registers **81** across
90 entries (a name repeats when one hook binds several events). The 15 extra
ledger names are excluded from every table below:

- test fixtures from `tests/test_fire_ledger.py` — `adv`, `ask`, `deny`,
  `crash`, `pass`, `p1`, `p2`, `boom`, `boom_mem`, `boom_rec`, `real_block`,
  `allow`, `_lib`;
- two hooks that existed during the window and have since left the manifest —
  `external-write-falsify-check` (last seen 2026-08-01),
  `verdict-gap-coexistence-gate` (last seen 2026-07-28). Their rows are
  history, not verdict targets.

### What changed since the first audit

The roster grew from 58 registered hooks to 81, and the sample from 49
sessions to 743. Two of the first audit's structural claims no longer hold —
see Axis 1 and the Axis 4 update below.

## Axis 1 — never-fired

**Result: none.** All 81 registered hooks fired at least once in the window.

The first audit carved out 4 shell hooks (`codex-review-route`,
`completion-verify`, `retrospect-mix-check`, `strike-counter`) as
*unmeasurable*, on the ground that a `body: impl.sh` hook reaches no recording
chokepoint. **That is no longer true.** Issue #892 gave shell bodies
`hooks/_lib/record_fire.sh`, and all four now record:

| Hook | Fires (30d) | Block | Advise |
| --- | --- | --- | --- |
| `retrospect-mix-check` | 7,680 | 3,026 | 0 |
| `strike-counter` | 3,359 | 75 | 75 |
| `codex-review-route` | 2,001 | 0 | 189 |
| `completion-verify` | 1,801 | 195 | 0 |

`bypass-review` itself carried the stale claim: `roster_split()` classified by
file extension, so the report printed "these 4 emit NO fire events" directly
below a table tabulating nearly 15,000 of them. The classification now follows the
chokepoint — Bash-dispatch-group membership, or a `fail_open` / `record_fire`
reference in the hook's body — and the extension test survives only as the
fallback for an unreadable body. With that fix the *uninstrumented* roster is
empty: every registered hook is scoreable.

## Axis 2 — advise-ignored-rate high

`Observed` counts advise fires with a later same-hook fire in the same session
to compare against; `Ignored` means that later fire was `advise` again — the
flagged condition recurred unchanged.

| Hook | Ignored / Observed | Rate | Verdict |
| --- | --- | --- | --- |
| `pipefail-advisory` | 250 / 1056 | 24% | **Keep — and the single largest advisory load.** See the ADVISE-tier note below. |
| `pre-gh-pr-create-dedup-gate` | 86 / 361 | 24% | **Investigate** (carried over) — 30% on the first sample, 24% on a 15× larger one, so the rate is real and stable, not small-n noise. The open question is unchanged: does the advisory say what to *do* next, or only that something is wrong? |
| `commit-title-length-check` | 11 / 60 | 18% | Keep — recurrence here is cheap (retitle and retry) and the gate is exact. |
| `cli-flag-incompat-advisory` | 1 / 9 | 11% | Keep — n too small to read. |
| `destructive-bash-guard` | 17 / 191 | 9% | Keep. |
| `external-write-path-existence-check` | 10 / 111 | 9% | Keep. |
| `count-assertion-verify` | 12 / 206 | 6% | Keep. |
| `fallback-negative-warn` | 4 / 71 | 6% | Keep. |
| `inspection-chain-advisory` | 34 / 643 | 5% | Keep. |
| `source-citation-probe-gate` | 3 / 120 | 2% | Keep. |
| `momentum-rule-retrieval-gate` | 7 / 510 | 1% | Keep. |
| `pre-output-falsification-gate`, `bash-worktree-existence-advisory`, `perf-multiplier-evidence-advisory`, `output-block-falsify-advisory`, `pre-commit-staged-file-enumeration`, `model-routing-advisory` | 0 / n | 0% | Keep. |
| `block-unmatched-glob` | 3 / 5 | 60% | Not scoreable — n=5. |

### The ADVISE tier is not inert (falsifies issue #874's premise)

Issue #874 opened on a single session (2026-07-27, `5d46110f`) where 42 ADVISE
fires produced no observable behaviour change, and inferred that the tier's
effect is zero because advisories go to stderr where the model cannot see them.

Across 743 sessions the recurrence rates above **do not support that
inference**. `pipefail-advisory` — the highest-volume advisory, and the one
the issue named first — recurs 24% of the time, meaning roughly three in four
advises are followed by the hook no longer flagging. `momentum-rule-retrieval-gate`
recurs 1% of 510 observed fires.

Two caveats keep this from being a clean refutation:

- The metric is the hook's own re-evaluation, not a literal behaviour diff.
  A later `pass` can also mean the session moved to commands the matcher does
  not cover, which would inflate the apparent heed rate.
- Right-censored fires (the last advise of a session, with nothing after it to
  compare against) are excluded, and that exclusion is not random — a session
  that ends right after an advisory is exactly the case where nothing was done
  about it.

The honest reading: the single-session "effect = 0" observation does not
generalise, and the ADVISE tier should not be deleted or promoted wholesale on
that basis. The delivery-channel question the issue raises (stderr vs.
`systemMessage`) stands on its own and is not settled by this data either way —
it needs an experiment, not a larger window.

## Axis 3 — coverage-duplicate

Two known duplicate-*code* pairs from the 2026-06-04 quality audit
(`project_praxis_audit_spec` Cluster B) were cross-checked against fire-rate
behavior to see if the duplication also shows up as duplicate *coverage*:

| Pair | Fire-rate behavior | Verdict |
| --- | --- | --- |
| `pre-output-falsification-gate` (advise ×161, block ×0 / 59,172 fires) vs. `output-block-falsify-advisory` (block ×547, ask ×460, advise ×7 / 59,457 fires) | Both scan for the same "self-authored proposal without falsification" pattern (Cluster B3 — shared evaluative-marker/`Falsified:`-phrase matcher, not yet extracted to `hooks/_lib/`). The 30-day window resolves the first audit's open question: `output-block-falsify-advisory` is **not** a narrower duplicate that never fires — it is the deny/ask escalation path (1,007 escalations), while `pre-output-falsification-gate` carries the advisory load (161 advises, zero escalations). The split is by severity, and both halves are live. | **Do not merge.** The first audit called the merge "premature" pending more data; the data arrived and argues against merging at all. Cluster B3's code dedup (shared `_lib` matcher) remains worth doing on its own terms — that is a code-duplication fix, not a hook merge. |
| `commit-title-length-check` (ask ×418, advise ×60 / 53,212) vs. `commit-title-format-check` (block ×4,868 / 63,709) | Different escalation levels (ask vs. block) gating different failure modes (length vs. format) that share a git-title-parsing helper (Cluster B2). | **Not coverage-duplicate** — keep both hooks; only the shared parser code is a dedup target. |

**False-positive check (naming-only clustering, ruled out):** `pre-merge-approval-gate`,
`merge-state-claim-gate`, `merge-menu-review-options-advisory`, and
`pr-state-refetch-gate` all contain "merge" in their name and could look like
a 4-way duplicate cluster from naming alone. They are **not** — each gates a
distinct point in the merge lifecycle: pre-merge ask-surface (PreToolUse),
post-hoc claim verification (Stop), AskUserQuestion menu-option advisory
(PreToolUse), and live PR-state re-fetch (PreToolUse, issue #719). All four
now carry real load on the 30-day window (343 ask / 174 block + 1,812 advise /
662 block / 153 block respectively). Defense-in-depth across different event
types, not redundant coverage. No action.

## Axis 4 — advisory-only-never-escalated

**Scope restriction (premise-verification):** a coarse-granularity hook records
only block-vs-pass — its ask/advise fold into `Pass` and are invisible here, so
scoring one on this axis reports a false "never escalates" for a hook whose
escalations are simply unmeasured. This axis is therefore restricted to the
**Bash dispatch group**, where the central dispatcher records the real decision
(block/ask/advise/pass) for every fire. 45 of the window's hooks are in that
group.

**Result: 4 of 45** dispatch-group hooks logged zero block, zero ask, and zero
advise across the whole window.

| Hook | Fires | Sessions | Purpose (verified via spec.md) | Verdict |
| --- | --- | --- | --- | --- |
| `jq-config-empty-dict-advisory` | 59,972 | 400 | Warns before `jq` reads a size-0 or malformed config, where `jq` returns the literal `empty` on stdout instead of erroring and downstream code silently takes a wrong default (issue #323). | **KEEP** — the trigger is "a config file that is empty or invalid *at the moment* a `jq` command reads it". 60k clean reads is the healthy state, not evidence of dead code. A guard for a silent-failure mode is supposed to sit quiet. |
| `version-bump-evidence-check` | 53,212 | 400 | Warns when a `gh issue/pr create/edit` body describes an **external dependency** version bump (`v24 → v25`, `bump SDK from 3.0 to 4.0`) with no changelog or breaking-changes evidence. `strict_env`-gated. | **KEEP** — and the first audit's reasoning for keeping it was wrong. It read the hook as gating praxis's own `VERSION` bump and excused the zero count by noting no release landed inside the window. The window now contains 7 merged `chore(main): release` PRs (7.2.1 → 7.8.0), and those are irrelevant either way: release PRs are authored by release-please, and the hook targets external-SDK bump prose in agent-authored bodies. The correct read is that the trigger is genuinely rare, not that it was untested. |
| `pytest-direct-exec-advisory` | 692 | 6 | Nudges away from invoking `pytest` directly instead of the repo's runner. | **KEEP — not scoreable.** 6 sessions is measurement-thin; revisit next audit. |
| `caller-probe-gate` | 330 | 6 | Requires the caller chain be probed before a claim about who calls what. | **KEEP — not scoreable.** Same reason. |

Every other dispatch-group hook escalated at least once. No hook in this set is
recommended for **drop**: two are narrow safety nets whose quiet is the
designed outcome, and two have too little history to judge.

> **Superseded (issue #847 / #892):** the first audit excluded eight
> `Stop`/`UserPromptSubmit` hooks from this axis because their decisions
> collapsed into coarse `Pass`. #847 gave the five completion-verify Stop gates
> a real `record_session_fire` at their emit point, and #892 did the same for
> shell bodies, so the blind spot that motivated the exclusion is closed.
> Pre-#847 "never escalates" reads on those hooks were measurement-absent and
> should not be carried forward as evidence.

## Summary verdict table

| Verdict | Count | Hooks |
| --- | --- | --- |
| **Keep** (active, or narrow safety net whose quiet is by design) | 77 | Every registered hook not listed below |
| **Investigate** (non-drop follow-up) | 2 | `pre-gh-pr-create-dedup-gate` (24% ignored — is the advisory actionable?), `memory-hint` (68,799 fires, 0 recorded escalations against 12 memories declaring `hookable: true`; it is coarse-recorded, so that 0 is *measurement-absent*, not evidence — the open question is whether its `hookKeywords` match real tool-call text) |
| **Keep, but not yet scoreable — revisit next audit** | 2 | `pytest-direct-exec-advisory`, `caller-probe-gate` (6 sessions each) |
| **Unmeasurable — instrument first** | 0 | — (closed by #847 + #892; see Axis 1) |
| **Drop** | 0 | — |
| **Total** | 81 | Matches `hooks/manifest.json`'s 81 distinct names (77+2+2) |

## Bottom line

No hook meets the bar for removal, on a sample 15× larger than the first
audit's. The roster grew 58 → 81 in a month, and that growth is still not
visibly dead weight: 41 of 45 dispatch-group hooks escalated at least once in
30 days, and the four that did not are two rare-by-design guards plus two
hooks with almost no history.

What the larger window did change is which *claims* survive:

1. The four shell hooks are no longer unmeasurable — #892 instrumented them,
   and `bypass-review` was reporting the opposite of its own data until this
   audit. Fixed here.
2. The falsification-gate pair should **not** merge. The first audit deferred
   the call for more data; the data shows a live severity split, not overlap.
3. Issue #874's "ADVISE tier effect = 0" does not generalise past the single
   session it was drawn from. The delivery-channel question is still open, but
   it needs an experiment rather than a bigger window.
4. `version-bump-evidence-check` was kept for the wrong reason and stays kept
   for the right one.

### What this audit still cannot answer

Every axis here scores *firing*, because firing is what the ledger records.
Whether a fire changed the outcome is inferred at best — Axis 2's heed rate is
the hook re-evaluating itself, not a behaviour diff. So this document can say
which hooks are inert and which are loud; it cannot rank hooks by value, and a
verdict of "keep" here means "no evidence for removal", not "demonstrated
worth". Closing that gap needs outcome instrumentation, not a longer window.

Remaining follow-ups: finish Cluster B3's code dedup (independent of the hook
verdict), look at why `pre-gh-pr-create-dedup-gate`'s advisory recurs 24% of
the time, and confirm `memory-hint`'s keyword sets match real tool-call text.
