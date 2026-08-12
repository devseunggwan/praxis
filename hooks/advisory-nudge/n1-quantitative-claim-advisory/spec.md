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
