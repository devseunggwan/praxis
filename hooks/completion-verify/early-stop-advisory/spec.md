# Stop Early-Stop Advisory

Supported hosts: all

`hooks/completion-verify/early-stop-advisory/impl.py` fires on the Stop event
and advises when the turn's last assistant message ends the turn while the
requested work still looks open.

## Why this exists

The Opus 5.5 prompting guide
([§ Unattended agentic runs](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5#unattended-agentic-runs))
names four ways a model ends its turn with requested work still owed:

1. a summary that closes by announcing the next step, with no tool call;
2. an offer to carry on unless the user would prefer otherwise;
3. a list of decisions for the user when none of them blocks the rest of the
   work;
4. stopping to report because the turn was long or a milestone is done.

Before this hook, praxis reacted to type 2 only when it went through
`AskUserQuestion` (`block-manufactured-action-menu`, `block-ask-end-option`)
and to type 3 only as a prose menu (`prose-option-menu-advisory`). Types 1, 4,
and 2-as-prose reached Stop with no hook reacting: issue #1498 ran all 18
Stop hooks on synthetic turns of each type (2026-09-25) and none produced
output for them.

## Decision predicate

The text graded is the payload's `last_assistant_message` (falling back to the
transcript's last main-chain assistant message) with fenced code blocks and
`>` quote lines removed. The **closing lines** are its last three non-empty
lines, split into sentences.

Advise when one of these holds, checked in this order:

| Type | Condition | Examples that match |
| ---- | --------- | ------------------- |
| 2 (prose) | A closing sentence makes continuing conditional on the user's preference **and** names continuing the requested work | `원하시면 남은 /payments도 이어서 진행하겠습니다`, `나머지도 계속 진행할까요?`, `If you'd like, I can continue with the remaining …`, `Want me to keep going with the rest?` |
| 1 | A closing sentence is a first-person future **and** carries a next-step cue | `다음 단계로 남은 /payments를 마이그레이션하겠습니다`, `이제 /payments를 옮길게요`, `Next, I'll migrate the remaining …` |
| 4 | The message frames itself as an interim or milestone report **and** some line lists an unfinished item that is not negated | `## 중간 보고` … `- payments: 미착수`, `## Progress update` … `- payments: not started` |

Vocabulary, Korean and English:

- Preference condition: `원하시면`, `필요하시면`, `괜찮으시면`, `…할까요`
  (`진행할까요`, `드릴까요`, …), `if you'd like/want/prefer`,
  `unless you'd prefer`, `want me to`, `shall I`, `should I`,
  `would you like me to`, `let me know if you'd like`.
- Continuation cue: `이어서`, `이어가`, `계속`, `남은`, `나머지`, `마저`,
  `continue`, `proceed`, `carry on`, `keep going`, `remaining`, `rest of`,
  `finish`. An offer of something *new* after the work is done
  (`원하시면 PR 설명도 작성해 드릴게요`) carries none of these and stays silent.
- First-person future: `…겠습니다`, `…겠어요`, `…ㄹ게요` (any syllable with a
  ㄹ final before `게요`), `I'll`, `I will`, `I'm going to`, `let me now`.
- Next-step cue: `다음 단계`, `다음으로`, `이제`, `이어서`, `계속`, `남은`,
  `나머지`, `next`, `now`, `then`, `continue`, `remaining`, `rest of`.
- Not a next step: `다음에는`, `다음번`, `다음 세션`, `다음 PR`, `후속`,
  `next time`, `next session`, `follow-up`, and closings (`마치겠습니다`,
  `않겠습니다`, `wrap up`, `won't`).
- Interim framing: `중간 보고`, `진행 상황`, `현재까지`, `지금까지`,
  `여기까지`, `이 시점에서`, `작업이 길어`, `마일스톤`, `progress update`,
  `status update`, `interim`, `checkpoint`, `so far`, `at this point`,
  `long run`.
- Unfinished item: `미착수`, `미완료`, `미반영`, `진행 중`, `남은 작업`,
  `남아 있`, `TODO`, `[ ]`, `not started`, `not yet`, `pending`,
  `remaining`, `in progress` — unless the same line negates it (`없습니다`,
  `nothing remaining`, `0 pending`).

Only the closing lines are read for types 1 and 2: the guide's type 1 is a
summary that *closes* on an announcement, and the same verb mid-report usually
narrates the order work was done in.

## Stops that stay silent

The guide keeps the stops the user wants — nothing can move without them, or a
blocker is deliberately protected. The hook is silent when:

- **A blocker is named** anywhere in the text: missing credentials, access or
  a token (`자격 증명`, `권한이 없`, `credentials`, `no access`), an approval
  or decision only the user can give (`승인이 필요`, `결정이 필요`,
  `needs your approval`, `once you provide`), or `blocked` / `waiting on` /
  `진행할 수 없`.
- **The user asked for the stop**: the human message that opened the turn
  (`read_last_user_message(human_only=True)`) asks for a report, a plan, or a
  pause (`진행 상황`, `현황`, `보고해`, `계획만`, `status`, `progress`,
  `make a plan`, `one at a time`, `step by step`), or is a question — an
  interrogative word (`왜`, `어떻게`, `why`, `how`, …) on a line with a
  question ending. A request phrased with `?` but no interrogative word
  (`마이그레이션 해줄 수 있어?`) is still a request.
- **The text is a type-3 menu**: `prose-option-menu-advisory`'s own
  `is_prose_menu` predicate, loaded from its `impl.py` by file location, holds.
  That hook owns decision menus; reusing its predicate rather than a copy keeps
  the two disjoint even when its vocabulary changes. A menu that also closes on
  a next-step announcement goes to the menu hook alone.
- **`stop_hook_active` is set** — the host's cap on automatic continuations.
- `PRAXIS_EARLY_STOP_BYPASS` is set to any non-empty value.

## Output

Advisory only: `{"systemMessage": ...}` on stdout, exit 0. It never blocks.
The message follows the guide's continuation pattern — it names the type
detected and quotes the line, then asks to continue with the open items or
state in one line what blocks them:

```text
[early-stop-advisory] This turn ended while requested work still looks open — a next step announced but not taken:
  "다음 단계로 남은 `/payments` 엔드포인트를 마이그레이션하고 테스트를 갱신하겠습니다."
  If nothing blocks the open items, continue with them instead of stopping. If something does (missing credentials or access, an approval or a decision only the user can make), state that blocker in one line. Bypass: PRAXIS_EARLY_STOP_BYPASS=1
```

A fire records one `advise` row in the fire ledger.

## Why advisory rather than a block

Every marker is also written by a turn that finished correctly, and a blocker
the model did not name reads the same as no blocker. A block forces a
continuation, which would override exactly the stops the guide says to keep. So
the hook names what looks open and leaves the decision to continue with the
reader.

## Measured corpus

None yet. No local transcript corpus was available when the hook was written,
so there is no fire rate or sampled precision; the fixture suite in
`tests/hooks/completion-verify/test_early_stop_advisory.sh` is the only
evidence. Replay it over `~/.claude*/projects/*/*.jsonl` before the
`review_by` audit.

## Relationship to the sibling hooks

- `completion-verify/prose-option-menu-advisory` — type 3, prose. Disjoint by
  construction (see above).
- `preflight-gate/block-manufactured-action-menu`,
  `preflight-gate/block-ask-end-option` — type 2 routed through
  `AskUserQuestion`; this hook covers the prose form, which emits no tool
  event.
- `completion-verify/completion-signal-gate` — the inverse claim: a message
  that says *done* without evidence. This hook reads a message that says, in
  effect, *not done yet* and stops anyway.

## Fail-open

Malformed or missing stdin, an unreadable or absent transcript, no last
assistant text, `stop_hook_active`, and any uncaught exception all exit 0 with
no output. A failed load of the sibling's predicate makes this hook silent
rather than risk a double fire; the fixture suite's must-fire cases then fail,
so that breakage is caught in CI.
