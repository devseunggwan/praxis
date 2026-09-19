# Stop Denied-Action Report Gate

`hooks/completion-verify/denied-action-report-gate/impl.py` runs on `Stop` and
`SubagentStop`. It fires when a tool call was **structurally denied during this
turn** — refused by the user, or blocked by a `PreToolUse` hook or a permission
rule — and the final assistant message never says so.

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
| Was something denied? | `_transcript.scan_user_rejections(kinds=DENIAL_KINDS)` — `toolDenialKind` is `user-rejected` or `permission-rule`, and `is_error: true` |
| Is the record a real refusal? | for `user-rejected`, the runtime's fixed refusal sentence as well — three co-agreeing markers. `permission-rule` has no fixed sentence, so it agrees on two (below) |
| Was it refused *this turn*? | the rejection's `tool_use_id` appears among the `tool_result` ids in `load_stop_turn(payload)` |
| Is it in scope? | `tool_name` is not `AskUserQuestion` |
| Did the report own it? | the final message names the refused tool — or carries acknowledgement vocabulary, when this is the turn's only in-scope refusal |

### The second denial kind (issue #1422)

The runtime records a `PreToolUse` hook block, and a permission-rule denial, as
`toolDenialKind: "permission-rule"`. Before #1422 the scan read only
`user-rejected`, so a blocked call was invisible here. In the turn #1422
observed, a label gate blocked `gh pr create`, the input was rewritten and
re-run successfully, the closing report listed two *other* skipped steps and
never mentioned the block, and this gate stayed silent. The omission surfaced
only when a later retrospect replayed the transcript.

The two kinds leave the same hole for the same reason: the call had no outcome,
so there is no error to explain and no correction to narrate. That is the whole
premise of this gate, and it is indifferent to who did the refusing.

**Why the third marker is dropped for this kind, and only this kind.** A
`user-rejected` record carries a fixed runtime sentence; a `permission-rule`
record carries the *blocking hook's own prose*. The corpus below holds exactly
two shapes for it and no third:

```text
PreToolUse:Bash hook error: [<plugin>/hooks/_dispatch.sh PreToolUse Bash claude]: …
Permission to use Bash with command <cmd> has been denied.
```

Requiring a sentence across those would be a natural-language judgement, which
this scan makes nowhere. Two structural markers instead of three; the field that
separates the kinds is one the runtime writes, not one this hook infers.

**Why the blocking hook is not named in the message.** Issue #1422 proposed
parsing the hook's name, "which the dispatcher prefixes with the hook path". It
does not: the prefix is the *dispatcher's* path (`…/hooks/_dispatch.sh PreToolUse
Bash claude`), the same string for all ~100 hooks, and past it the message is
per-hook prose with no common field. There is no name to parse, so the existing
acknowledgement machinery is reused instead and the advisory names the tool.

**One behaviour change to the pre-existing class.** A turn holding one user
refusal *and* one hook block now has two in-scope candidates, so `sole` is false
and a bare acknowledgement word no longer clears the user refusal on its own —
each is then cleared only by its own tool name. That is the existing rule ("a
word has one referent") reaching a case it could not reach before, not a new
one, but it is a change and it is stated rather than left for a reader to find.

### Corpus measurement (issue #1422 asks for it)

Every local transcript, 877 files under `~/.claude-2/projects/*/*.jsonl`:

```text
denial records (both kinds)          : 2793
  excluded (AskUserQuestion)         : 72
  considered by the gate             : 2721
  unacknowledged by the turn's report: 1501
turns holding a considered denial    : 1158
  of those, turns that would fire    : 672  (58.0%)
sessions holding >=1 such turn       : 328  (37.4% of transcripts)
  permission-rule  considered=2436  unacknowledged=1437
  user-rejected    considered=285   unacknowledged=64
```

Read it as a population, not as a defect count: a turn "would fire" means the
report carried no acknowledgement, and whether each of those 672 is a genuine
omission was not read one by one. The proxy's turn boundary is the next *human*
user message, and its report is the last assistant text before it — which is
what `stop_last_assistant_text` hands the gate when the denial's turn ends
there, and an approximation otherwise. Same caveat as the #1392 estimate below,
at 20× the corpus.

Two numbers are worth keeping side by side. The new kind is 90% of the
population (2436 of 2721), which is why the gate could be right about its rule
and silent in practice. And 37.4% of sessions would carry at least one fire —
close enough to the 59.5% that forced `negative-existence-verdict-gate` to
narrow that the default tier stays advisory here rather than block.

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

**A bare word from that list clears only a turn whose refusal is the only one.**
A refusal word has one referent, so with two refusals in a turn "the push was
denied" accounts for the push and says nothing about the other call — reading it
as covering both would let a single acknowledgement retire every omission beside
it, which is the case this gate exists for. With two or more in-scope refusals,
each is cleared only by its own tool name. Naming the tool always clears, at any
count. The refused `AskUserQuestion` class is excluded before the count is taken,
so it cannot turn a sole real refusal into a multi-refusal turn.

Every turn in the measured corpus carried exactly one refusal (14 of 14), so this
rule leaves the estimated rate untouched; it closes a case the corpus never
reached rather than one it got wrong.

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
| `rejected-mutation-reconsent-gate` | the same `scan_user_rejections` oracle, but `PreToolUse`: it blocks *re-attempting* a refused mutation. Different surface, different claim — and it keeps the `user-rejected`-only default, because re-issuing a corrected call after a hook block is the intended recovery, not a reconsent case. That is why `kinds` is a parameter rather than a widening in place |
| `negative-existence-verdict-gate` | source of the narrow-trigger discipline the turn scoping implements |

## Tests

`tests/hooks/completion-verify/test_denied_action_report_gate.py`
