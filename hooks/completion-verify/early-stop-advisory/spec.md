# Stop Early-Stop Advisory

Supported hosts: all

`hooks/completion-verify/early-stop-advisory/impl.py` fires on the Stop event
when the turn's last assistant message ends the turn while the requested work
still looks open. In every session it shows the user a notice the model does
not receive. In an unattended run — the marker `PRAXIS_UNATTENDED=1` — it
blocks the stop instead, and the block's reason goes to the model, at most
twice per human turn (see [Output](#output) and
[Continuation cap](#continuation-cap)).

## Why this exists

The Opus 5.5 prompting guide's
[§ Unattended agentic runs](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5#unattended-agentic-runs)
gives an example system-prompt addition, "written for agents that run fully
unattended, where you want the model to keep working rather than stop to
report". That addition names four ways a turn ends "while work they asked for
was still owed":

1. a summary that closes by announcing the next step, with no tool call;
2. an offer to carry on unless the user would prefer otherwise;
3. a list of decisions for the user when none of them blocks the rest of the
   work;
4. stopping to report because the turn was long or a milestone is done.

The guide scopes that addition to unattended runs: "leave the addition out of
human-in-the-loop applications, where someone is there to answer." The same
section's harness advice is about "An unattended agent loop that treats such a
turn as the end of the task". The hook splits along that line:

- **The user notice fires in every session**, attended or not. It reaches only
  the user, who decides whether to reply "continue".
- **The block fires only with the unattended-run marker** (`PRAXIS_UNATTENDED`
  set to exactly `1`), which matches the guide's unattended scope. See
  [Unattended-run marker](#unattended-run-marker).

Before this hook, praxis reacted to type 2 only when it went through
`AskUserQuestion` (`block-manufactured-action-menu`, `block-ask-end-option`)
and to type 3 only as a prose menu (`prose-option-menu-advisory`). Issue #1498
ran the 18 Stop hooks on synthetic turns (2026-09-25): none reacted to T1
(type 1), T2 (type 2 in prose) or T4b (type 4). T4, the same milestone report
with the word `완료`, drew a `completion-verify` block only incidentally — it
read `완료` as a completion claim with the `28 passed` token unquoted, not
the milestone stop.

## Decision predicate

The text graded is the payload's `last_assistant_message` (falling back to the
transcript's last main-chain assistant message) with fenced code blocks, `>`
quote lines, and inline quoted spans (`"…"`, `“…”`, `‘…’`, `'…'`, `「…」`,
`『…』`) removed: a quoted plan step or user phrase is not the model's own
announcement. A straight `'` between two word characters (`I'll`) is an
apostrophe, not a quote mark.

The **closing lines** are the last three prose lines, plus up to ten short
`Key: value` status lines after them (`Tests: 28 passed`), split into
sentences. Only they are read for types 1 and 2: the guide's type 1 is a
summary that *closes* on an announcement, and the same verb mid-report usually
narrates the order work was done in.

Advise when one of these holds, checked in this order:

| Type | Condition | Examples that match |
| ---- | --------- | ------------------- |
| 2 (prose) | A closing sentence makes continuing conditional on the user's preference **and** names continuing the requested work, with no out-of-turn deferral | `원하시면 남은 /payments도 이어서 진행하겠습니다`, `남은 payments도 진행하면 될까요?`, `If you'd like, I can continue with the remaining …`, `I can continue with /payments if that works for you` |
| 1 | A closing sentence binds a next-step cue to a first-person future | `다음 단계로 /payments를 마이그레이션하겠습니다`, `이제 /payments를 옮길게요`, `/payments 마이그레이션 진행할게요`, `Next, I'll migrate /payments`, `I'll now migrate …`, `I'll tackle /payments after this`, `Next up: migrating /payments` |
| 4 | A sentence frames the message as an interim or milestone report **and** a different sentence lists an unfinished item that it does not negate | `## 중간 보고` … `- payments: 미착수`, `## Progress update` … `- payments: not started` |

How the cue binds in type 1:

- Korean: the cue precedes the future verb in the same sentence (`이제 …겠습니다`,
  `다음 단계로 …ㄹ게요`), or the verb is itself the next step (`진행하겠습니다`,
  `진행할게요`, `착수하겠습니다`).
- English: the cue sits right before the verb (`Next, I'll`, `now let me`),
  right after it (`I'll now`, `I'll next`), is the verb (`I'll continue`,
  `I'll proceed`), names the object within a few words (`I'll migrate the
  remaining …`, `I'll … after this`), or opens the sentence (`Next up:`,
  `Moving on to`).
- A reporting verb after `I'll` is not a next step: `summarize`, `recap`,
  `note`, `report`, `mention`, `point out`, `let you know`, `wait`. (`wait`:
  the guide gives that wait to the harness — "If something the model started
  is still running, such as a background command or a subagent, don't treat
  the task as done yet: wait for it to finish and return its output to the
  model as the next user message." — so "I'll wait for CI" is not read as an
  announced next step.)

Vocabulary examples, Korean and English. **These are examples; the regexes
in `impl.py` are the authority.**

- Preference condition: `원하시면`, `괜찮으시면`, `…할까요`, `…면 될까요`,
  `if you'd like`, `unless you'd prefer`, `want me to`, `shall I`,
  `if that works for you`.
- Continuation of the requested work: `이어서`, `계속`, `남은`, `나머지`,
  `마저`, `continue`, `proceed`, `keep going`, `remaining`, `the rest`,
  `finish the rest`. An offer of something *new* (`원하시면 PR 설명도 작성해
  드릴게요`, `I can also finish the changelog entry`) carries none and stays
  silent.
- First-person future: `…겠습니다`, `…겠어요`, `…ㄹ게요` (any syllable with a
  ㄹ final before `게요`), `I'll`, `I will`, `I'm going to`, `let me`.
- Next-step cue: `다음 단계`, `다음으로`, `이제`, `이어서`, `계속`, `남은`,
  `나머지`, `곧바로`, `next`, `now`, `then`, `remaining`, `after this`.
- Out of this turn (types 1 and 2): `다음에는`, `다음 PR`, `후속`, `앞으로`,
  `나중에`, `내일`, `next time`, `follow-up`, `from now on`, `later`,
  `tomorrow`. Closings (type 1): `마치겠습니다`, `않겠습니다`, `wrap up`,
  `won't`.
- Interim framing: `중간 보고`, `진행 상황`, `지금까지`, `이 시점에서`,
  `작업이 길어`, `마일스톤`, `progress update`, `status update`, `so far`,
  `at this point`, `long run`.
- Unfinished item: `미착수`, `미완료`, `진행 중`, `남은 작업`, `TODO`, `[ ]`,
  `not started`, `not yet`, `pending`, `still to do` — unless the same
  sentence negates it (`없습니다`, `nothing remaining`, `0 pending`), and not
  in the framing sentence itself (`So far the pipeline has not yet …` is one
  sentence and stays silent).

## Stops that stay silent

**The guide's wanted stops.** The addition names two: "the ones where nothing
can move without them, or where the thing blocking you is deliberately
protected from you." The hook approximates both with a blocker stated as a
need or a lack, anywhere in the text:

- a missing or needed credential, secret or access: `자격 증명이 필요`,
  `권한이 없`, `needs a staging DB password`, `no staging DB credentials`,
  `don't have access` (the English trigger and noun must share a clause,
  within 50 characters);
- a handover only the user can make: `비밀번호를 알려주시면`, `승인해 주시면`,
  `승인이 필요`, `결정이 필요`, `waiting on your approval`,
  `once you share/send/give/provide`;
- `blocked on/by`, `can't proceed`, `진행할 수 없`.

A bare word (`credentials table`, `waiting for the lock`) is not a blocker.

**praxis additions** (not from the guide):

- **The user asked for the stop**: the human message that opened the turn
  (`read_last_user_message(human_only=True)`) requests a report, a plan, or a
  pause (`진행 상황 알려`, `현황 알려`, `계획만`, `status of`,
  `give me a status`, `progress report`, `make a plan`, `one at a time`), or is
  a question — a word-initial interrogative (`왜`, `어떻게`, `why`, `how`, …)
  on a line with a question ending, or an English auxiliary-inversion question
  (`Are the endpoints done?`). Indefinites are not interrogatives
  (`어떻게든`, `언제나`), and `Can/Could/Will/Would you …?` is a request.
  A word inside a request (`fix the status field`, `결제 현황 API`) is not a
  status request.
- **The text is a type-3 menu**: `prose-option-menu-advisory`'s own
  `is_prose_menu` predicate, loaded from its `impl.py` by file location, holds.
  That hook owns decision menus; reusing its predicate rather than a copy keeps
  the two disjoint even when its vocabulary changes. A menu that also closes on
  a next-step announcement goes to the menu hook alone.
- **`stop_hook_active` is set, in notice mode** — the host's re-entry flag,
  true when this Stop follows a continuation that a Stop hook's block forced.
  Block mode does not read it; the [continuation cap](#continuation-cap)
  bounds it instead.
- `PRAXIS_EARLY_STOP_BYPASS` is set to any non-empty value.

## Output

Two modes. Which one applies is decided per fire, after every silence rule
above has passed.

### Notice mode (default, every session)

`{"systemMessage": ...}` on stdout, exit 0. It never blocks.

Per `hooks/_lib/_hook_io.py` (Stop-event emitters), a Stop `systemMessage` is
"Shown to the user in the transcript; does NOT block the stop and is NOT fed
to the model." So nothing here reaches the model or continues the run. The
notice names the type, quotes the line, and tells the user they can reply
"continue":

```text
[early-stop-advisory] The turn ended with requested work apparently still open — a next step announced but not taken:
  "다음 단계로 남은 `/payments` 엔드포인트를 마이그레이션하고 테스트를 갱신하겠습니다."
  Claude does not see this notice. If nothing blocks the open work, reply "continue". Bypass: PRAXIS_EARLY_STOP_BYPASS=1
```

When block mode has spent its cap for the turn, it emits this notice with one
extra line, `Automatic continuation cap reached (2 this turn): the run stops
here so it can be reviewed.`

### Block mode (`PRAXIS_UNATTENDED=1`)

`{"decision": "block", "reason": ...}` on stdout, exit 0. Per `_hook_io.py`
the block tier "Blocks the stop; `reason` is fed to the model so it can
self-correct." This is the guide's harness pattern: "If a turn ends with items
still open and no blocker stated, send a short user message naming them, like
the following one." Its example:

```text
Your task list still has open items: migrate the remaining two endpoints and update their tests. Continue with them. If one is blocked, say what is blocking it.
```

The hook has no task list to read, so instead of listing items it names the
detected type and quotes the line it read. The reason is addressed to the
model:

```text
[early-stop-advisory] Your turn ended with requested work still open — a next step announced but not taken:
  "다음 단계로 남은 `/payments` 엔드포인트를 마이그레이션하고 테스트를 갱신하겠습니다."
Continue with the open items. If one is blocked, say in one line what is blocking it.
This does not override the need for confirmation on risky or destructive actions: ask before those as you otherwise would.
(Unattended run, PRAXIS_UNATTENDED=1: automatic continuation 1 of 2 this turn.)
```

The confirmation line is there because the guide's standing instruction ends:
"This does not override the need for confirmation on risky or destructive
actions." The guide also tells the harness author to "keep your own
confirmation step for risky or irreversible actions". A forced continuation
must not read as permission to skip that step.

For type 2 the kind reads "an offer to continue that waits on the user's
preference": the notice's "your preference" addresses the user, and the model
is not the user.

A fire records one row in the fire ledger: `advise` for a notice, `block` for
a block.

### Continuation cap

The guide: "Either way, stop after two or three automatic continuations on the
same task rather than repeating them indefinitely, so that a run that is
genuinely stuck ends and can be reviewed."

Block mode blocks at most **2** times per turn (`_MAX_CONTINUATIONS` in
`impl.py`, a constant with no env override). The third stop in the same turn
gets the capped notice, and so does any later stop in that turn.

- **What "the same task" means here.** The hook cannot see a task list. It
  treats the human message that opened the turn as the task: the most recent
  user record read by `read_last_user_record(human_only=True,
  skip_hook_feedback=True)` in `hooks/_lib/_transcript.py`. That skips
  host-injected records (`isMeta`, compaction summaries, non-human `origin`)
  and Stop-hook feedback, the user-role record whose text starts `Stop hook
  feedback:` and carries a block's reason back to the model
  ([`docs/retrospect-prune-audit.md`](../../../docs/retrospect-prune-audit.md)
  counted 130 of them in a local corpus). Without that skip, every block
  would read as a new human turn and the cap would never be reached.
  Measured live on 2026-09-26 (Claude Code 2.1.283, `claude -p --model
  haiku` with a canary Stop hook that blocks once): the block's reason was
  recorded as a user record with `isMeta: true` and content
  `Stop hook feedback:\n<reason>`. Both guards therefore exclude it: the
  `isMeta` check in `human_only` and the text-prefix check.
- **The key** is that record's `uuid`, else its `timestamp`, else a hash of
  its text. A new human message resets the count, even if its text repeats
  the previous one (a second "continue"). Only the text-hash fallback cannot
  tell two identical messages apart; live transcripts carry a `uuid`.
- **Where the count lives:** `early-stop-continuations-<session_id>.json` in
  the praxis cache dir (`resolve_cache_file`: `~/.praxis/cache/` by default,
  `PRAXIS_HOME` relocates it, `${TMPDIR}` if unwritable), holding
  `{"turn": <key>, "blocks": <n>}`. The read-modify-write runs under
  `_state_lock.state_lock` and stages through a per-pid name, per
  [`DESIGN.md` → Session-state concurrency](../../../DESIGN.md#session-state-concurrency)
  (Q1: a threshold reads the count).
- **`stop_hook_active` in block mode.** The host sets it on every stop that
  follows a Stop-hook block — this hook's or any sibling's — so it cannot
  count continuations. Block mode therefore ignores it and relies on the
  counter. The counter is also what ends a loop with a sibling: at most two
  blocks from this hook per turn, whatever else blocks. Notice mode keeps the
  flag as its re-entry guard, unchanged.
- **Order of writes.** The new count is written before the block is emitted.
  A count that cannot be written never blocks, so a storage failure can cost
  a continuation but cannot loop.

### Unattended-run marker

`PRAXIS_UNATTENDED`, exact value `1`, unstripped: `true`, `yes`, ` 1` and every
other value leave the hook in notice mode. No unattended marker existed in the
repo before this hook, so the name is new. It is declared as the hook's
`strict_env` in `hooks/manifest.json` and listed in
[`docs/bypass-vars.md`](../../../docs/bypass-vars.md) → Strict, because it is
what promotes this hook's notice to a block.

**Nothing sets it yet.** `cmux-delegate` workers are the intended setter, but
changing `skills/cmux-delegate/SKILL.md` is left to a follow-up (other open
PRs edit that file). Until something sets the marker, block mode is opt-in by
hand: export `PRAXIS_UNATTENDED=1` in the environment of an unattended run.

## Why a notice by default

Every marker is also written by a turn that finished correctly, and a blocker
the model did not name reads the same as no blocker. In an attended session a
block would force a continuation over exactly the stops the guide says to keep,
while the user is there to decide. So outside unattended runs the hook names
what looks open and leaves the decision with the user. In an unattended run no
one is there to decide, the guide's harness advice is to continue, and the cap
bounds the cost of a false positive to two extra continuations per turn.

## Measured corpus

None yet. No local transcript corpus was available when the hook was written,
so there is no fire rate or sampled precision; the fixture suite in
`tests/hooks/completion-verify/test_early_stop_advisory.sh` is the only
evidence. Issue #1498's corpus fire-rate item is therefore still open. Replay
the hook over `~/.claude*/projects/*/*.jsonl` before the `review_by` audit.

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
assistant text, and any uncaught exception all exit 0 with no output. In notice
mode `stop_hook_active` does too. A failed load of the sibling's predicate
makes this hook silent rather than risk a double fire; the fixture suite's
must-fire cases then fail, so that breakage is caught in CI.

No bypass or fail-open path blocks. In block mode, a count that cannot be kept
degrades to notice mode (a notice, or silence when `stop_hook_active` is set):

- no `session_id` in the payload, or no human message found in the
  transcript;
- a state file that cannot be read, is not JSON, is not an object, or holds a
  non-integer count for this turn. The hook replaces it with a spent count for
  the current turn, so this turn gets the notice and the next human turn
  counts from zero;
- a state write that fails.

`PRAXIS_EARLY_STOP_BYPASS` silences the hook in both modes.
