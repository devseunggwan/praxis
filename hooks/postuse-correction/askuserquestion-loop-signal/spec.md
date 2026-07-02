# askuserquestion-loop-signal

Supported hosts: all

Observe-only PostToolUse(AskUserQuestion) hook that appends one fire-ledger
record per `AskUserQuestion` call. Implements issue #740, outcome-proxy
signal 2 of 3 (issue #737's "re-clarification loop"; signal 1,
external-write revert, shipped in PR #739).

## Why this exists

A "re-clarification loop" is a session where the AI fires `AskUserQuestion`
repeatedly on what a human would judge to be the same topic, forcing the
user to re-explain more than once. There was previously no telemetry source
that could surface this pattern — `bypass-review fire-rate`'s Outcome Proxy
section only had `strike_count` and `external_write_revert_count`.

## Behavior

| Condition                          | Action                                     |
| ----------------------------------- | ------------------------------------------- |
| `tool_name == "AskUserQuestion"`    | Append one fire-ledger record; exit 0      |
| `tool_name != "AskUserQuestion"`    | No-op, exit 0                              |
| Missing/non-string `session_id`     | No-op, exit 0 (cannot attribute the fire)  |
| `PRAXIS_FIRE_TELEMETRY_DISABLE=1`   | No-op (fire-ledger's shared opt-out)       |
| Malformed JSON stdin                | Silent fail-open, exit 0                   |

**This hook NEVER blocks.** It is registered as a `PostToolUse` hook and
cannot influence tool execution regardless.

## Same-topic detection — design decision

The issue left the "same topic" judgment method open, with 3 candidates:

- (a) exact match on `questions[].header` / `questions[].question` text
- (b) text-similarity / embedding across calls
- (c) coarse proxy — total `AskUserQuestion` call count per session, no
  topic clustering at all

**This hook implements (c).** Every `AskUserQuestion` call appends one
record; `bypass-review fire-rate` reads the per-session count as the loop
proxy (`reclarification_loop_count`, threshold `>=2`). (a)/(b) were rejected
for this round because they would require adding new fields (header/question
text) to the shared fire-ledger record shape that every other hook's records
lack, plus a text-similarity threshold with its own tuning burden — neither
fits the existing per-session-count pattern `strike_count` /
`external_write_revert_count` already use. See
`skills/bypass-review/bypass-review`'s `OUTCOME PROXY LIMITATIONS` section
for the resulting accuracy limitation: a `>=2` count means "the AI asked
`AskUserQuestion` more than once in the session," not proof those calls were
about the same topic — a session with 2 calls on 2 unrelated topics is
indistinguishable from a true same-topic loop under this proxy.

## Emission point — design decision

`PostToolUse(AskUserQuestion)`, appending one record per call as it happens
— mirrors `bypass-telemetry`'s (`hooks/postuse-correction/bypass-telemetry`)
per-event append-as-you-go pattern. The alternative considered was a
Stop-hook transcript sweep at session end; rejected because
`hooks/completion-verify/retrospect-mix-check` already does an expensive
transcript parse for its own, unrelated self-validation purpose (see
"Boundary" below) — a second sweep would duplicate that cost for no shared
benefit, and per-event append needs no transcript access at all.

## Boundary: does NOT touch retrospect-mix-check

`hooks/completion-verify/retrospect-mix-check/impl.sh` (L607-668, L732-746)
already has a "user-correction marker" regex, but it was built for
retrospect's own Stop-hook self-validation (did the AI's own response get
corrected) — a different question from "did the AI ask the same clarifying
question more than once." This hook does not read, call, or modify
retrospect-mix-check in any way; it is an independent detector.

## Fire-ledger record format

Reuses `hooks/_lib/_fire_ledger.py`'s `record_session_fire` (issue #740),
which appends a RICH-shaped record (companion to `record_group_fires`'
per-record shape, without requiring dispatcher batching):

```json
{
  "timestamp": "2026-07-02T12:34:56.789012+00:00",
  "session_id": "sess-abc123",
  "tool": "AskUserQuestion",
  "hook": "askuserquestion-loop-signal",
  "role": "postuse-correction",
  "decision": "pass",
  "granularity": "rich"
}
```

`decision` is always `"pass"` — every call is inherently the countable
event; there is no interesting/uninteresting split to encode per-record
(unlike `destructive-bash-guard`, which fires on every Bash call and must
distinguish flagged commands from ordinary ones via `decision`).

**Coarse duplicate**: this hook also goes through the standard `@fail_open`
decorator, which automatically appends its own COARSE record for this hook
name (`session_id=""`, `granularity="coarse"`) after every invocation — this
is unrelated to the RICH record above and is the same behavior every other
`@fail_open` standalone hook has. Readers computing per-session counts
(`skills/bypass-review/bypass-review`'s `compute_reclarification_loop_counts`)
filter to `granularity=="rich"` to avoid conflating the two.

## Storage

Same file as every other fire-ledger record — no separate stream:

```
~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl
```

## Configuration

| Env var                        | Default | Effect                                          |
| ------------------------------- | ------- | ------------------------------------------------ |
| `PRAXIS_FIRE_TELEMETRY_DISABLE` | unset   | `1` = full opt-out (fire-ledger's shared toggle) |
| `PRAXIS_FIRE_TELEMETRY_FILE`    | (daily path above) | Override full file path — used by tests |

No hook-specific opt-out variable was added; the fire-ledger's existing
opt-out already covers this hook since it writes through
`record_session_fire`.

## Known limitations

- **Coarse loop proxy, no topic clustering** — see "Same-topic detection"
  above; documented in bypass-review's `OUTCOME PROXY LIMITATIONS`.
- **No cross-session correlation** — each session's count is independent;
  a user re-explaining the same thing across separate sessions (e.g. after
  a compaction or a new terminal) is not counted as one loop.

## Tests

```bash
bash tests/hooks/postuse-correction/test_askuserquestion_loop_signal.sh
python3 -m pytest tests/test_fire_ledger.py -q
```
