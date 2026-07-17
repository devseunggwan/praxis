# Stop Runtime-State Claim Gate

Supported hosts: all

`hooks/completion-verify/runtime-state-claim-gate/impl.py` runs on the `Stop`
event. It scans the **final assistant message** for a present-state
runtime/execution assertion and, when the **current turn** contains no probe
tool call, emits a **stdout `{"systemMessage": ...}` JSON advisory**.

## Why this exists

An Agent launch with `isolation: "remote"` silently fell back to local
execution (remote is availability-gated; the launch success message does not
distinguish the fallback). When the user asked "remote로 처리한다며?" and
"지금 서브에이전트 돌아가는 건 뭔데?", the assistant asserted twice — with zero
probes — that the subagent was "running in a cloud sandbox on a fresh clone,
not touching the local worktree". It was writing files into the local worktree
at that moment; the user caught it first via editor diagnostics (issue #809).

The prompt layer had already failed: the fabricated-rationale strike memory,
the full-code-path family (4+ recurrences), and the CLAUDE.md rule "runtime
behavior claims only after actual execution + evidence" were all loaded at
failure time. Every existing falsification gate anchors on an *action* event
(issue create, recommendation surface, merge claim) — a conversational answer
describing runtime state is also a claim-emission point, and nothing fired
there. This is the runtime-state sibling of
[merge-state-claim-gate](../merge-state-claim-gate/spec.md) (#503): a Stop
hook sees the final assistant text, the exact surface PreToolUse cannot see.

## What is emitted

Advisory by default — exit 0 + stdout `{"systemMessage": ...}` JSON
(issue #647 H3). `PRAXIS_RUNTIME_CLAIM_STRICT=1` escalates to a
`{"decision": "block", "reason": ...}` JSON, which re-prompts the model to
probe before stopping.

| Condition                                                                                                                | Result                                  |
| ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------- |
| Final message asserts a runtime state (running / isolation) AND the current turn has no probe tool_use                   | `[runtime-state-claim-gate]` advisory   |
| Same claim, but the current turn contains a probe-class tool_use (Bash, Read, Grep, Glob, LSP, Task*, Monitor, `mcp__*`) | silent (claim is backed by observation) |
| Final message has no runtime-state claim                                                                                 | silent                                  |
| Claim line is a question (`…돌고 있나요?`) — either kind                                                                 | silent                                  |
| "running" claim line is future intent (`will run`, `실행하겠습니다`)                                                     | silent (intent, not state)              |
| Line is quoted (`> …`)                                                                                                   | silent                                  |
| `PRAXIS_RUNTIME_CLAIM_BYPASS=1`                                                                                          | silent                                  |
| `stop_hook_active` in payload                                                                                            | silent (re-entrancy guard)              |

## Claim model

A claim needs a runtime SUBJECT and a state CLAIM **on the same line**
(line-localization mirrors the sibling; cuts false positives on long final
messages). EN tokens use `(?<![A-Za-z0-9_])…(?![A-Za-z0-9_])` lookarounds, not
`\b` — Python puts no word boundary between Hangul and ASCII, so `\bagent\b`
misses "agent가" (the sibling's `\bPR\b` trap).

- **SUBJECT**: 에이전트/서브에이전트/프로세스/컨테이너/클라우드/원격/로컬/샌드박스/
  백그라운드/워커, `agent(s)`, `worker(s)`, `process(es)`, `container(s)`,
  `pod(s)`, `job(s)`, `cloud`, `remote`, `local`, `sandbox`, `background`, `DAG`
- **running**: 돌고 있/돌아가/실행 중/실행되고/동작 중/작동 중, `running`,
  `executing`, `runs on`, `is live`
- **isolation**: 건드리지 않/사용하지 않/접근할 수 없/접근 불가/영향 없/격리,
  `does not touch|use|access`, `cannot access`, `isolated from`, `untouched`

**No generic negation-skip** — unlike the sibling, a negative sentence here IS
the claim: "로컬은 건드리지 않습니다" is an isolation assertion (the motivating
incident verbatim). Future/question suppressors apply to "running" only;
"건드리지 않을 겁니다" still projects an unverified isolation guarantee and is
gated.

## Evidence model

Evidence is scanned over the **current turn only** (not the sibling's 80-event
window): runtime state goes stale across turns, and the incident turns
contained zero tool calls of any kind. Any read-class tool_use counts —
`Bash`, `Read`, `Grep`, `Glob`, `LSP`, `TaskList`, `TaskGet`, `TaskOutput`,
`Monitor`, or any `mcp__*` tool. `Write`/`Edit`/`Agent`/`Skill`/
`AskUserQuestion` are deliberately excluded: launching or mutating is not
observing. Sidechain events are ignored.

Command-level classification of Bash probes was deliberately rejected: this is
a fail-open advisory hook, and the false-negative (an unrelated Bash call
clears a claim) is cheaper than maintaining a probe-command taxonomy.

## Env vars

| Var                           | Effect                                       |
| ----------------------------- | -------------------------------------------- |
| `PRAXIS_RUNTIME_CLAIM_BYPASS` | `=1` skips the gate entirely                 |
| `PRAXIS_RUNTIME_CLAIM_STRICT` | `=1` escalates advisory → `decision: block`  |

Fully fail-open (`@fail_open`; missing transcript, bad JSON, empty turn → exit
0 silent).
