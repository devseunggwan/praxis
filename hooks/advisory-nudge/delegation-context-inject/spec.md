# SubagentStart Delegation Context Inject

Supported hosts: claude (the `SubagentStart` event exists only in Claude Code)

`hooks/advisory-nudge/delegation-context-inject/impl.py` fires on
`SubagentStart` and returns a fixed shared-state isolation contract as
`hookSpecificOutput.additionalContext`. It never blocks.

### Why this exists

A delegated worker overwrote this machine's operational ledger. The delegation
prompt had derived its risk axis from rule vocabulary — "do not write to
external surfaces" — rather than from the **target's reach**, so it forbade
`gh` writes and said nothing about `PRAXIS_HOME`,
`PRAXIS_FIRE_TELEMETRY_FILE`, or `HOME`. The worker pointed praxis's own state
at the live paths and polluted them. It cost a strike.

The prompt was written by hand, and the missing paragraph is exactly the kind
that goes missing. `SubagentStart` fires as the subagent spawns regardless of
what the delegator wrote, so the contract arrives structurally instead of by
recall.

### Why injection is the only available design

The payload carries `agent_id`, `agent_type`, `cwd`, `hook_event_name`,
`prompt_id`, `session_id`, `transcript_path` — and nothing else. There is **no
`task` field**, though the published reference lists one (measured 2026-09-07
on Claude Code 2.1.263; `RUNTIME_CONSTRAINTS.md` entry 8). The delegation
prompt is therefore unreadable here, so the shape "inspect the prompt, warn
when the isolation paragraph is missing" cannot be built on this event. The
hook can only add context, so it adds it unconditionally.

This is a real reduction in capability, not a design preference. A gate that
verified the delegator's prompt would need a different event.

### Why every agent type, with no allowlist

Exempting read-only agents is the obvious optimisation and it is declined.
Agent types are declared by whoever wrote the agent, not by this hook: a type
that is read-only today is one frontmatter edit away from not being, and an
allowlist tracking that would fail **open** — into silence — exactly when it
went stale. The contract is six lines. Paying it on every spawn is cheaper
than maintaining a list whose staleness is invisible.

The cost is real and bounded: the injected text is a fixed string, so the
per-spawn context charge does not grow with the task, the transcript, or the
number of rounds.

### What is injected

A fixed string naming, individually:

- `PRAXIS_HOME`, `PRAXIS_FIRE_TELEMETRY_FILE`, `PRAXIS_STATE_DIR`, `HOME` —
  must not be set or overridden. They are named one by one rather than
  described, because the recorded failure was precisely that a general phrase
  did not make the delegator think of them.
- scratch files belong under the worker's own working directory or `$TMPDIR`,
  never under `~/.praxis` or `~/.claude`
- every path outside the assigned worktree is read-only unless the task named
  it
- the closing line states the rule the incident violated: the risk axis is the
  target's reach, not the verb.

### Response format

`{"hookSpecificOutput": {"hookEventName": "SubagentStart",
"additionalContext": "<contract>"}}` on stdout, exit 0. The
`hookEventName` must mirror the incoming event or the harness discards the
reply.

### Opt-out

`PRAXIS_HOOK_BYPASS_DELEGATION_CONTEXT=1` skips the injection entirely.

### Relationship to sibling hooks

- `postcompact-context` is the convention source for the
  `additionalContext` emission block and for injecting on an event whose
  firing *is* the trigger (no state file, no transcript scan).
- `fan-out-scope-gate` governs how many subagents get spawned; this hook
  governs what each one is told once spawned. They do not overlap.

### Tests

`tests/hooks/advisory-nudge/test_delegation_context_inject.sh` — 6 cases. A
`SubagentStart` payload yields valid JSON whose `hookEventName` is
`SubagentStart` and whose `additionalContext` names every isolation variable;
the bypass env suppresses all output; a malformed payload fails open with no
stdout; and a payload carrying `task` produces identical output, so no later
edit can start depending on a field the runtime does not send.

One case pins the **rules** rather than the variable names. Review found that
the variable-name check passes even when a whole clause is deleted, because all
four names live inside a single rule — measured by removing the scratch-path
clause and watching the suite still report 5/5. Each of the four rules is now
asserted by a distinctive phrase, and deleting any one of them names that rule
in the failure.
