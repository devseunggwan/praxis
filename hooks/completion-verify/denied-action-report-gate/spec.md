# Stop Denied-Action Report Gate

`hooks/completion-verify/denied-action-report-gate/impl.py` runs on `Stop` and
`SubagentStop`. It fires when a tool call was **structurally refused during this
turn** and the final assistant message never says so.

Supported hosts: all

## Why this exists

Issue #1392. `retrospect-mix-check` Gate-12 (#1013) already holds this rule, and
states the mechanism better than a restatement would: a refused action "has no
outcome, so it leaves no error, no correction and no confession, and selection
by ease of recall never reaches it". Everything else in a session leaves a trace
that pulls it back into the report. A refusal leaves none.

But Gate-12 keys on the retrospect Stage-3 report's structural fences, so it is
reachable only from inside a retrospect. An ordinary closing report — the far
more common surface — is uncovered, and the always-loaded rule that failures are
ranked by damage rather than by comfort has no gate behind it there.

`PreToolUse` cannot see in-flight assistant prose, so `Stop` is the only surface
where an omission from the final report is observable.

## What is detected

No judgement about meaning is made at any point.

| Step | Oracle |
| ---- | ------ |
| Was something refused? | `_transcript.scan_user_rejections` — `toolDenialKind`, `is_error: true`, and the runtime's fixed refusal sentence, three co-agreeing markers |
| Was it refused *this turn*? | the rejection's `tool_use_id` appears among the `tool_result` ids in `load_stop_turn(payload)` |
| Is it in scope? | `tool_name` is not `AskUserQuestion` |
| Did the report own it? | the final message carries acknowledgement vocabulary, or names the refused tool |

### Turn scoping, and why the cursor cannot supply it

`scan_user_rejections` is resumable, but resumability is about **cost**, not
scope: `scan_transcript_resumable` persists the reducer state beside the byte
offset, so a cursored scan still answers for the whole session. Firing on a
refusal from fifty turns ago would be the false-positive shape the sibling
`negative-existence-verdict-gate` measured — 462/777 sessions (59.5%) for its
broad v1, against 19/1773 (1.1%) for the narrow form that shipped. The turn's
own `tool_result` ids are the scoping key, and they need no state file.

### Two designs this replaces

Both were falsified by measurement against 60 recent session transcripts
(14 refusals) during the mandated input-surface enumeration
(`praxis:surface-enumeration`, required because this is a classifier). They are
recorded so they are not re-derived.

```text
rejections=14  AskUserQuestion=7 (50%)  other=7
naive design (token containment, no exclusion):  FIRE 6/14 — 5 of them false
revised design (ack vocabulary, exclusion):      FIRE 3/60 sessions = 0.05/session
```

1. **Treating a refused `AskUserQuestion` as a denied action.** It is half the
   corpus, and it is not this class at all: the user dismissed the menu and
   answered in their own words, so nothing was prevented and nothing goes
   unreported. Every one of those rows would have been a false fire.
2. **Testing for the mention by identifier containment** — the refused call's
   tool name or the identifier tokens of its input appearing in the report. It
   was wrong in both directions on the same corpus: it cleared rows on
   incidental overlap between a refused shell command and unrelated prose, and
   it missed every report that owned the refusal in plain Korean
   (`취소하고 정리하겠습니다`) without repeating an identifier. What a report
   that owns a refusal actually carries is a word for the refusal.

Measurement caveat: the corpus proxy read the first assistant prose *after* each
refusal, while the hook reads the turn's final message. The two coincide when
the refusal ends the turn, which is the common case, but the rate above is an
estimate rather than a firing measurement. Revisit it once
`bypass-review fire-rate` has a window on this hook.

### Acknowledgement vocabulary

`거부` · `거절` · `차단` · `반려` · `미승인` · `승인` · `취소` · `중단` · `철회` ·
`보류` · `멈추` · `안 했` · `못 했` · `denied` · `deny` · `rejected` · `reject` ·
`blocked` · `refused` · `declined` · `cancelled` · `aborted` · `stopped` ·
`skipped` · `not approved` · `permission`; or the refused tool's name.

The list is deliberately generous. Both errors are possible, and they are not
symmetric: a missed omission costs one unfired advisory, while a false fire adds
noise to every clean turn across a 100-hook suite. Where the two are in tension,
this gate stays quiet.

## What is emitted

| Tier | Condition | Shape |
| ---- | --------- | ----- |
| advisory | default | stdout `{"systemMessage": ...}`, exit 0 |
| block | `PRAXIS_DENIED_ACTION_STRICT=1` | stdout `{"decision": "block", "reason": ...}`, exit 0 |
| silent | `PRAXIS_DENIED_ACTION_BYPASS=1` | no output, exit 0 |

**Why advisory by default, and what that costs.** `hooks/_lib/_hook_io.py`
records that a Stop advisory is "Shown to the user in the transcript; does NOT
block the stop and is **NOT fed to the model**", while `block` feeds its `reason`
"to the model so it can self-correct". So the default tier reaches the *user*,
not the actor — the channel failure issue #1265 is open on.

The firing rate above is an estimate from a proxy, and an estimated rate is not
shipped as a block to every installer. The strict env is what resolves that: the
default stays reversible for everyone, and a session that wants the finding to
reach the model opts in.

The advisory body leads with an English line, as every emitted body must
(`tests/test_emit_english_lead.py`).

## The indeterminate branch

`scan_user_rejections` returns `None`, distinct from `[]`, when the scan did not
reach the end of the file within its byte budget (20 MB per call) — issue #1231:
"the bound is hit by long sessions, and a long session is where standing
refusals accumulate". Folding it into `[]` would silence the gate exactly where
it carries the most.

So `None` emits its own message — but only when this turn actually contains an
errored `tool_result`, a necessary condition for any refusal. Without that gate a
session catching up after a resume would announce an indeterminate scan on turns
that plainly had nothing to refuse.

## Parsing guarantees (fail-open)

| Condition | Behaviour |
| --------- | --------- |
| malformed / non-dict payload | exit 0, silent |
| `stop_hook_active` true | exit 0, silent (no re-entrant re-fire) |
| transcript missing or unreadable | exit 0, silent — no path, no oracle |
| refusal with an unresolvable `tool_name` | still counted; reported as `?`, and the `AskUserQuestion` exclusion cannot apply to it |
| any unexpected exception | `@fail_open`, exit 0 |

The transcript is written asynchronously and may lag, so the final text comes
from the payload's `last_assistant_message` first (`stop_last_assistant_text`).
On `SubagentStop` the payload carries both the parent and the subagent
transcript; `resolve_stop_transcript` picks the right one and `load_stop_turn`
drops sidechain markers itself.

## Relationship to sibling hooks

| Hook | Overlap |
| ---- | ------- |
| `retrospect-mix-check` Gate-12 | same rule and same oracle, but reachable only inside a retrospect's Stage-3 fences. This gate is the ordinary-report half; neither subsumes the other |
| `rejected-mutation-reconsent-gate` | the same `scan_user_rejections` oracle, but `PreToolUse`: it blocks *re-attempting* a refused mutation. Different surface, different claim |
| `negative-existence-verdict-gate` | source of the narrow-trigger discipline the turn scoping implements |

## Tests

`tests/hooks/completion-verify/test_denied_action_report_gate.py`
