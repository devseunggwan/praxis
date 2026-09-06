# PreToolUse Pre-Output Falsification Gate

Supported hosts: all

`hooks/pre-output-falsification-gate.sh` adds two independent **stderr
advisory** lanes (issue #487). Both are advisory-only — they emit a one-line
reminder to stderr and exit 0. Neither ever blocks tool execution.

### Why this exists

The *Output-Block-Level Falsification Gate* and *One-Probe-Before-Action
Gate* rules ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) fail to fire at execution time because
rule retrieval is not structural — the rule is loaded but not re-triggered at
the moment a `(Recommended)` option is surfaced or a status check is repeated.
This hook adds two structural enforcement points that nudge the agent before
those two specific failure modes complete.

### Lane A — AskUserQuestion evaluative-option gate

Fires on `AskUserQuestion` when **all three** conditions hold:

| Leg    | Condition                                                                                                                                                                                                             |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A1     | An option label or description contains an evaluative marker: `Recommended`, `natural`, `safer` (EN, ASCII word-boundary) or `권장`, `안전한`, `더 안전` (KO substring)                                               |
| A2     | Recent transcript (last 400 JSONL lines) contains a negative-evidence substring in a `tool_result` content block OR a user message: `fail`, `timeout`, `0 row`, `blank`, `error`, `not found`, `verification failing` |
| A3-alt | The question body / option descriptions do **NOT** contain a falsification phrase (`if … wrong`, `if … incorrect`, `invalidating link`, `disconfirm`, `반증`, `missing observation`, `이게 틀렸을 때`)                |

When A1 ∧ A2 ∧ ¬A3-alt, the hook emits:

```
[falsification-gate] (Recommended) option surfaced under negative evidence. Pre-output disconfirming probe statement is missing. Bypass: PRAXIS_FALSIFY_GATE_BYPASS=1
```

### Lane B — repeated read-only status command gate (B-i)

Fires on `Bash` when an identical read-only `(binary, subcommand)` pair —
where the subcommand is one of `status`, `get`, `list` — has been invoked
**≥ 3 times** in the current session. A per-`(session_id, binary+subcommand)`
counter is bumped on each matching command; the advisory fires on the call
that crosses the threshold and on every subsequent matching call:

```
[probe-gate] Same status check repeated N times this session. Try a depth probe (logs, describe, alternative path).
```

`N` is the real session count. The hook counts **commands, not outputs**.

### State

Lane B counters live under:

```
${TMPDIR:-/tmp}/praxis-pre-output-falsification-gate/<session_id_hash>/<key_hash>
```

Each file holds the integer count for one `(session, binary+subcommand)`
pair. Stale files are cleaned up by the OS tmp purge policy.

### Behavior table

| Tool                                     | Condition                                       | Behavior                        |
| ---------------------------------------- | ----------------------------------------------- | ------------------------------- |
| AskUserQuestion                          | A1 ∧ A2 ∧ ¬A3-alt                               | Advisory to stderr (exit 0)     |
| AskUserQuestion                          | falsification phrase present (A3-alt)           | Silent — probe already stated   |
| AskUserQuestion                          | no evaluative marker (¬A1)                      | Silent                          |
| AskUserQuestion                          | no recent negative evidence (¬A2)               | Silent                          |
| Bash                                     | read-only subcommand pair repeated ≥3×          | Advisory to stderr (exit 0)     |
| Bash                                     | read-only subcommand pair < 3×                  | Silent (count recorded)         |
| Bash                                     | non-read-only subcommand (`commit`, `apply`, …) | Silent                          |
| `PRAXIS_FALSIFY_GATE_BYPASS=1`           | any                                             | Silent for both lanes           |
| Malformed JSON stdin                     | any                                             | Silent (fail-open)              |
| Missing / unreadable transcript (Lane A) | A1 ∧ ¬A3-alt                                    | Silent (A2 fails open to False) |
| Other tool name (Edit, Read, …)          | any                                             | Silent                          |

### Env vars

| Variable                       | Effect                                      |
| ------------------------------ | ------------------------------------------- |
| `PRAXIS_FALSIFY_GATE_BYPASS=1` | Disable both lanes entirely for the session |

### Heuristic limits

- **A3 is infeasible as the issue originally specified.** The issue's A3 leg
  wanted the hook to confirm that a disconfirming-probe statement appears in
  the *in-flight assistant turn* before the `(Recommended)` option is
  surfaced. A PreToolUse hook cannot see the assistant turn being authored —
  it only receives the `tool_input` being submitted. We substitute a
  **question-body proxy**: the hook scans the `AskUserQuestion`
  `questions[].question` body (and option descriptions) for any falsification
  phrase. Presence suppresses the advisory; absence is the
  surfaced-without-probe signal. This catches the case where the agent
  embeds the probe in the question itself, but cannot detect a probe stated
  only in prose preceding the tool call.

- **Lane B is command-identity only (B-i), not output-identity.** The
  broader Lane B in the issue would also hash identical command *outputs*
  via a PostToolUse leg. This hook implements B-i only. It cannot
  distinguish a status check that returns *changing* output (legitimate
  polling of an evolving state) from one that returns the *same* output
  every time (the actual depth-probe-needed signal). It nudges purely on
  command-string identity, so a genuine poll loop will also trip it after
  three calls — the advisory is a reminder to consider a depth probe, not an
  assertion that the polling is wrong.

- **A1 / A2 are purely lexical.** The marker and negative-signal checks are
  substring/regex matches. They cannot tell an evidenced "could fail"
  context from a hypothesis one, nor an internal status output from an
  external one. The user remains responsible for interpreting the reminder
  in context.

### Fail-open

The hook returns exit 0 on every infrastructure error:

- malformed JSON stdin
- non-target tool invocation (Edit, Read, etc.)
- missing `questions` / `command` field
- missing or unreadable `transcript_path` (Lane A A2 fails open to "no
  negative evidence" → no advisory)
- `OSError` writing the Lane B counter (count treated as 0 → no advisory)
- any uncaught exception in the inner logic

### How to enable

The hook is registered in `hooks/manifest.json` under `PreToolUse` with two
matcher entries (`AskUserQuestion` and `Bash`). When installed as a Claude
Code plugin, it is active automatically.

### Tests

```bash
bash tests/hooks/advisory-nudge/test_pre_output_falsification_gate.sh
```

Covers: Lane A positive (evaluative marker + transcript negative evidence +
no falsification phrase), Lane A false-positive #1 (falsification phrase in
body), Lane A false-positive #2 (no evaluative marker), Lane B positive
(3× identical `git status`), Lane B false-positive (non-read-only subcommand;
3× distinct subcommands), malformed JSON fail-open, and bypass env.
