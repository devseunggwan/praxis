# PreToolUse Fan-Out Scope Gate

Supported hosts: all

`hooks/preflight-gate/fan-out-scope-gate/impl.py` fires on every
`PreToolUse(Bash|Agent)` event. It counts the delegation targets the current
turn has already created and, from the second one onward, emits
`permissionDecision: "ask"` so the user sees the fan-out growing and decides
whether the new target belongs to what they asked for.

## Why this exists

A delegation request named a single target — one link, one item. Three workers
were spawned. Two of them mapped to nothing in the instruction: they came from
adjacent items that happened to sit in the same document the named target was
read from, and the document became the unit of scope in place of the link.

The failure shape is **acting on the container instead of the named thing**,
and it is invisible from the inside. Enumerating adjacent work reads as
thoroughness at authoring time, so nothing feels like an expansion while it
happens. Three rule-layer clauses forbid exactly this (scope discipline, no
over-engineering, "do not widen the requested scope"); all three were loaded
and none fired. The user paid for it in a correction turn.

The count is what the user can judge and the agent cannot. Whether a target
belongs to the request is a question about the request, so the gate routes it
to the person who wrote it rather than asking the agent to grade its own scope.

## What is gated

| Scenario | Action |
| ---------- | -------- |
| 1st delegation target in a turn | silent pass-through |
| 2nd (and later) delegation target in a turn | `permissionDecision: "ask"` |
| One command that can create more than one target | `ask` — even as the 1st |
| A creation written inside `$( ... )` or backticks | counted, at any nesting depth up to 4 |
| The same, inside single quotes or after a backslash | not counted — the shell would print it, not run it |
| `Agent` tool call | counted as a delegation target |
| `cmux workspace create` / `cmux new-workspace` | counted as a delegation target |
| Any env-var prefix (`env FOO=1 cmux workspace create`) | counted — no environment variable exempts a target |
| Marker comment (`# fan-out-mapped:`, any comment text) | `ask` — no agent-attachable bypass exists by design |
| A prior target whose `tool_result` is `is_error` | not counted — no worker started |
| `--help` / `--dry-run` rehearsal | not counted, and not gated |
| Command text inside `echo` / `printf` | not counted — `argv[0]` is the echo, not `cmux` |
| Any other Bash command | silent pass-through |

Both workspace-creation spellings count. The canonical form is
`cmux workspace create`; `cmux new-workspace` is a deprecated alias that keeps
working indefinitely, and it is the form the motivating incident used, so
matching only the canonical one would leave the observed command unmatched.

## One call can carry the whole fan-out

The first build of this gate counted tool calls, and it did not fire on its own
motivating incident. That incident created three workspaces from **one** Bash
call: a shell function invoked three times, with the creation written as
`WS_RAW=$(cmux new-workspace ...)`. Two separate blind spots stacked up.

- The creation sat inside a command substitution. The tokenizer coalesces a
  `$( ... )` run into a single token, so a scan of the outer text finds no
  command start there at all — the call read as **zero** targets, not one.
- Even read correctly it is one *call*. The function runs as many times as it
  is called, and no amount of static reading recovers that number.

Quoting decides whether a substitution is one. Single quotes and a backslash
make `$(` and a backtick literal, so `printf '%s' '$(cmux workspace create …)'`
prints a string and starts nothing; double quotes disable neither form, so
those are followed. The span scanner tracks quote and escape state on the way
in and again while finding the closing delimiter, so a parenthesis inside a
quoted argument does not end the span early.

So the gate asks on a single call when that call can create more than one
target: two or more literal creation segments, or one creation reached through
a loop or a function body. `$( ... )` and backtick spans are unwrapped and
scanned recursively (depth 4) before any of that counting happens.

"Can create more than one" is deliberately not "does". A creation sitting beside
an unrelated `for` loop asks too. The response is a prompt, not a block, so the
cost of that imprecision is one keystroke — and the alternative is the silence
that produced the incident.

