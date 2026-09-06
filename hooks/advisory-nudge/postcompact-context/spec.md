# SessionStart(compact) Post-Compaction Context Injection

Supported hosts: claude

`hooks/advisory-nudge/postcompact-context/impl.py` runs on the `SessionStart`
event with manifest matcher `compact` — the session start Claude Code raises
after a context compaction — and injects a `hookSpecificOutput.additionalContext`
block summarising the surviving session state (session_id, cwd, branch,
active PR, strike state) so the post-compaction turn starts with it.

## Why this exists (issues #466, #472, #1339)

Three events touch a compaction, and only one of them carries context back
to the model. Quotes are from <https://code.claude.com/docs/en/hooks> and
<https://code.claude.com/docs/en/hooks-guide>, read 2026-09-06:

- **`PreCompact`** — the original #466 design. It accepts only top-level
  `decision` / `reason` / `continue` / `stopReason` / `suppressOutput` /
  `systemMessage`; there is no `hookSpecificOutput.additionalContext` channel
  at that event. The #472 finding still holds.
- **`PostCompact`** — "hooks have no decision control … Claude Code discards
  a PostCompact hook's `systemMessage` and `continue` fields." No
  `additionalContext` channel either.
- **`SessionStart`** — matcher values `startup`, `resume`, `clear`, `compact`,
  `fork`; the input carries `source`; decision control supports
  `hookSpecificOutput.additionalContext` with `hookEventName: "SessionStart"`,
  and plain stdout is also added to context. The hooks guide's recipe titled
  "Re-inject context after compaction" is exactly `SessionStart` +
  `"matcher": "compact"`.

Until #1339 this hook lived on `UserPromptSubmit` and answered "did a
compaction just happen?" from the outside: it seek-read the transcript tail
for the `{"type": "user", "isCompactSummary": true, "uuid": …}` record
(#472, bounded in #1155), kept a per-session state file with the last injected
uuid so the marker still sitting in the tail on later prompts would not
re-inject, and serialized that read-modify-write with `state_lock` after
issue #1034 had measured the unlocked write tearing 5 of 300 concurrent
pairs. All of that was scaffolding for a trigger the runtime now raises directly:
`SessionStart(compact)` fires once per compaction, so the event is the
trigger and the scan, the uuid state, the lock, and
`PRAXIS_POSTCOMPACT_TAIL_LINES` are removed with it.

**Coverage**: the hooks reference's `SessionStart` matcher table reads
`compact` — "Auto or manual compaction" (read 2026-09-06), so one registration
covers both `/compact` and the automatic compaction that fires when the
context window fills. That is documented behaviour, not a live measurement:
the firing has not been observed in a session from this repo yet.

## Trigger criteria

The hook emits when **all** are true:

1. `PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT` is not `1`.
2. The payload's `source`, when present, is `compact`. The manifest matcher
   already restricts delivery; the check is a belt for a host that ignores
   the matcher, so context never lands on a `startup` / `resume` / `clear` /
   `fork` session that has no compaction boundary. A missing `source` is
   accepted.
3. The payload carries `session_id` and `cwd`.

There is no dedup state: one event, one injection.

## Context payload

```
📎 Praxis post-compaction context

Session state carried across the compaction boundary:
  • session_id : <uuid from payload>
  • cwd        : <worktree absolute path>
  • branch     : <git branch --show-current>
  • active PR  : #N — <title>
                 <url>
  • strikes    : K/3
      1. <reason 1>
      2. <reason 2>

Injected by the SessionStart(compact) hook once per compaction; later prompts
will not repeat it.
```

When a source is unavailable (no PR, no strikes, detached HEAD) the field
degrades gracefully — `(none for current branch)` / `0/3` /
`(detached/unknown)` — instead of being dropped.

## Configuration

| Env var | Default | Scope | Effect |
| --------- | --------- | ------- | -------- |
| `PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT` | unset | hook | When `1`, exits silently before reading stdin |
| `PRAXIS_POSTCOMPACT_GIT_TIMEOUT` / `PRAXIS_POSTCOMPACT_GH_TIMEOUT` | `1.5` / `3.0` | test override | Subprocess timeouts in seconds; the suite raises them so CPU contention cannot fake an empty PR field |
| `PRAXIS_STATE_DIR` | `~/.praxis/state` | external lookup only | Strike-counter state directory the hook *reads* (host-neutral default, #527; falls back to the legacy `~/.claude/state/praxis` when unset and the new location is absent) |

The hook keeps no state file of its own. `PRAXIS_POSTCOMPACT_CONTEXT_FILE`
and `PRAXIS_POSTCOMPACT_TAIL_LINES` were removed in #1339 together with the
scan and the dedup they configured; the
[`DESIGN.md → Session-state concurrency`](../../../DESIGN.md#session-state-concurrency)
table no longer carries a row for this hook, and the #1034 measurement stays
on record in
[`docs/hook-state-concurrency-measurements.md`](../../../docs/hook-state-concurrency-measurements.md)
as history.

## Time budget

`git` (1.5s) + `gh` (3.0s) + ~0.5s interpreter startup is a 5.0s ceiling
under the manifest's `timeout: 8`. The previous design added up to 2.0s of
`state_lock` acquisition on top; that term is gone.

## Response format

Success path:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "<context body>"
  }
}
```

exit 0. Every other path (bypass, non-compact `source`, missing payload
field, infrastructure error) is **silent** — no stdout, no stderr, exit 0.
The hook never blocks the session.

## Fail-open contract

- bypass env set → silent
- malformed JSON stdin → silent
- `source` present and not `compact` → silent
- missing `session_id` / `cwd` → silent
- `git` / `gh` absent or non-zero → field degrades, hook continues
- uncaught exception in inner logic → swallowed, exit 0

## Host filtering

`hosts: ["claude"]` in `hooks/manifest.json`. The `SessionStart` `compact`
matcher and its `source` field are Claude Code-specific; other hosts
(Codex, Cursor) have different session-shrinking semantics, so the hook is
not emitted on their platforms.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `strike-counter` (SessionStart, no matcher) | re-emits strike count on every session start | Complementary — on a `compact` start both fire; strike-counter carries the count, this hook carries the count plus cwd / branch / PR in one block |
| `session-intent` (UserPromptSubmit) | classifies first-prompt read-intent | None — different event |
| `path-probe-gate` | guard Edit/Write nested path | Complementary — that hook addresses the consequence (post-compaction path-guessing); this hook addresses the upstream signal |

## Known limitations

| Case | Behaviour |
| ------ | ----------- |
| Automatic compaction vs `/compact` | Both documented under the one `compact` matcher — "Auto or manual compaction"; not yet observed live from this repo — see *Coverage* above |
| Host delivers a non-`compact` `source` to this matcher | Silent; the matcher contract is the host's, the guard only refuses to inject on the wrong start |
| Branch with no open PR | `active PR : (none for current branch)` — explicit absence rather than dropped field |
| `gh` not installed / not authenticated | PR field reads `(none for current branch)` — fail-open |
| Worktree with detached HEAD | `branch : (detached/unknown)`; PR lookup skipped |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_postcompact_context.sh
```

Cases cover: emit on `source: "compact"` with `hookEventName: "SessionStart"`,
emit when `source` is absent, silent on every other `source`, bypass env,
fail-open on malformed / field-missing payloads, missing `git` / `gh` still
exits 0 inside the budget, strike state integration, branch / PR field
degradation.
