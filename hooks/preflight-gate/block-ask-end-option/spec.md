# PreToolUse AskUserQuestion End-Option Gate

Supported hosts: all

`hooks/preflight-gate/block-ask-end-option/impl.py` fires on every PreToolUse(AskUserQuestion)
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

Text rules in a `CLAUDE.md` or a skill body alone cannot enforce this — the
`loaded != retrieved` limit. This hook enforces the rule at the tool
boundary, where the check runs mechanically regardless of retrieval state.

### Agent-facing: when to surface an end option

The hook below enforces the rule mechanically at the tool boundary. This
section is the human-facing decision logic it backs — the reference an
agent consults when deciding whether an `AskUserQuestion` call should carry
an end option at all. Formerly this table lived in the author's always-loaded
`CLAUDE.md`; it moved here so the always-loaded surface can shrink
to a one-line pointer while the enforcement stays at the tool boundary.

Do **not** mechanically transcribe "end here" / "session end" / "여기서 종료"
boilerplate from skill guides into `AskUserQuestion` options without context
verification. **Agent-generated** unilateral end declarations ("이 세션을
여기서 마무리합니다" / "I'll wrap up here", "다음 세션에서 진행하세요" /
"continue in the next session") are forbidden. The same phrases carry the
opposite weight when they come from the **user**: `다음 세션` in the user's
most recent message is a recognized stop signal
([Stop signals](#stop-signals-user-message)) and legitimately permits an end
option — the prohibition is on the agent originating the language, not on
honoring it.

The stop-signal set and its scope are defined authoritatively in
[Stop signals (user message)](#stop-signals-user-message) below — this table
summarizes the decision, it is **not** a second signal list. The hook reads
only the **most recent** user message (not accumulated earlier context), and
recognizes Korean bare tokens (`종료`, `여기까지`, `그만`, `마무리`, `스톱`,
`중단`) but only English *phrases* (`stop here`, `we're done`, `cancel this`,
…) — bare English words (`stop` / `done` / `cancel`) are deliberately not
signals. Surface an end option only under a signal the hook actually
recognizes, or the call is blocked at the tool boundary.

| Context signal | Surface end option? |
| --- | --- |
| The **most recent** user message carries an explicit stop signal from the recognized set ([Stop signals](#stop-signals-user-message)) | OK to add as 4th option |
| 4+ step chained intent clearly established (e.g., worktree → planning → sub-issue → start work) | End surface ignores intent — **omit** |
| Natural next actionable step clearly exists in immediate context | Surface that step, or wait for explicit user direction |

**Skill-guide boilerplate handling:** even when a skill's "Step N — chaining"
section lists an "end here" option, this rule takes precedence (*Rule
Conflict Precedence — CLAUDE.md over Skills*, [`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)). Apply the
context table; omit or include based on signal.

**Exceptions (skills where ending IS the intent):** in issue-closing skills
(`close-hub-issue`, equivalent) and release / deploy completion skills,
ending is the legitimate terminal step. The hook has **no skill-context
detection** — it cannot see which skill is running — so an end option in
these flows is still blocked unless the most recent user message carries an
explicit stop signal. When ending is genuinely intended but no stop signal is
present, opt out for that call with `PRAXIS_ASK_END_ADVISORY=1`.

### What is blocked

| Scenario | Action |
| ---------- | -------- |
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
- `여기서 끝`
- `여기까지`

Heading-separator patterns (issue #236) — match a bare end-token only when
followed by a heading separator (` —` / ` -` / `:`), which excludes inflected
nouns like `종료된`, `마무리 방식`:

- `종료 —`, `종료 -`, `종료:`
- `그만 —`, `그만 -`, `그만:`
- `마무리 —`, `마무리 -`, `마무리:`
- `세션 종료 —`, `세션 종료 -`, `세션 종료:` (demoted from a bare phrased
  form by issue #922 — `세션` is an everyday noun for
  process/workspace/agent sessions in this tool environment, so bare
  `세션 종료` clustered in legitimate work labels like
  `중복 MCP만, 부모 세션 종료 후` and `21873 세션 종료 여부를 먼저 확인`;
  same false-positive risk the bare-token exception below already guards
  against)

Bare `종료` / `그만` / `마무리` / `세션 종료` are intentionally **not**
markers on the option-label side: Korean productively inflects, and labels
like `종료된 이슈 목록` / `회의 마무리 방식 검토` / `종료 시각 기준` /
`21873 세션 종료 여부를 먼저 확인` are legitimate triage options. The
asymmetry with `STOP_SIGNALS_KO` (which does match these bare tokens in user
prose) is intentional — option labels are exactly where these noun forms
cluster, while user messages typically use phrasal stop signals.

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
| --------------- | ------ | ------------------- |
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

Action-directive guard (issue #515): a small set of ambiguous phrases —
`wrap up`, `wrap this up`, `finish up`, `no more`, `we're done`,
`we are done`, `i'm done`, `i am done` — read as stop signals in isolation
but are routinely action directives when followed by an object + action
verb (`proceed`, `continue`, `implement`, `deploy`, `merge`, `run`, …)
within 80 characters. Such a match is NOT treated as a stop signal:
"I'm done with the analysis, proceed to implementation" and "wrap up the PR
tests and deploy" keep the end-option block. Termination-specific phrases
(`stop here`, `end the session`, `session end`, `that's all`, `quit now`,
`cancel this`) are excluded from this guard and stay unconditional.

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
bash tests/hooks/preflight-gate/test_block_ask_end_option.sh
```

Covers: direct Korean/English end markers (block + advisory modes),
heading-separator KO end-tokens in option labels (issue #236 — `종료 —`,
`그만 —`, `마무리:`, etc., plus `세션 종료 —` per issue #922) including
inflected-noun false-positive regression (`종료된 이슈 목록`,
`회의 마무리 방식 검토`, `종료 시각 기준`) and the issue-#922 real-world
false positives (`중복 MCP만, 부모 세션 종료 후`,
`21873 세션 종료 여부를 먼저 확인`), indirect
English phrasing (take a break, prioritize other work, pause for now, resume in
a later session, other work first), indirect Korean phrasing (잠시 멈춰, 잠시
보류, 휴식, 다른 작업 우선, 다음 세션), 4-option padding pattern (4th
option only carries indirect marker), false positive avoidance (normal work
options, partial keyword matches that must not trigger), explicit strict env var
(deprecated compatibility), advisory opt-out via `PRAXIS_ASK_END_ADVISORY=1`,
graceful degrade on missing transcript, F1 regression (bare-word stop tokens in
neutral messages), F2 regression (tool_result-only user entries skipped when
walking backward for human text).
