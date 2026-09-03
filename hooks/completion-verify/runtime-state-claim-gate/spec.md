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

**Second gap, closed by #1062**: the trigger above fires only when the USER
asks about runtime state. The path a partial measurement actually took to a
public surface was voluntary RE-STATEMENT, which has no firing point at all.
PR #1058 anchor rev4: `FAILs: 0` was measured once against a 348-line log
(6.7% of the eventual 5223-line run); the next sentence carried an accurate
qualifier ("348줄 중 FAIL 0"), then the same number was restated 4 more times
over ~2 minutes with the qualifier dropped, while an unrelated progress
number (log line count: 877/1020/1110) kept changing beside it — making the
sentence read as fresh. It reached the PR anchor as "실패 0 으로 진행 중"; the
real failures sat 53.8% into the final log, past the measured window. This
hook now also scans for a verdict-count claim (`실패 N` / `FAIL N` / bare
`통과`) that was already stated earlier this session, carries no scope
qualifier this time, and has no fresh probe in the current turn — same
evidence gate as the claim above.

## What is emitted

Advisory by default — exit 0 + stdout `{"systemMessage": ...}` JSON
(issue #647 H3). `PRAXIS_RUNTIME_CLAIM_STRICT=1` escalates to a
`{"decision": "block", "reason": ...}` JSON, which re-prompts the model to
probe before stopping.

| Condition                                                                                                                                                                          | Result                                  |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| Final message asserts a runtime state (running / isolation) AND the current turn has no probe tool_use                                                                             | `[runtime-state-claim-gate]` advisory   |
| Final message restates a verdict number (`실패 N`/`FAIL N`/bare `통과`) already stated earlier this session, WITHOUT a scope qualifier, AND the current turn has no probe tool_use | `[runtime-state-claim-gate]` advisory   |
| Same claim, but the current turn contains a probe-class tool_use (Bash, Read, Grep, Glob, LSP, Task*, Monitor, `mcp__*`)                                                           | silent (claim is backed by observation) |
| Final message has no runtime-state claim                                                                                                                                           | silent                                  |
| Verdict number restated WITH its scope qualifier in the same clause (`"1110줄 중 FAIL 0"`)                                                                                         | silent (transparent about coverage)     |
| Verdict number appears for the first time this session (no prior mention) — or the number changed (re-measured)                                                                    | silent                                  |
| Claim line is a question (`…돌고 있나요?`) — either kind                                                                                                                           | silent                                  |
| "running" claim line is future intent (`will run`, `실행하겠습니다`)                                                                                                               | silent (intent, not state)              |
| Line is quoted (`> …`)                                                                                                                                                             | silent                                  |
| `PRAXIS_RUNTIME_CLAIM_BYPASS=1`                                                                                                                                                    | silent                                  |
| `stop_hook_active` in payload                                                                                                                                                      | silent (re-entrancy guard)              |

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

## Verdict-restatement model (#1062)

A different claim shape from the SUBJECT+CLAIM model above: not "is X
happening now" but "the number I already measured, said again". A claim key
is `(kind, number)` — e.g. `fail:0`; a bare pass/fail word with no adjacent
number (`통과`) keys as `pass:bare`.

- **Verdict vocabulary**: a count directly adjacent to
  `FAIL(s)`/`fail(ed)`/`실패`/`오류`/`에러`/`error` (kind `fail`), or to
  `PASS(ED)`/`통과`/`성공`/`success` (kind `pass`), in either order
  (`"FAILs: 0"`, `"0 FAILED"`, `"실패 0"`, `"0건 실패"`). A standalone `N건`
  with no fail/pass word nearby is deliberately **not** matched — too broad
  ("이슈 3건" is not a verdict count) — this stays scoped to the vocabulary the
  issue named, no invented generic-count abstraction.
- **EN vocabulary carries lookaround boundaries**: `(?<![A-Za-z])…(?![A-Za-z])`
  around `fail(ed|s)`/`error`/`pass(ed)`/`success`, the same convention as the
  sibling gates' `_en_token_present` (`\b` puts no boundary between Hangul and
  ASCII). Without them `"bypass 0"` keyed `pass:0` and `"successful 0"` keyed
  `pass:0` — a bogus prior mention that makes a later **genuine first** verdict
  fire, and under `PRAXIS_RUNTIME_CLAIM_STRICT=1` blocks a correct message.
  The Korean half needs no boundary; 실패/통과 carry their own.
- **The count must belong to the verdict word**: in the forward order only
  separators (`\s:=`) and the Korean counter/particle that attaches to the
  word (`건수`/`개수`/`수`/`은`/`는`/`이`/`가`) may sit between them. A blanket
  `\D{0,6}` bridged an intervening English noun, so `"error code 0"` keyed
  `fail:0` — the count is "code"'s, not the verdict's.
- **Reversed order binds tightly**: in the `"0 FAILED"` / `"0건 실패"` order
  only whitespace and a Korean counter suffix (`건`/`개`) may sit between the
  number and the word. A permissive gap lets a range denominator's trailing
  `"중"` bridge instead: on `"120/348 중 FAIL 0"` the reversed alternative
  matched `"348 중 FAIL"` first, keyed `fail:348`, and consumed the real
  `"FAIL 0"` — so a later unqualified `"실패 0"` found no prior mention and
  the gate silently never fired.
- **Scope qualifier, scoped per clause**: `"N줄 중"` / `"of N lines"` /
  `"N/M"` / `"N%"` is transparent about what was actually covered and stays
  silent even when the number was already stated — this is what keeps the
  motivating incident's own first restatement ("348줄 중 FAIL 0") silent. The
  qualifier only counts in the **clause it shares with the verdict** (clauses
  split on `,;，、` and sentence enders). Scanned per line, any percentage or
  fraction anywhere on the line silenced the verdict — and the incident's own
  shape is a moving progress number beside a frozen one, so
  `"진행률 80%, 실패 0"` read as qualified while `"실패 0"` was exactly the
  stale restatement. The cost of the tighter linkage is that a qualifier
  parked in its own clause (`"총 348줄, FAIL 0"`) now fires; the advisory it
  emits asks for `"348줄 중 FAIL 0"`, which is the discipline this gate exists
  to enforce.
- **Prior-mention scan**: run over the **whole transcript before the current
  turn** (not the sibling's turn/window scoping) — the incident's
  restatements spanned several turns, each ending its own Stop event, so a
  turn-scoped or 80-event-scoped prior-mention check would miss the earlier
  turns entirely. A claim only fires when its key was already stated
  earlier in the session, qualified or not. The scan is **resumable**
  (#1237): the `{confirmed, pending}` mention maps are persisted with a byte
  offset in `cache/stop-scan-runtime-state-claim-gate-<session_id>.json`, so
  each Stop parses only the bytes appended since the last one; the cursor
  is dropped (full re-scan) on a different inode, a shrunken file, or an
  offset off a line boundary.
- **Elapsed-time message**: the advisory names how many minutes ago the
  claim key was first stated (`ISO8601` transcript timestamps compared as
  strings for the minimum, parsed with `datetime` for the delta); "unknown"
  when either timestamp is missing (older transcripts, or a test fixture
  that omits `timestamp`) rather than fabricating a number.
- **Evidence gate is shared** with the SUBJECT+CLAIM model: a probe-class
  tool_use anywhere in the current turn silences both claim kinds together,
  since both conditions are computed before that gate is checked and folded
  into one `systemMessage`/`block` emission (never two JSON blobs on one
  Stop call).

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
