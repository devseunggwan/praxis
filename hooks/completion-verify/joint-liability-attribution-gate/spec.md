# Stop Joint-Liability Attribution Gate

`hooks/completion-verify/joint-liability-attribution-gate/impl.py` runs on
`Stop` and `SubagentStop` and scans the **first paragraph** of the final
assistant message for cross-session blame attribution used as the report's
opening move.

Supported hosts: all

## Why this exists

Issue #1391. The ruleset forbids opening a report by attributing a fault to
another session: attribution may be stated **once, as a routing fact**, never as
the opening move, and never as the reason an item goes unhandled. Whichever
session receives the report owns the fix until it hands it to a named owner.

In the motivating session that rule was broken in the **first sentence of the
first report**, and four further prose rules went the same way in the following
turns. The rule text was in context the whole time, so this is a retrieval
failure, and nothing was positioned to catch it — no hook existed, and the rule
is not named in `ETHOS.md`.

`PreToolUse` cannot see in-flight assistant prose, so `Stop` — which sees the
final output — is the only surface where an opening move is observable.

## What is detected

A conjunction of two lexical axes **inside the first paragraph**, plus one clear.
No judgement about meaning is made at any point.

| Axis | Matches |
| ---- | ------- |
| Subject referent | `세션` · `워크트리` · `에이전트` · `session` · `worktree` · `agent` |
| Non-ownership | `아닙니다` · `아니라` · `아닌` · `없어서` · `없습니다` · `없었` · `담당이` · `not mine` · `other` · `another` · `different` · `someone else` |
| **Clear** | the **most recent user message** asks for routing (`누가` · `어느 세션` · `담당` · `who` · `which session` · `routing`) |

Either axis alone is ordinary prose, which is why the conjunction carries the
test. The clear implements the rule's own carve-out — attribution is permitted
when the user asks for it — and mirrors `block-ask-end-option`, which likewise
clears only on a signal in the most recent user message.

**Position is the whole discriminator.** The rule permits the identical sentence
later in the report as a routing fact, so a whole-message scan would be the
false-positive generator the sibling `negative-existence-verdict-gate` already
measured: its broad v1 fired on 462/777 sessions (59.5%), the shipped narrow
form on 19/1773 (1.1%), and only the second made a hard tier affordable.

### First-paragraph resolution

Blocks are split on a blank line, `\r\n` normalised first. Leading blank blocks,
fenced code, and blocks consisting only of ATX headings are skipped — a report
that opens with `### Status` makes its first claim in the block after it. The
first surviving block is the opening move; a message with none is a pass.

## Two designs this replaces

Both were falsified against the motivating paragraph itself during the mandated
input-surface enumeration (`praxis:surface-enumeration`, required because this
is a classifier). They are recorded so they are not re-derived.

Measured against `이 세션에는 제가 한 작업 기록이 없어서 … 다만 다른 세션 소행이든
제 소행이든 처리는 여기서 하겠습니다`:

```
A. matched by the ruleset's forbidden-phrase list: False
B. matched by the loose substring "제 세션":        False
C. finding/fix vocabulary in the same paragraph:    ['처리']   → would have cleared it
```

1. **Keying on the ruleset's list of forbidden phrases.** The rule prints
   `제 세션에서 한 작업이 아닙니다` and three siblings as illustrations. The
   sentence that actually shipped shares no phrase with any of them and does not
   even contain `제 세션` as a substring. Lifting a rule's examples into a
   matcher catches the examples, not the behaviour.
2. **Clearing on finding/fix vocabulary in the same paragraph.** The violating
   paragraph asserts ownership *and* attributes in the same breath — that
   co-occurrence is the violation's normal shape, so treating fix vocabulary as
   exculpatory inverts the test.

This is the same failure `negative-existence-verdict-gate` records for its own
v1: "동기 사례 못 잡음".

## What is emitted

| Tier | Condition | Shape |
| ---- | --------- | ----- |
| advisory | default | stdout `{"systemMessage": ...}`, exit 0 |
| block | `PRAXIS_JOINT_LIABILITY_STRICT=1` | stdout `{"decision": "block", "reason": ...}`, exit 0 |
| silent | `PRAXIS_JOINT_LIABILITY_BYPASS=1` | no output, exit 0 |

**Why advisory by default, and what that costs.** `hooks/_lib/_hook_io.py`
records that a Stop advisory is "Shown to the user in the transcript; does NOT
block the stop and is **NOT fed to the model**", while `block` feeds its `reason`
"to the model so it can self-correct". So the default tier reaches the *user*,
not the actor — which is the channel failure issue #1265 is open on.

Shipping at block by default would put an unmeasured false-positive rate in
front of every installer, and the false-positive rate of this trigger is not yet
known. The strict env is what resolves that: the default is reversible for
everyone, and a session that wants the gate to reach the model opts in. Revisit
the default once `bypass-review fire-rate` has a window on it.

The advisory body leads with an English line, as every emitted body must
(`tests/test_emit_english_lead.py`).

## Parsing guarantees (fail-open)

| Condition | Behaviour |
| --------- | --------- |
| malformed / non-dict payload | exit 0, silent |
| `stop_hook_active` true | exit 0, silent (no re-entrant re-fire) |
| transcript missing or unreadable | still evaluates `last_assistant_message`; the routing clear is skipped |
| any unexpected exception | `@fail_open`, exit 0 |

The transcript is written asynchronously and may lag the current turn, so the
final text comes from the payload's `last_assistant_message` first
(`stop_last_assistant_text`). On `SubagentStop` the payload carries both the
parent and the subagent transcript; `load_stop_turn` resolves that and drops
sidechain markers itself, and `resolve_stop_transcript` supplies the path used
for the most-recent-user-message clear.

## Relationship to sibling hooks

| Hook | Overlap |
| ---- | ------- |
| `caller-probe-gate` | also a blame axis, but `PreToolUse(Bash)` on external-write bodies, and it blames *code* (a path or symbol), never a session |
| `negative-existence-verdict-gate` | same paragraph-scoped conjunction shape; different claim class |
| `block-ask-end-option` | source of the most-recent-user-message clear convention |

## Tests

`tests/hooks/completion-verify/test_joint_liability_attribution_gate.py`