## Counting rule

Evidence is scoped to the **current turn**: the question is whether *this
request* is being fanned out past what it named. A worker from a previous turn
belonged to a different request.

Text that spells out a creation is not a creation. `echo`, `--help`, and
`--dry-run` were excluded from the first build; a **heredoc body** was not, and
that gap fired the moment the gate ran against a live session — a
`python3 - <<'PY' … PY` call editing this hook's own test file was counted
twice, because the fixture strings inside it contain the literal command.
`_hook_utils.strip_heredoc_bodies` now blanks those bodies before the scan,
which is the same helper the merge gates use for the same reason.

A failed creation is not a target — a rejected call left no worker running, so
it must not push the count toward the prompt. Correlation is tool_use `id` <->
tool_result `tool_use_id`, mirroring `pr-claim-mutation-gate`. A tool_use with
no result yet counts as a success, biasing toward asking: the gate exists to be
seen while the fan-out is still growing, and silence is the failure it was
built for.

## Response

`permissionDecision: "ask"` — the Claude Code permission dialog surfaces before
the target is created. There is no environment bypass and no marker.

The reason text carries the **first line of the turn's user message** beside
either the **target ordinal** or the multi-target ground, so the prompt is
answerable without scrollback. That pairing is the whole judgement: the reader
sees "target #3" — or "this one command creates several" — next to what they
actually asked for, and the mismatch is usually obvious on sight.

**A message the user did not write is not quoted.** A turn can open on
something the host put in the user slot — a background task notification, a
system reminder, the preamble of a session resumed after compaction. Quoting
one of those back as "the request" is worse than showing nothing: it fills the
one slot the reader uses to judge the targets with a sentence they never
wrote. Those openers are recognised and replaced with an explicit "no request
in this turn". The list of openers is not exhaustive, which is why the line
reads *the request asked for* rather than asserting authorship.

An agent-attachable marker was considered and rejected. The natural design —
"emit a table mapping each target to a span of the request, and pass" — is
self-attestation: the agent that mis-scoped the fan-out is the same one filling
in the table. `pre-merge-approval-gate` states the same contract for
`# merge-approval:ack`.

## Known limits

- **The first target is already running.** `PreToolUse` sees one call at a
  time, so the earliest possible firing point is the second. When the
  legitimate target is first and the extra one second, the gate lands exactly
  right; in the reverse order one unwanted worker starts before the prompt
  appears. Either way the user sees the whole fan-out at the prompt.
- **Legitimate multi-target fan-out is gated too.** This is the designed cost,
  not a false positive: the gate cannot tell a justified third worker from an
  unjustified one, which is why it asks instead of blocking. The tuning lever
  is the threshold.
- Only `Bash` workspace creation and the `Agent` tool are counted. Worktree
  creation and other delegation-adjacent commands are out of scope — they are
  routinely multi-target in ordinary work, so counting them would spend the
  prompt on cases that are almost never the failure.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ---- | ----- | ------- |
| `pre-merge-approval-gate` | PreToolUse `ask` on every `gh pr merge`, no bypass by design | Structural sibling — same `ask`-with-no-marker shape, different action |
| `block-manufactured-action-menu` | Blocks a surfaced menu whose options were not asked for | Adjacent: that gate catches invented *choices*, this one invented *work* |
| `side-effect-scan` | Enumerates side effects of a pending command | None — that hook grades one command, this one counts across a turn |

## Parsing guarantees (fail-open)

Returns exit 0 on every infrastructure error — malformed stdin, a missing or
unreadable transcript, an absent `transcript_path`, and any uncaught exception
(via the shared `@fail_open` decorator in `hooks/_lib/_hook_runtime.py`). It
never blocks a target on a parsing failure.

## Tests

```bash
bash tests/hooks/preflight-gate/test_fan_out_scope_gate.sh
```
