# Retrospect-Active Session Marker

Supported hosts: all

`hooks/preflight-gate/retrospect-active-marker/impl.py` (runtime wrapper:
`hooks/retrospect-active-marker.sh`) is a multi-event hook
(`PreToolUse(Skill)` + `UserPromptSubmit`) that maintains a session-scoped
marker recording that a retrospect Stage 3 report is **owed in the current
turn**. It is the
format-independent foundation for the issue
[#666](https://github.com/devseunggwan/praxis/issues/666) Stage-3
fence-omission bypass gate in
`hooks/completion-verify/retrospect-mix-check/impl.sh`.

## Why this exists

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

## What it does

| Event | Action |
| ------- | -------- |
| `PreToolUse(Skill)` with `tool_input.skill` matching `retrospect` | **SET** the marker (`source: skill`). Primary capture point — covers slash-command, natural-language, and auto-invocation, all of which route through the Skill tool. |
| `UserPromptSubmit` whose prompt starts with `/retrospect` or `/praxis:retrospect` | **SET** the marker (`source: slash`). Arms the gate even before the Skill `tool_use` record exists. |
| `UserPromptSubmit` for any other prompt | **DECAY** the marker: spend one turn of the budget (`MARKER_TURN_BUDGET`, 2) and CLEAR at zero. |

Natural-language mentions ("retrospect" / "회고", the Korean word, in prose) deliberately do NOT
SET on `UserPromptSubmit` — a casual mention must not arm the gate. The
`PreToolUse(Skill)` path covers genuine natural-language invocation, because it
still routes through the Skill tool.

## Marker lifecycle

```text
UserPromptSubmit (/retrospect)  ─SET (budget 2)─┐
PreToolUse(Skill: …retrospect)  ─SET (budget 2)─┤
                                                ├─► marker present ─► Stop gate armed
UserPromptSubmit (other prompt) ─DECAY──► budget 0 ─CLEAR
Stop hook sees '## Actions Executed' ─CLEAR   (Stage 4 complete)
```

While the marker exists it means "retrospect invoked, not yet completed" —
exactly when a Stage 3 distribution fence is required.

### Why a budget and not a clear ([#1098](https://github.com/devseunggwan/praxis/issues/1098))

The non-invocation branch used to CLEAR outright. The retrospect skill documents
mid-cycle stops that need a user reply (SKILL.md self-conflict, ambiguous
`backing_repo`), so the ordinary flow *skill invoked → clarification → user
answers → Stage 3 report* put one ordinary prompt between SET and the report.
That prompt disarmed the gate, and the fence-less report the gate exists to
catch then reached the Stop hook with `RETRO_ACTIVE=false` — gates 1..12 no-op.
One clarification round-trip was the whole bypass.

Never clearing is not the fix either: a user who genuinely changes topic would
leave the gate armed for the rest of the session, so any later Stop carrying a
markdown table would be blocked. The budget keeps both ends bounded — the marker
survives one ordinary reply and disarms on the second.

#### Where the number 2 comes from

Replaying both hooks over the local transcript corpus — 24 sessions that invoked
`/praxis:retrospect` and reached a findings table, 2105 Stop events — the gate's
in-cycle blocks by budget were:

| budget | in-cycle blocks | of those, actual #666 bypasses | blocks after a fenced Stage 3 |
| ------ | --------------- | ------------------------------ | ----------------------------- |
| 1 (pre-#1098) | 0 | 0 | 0 |
| 2 | 2 | 0 | 0 |
| 3 | 2 | 0 | 2 |
| 4 | 3 | 0 | 6 |
| 5 | 5 | 0 | 6 |
| 7 | 7 | 0 | 10 |

Every blocked message was read by hand. None was the #666 shape (a Stage 3
findings report evading the distribution fence); they were merge briefings,
worker-dispatch tables, an intermediate Stage-2 progress table, and Stage 4
output written under a localized header instead of `## Actions Executed`. So the
corpus holds **zero organic instances of the defect** — it is latent, reachable
by code path, and every budget increment above the documented round-trip buys
false positives with no measured catch. 2 is therefore the smallest value that
still closes the flow #1098 reported, and the largest one that is free on this
corpus.

The scan covered 985 transcripts under `~/.claude/projects`; 25 genuine
invocations were found (36 user records match the invocation string, 11 of them
tool results quoting it), of which 24 reached a table report and 1 did not.

A marker body written before #1098 carries no `turns_remaining`; it is read as a
full budget, so an in-flight retrospect keeps its arm across the upgrade. A
corrupt body reads the same way.

If the decaying rewrite cannot land the hook falls back to clearing the marker —
but on the one cause that blocks the rewrite outright, an **unwritable state
directory**, `unlink` is blocked by the same permission, so the marker freezes at
its current budget. Measured: a marker at budget 3 in a `chmod 500` directory sat
unchanged through five ordinary turns. A full disk (`ENOSPC`) blocks the write
but not the unlink, so there the clear succeeds.

A frozen marker only keeps the #666 gate armed while `retrospect-mix-check.sh`
still resolves to that same file. An unwritable **cache** directory does not do
that: `praxis_resolve_writable` and `resolve_cache_file` both fall back to
`${TMPDIR}`, so the frozen file under `<PRAXIS_HOME>/cache` is orphaned and the
gate reads as disarmed. The armed-and-frozen state needs the *resolved* directory
to lose write permission after the marker landed in it — measured by arming with
the cache dir already unwritable, so the marker lands in `${TMPDIR}`, then making
`${TMPDIR}` unwritable: two ordinary turns later the Stop hook still blocks.

Nothing in this hook can recover from that, but the user can, and any one of
these clears it: start a new session (the marker is session-keyed), point
`TMPDIR` at a writable directory, or delete the file directly —
`chmod u+w "$TMPDIR" && rm "$TMPDIR/praxis-retrospect-active-<session_id>.json"`.
Recorded here rather than hidden behind a fallback that does not fire.

## State file

Priority order:

1. `PRAXIS_RETROSPECT_ACTIVE_FILE` env var (explicit override for tests).
2. `<PRAXIS_HOME>/cache/retrospect-active-${session_id}.json` — the canonical
   praxis hook session key (same field used by `session-intent`,
   `retrospect-mix-check.sh`, `strike-counter.sh`). Pre-[#903](https://github.com/devseunggwan/praxis/issues/903)
   this lived under `${TMPDIR}`, and that path is still adopted if present.
3. `${PPID}` replaces `${session_id}` in the filename when no `session_id` is
   supplied (direct CLI / test usage).

The file body
(`{"retrospect_active": true, "source": "skill|slash", "turns_remaining": N}`)
is a hint; **existence is the signal** — the Stop hook reads no field of it.
`turns_remaining` is consumed only by this hook's own decay. Writes are atomic (temp + rename) so a
concurrent Stop-hook read never sees a truncated file.

## Fail-safe

The hook **never blocks** — it only records side-effect state. Malformed
payloads, unreadable/unwritable state, and missing fields all exit 0 silently.

## Pairs with

`hooks/completion-verify/retrospect-mix-check/spec.md` (the #666 gate that
consumes this marker).
