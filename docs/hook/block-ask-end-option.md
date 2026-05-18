# PreToolUse AskUserQuestion End-Option Gate

Supported hosts: all

`hooks/block-ask-end-option.py` fires on every PreToolUse(AskUserQuestion)
event and inspects `options[].label` for end-option markers — both direct
("end here", "여기서 종료") and indirect ("take a break", "잠시 보류").
When a marker is found, the most recent user message in the transcript is
checked for an explicit stop signal. If no signal is present, the call is
blocked (default) or an advisory is emitted (opt-out mode).

### Why this exists

Skill guides authoring "Step N: chaining" sections frequently include an
"end here" boilerplate option. Agents mechanically transcribe this into
`AskUserQuestion` call sites even when the conversation has a clearly chained
intent or the user has expressed no desire to stop. This pattern has been
observed 6+ times in a single session, fragmenting decisions and ignoring
user intent.

Indirect phrasing ("take a break / prioritize other work", "pause for now",
"다른 작업 우선") emerged as a bypass when direct keywords are detected at
the option-label level. This hook detects both pattern classes so that the
spirit of the rule survives phrasing variation.

Text rules in CLAUDE.md or skill bodies alone cannot enforce this — the
`loaded != retrieved` limit. This hook enforces the rule at the tool
boundary, where the check runs mechanically regardless of retrieval state.

### What is blocked

| Scenario | Action |
|----------|--------|
| Default mode, direct end marker in any option label, no user stop signal | exit 2 (block) |
| Default mode, indirect end marker ("take a break" / "잠시 보류" etc.), no stop signal | exit 2 (block) |
| `PRAXIS_ASK_END_ADVISORY=1`, marker present, no stop signal | exit 0 + advisory stderr |
| `PRAXIS_ASK_END_STRICT=1` (deprecated), marker present, no stop signal | exit 2 (block) |
| Any tool name other than `AskUserQuestion` | silent pass-through |
| Marker present BUT user message contains a stop signal | silent pass-through |
| Missing / unreadable transcript | silent pass-through (graceful degrade) |
| No options match any end marker | silent pass-through |

### Detect patterns

#### Direct end-option markers (English)

- `end here`
- `session end`
- `stop here`
- `end the session`
- `wrap up here`

#### Indirect end-option markers (English)

- `take a break`
- `prioritize other work`
- `pause for now`
- `resume in a later session`
- `other work first`

#### Direct end-option markers (Korean)

Phrased forms:

- `여기서 종료`
- `세션 종료`
- `여기서 끝`
- `여기까지`

Heading-separator patterns (issue #236) — match a bare end-token only when
followed by a heading separator (` —` / ` -` / `:`), which excludes inflected
nouns like `종료된`, `마무리 방식`:

- `종료 —`, `종료 -`, `종료:`
- `그만 —`, `그만 -`, `그만:`
- `마무리 —`, `마무리 -`, `마무리:`

Bare `종료` / `그만` / `마무리` are intentionally **not** markers on the
option-label side: Korean productively inflects, and labels like
`종료된 이슈 목록` / `회의 마무리 방식 검토` / `종료 시각 기준` are
legitimate triage options. The asymmetry with `STOP_SIGNALS_KO` (which
does match these bare tokens in user prose) is intentional — option
labels are exactly where these noun forms cluster, while user messages
typically use phrasal stop signals.

#### Indirect end-option markers (Korean)

- `잠시 멈춰`
- `잠시 보류`
- `휴식`
- `다른 작업 우선`
- `다음 세션`

Bare `보류` is intentionally **not** a marker: substring match would
false-block legitimate labels such as `보류 중인 이슈 확인`. Use
`잠시 보류` for the session-pause-specific form.

All matches are case-insensitive substring checks against the option label.

### Mode and env var behavior

| Env var state | Mode | Exit code on match |
|---------------|------|-------------------|
| Neither var set (default) | **Strict** | 2 (block) |
| `PRAXIS_ASK_END_ADVISORY=1` | Advisory | 0 + stderr |
| `PRAXIS_ASK_END_STRICT=1` (deprecated) | Strict | 2 (block) |
| Both vars set | Strict (`STRICT` takes precedence) | 2 (block) |

`PRAXIS_ASK_END_STRICT=1` was the original strict-mode env var. It is
deprecated — the default is now strict without any env var. Set
`PRAXIS_ASK_END_ADVISORY=1` to opt out to advisory behavior. If
`PRAXIS_ASK_END_STRICT=1` is explicitly set, it forces strict regardless of
`PRAXIS_ASK_END_ADVISORY`.

### Stop signals (user message)

The hook walks the transcript in reverse to find the most recent human-authored
user message (skipping `tool_result`-only entries). Stop is detected when:

- **Korean** (substring match): `종료`, `여기까지`, `그만`, `마무리`, `스톱`, `중단`
- **English** (phrase match + negation guard): `stop here`, `stop now`,
  `let's stop`, `we're done`, `we are done`, `i'm done`, `i am done`,
  `end here`, `end now`, `end this`, `end the session`, `wrap up`,
  `wrap this up`, `that's all`, `that is all`, `no more`, `quit now`,
  `cancel this`, `finish here`, `finish up`, `session end`

Negation guard: a match preceded by `don't`, `do not`, `never`, `no`, `not`,
`won't`, `wouldn't`, `shouldn't`, `can't`, or `cannot` within 30 characters
is disqualified (prevents "don't stop" from being a stop signal).

### Response

Block response (exit 2):

```json
{
  "decision": "block",
  "reason": "AskUserQuestion includes an end-option without user stop signal..."
}
```

Advisory response (exit 0 + stderr message only — no JSON output):

```
[advisory] AskUserQuestion includes an end-option ...
```

### Tests

```bash
bash hooks/test-block-ask-end-option.sh
```

Covers: direct Korean/English end markers (block + advisory modes),
heading-separator KO end-tokens in option labels (issue #236 — `종료 —`,
`그만 —`, `마무리:`, etc.) including inflected-noun false-positive regression
(`종료된 이슈 목록`, `회의 마무리 방식 검토`, `종료 시각 기준`), indirect
English phrasing (take a break, prioritize other work, pause for now, resume in
a later session, other work first), indirect Korean phrasing (잠시 멈춰, 잠시
보류, 휴식, 다른 작업 우선, 다음 세션), 4-option padding pattern (4th
option only carries indirect marker), false positive avoidance (normal work
options, partial keyword matches that must not trigger), explicit strict env var
(deprecated compatibility), advisory opt-out via `PRAXIS_ASK_END_ADVISORY=1`,
graceful degrade on missing transcript, F1 regression (bare-word stop tokens in
neutral messages), F2 regression (tool_result-only user entries skipped when
walking backward for human text).
