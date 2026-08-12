# n1-quantitative-claim-advisory

Supported hosts: all

`hooks/advisory-nudge/n1-quantitative-claim-advisory/impl.py` runs on
`PreToolUse(Bash)`. It fires a stderr advisory (never blocks, no bypass token)
when a **sample-dependent quantitative claim** appears in the body of a
`gh issue|pr create|comment` invocation and **no sample size above 1** is stated
anywhere in that same body.

This document contains its own trigger vocabulary as documentation, so it
carries the opt-out marker below to keep the hook from firing on itself when
the spec text is posted verbatim: sample-size-noted.

## Why this exists — recurrence 6, self-completion 0/3

Memory `feedback_exhaust_cheap_evidence_before_recommendation_surface` reached
**recurrence 6** with `enforcement: none`, while the same rule also sat in
always-loaded `CLAUDE.md` (Falsification Gates → "Exhaust cheap evidence before
surfacing"). Both retrieval layers were loaded. Both failed six times.

The 2026-08-05 session produced three instances, and **every one of them
required a user prompt before the deliverable was completed** — self-completion
rate 0/3:

| # | Surfaced | User's prompt | What the fuller measurement showed |
| --- | --- | --- | --- |
| 1 | PR anchor row marked `PASS` from an **n=1** sample | `추가로 측정이 필요하지 않을까?` | at n=15: p50 2,920ms → 91ms, non-overlapping distributions — far stronger than the claim stopped at |
| 2 | Latency root-cause report | `추가적으로 버벅이는 부분은 없었는지 궁금합니다` | further endpoints surfaced |
| 3 | Delegation briefing | `그러면 저부분 외에는 더 볼 부분은 없는건가요?` | further scope surfaced |

The failure is not a wrong conclusion — each conclusion was defensible. It is
that the **termination condition** used was "my claim is defensible" rather than
"cheap evidence is exhausted". Per `CLAUDE.md` §"Prompt-layer retrieval failure
3-gen threshold", 3+ generations with the remedy in the prompt layer means the
prompt layer is the wrong layer. This is generation 6, so the untried layer is
**emission time** — which is what this hook is.

Reference: issue [#949](https://github.com/devseunggwan/praxis/issues/949).

## Coverage ceiling — 1 of the 3 instances

Only instance 1 is mechanically detectable. Instances 2 and 3 are
prose-completeness misses (enumeration breadth, not sample size), and judging
whether a prose report is "complete" is semantic — it would produce noise rather
than signal. Those two remain **memory-layer only**.

This is stated rather than papered over: the hook closes one third of the
observed surface, and the issue body records the same limit.

## What is detected

All three conditions must hold, scanned over the **same** body text.

### (1) A sample-dependent quantitative claim

| Form | Definition | Example |
| --- | --- | --- |
| A — summary statistic | a percentile (`p50`/`P95`/`p99.9`) or a central-tendency word (`median`, `average`, `avg`, `mean`, `중앙값`, `평균`, `백분위`) with a number within 80 chars | `p50 91ms 로 개선` |
| B — verdict-attached measurement | a verdict token (`PASS`, `FAIL`, `verified`, `검증 완료/됨/함`) within 80 chars of a number + sub-minute unit | `PASS(live) — 응답 91ms` |

A percentile or a central tendency **is** a sample statistic: asserting one is
asserting a distribution. Form B is instance 1's exact shape — a verification
row carrying a timing figure.

Form C is a third, sibling family added after a live recurrence (below). It
uses the same co-location shape as Form B with a **count** in place of a
unit-bearing measurement, and it carries its own pass condition.

| Form | Definition | Example |
| --- | --- | --- |
| C — verdict-attached bare count | a verdict token within 80 chars of a count (`48건`, `12 rows`, `41 failed`, `3 hits`) | `검증 완료 — 48건 확인` |

Form C's pass condition is **not** sample size — it is the count's run
condition. Any one of three fields silences it:

| Field | Recognised as |
| --- | --- |
| the command | a cited `$ …` line, or a backticked invocation naming `pytest`/`grep`/`rg`/`gh`/`git`/`find`/`wc` |
| what it collected | `수집 범위`, `collection scope`, `PYTHONPATH`, `--maxfail`, `테스트 범위` |
| where it ran | `로컬 재현`, `CI run`, `CI 조건`, `workflow run`, `ran on/in` |

### Why Form C exists — a live recurrence

A count reported as **48건** in this repo's own workflow was actually 43. The
number was not so much wrong as **unverifiable**: the reporter's reconstruction
and the measured scope differed (whole-turn concatenation including subagent
files, versus the single last assistant message with sidechains excluded), and
nothing in the report let a reader detect that. `CLAUDE.md` §"Negative Claims
State Their Scan Scope" already requires the form
`<n> <unit> (<command>, <scope>, <where it ran>)`; Form C is that clause at
emission time.

Scope note: firing on *every* count missing all three fields would hit every
number in every PR body and blow the noise budget this hook exists to protect.
The trigger is therefore the **intersection with a verdict token** — a count
being presented as a verified result, which is the shape that actually missed.

### The hook is narrower than the rule — silence is not compliance

The rule requires **all three** run-condition fields. This hook goes silent on
**any one** of them. The gap is deliberate (a three-field requirement enforced
mechanically would fire on nearly every count in every body), but it means:

> **A silent hook is not evidence that the rule was followed.** It reports that
> one field was found, never that the claim is well-formed.

The same asymmetry applies to Forms A and B — see *Adequacy is out of scope*
below. Both are presence checks; neither judges whether the evidence found
actually backs the claim.

### Known misses — two failures from this hook's own authoring session

While this hook was being written, its author produced two instances of exactly
the failure family it targets. **Neither fires.** They are recorded here because
a coverage claim is worth less than a worked counterexample, and because both
were caught by a human reviewer rather than by any hook.

| # | The claim | Why it does not fire |
| --- | --- | --- |
| 1 | `하드코딩된 카운트 단언은 없다` — a `grep` over `tests/*.py` (which excludes `tests/hooks/_lib/`) reported as a general absence. CI then failed on a hardcoded count in the unscanned directory | It is an **absence** claim, not a count claim. Form C reads counts presented as results; a claim that something is *missing* carries no count to co-locate with a verdict |
| 2 | `둘 다 들어가면 48/22/48/43` — four counts stated as fact, derived by inference, never measured. A one-line probe disproved them | The numbers carry **no unit**. `_COUNT_RE` requires a unit token (`건`, `rows`, `failed`, …); a bare `48/22/48/43` is indistinguishable from a version string, a ratio, or a date |

Miss 1 is the more important one: absence claims are the larger half of this
failure family and this hook does not address them at all. Widening to catch
miss 2 would mean treating every unitless number as a count, which is the
noise-budget collapse this design exists to avoid. Both remain memory-layer
only, alongside instances 2 and 3 above.

Counting these, the hook covers **1 of 5** observed instances across two
sessions. That number is the honest one, and it is here rather than in a commit
message because a coverage ceiling that is not written down gets forgotten at
exactly the moment someone wants to trust the hook's silence.

### (2) No sample size ≥ 2 anywhere in the body

Any one of these silences the scan:

| Form | Example |
| --- | --- |
| `n=` notation, left-guarded | `n=15`, `N = 15` |
| Korean repetition count | `15회 측정`, `15번 반복`, `15회 실행` |
| Korean sample word | `표본 15`, `표본 크기 15`, `샘플 15` |
| English run count | `15 runs`, `15 trials`, `15 samples`, `15 iterations` |

An explicit `n=1` does **not** pass — it is the case the hook exists for.

### (3) No opt-out marker

The literal string `sample-size-noted` anywhere in the body silences the hook,
mirroring `count-assertion-verify`'s `# count-verified` marker convention. Use
it when the sample size is deliberate and already stated in prose.

## Scope boundaries — what is deliberately NOT a trigger

| Excluded | Why |
| --- | --- |
| A bare `PASS` with no measurement near it | every PR verification anchor carries `PASS(live)` rows; the token alone would fire on every anchor comment ever posted. The issue names `PASS` in its trigger vocabulary; this narrows it to PASS-with-a-measurement, the shape that actually missed |
| Multiplier notation (`3x`, `1.76배`) and percentage-with-direction | [`perf-multiplier-evidence-advisory`](../perf-multiplier-evidence-advisory/spec.md) already owns those tokens on this exact surface; duplicating them would double-fire one body |
| Minute- and hour-scale durations (`12분`, `2h`) | overwhelmingly build/run times, not sample-dependent claims |
| A one-digit `[P1]` tag | Codex finding-severity tags appear in this repo's own PR bodies; the percentile pattern requires two digits |
| Prose completeness / enumeration breadth | semantic, noisy — the coverage ceiling above |

## Adequacy is out of scope

The pass condition is **presence-only**: the hook does not verify that the
stated sample size actually backs the specific claim. That adequacy judgment
mirrors the sibling gates — `perf-multiplier-evidence-advisory`'s timing
artifact and `negative-existence-verdict-gate`'s `Enumerated:` line are both
presence, not adequacy.

The consequence is the same one stated for Form C above, and it holds for
**every** form in this hook: an `n=15` written next to a figure measured once
silences the hook completely. **A silent hook is not evidence that the rule was
followed** — it reports only that a token was found.

### Suppressor scope is the whole body, and that leaks across claims

The abstract statement above has a concrete failure mode worth naming, because
it is reachable on this hook's primary surface rather than hypothetical.

Claim **detection** is narrow — `_has_verdict_measurement` and
`_has_verdict_count` both require a verdict token and a figure within
`_VERDICT_WINDOW` chars of each other. Claim **suppression** is not: the
opt-out marker, the sample-size scan, and the run-condition scan all run over
the entire body. So a suppression signal belonging to one claim silences
*every* claim in the same body:

| # | Claim | Result |
| --- | --- | --- |
| 1 | 목록 응답 | PASS(live) — 91ms (1회 측정) |
| 2 | 배치 처리량 | PASS(live) — 별도 벤치 n=15 |

Row 1 rests on a single reading, but row 2's unrelated `n=15` silences the whole
scan. A verification anchor is exactly a multi-row table, so this is the shape
the hook most often sees.

This is left as-is deliberately, and for exactly one reason: **narrowing
suppression to the claim's neighbourhood raises the fire rate by an amount
nobody has measured.** The noise budget is what this hook's whole design is
spending, so it cannot be reopened on an estimate.

It is explicitly **not** left as-is for consistency with the siblings. A survey
of every hook impl in the repo found per-candidate scoping already established
on this axis: `caller-probe-gate` scopes its suppressor per write — the code
says so at `impl.py:373`, *"Arm A is per-write: a probe line in one body says
nothing about a claim published by another"*, and a regression test pins it
(`two writes: probe in write A does not clear write B (warn)`).
`merge-state-claim-gate` scopes per PR number via
`has_fresh_query_for_number(events, n)`. The only gate that scans the whole
body symmetrically is `perf-multiplier-evidence-advisory`, and it *detects*
over the whole body too, so it has no narrow/wide mismatch to begin with.

Per-candidate scoping is therefore the convention, and this hook diverges from
it. That is a debt this spec records rather than a design it defends. What the
pairing above buys is that the debt is executable: it is fixed by a test case,
so narrowing the suppressors later breaks that case and reopens the decision on
purpose instead of by accident.

## Relationship to the sibling hooks (duplication check)

| Hook | Surface | Predicate | Overlap |
| --- | --- | --- | --- |
| `count-assertion-verify` | `PreToolUse(Bash)`, the command itself | `grep -c` with regex alternation | none — different surface |
| `output-block-falsify-advisory` | `PreToolUse(AskUserQuestion)` + Bash bulk-action | `(Recommended)` without `Falsified:` | none — no sample-size predicate anywhere in its impl or spec |
| `perf-multiplier-evidence-advisory` | same surface as this hook | multiplier/lever verdict **without** a controlled-timing artifact | adjacent, inverted. Its pass condition is the presence of a `$ cmd -> output` timing artifact — which an n=1 measurement produces, so it goes silent on exactly the case this hook targets |

Extending the perf hook would have meant inverting its pass condition inside the
same hook, which is two predicates in one gate. Hence a separate hook on a
shared surface, reusing its body-extraction and fail-open conventions verbatim.

## Body-text extraction

Identical to `perf-multiplier-evidence-advisory`:

- `--body "..."` / `-b "..."` / `--body=...` — inline value from the parsed argv
  (`shlex.split`).
- `--body-file <path>` / `-F <path>` / `--body-file=<path>` — read from disk,
  relative paths resolved against the hook process's cwd. An unreadable target
  silently yields no body text for that invocation.

## Advisory message

```text
[n1-quantitative-claim-advisory] this deliverable states a sample-dependent
quantitative claim (a percentile / central-tendency figure, or a PASS-attached
measurement) with no sample size above 1 anywhere in the body.
  Rule (CLAUDE.md, Falsification Gates): the termination condition is 'cheap
  evidence is exhausted', not 'my claim is defensible'.
  Cheapest unexecuted broadening probe: re-run the SAME measurement command
  N>=5 times and report the distribution, not the single reading — no new
  tooling, no new environment, same command.
  Reference: issue #949 ...
  Opt-out: include `sample-size-noted` in the body once the sample size is
  deliberate and stated.
```

Emitted to stderr, exit code `0`. Never blocks.

The "cheapest unexecuted broadening probe class" named in the message is
deliberately generic — repeating the *same* command is the cheapest broadening
move for any single measurement, requiring no new tooling and no new
environment. The hook cannot know which command produced the figure.

## Fail-open contract

| Condition | Behavior |
| --- | --- |
| Malformed / missing stdin JSON | exit 0, silent |
| `tool_name != "Bash"` | exit 0, silent |
| Missing / empty `command` | exit 0, silent |
| No `gh issue\|pr create\|comment` invocation | exit 0, silent |
| `shlex` parse error (unbalanced quote) | exit 0, silent |
| `--body-file` unreadable | exit 0, silent for that invocation |
| Any uncaught exception | exit 0 (`@fail_open`) |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_n1_quantitative_claim_advisory.sh
```

Covers the issue's four required fixtures (instance 1 fires; instances 2 and 3
do not; the n=15 follow-up does not), the enumerated input surface
(word-boundary collisions, Korean particles, thousands separators, flag forms,
fail-open paths), and a **self-application pair**: this spec posted verbatim is
silent, and the same text with its opt-out marker stripped fires. The second
half is the negative control — without it, the silence could not be
distinguished from a dead detector.
