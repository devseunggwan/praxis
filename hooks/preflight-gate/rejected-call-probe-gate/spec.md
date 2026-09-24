# PreToolUse Rejected Call Probe Gate

Supported hosts: claude

Reference: [Autonomy vs Convention, ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/rejected-call-probe-gate/impl.py` asks before a mutating
call when an earlier mutating call was refused or interrupted and no probe of
state has run since (issue #1488).

## Why this exists

Observed sequence, from one session transcript:

1. A Bash call ran a CLI that creates a branch, pushes it, and opens a pull
   request.
2. The call was recorded with `toolDenialKind: user-rejected` and the fixed
   "User rejected tool use" result.
3. The pull request existed, created at the time of that call, and the working
   tree was left on the CLI's branch.
4. The agent read the refusal as "nothing happened" and learned otherwise only
   later, while probing for unrelated reasons.

`denied-action-report-gate` treats a refused call as one with no outcome and
checks only that it is reported. `rejected-mutation-reconsent-gate` guards
re-running a refused destructive target. Neither considers that the refused
call may have produced side effects.

## Trigger

All three, the cheapest first:

1. The pending call is mutating and is not itself a probe.
2. An earlier mutating call in this session was refused: `toolDenialKind`
   `user-rejected` with the refusal sentence, or `interrupted`.
3. Since that refusal, no probe has run and no mutating call has run.

## Refusals

| Record | Counts | Why |
| --- | --- | --- |
| `user-rejected` with the refusal sentence | yes | the call may have started before the user refused |
| `interrupted` | yes | the call was stopped mid-run |
| `user-rejected` without the refusal sentence (a declined hook ask) | no | stopped before it ran |
| `permission-rule` (a hook block) | no | stopped before it ran |

A refused call arms the gate only when it was mutating.

## Mutating calls

`is_mutating_call` from `hooks/_lib/_mutating_call.py`, applied after peeling
leading `cd <dir> &&` segments: the shared allowlist has no `cd`, so without
the peel `cd <repo> && git status` reads as a mutation.

## Probes

The maintainer's-call default. A probe is a call that ran and reads state:

- a read-only Bash command (per the shared classifier) with at least one
  `git`, `gh`, `kubectl`, `aws` or `docker` segment, the subcommand-gated
  families of the shared allowlist;
- an MCP call whose name carries no write verb.

`ls` and `cat` read local files only and are not probes. Any probe disarms,
whatever surface the refused call touched: in the incident the surfaces were a
branch, a push and a pull request, and `git status` or `gh pr list` are what
would have seen them. Matching the probe to the refused call's surface is left
open.

## Disarming

- a probe that ran;
- a mutating call that ran: the user approved this very ask or chose to act,
  and asking on every later mutation would be noise.

## Fail-open

A malformed payload, a missing or unreadable transcript, or a scan that does
not reach the end of the transcript is silent.

## Tier

`ask`, with no bypass marker or bypass env. Approving the ask is the user's
decision to act without looking.

## Hosts

Claude only. The refusal fields (`toolDenialKind`, the refusal sentence) are
Claude Code transcript fields. Cursor's `preToolUse` accepts `ask` but does not
enforce it, and Codex reports an `ask` hook as failed and lets the call through
(see `cross-tool-reroute-gate/spec.md`, *Known limitations* 4).

## Recurrence evidence

Replay over every local Claude Code transcript (868 files), folding each record
into the gate's own state and evaluating every tool_use against the state
before it:

| Measure | Count |
| --- | --- |
| Refused mutating calls that arm the gate | 33 (29 `user-rejected`, 4 `interrupted`) |
| Disarms (probe or mutation ran) | 32 |
| Asks raised | 27, in 24 sessions |
| Of those, the pending call is a clear write (MCP send, `gh issue edit`, `gh pr merge`, a message-sending CLI) | 5 |

The replay script is not committed: it imports this `impl.py` and walks each
transcript once.

## Known limitations

1. **Read-only compounds ask.** The shared classifier is fail-closed, so a
   read-only command it does not fully recognise (`sed -n`, a `for` loop, a
   compound with `echo`) is mutating and not a probe. 22 of the 27 replay asks
   landed on such calls. The same applies on the arming side: a refused
   read-only compound arms the gate.
2. **Any probe disarms.** A `git status` in an unrelated repository disarms a
   refusal of a `gh pr create` elsewhere.
3. How a headless run (`claude -p`) resolves the ask was not tested.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| --- | --- | --- |
| `denied-action-report-gate` | reports a refused call at `Stop` | this one asks before the next mutation |
| `rejected-mutation-reconsent-gate` | re-running a refused destructive target | this one covers any mutation after any refused mutating call |
| `cross-tool-reroute-gate` | reaching a hook-blocked target through another tool | reads hook blocks, which this one ignores |

## Tests

```bash
bash tests/hooks/preflight-gate/test_rejected_call_probe_gate.sh
```
