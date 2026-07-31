# Stop Negative-Existence Verdict Gate

Supported hosts: all

`hooks/completion-verify/negative-existence-verdict-gate/impl.py` runs on the
`Stop` event. It scans the final assistant message for a **negative-existence
verdict surfaced under a registered decision/gate framing without an
`Enumerated:` line**, and blocks the stop (default) so the model re-surfaces
the verdict with the candidates it actually covered.

## Why this exists

`#346` added a **critic pre-lock probe gate** to codex-review-wrap Step 5g: a
critic surfacing "X does not exist / is fabricated / is unused" must cite a
live `Probe: <command> → <output>` first. The blind spot is the **critic role
only** — the identical failure in the agent's **own** verdict is gated by
nothing.

**Motivating case (Hub #3981).** An acceptance criterion read (verbatim):

```text
- [ ] SQL 파일 → 영향 테스트 매핑 규칙 실재 여부 확인 (없으면 후보 A 재설계 또는 중단)
```

The agent surfaced this verdict to the user:

> **게이트 결과가 나왔습니다 — 매핑 규칙이 없습니다.**

The evidence was a read of `dags/tests/laplace/templates/sql` (**plural**, 1
test). The real asset lives at `dags/tests/laplace/template/sql`
(**singular**, 37 tests) — a one-character directory-name difference. Accepted
as-is, the verdict would have discarded approach A entirely (the AC's kill
branch); it was retracted only by the agent's own later probe.

At failure time the whole prompt layer was loaded and still missed it
(`feedback_no_premature_negative_lock_on_corpus_slice`, the "Enumerate ≠
Exhaustive — no first-match-stop" CLAUDE.md rule, three more memory entries).
The control group in the same session — `output-block-falsify-advisory` —
blocked 4/4 on a missing `Falsified:` line. Prompt layer fails, hook layer
succeeds, both observed in one session. Text rules alone have proven
insufficient; a structural Stop-hook gate moves enforcement to the surface
site.

`PreToolUse` cannot see in-flight assistant prose (issue #487 A3); the verdict
here is declarative text (not an `AskUserQuestion`), so the `Stop` hook — which
sees the final assistant output — is the only surface that can intercept it.
This is the same surface as the sibling `merge-state-claim-gate` /
`completion-signal-gate`.

Reference: issue [#804](https://github.com/devseunggwan/praxis/issues/804)
(Refs #346).

## Design — narrowed on a 200-session simulation

The risk is **not** a negative claim in isolation. "Column `cost` is missing"
surfaces its own error on the next query. The risk is a negative verdict that
**answers a pre-registered decision question with a kill branch** — if that is
wrong, the whole approach is discarded, and nothing downstream re-checks it.

Three candidates were simulated over 200 laplace-dev-hub sessions:

| Candidate | Rule | Fires | Motivating case caught |
| --- | --- | --- | --- |
| v1 | negative marker + identifier + no enumeration probe in last 5 calls | 462/777 (59.5%, 2.3/session) | **No** — the case HAD a probe (it read the wrong dir) |
| v2 | "sufficient enumeration = broad search OR ≥2 root paths" | 605/717 (84.4%, 3.0/session) | — (rejected: adequate enumeration scope is claim-dependent and unknowable at this layer) |
| **v3 (this hook)** | negative marker **+** decision framing in the same paragraph | **19/1773 (1.1%, 0.10/session)** | **Yes** (`게이트 결과` match) |

v1/v2 are rejected in the issue body. The v3 firing rate — 1/23 of v1 — is
what makes a hard block affordable.

## What is detected

The trigger requires **both**, in the same `\n\n`-delimited paragraph:

### (a) Negative-existence marker

- KO substrings: `없습니다`, `존재하지 않`, `미구현`, `찾지 못했`
- EN substring (case-insensitive): `does not exist`

### (b) Registered decision / verdict framing

- KO substrings: `게이트 결과`, `게이트 판정`, `판정이 나왔`
- EN substrings (case-insensitive): `acceptance`, `ac #`

Each EN framing/marker still requires its counterpart in the same paragraph,
so a bare positive "Acceptance criteria met" cannot fire on its own.

### Removed in issue #901 — `확인 결과` / `검증 결과` / `완료 조건`

The proposal listed these as "verification/confirmation result …없" framings,
with the trailing "…없" carried by the separate marker requirement. Live
deployment showed the marker co-occurrence requirement is not a sufficient
discriminator for them. Applying this hook's own detector to a 30-day
transcript corpus:

| framing token | fires |
| --- | --- |
| `확인 결과` | 7 |
| `완료 조건` | 4 |
| `게이트 결과` / `게이트 판정` / `판정이 나왔` | **0** |

7 of 8 sampled fires were incidental substring matches, not verdicts —
`확인 결과, 정리할 부분 없습니다.` (an ordinary answer), `"미구현 항목 — 닫지
마세요" 절을 … 교체` (a changelog of an issue-body edit), `… 자기완결형
(존재하지 않는 이슈 참조 없이)` (an adverbial phrase). `확인 결과` is everyday
Korean for "upon checking" and appears in any investigative prose; it does
not mark a *registered decision*. Meanwhile the tier the hook was designed
for never fired at all. Removing the three eliminates every observed
false positive while the motivating case (`게이트 결과가 나왔습니다 — 매핑
규칙이 없습니다`) still triggers on `게이트 결과`.

## The requirement — presence enforcement, not adequacy verification

A probe-existence check is **unusable**: the motivating case HAD a probe (it
read the wrong directory). Mirroring `output-block-falsify-advisory`'s
`Falsified:` line, this hook forces the verdict paragraph to contain a line
starting at column 0:

```text
Enumerated: <candidates actually covered> → <result of each>
```

It does **not** judge whether the enumeration was adequate — only that the
line exists with non-empty content after the colon (`^Enumerated:\s*\S`). The
single-candidate fact then sits visibly next to the kill-branch verdict.
Placement is paragraph-scoped: the marker, the framing, and the satisfying
`Enumerated:` line must all live in the **same** verdict paragraph (an
`Enumerated:` line in an unrelated paragraph does not clear the verdict).

## Honest limitation

Forcing the `Enumerated:` line is **not guaranteed** to have caught the
motivating case. Writing `Enumerated: .../templates/sql` does not automatically
reveal the singular-form twin directory. This mechanism is **surfacing
enforcement, not verification**, and its efficacy against this specific
typo-class error is unproven. What it does provide: (a) the trigger catches
the case, (b) the single-candidate enumeration is made visible next to the
kill branch, and (c) the same mechanism blocked 4/4 on the adjacent
`Falsified:` problem in one session.

## Tiers

| Condition | Decision |
| --- | --- |
| Triggering verdict, no `Enumerated:` line (default) | `{"decision": "block"}` — re-prompts the model |
| Same, with `PRAXIS_NEGATIVE_EXISTENCE_ADVISORY=1` | `{"systemMessage": ...}` — non-blocking advisory |
| Triggering verdict WITH a valid `Enumerated:` line | Silent pass |
| Marker XOR framing (only one present) | Silent pass |
| Marker + framing in different paragraphs | Silent pass |
| `PRAXIS_HOOK_BYPASS_NEGATIVE_EXISTENCE_GATE=1` | Silent pass (full bypass) |

Default is **block** (not advisory): the issue's v3 design was built
specifically to justify a hard block at 0.10 fires/session, and the
`Falsified:` precedent (`output-block-falsify-advisory` T1) is a hard deny.
`PRAXIS_NEGATIVE_EXISTENCE_ADVISORY=1` demotes to advisory for callers who
want the nudge without the block.

## Response shape

Block (issue #647 H3 — completion-verify Stop hooks signal via stdout JSON,
exit 0; the block is carried by the `decision` field, not the exit code):

```json
{"decision": "block", "reason": "[negative-existence-verdict-gate] ..."}
```

Advisory (`PRAXIS_NEGATIVE_EXISTENCE_ADVISORY=1`):

```json
{"systemMessage": "[negative-existence-verdict-gate] ..."}
```

**Exit code:** `0` in every case.

## Fail-open contract

| Condition | Behavior |
| --- | --- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| Missing / unreadable / empty transcript | exit 0 (silent pass) |
| No last assistant text | exit 0 (silent pass) |
| `stop_hook_active=true` | exit 0 (re-entrancy guard) |
| Any uncaught exception | exit 0 (`@fail_open` on `main()`) |

This is a standalone Stop hook (not a `(PreToolUse, Bash)` dispatch-group
member), so `main()` carries the `@fail_open` decorator directly per DESIGN.md
(issue #645, enforced by `check-plugin-manifests.py` Rule 16). No external
dependencies — standard library only.

## Tests

```bash
bash tests/hooks/completion-verify/test_negative_existence_verdict_gate.sh
```

28 cases: F5 motivating-case regression (block without / pass with an
`Enumerated:` line), the issue's false-positive-regression verdict
(`게이트 결과 … gmail OAuth 는 없습니다`), marker-XOR-framing negatives,
marker+framing in different paragraphs, EN `does not exist` / `Acceptance` /
`AC #` tokens, other KO markers (`미구현`, `찾지 못했`) and framings
(`게이트 판정`, `판정이 나왔`), the three framings removed in #901 asserted
silent (`확인 결과`, `검증 결과`, `완료 조건`), the Hub #3981 verdict asserted
still-blocking under the narrowed set, `Enumerated:` edge cases (empty after colon,
inline/indented, multi-paragraph partial coverage), advisory demote, bypass
env, `stop_hook_active` loop guard, and fail-open (missing transcript,
malformed JSON, empty payload).
