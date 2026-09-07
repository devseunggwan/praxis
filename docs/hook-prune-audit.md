# Hook Prune Audit (issue #713)

> **Snapshot.** Scored against the August 2026 roster (81 hooks, 63 exactly
> scoreable). Every count below is historical by construction; the current
> roster is `hooks/manifest.json` and the ledger it scores is described in
> `docs/bypass-telemetry.md`.

Evidence-based `keep` / `merge` / `drop` verdict for every hook in
`hooks/manifest.json`, scored against the fire-rate ledger `bypass-review
fire-rate` produces (issue #710). This applies a deletion-over-addition
lens to praxis's own hook accretion: a hook that never fires is weight the
suite carries on every tool call, not safety, and the ledger is the evidence
that decides which is which.

## Data source

```bash
./skills/bypass-review/bypass-review fire-rate -d 30
```

Window: **2026-07-05 → 2026-08-03, 466 sessions** — a full 30 days with
fire-events throughout, superseding the first audit's effective 5-day /
49-session sample (2026-06-26 → 2026-07-01). Recalibrated for issue #874.

**Contamination floor (issue #939).** This audit originally read the window as
743 sessions. That count included fixture sessions: before issue #934 redirected
development runs to `<checkout>/.praxis-dev-telemetry`, invoking a shell test
directly wrote straight into the production ledger. Re-measuring the same window
gives **751 session ids read, 285 synthetic (37.9%), 466 human** and **53,654
synthetic records of 2,958,085 (1.81%)**. The 743 → 751 gap on the read side is
the snapshot boundary — the original figure was taken 2026-08-03T07:5xZ, part-way
through the window's last day.

`bypass-review fire-rate` now drops synthetic records on read and prints both
figures under `Record provenance`, so this correction does not have to be
remembered. The ledger is append-only; nothing was deleted.

Contamination is heavily skewed by hook, so the correction is not uniform
(re-measured over the whole window, so the denominators run slightly above the
part-way snapshot the tables below use):

| Hook | Fires | Synthetic | Share |
| --- | --- | --- | --- |
| `retrospect-mix-check` | 7,837 | 6,285 | 80.2% |
| `completion-signal-gate` | 13,106 | 1,245 | 9.5% |
| `output-block-falsify-advisory` | 61,175 | 1,965 | 3.2% |
| `jq-config-empty-dict-advisory` | 61,676 | 1,124 | 1.8% |
| `askuserquestion-loop-signal` | 1,948 | 0 | 0.0% |
| `pytest-direct-exec-advisory` | 1,734 | 0 | 0.0% |

Per-hook figures below predate the filter and come from the part-way snapshot,
so re-running the command moves them: fire counts down (synthetic records leave)
and per-hook session counts either way (synthetic sessions leave, but the rest of
the last day arrives). `pytest-direct-exec-advisory`, for instance, reads 8
sessions below and 12 filtered over the whole window.

**The verdicts do not move.** They rest on escalation counts, and re-aggregating
the three Axis 4 escalation-free candidates with synthetic sessions excluded
leaves all three still escalation-free:

| Hook | Sessions (filtered) | Decisions | Escalations |
| --- | --- | --- | --- |
| `askuserquestion-loop-signal` | 192 | `pass` 1,948 | 0 |
| `jq-config-empty-dict-advisory` | 403 | `pass` 60,552 | 0 |
| `pytest-direct-exec-advisory` | 12 | `pass` 1,734 | 0 |

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
sessions to 466. Two of the first audit's structural claims no longer hold —
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
chokepoint — Bash-dispatch-group membership, or an anchored instrumentation
statement in the hook's body (`@fail_open`, an import of `fail_open`, a
`source`/`.` of `record_fire.sh`) — and the extension test survives only as the
fallback for an unreadable body. Anchoring matters as much as the marker: every
shell hook carries `# shellcheck source=…/record_fire.sh` one line above its
real source line, so a substring test would keep calling a body instrumented
after the executable line was deleted. With that fix the *uninstrumented*
roster is empty: every registered hook can produce fire events.

> **2026-09-05 note (issue #1304).** `codex-review-route` was ported from
> `impl.sh` to `impl.py`, so the shell roster above is now three
> (`completion-verify`, `retrospect-mix-check`, `strike-counter`). Its
> ledger shape is unchanged — one RICH `advise` per emitted advisory via
> `record_session_fire`, coarse `pass` otherwise — so the row and counts
> above stand as measured; only the recording path moved. The three
> remaining ports are tracked in the same issue.

## Axis 2 — advise-ignored-rate high

`Observed` counts advise fires with a later same-hook fire in the same session
to compare against; `Ignored` means that later fire was `advise` again — the
flagged condition recurred unchanged.

> **The metric this axis was scored from no longer exists (issue #1238).** Its
> whole inference is "the next fire of this hook was a `pass`, so the advisory
> was heeded", and since #1238 a `pass` is not a row in the stream at all — it
> is a per-session counter, which carries no position relative to the advise
> that preceded it. The numbers below stand as the measurement that was taken;
> `bypass-review fire-rate` no longer prints an `Advise-Ignored Detail`
> section, so they cannot be re-derived from a ledger written after that
> change. Re-deriving them needs the ordered stream back, which is the cost
> this axis was traded against.

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

Across 466 sessions the recurrence rates above **do not support that
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
| `pre-output-falsification-gate` (advise ×161, block ×0 / 59,172 fires) vs. `output-block-falsify-advisory` (block ×547, ask ×460, advise ×7 / 59,457 fires) | Both scan for the same "self-authored proposal without falsification" pattern (Cluster B3 — shared evaluative-marker/`Falsified:`-phrase matcher, not yet extracted to `hooks/_lib/`). The 30-day window resolves the first audit's open question: `output-block-falsify-advisory` is **not** a narrower duplicate that never fires — it is the deny/ask path (547 block + 460 ask), while `pre-output-falsification-gate` carries the advisory load (161 advises, zero block and zero ask). The split is by severity, and both halves are live. | **Do not merge.** The first audit called the merge "premature" pending more data; the data arrived and argues against merging at all. Cluster B3's code dedup (shared `_lib` matcher) remains worth doing on its own terms — that is a code-duplication fix, not a hook merge. |
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

**Scope restriction (premise-verification):** a coarse-only hook records just
block-vs-pass — its ask/advise fold into `Pass` and are invisible here, so
scoring one on this axis reports a false "never escalates" for a hook whose
escalations are simply unmeasured. The axis population is therefore every
registered hook that emits **at least one rich record**, because a rich record
carries the real decision and makes the block/ask/advise counts exact. That is
**63 of the 81** registered hooks; the other **18 are coarse-only** and stay out
of this axis (`memory-hint`, `external-api-literal-trigger`,
`block-personal-asset-leak`, `protected-paths-guard`, `worktree-edit-gate`,
`secret-print-redaction-advisory`, `pre-edit-md-escape-advisory`,
`pre-edit-protected-branch-guard`, `advisory-wrapper-signature-verify`,
`bulk-write-memory-checkpoint`, `path-probe-gate`, `exclusion-probe-gate`,
`push-remote-ref-verify`, `write-decision-consistency-gate`, `bypass-telemetry`,
`postcompact-context`, `retrospect-active-marker`, `builtin-task-postuse`).

This is wider than the Bash dispatch group alone (45 hooks). Issue #847 gave
the completion-verify Stop gates a real `record_session_fire` at their emit
point and #892 did the same for shell bodies, so those hooks now record their
escalations exactly and belong **inside** the population — the first audit's
Stop/UserPromptSubmit exclusion is closed, not merely noted. Pre-#847 "never
escalates" reads on them were measurement-absent and are not carried forward.

**Result: 3 of 63** logged zero block, zero ask, and zero advise across the
window — and none of the three is a prune signal.

| Hook | Fires | Sessions | Purpose (verified via spec.md) | Verdict |
| --- | --- | --- | --- | --- |
| `jq-config-empty-dict-advisory` | 60,634 | 402 | Warns before `jq` reads a size-0 or malformed config, where `jq` returns the literal `empty` on stdout instead of erroring and downstream code silently takes a wrong default (issue #323). | **KEEP** — the trigger is "a config file that is empty or invalid *at the moment* a `jq` command reads it". 60k clean reads is the healthy state. A guard for a silent-failure mode is supposed to sit quiet. |
| `askuserquestion-loop-signal` | 1,930 | 189 | **Observe-only** PostToolUse recorder (issue #740): appends one ledger record per `AskUserQuestion` call to feed the re-clarification-loop outcome proxy. | **KEEP — not a finding.** It has no escalation path at all, so zero escalations is its specification, not evidence about it. An instrumentation-only hook cannot be scored on this axis and should not be read as inert. |
| `pytest-direct-exec-advisory` | 1,030 | 8 | Nudges away from invoking `pytest` directly instead of the repo's runner. | **KEEP — not scoreable.** 8 sessions is measurement-thin; revisit next audit. |

Every other hook in the population escalated at least once. Excluding the
observe-only recorder and the thin-sample hook, **no hook with an escalation
path and an adequate sample sat inert** — there is nothing here to drop.

> **`version-bump-evidence-check` — the first audit kept it for the wrong
> reason.** That audit read it as gating praxis's own `VERSION` bump and
> excused its zero count by noting no release landed inside the window. The
> hook actually targets **external dependency** bump prose (`v24 → v25`,
> `bump SDK from 3.0 to 4.0`) in agent-authored `gh issue/pr` bodies; the 7
> `chore(main): release` PRs in this window are irrelevant to it either way,
> being authored by release-please. It sat at zero escalations for most of
> this audit and then logged its first `advise` before the audit finished, so
> it is no longer in the table above — the trigger is rare, not absent.

**Observer effect.** The ledger is append-only and live, and the session
writing this audit is one of the sessions being counted.
`version-bump-evidence-check` and `caller-probe-gate` both moved from zero
escalations to non-zero while the audit was being written. Treat single-digit
escalation counts as provisional.

## Summary verdict table

| Verdict | Count | Hooks |
| --- | --- | --- |
| **Keep** (active, or narrow safety net whose quiet is by design) | 60 | Every rich-recording hook not listed below |
| **Investigate** (non-drop follow-up) | 1 | `pre-gh-pr-create-dedup-gate` — 24% ignored on 361 observed advises; is the advisory actionable? |
| **Keep, but not scoreable on Axis 4** | 2 | `askuserquestion-loop-signal` (observe-only by design — no escalation path), `pytest-direct-exec-advisory` (8 sessions) |
| **Keep, but coarse-recorded — Axis 4 cannot see them** | 18 | The coarse-only list under Axis 4, including `memory-hint` (68,799 fires against 12 memories declaring `hookable: true`; its 0 escalations are *measurement-absent*, so the open question — do its `hookKeywords` match real tool-call text? — stays open) |
| **Unmeasurable — instrument first** | 0 | — (closed by #847 + #892; see Axis 1) |
| **Drop** | 0 hooks; 3 *registrations* | The `mcp__.*slack.*\|mcp__.*notion.*` entries of `caller-probe-gate`, `composed-command-gate` and `source-citation-probe-gate`, removed 2026-09-06 (#1359). No hook NAME was dropped; all three keep their Bash registration |
| **Total** | 81 | Matches `hooks/manifest.json`'s 81 distinct names (60+1+2+18) |

## Bottom line

No hook meets the bar for removal, on a sample 15× larger than the first
audit's. The roster grew 58 → 81 in a month, and that growth is still not
visibly dead weight: 60 of the 63 hooks whose escalations are exactly recorded
fired a real decision at least once in 30 days, and of the three that did not,
one is an observe-only recorder with no escalation path and one has an 8-session
history. Only `jq-config-empty-dict-advisory` is genuinely inert — and it guards
a silent-failure mode, so quiet is what it looks like when it works.

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
   for the right one — it gates external-dependency bump prose, not praxis's
   own release, and it logged its first `advise` before this audit finished.
5. Axis 4's population is no longer the Bash dispatch group alone. #847 and
   #892 made 63 of the 81 hooks exactly scoreable, and widening the axis to
   all of them is what surfaced `askuserquestion-loop-signal` — a hook whose
   zero escalations are its specification, not a signal about it.

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
