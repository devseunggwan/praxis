# Retrospect-Active Session Marker

Supported hosts: all

`hooks/retrospect-active-marker.py` is a multi-event hook (`PreToolUse(Skill)`
+ `UserPromptSubmit`) that maintains a session-scoped marker recording that a
retrospect Stage 3 report is **owed in the current turn**. It is the
format-independent foundation for the issue
[#666](https://github.com/devseunggwan/praxis/issues/666) Stage-3
fence-omission bypass gate in
`hooks/completion-verify/retrospect-mix-check/impl.sh`.

### Why this exists

The Stop hook `retrospect-mix-check` identifies a Stage 3 report by the agent's
**own output format** — a `## Retrospect Report` header AND a
`<!-- retrospect:distribution begin -->` fence. A free-form report (localized
header, plain markdown findings table, no fence) fails the identifier checks,
so the hook `exit 0`s and **every downstream gate (including Gate-7) silently
no-ops** — precisely when the agent has deviated from the prescribed schema.
This is one level deeper than "rule exists ≠ retrieval": *the gate exists but
does not fire*, because firing depends on a self-format the violator can avoid.

The only signal a bypassing report cannot avoid is **"the retrospect skill was
actually invoked this turn"** — a session-level fact captured at
skill-invocation time, not report time. This hook records that fact so the Stop
gate can key on it instead of on the avoidable output format.

### What it does

| Event | Action |
|-------|--------|
| `PreToolUse(Skill)` with `tool_input.skill` matching `retrospect` | **SET** the marker (`source: skill`). Primary capture point — covers slash-command, natural-language, and auto-invocation, all of which route through the Skill tool. |
| `UserPromptSubmit` whose prompt starts with `/retrospect` or `/praxis:retrospect` | **SET** the marker (`source: slash`). Arms the gate even before the Skill `tool_use` record exists. |
| `UserPromptSubmit` for any other prompt | **CLEAR** the marker. A new user turn that is not a retrospect invocation resets the window. |

Natural-language mentions ("retrospect" / "회고" in prose) deliberately do NOT
SET on `UserPromptSubmit` — a casual mention must not arm the gate. The
`PreToolUse(Skill)` path covers genuine natural-language invocation, because it
still routes through the Skill tool.

### Marker lifecycle

```
UserPromptSubmit (/retrospect)  ─SET─┐
PreToolUse(Skill: …retrospect)  ─SET─┤
                                     ├─► marker present ─► Stop gate armed
UserPromptSubmit (other prompt) ─CLEAR
Stop hook sees '## Actions Executed' ─CLEAR   (Stage 4 complete)
```

Between SET and CLEAR the marker means "retrospect invoked this turn, not yet
completed" — exactly when a Stage 3 distribution fence is required. Clearing on
every non-invocation `UserPromptSubmit` (and on Stage 4 in the Stop hook) bounds
the armed window so an abandoned retrospect / topic change does not cause a
later unrelated Stop to be blocked.

### State file

Priority order:

1. `PRAXIS_RETROSPECT_ACTIVE_FILE` env var (explicit override for tests).
2. `${TMPDIR:-/tmp}/praxis-retrospect-active-${session_id}.json` — the
   canonical praxis hook session key (same field used by `session-intent`,
   `retrospect-mix-check.sh`, `strike-counter.sh`).
3. `${TMPDIR:-/tmp}/praxis-retrospect-active-${PPID}.json` back-compat
   fallback when no `session_id` is supplied.

The file body (`{"retrospect_active": true, "source": "skill|slash"}`) is a
hint; **existence is the signal**. Writes are atomic (temp + rename) so a
concurrent Stop-hook read never sees a truncated file.

### Fail-safe

The hook **never blocks** — it only records side-effect state. Malformed
payloads, unreadable/unwritable state, and missing fields all exit 0 silently.

### Pairs with

`hooks/completion-verify/retrospect-mix-check/spec.md` (the #666 gate that
consumes this marker).
