# PreToolUse Rejected Call Probe Gate

Supported hosts: claude

Reference: [Autonomy vs Convention, ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/rejected-call-probe-gate/impl.py` asks before a mutating
call when an earlier mutating call was refused or interrupted and probes have
not yet looked at every surface it could have changed (issue #1488).

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
3. Since that refusal, some surface of it is still unprobed and no mutating
   call has run.

## Refusals

| Record | Counts | Why |
| --- | --- | --- |
| `user-rejected` with the refusal sentence | yes | the call may have started before the user refused |
| `interrupted` | yes | the call was stopped mid-run |
| `user-rejected` without the refusal sentence (a declined hook ask) | no | stopped before it ran |
| `permission-rule` (a hook block) | no | stopped before it ran |

A refused call arms the gate only when it was mutating.

## Mutating calls

`is_mutating_call` from `hooks/_lib/_mutating_call.py`.

## Surfaces

A refused call arms the gate with the surfaces it could have changed, taken
from each segment that the shared classifier does not read as read-only:

| Refused call | Surfaces |
| --- | --- |
| `git <sub>` | local git; with `push`, `fetch`, `pull`, `clone`, `remote`, `ls-remote` or `submodule`, also a remote ref |
| `gh ...` | GitHub |
| `kubectl`, `aws`, `docker` | that tool's own surface |
| a local file writer (`rm`, `mv`, `cp`, `mkdir`, `touch`, `tee`, `sed`, ...), a file-writing redirect, Edit / Write / NotebookEdit | local files |
| an MCP call with a write verb | that MCP server |
| any other CLI, or a command substitution | local git, a remote ref and GitHub |

The last row is the incident: a CLI the gate does not know created a branch,
pushed it and opened a pull request, so all three must be looked at.

## Probes

A probe is a call that ran and reads state. It covers the surfaces it reads:

| Probe | Covers |
| --- | --- |
| a read-only `git` command | local git; `git ls-remote` also a remote ref |
| a read-only `gh` command | GitHub and remote refs |
| any read-only Bash command, the Read, Grep and Glob tools | local files |
| a read-only `kubectl`, `aws` or `docker` command | that tool's surface |
| an MCP call with no write verb | its own server |

A probe aimed somewhere else covers nothing. The target of a call is the
directory of a leading `cd` or of `git -C`, or the repository of `--repo` /
`-R`. Two targets are compared only when both are named and of the same kind: a
call that names none runs wherever the session is, and a directory cannot be
compared with a repository.

## Disarming

- probes that together cover every surface of the refused call;
- a mutating call that ran: the user approved this very ask or chose to act,
  and asking on every later mutation would be noise.

The ask lists the surfaces still unprobed.

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

Replay over every local Claude Code transcript (871 files), folding each record
into the gate's own state and evaluating every tool_use against the state
before it, with the shared classifier as of #1497:

| Measure | Count |
| --- | --- |
| Refused mutating calls that arm the gate | 32 (28 `user-rejected`, 4 `interrupted`) |
| Disarms (every surface probed, or a mutation ran) | 30 |
| Probes that covered some surfaces but not all | 10 |
| Asks raised | 28, in 25 sessions |
| Asks raised if any probe disarmed | 20 |

Of the 8 asks that surface matching adds, 3 follow a refused command that did
reach a remote (`git fetch`, a download, a schema-sync CLI of the incident's
kind), 2 a local-only CLI the gate does not know (`pytest`, `pkill`), 2 a
malformed `cd` whose local git was never probed, and 1 an unknown CLI run from
a scratch directory.

The replay script is not committed: it imports this `impl.py` and walks each
transcript once.

## Known limitations

1. **An unknown CLI needs three probes even when it is local-only.** A refused
   `pytest` or `pkill` asks until local git, a remote ref and GitHub are all
   probed. Listing such CLIs would narrow this, and each entry would be a
   guess about what the CLI can reach.
2. **A probe is matched by surface, not by object.** `gh pr list` covers
   GitHub whether or not it lists the pull request the refused call opened.
3. **Targets of different kinds are not compared.** A `cd /repo` refusal and a
   `gh --repo owner/name` probe count as the same place.
4. The shared classifier is fail-closed: a read-only command it does not
   recognise is mutating and not a probe, and a refused one arms the gate.
5. How a headless run (`claude -p`) resolves the ask was not tested.

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
