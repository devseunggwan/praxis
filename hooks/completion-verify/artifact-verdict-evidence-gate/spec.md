# Stop Artifact-Verdict Evidence Gate

Supported hosts: all

`hooks/completion-verify/artifact-verdict-evidence-gate/impl.py` runs on the
`Stop` event. It scans the final assistant message for a **positive-polarity
verdict about local artifacts, surfaced as a candidate list, without an
adjacent `Verdict-evidence:` line**, and emits a stderr-free stdout advisory
(default) so the model re-surfaces the list with the evidence it actually ran.

## Why this exists

`#804` added `negative-existence-verdict-gate` for the *negative* polarity —
"X does not exist" answering a pre-registered decision question. The mirror
image is ungated: a *positive* verdict about a local artifact ("this file is a
duplicate / can be deleted / was superseded"), surfaced as a candidate table,
where the judgement rests on a cheap proxy rather than the artifact's content.

**Motivating case (2026-07-26 memory-cleanup session).** The agent surfaced a
21-row table labelled verbatim `Tier A — 삭제 후보 (근거 확정)`. The basis per
row was a proxy, not the artifact:

| 판단하려던 속성 | 실제로 쓴 대리지표 | 반증에 필요했던 것 |
| --- | --- | --- |
| 두 메모리가 같은 내용인가 | `description` 유사도 | 본문 read |
| 서로 연결돼 있는가 | `grep '\[\['` | 본문 read (산문 참조를 못 봄) |
| 섹션이 중복인가 | 섹션 제목 + 줄 간격 | 섹션 본문 read |
| 위반 기록인가 | `grep '재발\|strike'` | 본문 read |

Two proposals were refuted by the files' own bodies — both carried an explicit
`Facet sibling (병합 아님)` decision recorded three months earlier, i.e. the
merge had already been considered and rejected. 17 of 21 rows had no executed
evidence behind a label that said `근거 확정`.

The prompt layer was fully loaded and still failed. The *Information
Accuracy* rule's Layer 3, **Author-exempt verification trap**
([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)), describes this case verbatim:

> applies to *own-authored content* (mapping tables, example blocks, default
> values, identifiers…). Plausibility from naming pattern, sibling-repo
> recall, or training-data prior ≠ verification.

## Why the existing fleet does not cover it

| Hook | Fires on | Why it missed |
| --- | --- | --- |
| `source-citation-probe-gate` | external-write bodies (PR/issue/Slack/Notion) | the table was in-conversation output |
| `output-block-falsify-advisory` | `AskUserQuestion` / `Bash` surfaces | the table was plain assistant text |
| `pre-output-falsification-gate` | AskUserQuestion evaluative options | same |
| `negative-existence-verdict-gate` | negative-existence verdicts | polarity is positive |
| `path-probe-gate` | Write/Edit path depth | not a path problem |

## Trigger

All three conditions must hold in one **unit** — a list-shaped paragraph
joined with its immediately preceding lead-in paragraph:

1. **Positive artifact-verdict marker** — `삭제 후보` / `삭제 가능` /
   `삭제 대상` / `중복` / `통합 후보` / `통합 대상` / `병합 후보` /
   `폐기 대상` / `승계`, or (case-insensitive) `duplicate` / `superseded` /
   `redundant` / `obsolete` / `safe to delete`
2. **Candidate / decision framing** — `후보` / `정리 대상` / `판정` / `티어`,
   or `candidate` / `tier`
3. **List shape** — a markdown table row, or two or more `-` / `*` bullets

The single-bullet and prose-only cases are deliberately below threshold: a
one-off mention is not a candidate list, and the verdict surfaces its own
error on the next probe.

Fenced blocks are stripped before the scan. They carry illustrations, and both
directions of that hole are real: a message *documenting* this hook quotes a
candidate table (false trigger), and a fenced `Verdict-evidence:` placeholder
would otherwise clear a genuine adjacent verdict (false clear).

### Unit scoping — divergence from the sibling

`negative-existence-verdict-gate` scopes the trigger to one paragraph. This
hook attaches **one** preceding paragraph, because the lead-in carries the
framing (`다음 두 메모리는 통합 후보입니다`) far more often than the list
itself does — single-paragraph scoping missed the exact shape this hook exists
for. Framing two or more paragraphs away is not treated as attached.

### Satisfying-line scoping — second divergence

The satisfying `Verdict-evidence:` line may sit in the triggering list
paragraph **or the one immediately after it**. Reason: the verdict here is
table-shaped, and a trailing non-table line inside the same `\n\n` block
breaks markdown table rendering. Adjacency is still enforced — an evidence
line elsewhere in the message does not clear the verdict.

## Requirement

Presence enforcement, **not** adequacy verification — mirroring the
`Enumerated:` precedent:

```text
Verdict-evidence: <command run this session> → <output>
```

Writing that line is what makes the empty rows visible at authoring time. It
is explicitly not a guarantee the judgement is right.

### Rejected alternative

Requiring "a `Read` of every judged path exists in the transcript" was
rejected on two grounds: the motivating session *did* read some of the files
(so probe-existence alone would have passed), and per-row path extraction from
free prose is unreliable enough to produce false blocks.

## Tiers

| Setting | Behaviour |
| --- | --- |
| default | advisory (`systemMessage`, non-blocking) |
| `PRAXIS_ARTIFACT_VERDICT_STRICT=1` | hard block (`{"decision": "block"}`) |
| `PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE` set to any non-blank value | full bypass (exit 0) |

Default is advisory rather than block: the trigger is text-pattern based and
has no fire-rate simulation behind it yet, unlike the `Enumerated:` precedent
whose 0.10-fires/session measurement is what made a hard block affordable.
Promotion to default-block is a separate call once fixture data accumulates.

The two env vars are parsed differently, each matching its own fleet
convention — `0` is **not** a universal off switch:

- `PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE` is **presence-based**, as in all
  six `completion-verify` bypass vars: any non-blank value bypasses, including
  `0` and `false`. Unset or blank to keep the gate.
- `PRAXIS_ARTIFACT_VERDICT_STRICT` requires the **exact value `1`**, as in the
  `_STRICT_ENV` of `merge-state-claim-gate` / `runtime-state-claim-gate` /
  `readonly-verify-deferral-gate`. Every other value keeps the default advisory.

## Fail-open contract

- Malformed / missing stdin JSON → exit 0
- Missing / unreadable / empty transcript → exit 0
- No last assistant text → exit 0
- `stop_hook_active=true` → exit 0 (re-entrancy guard)
- Any uncaught exception → exit 0 (`@fail_open`)

## Honest limitation

The gate cannot tell a genuine verdict from a passing mention that happens to
carry all three tokens, and it does not judge whether the cited evidence
actually supports the row. It makes the *absence* of evidence visible next to
the verdict; the reader still does the judging. The 2026-07-26 session's fifth
misjudgement — asserting an escalation was missing when the tracking issue
existed and had been closed `NOT_PLANNED` — is **out of scope**: that needs an
external-state oracle (`gh search`), not a transcript one.

## Tests

`tests/hooks/completion-verify/test_artifact_verdict_evidence_gate.sh` — 24
cases covering the four in-scope misjudgements as fixtures, the clearing arm
in both scopes, all three trigger conditions in isolation, lead-in attachment
and its two-paragraph limit, fenced blocks in both directions, English tokens,
both env-var parsings, and the fail-open paths.

Reference: issue #862. Sibling: `completion-verify/negative-existence-verdict-gate`.
