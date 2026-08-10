# Role-skill regression answer key

Seeded-defect keys for the four delegating role skills (`critique`,
`audit`, `trace`, `interview`).

These fixtures are **inputs with known defects**, not unit tests. The skills
dispatch a language model, so the check is a graded comparison against the key
below, not an exact-match assertion. Run them when a skill's Role Block or
Output Contract changes.

The fixtures are written in Korean because these skills are exercised in Korean
sessions and the ambiguity/negation forms the roles must catch (`너무 자주`,
`자연스럽게`, `없음`) do not survive translation intact.

## How to run

For each fixture, invoke the matching skill with the fixture path as target and
this repository root as the codebase root. Grade the returned findings against
the key. A key item counts as detected when the finding names the same defect,
regardless of wording or severity label.

## critique.md — 6 seeded defects

| ID | Defect | Kind |
| -- | ------ | ---- |
| D1 | `820ms` and `62%` carry no measurement method or sample | unsourced premise |
| D2 | `scripts/manifest_cache.py` and `manifests.CheckAll()` do not exist (verified: 0 matches outside this fixture) | unresolvable reference |
| D3 | Phase 0 removes the cache; Phase 1 reads that cache's hit rate | phase contradiction |
| D4 | Completion criterion is a wall-clock 300ms; verification is a unit-test pass only | criterion with no oracle |
| D5 | Phase 3 (`docs/` mkdocs) does not serve the stated goal — and no mkdocs config exists | scope creep |
| D6 | Worker count fixed at 16 with no basis | magic number |

Pass: all six detected, verdict `REJECT`, falsification line present.

Bonus (not required): the target is unreachable by the plan's own arithmetic —
820ms x 62% = 508ms of checking, leaving 312ms of non-checking cost, so 300ms is
unreachable even at zero checking cost.

## trace.md — 5 key items

| ID | Item |
| -- | ---- |
| T1 | Root cause is the timezone boundary: `datetime.now()` yields a KST date while `created_at` is UTC, so a run before 09:00 KST queries a UTC date that has not started |
| T2 | Hypothesis A (pool exhaustion) refuted — zero connection-error logs in two weeks |
| T3 | Hypothesis B (3s timeout) refuted — a timeout raises rather than returning an empty set |
| T4 | Hypothesis C (missing index) refuted for this symptom — a slow query yields latency, not zero rows |
| T5 | Silent-failure path: `if not rows: return 0` with exit 0 suppressed the alert |

Pass: all five, verdict `ROOT CAUSE IDENTIFIED`, falsifying probe present.

## audit.md — 6 key items

| ID | Claim | Correct grade |
| -- | ----- | ------------- |
| V1 | Unit tests alone (claim 1) | `INSUFFICIENT` |
| V2 | Mock-only reproduction, self-confirming (claim 2) | `VOID` |
| V3 | Lint green bought with `# noqa` (claim 3) | `VOID` |
| V4 | "No other integration affected" with no scan scope (claim 4) | `VOID` |
| V5 | "Shorter path so faster" with no measurement (claim 5) | `VOID` |
| V6 | CI green as an aggregate, not a source (claim 6) | `INSUFFICIENT` |

Pass: all six graded at or below the grade above, verdict `RE-VERIFY`.

Bonus (not required): naming that the original symptom was never observed to
stop on the actual failing run.

## interview.md — 8 key items

| ID | Ambiguity |
| -- | --------- |
| A1 | "지표가 이상하면" — no definition of anomalous (threshold / delta / detection) |
| A2 | Which metrics are in scope |
| A3 | Who receives the alert (account / project / channel) |
| A4 | "너무 자주" — no frequency cap or dedup policy |
| A5 | Slack integration assumed to exist (workspace, auth, install path) |
| A6 | "자연스럽게" — no UI entry point |
| A7 | "다음 스프린트" — no absolute date, scope not sized against it |
| A8 | **No acceptance criterion — how anyone knows it succeeded** |

Pass: all eight, verdict `INTERVIEW REQUIRED`.

`A8` is the regression-sensitive item. It was the single key item that both
reference implementations missed during the skill's design, and the Role Block
carries an explicit instruction for it — a run that misses `A8` means that
instruction has regressed.
