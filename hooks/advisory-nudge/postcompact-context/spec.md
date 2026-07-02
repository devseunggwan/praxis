# UserPromptSubmit Post-Compaction Context Injection

Supported hosts: claude

`hooks/advisory-nudge/postcompact-context/impl.py` detects whether the most
recent Claude Code compaction has been "carried over" into the next prompt,
and if not, injects a `hookSpecificOutput.additionalContext` block summarising
the surviving session state (session_id, cwd, branch, active PR, strike
state).

### Why this exists (issue #472)

The original idea (#466) was to use a `PreCompact` hook to seed the
compaction summary with carry-over context. The Wave 0 probe falsified that
premise: Claude Code's `PreCompact` event accepts only
`decision` / `reason` / `continue` / `stopReason` / `suppressOutput` /
`systemMessage` — there is no
`hookSpecificOutput.additionalContext` channel at that event
([docs](https://code.claude.com/docs/en/hooks)).

The transcript JSONL however records each compaction as a `user`-type record
with `isCompactSummary: true` and a stable `uuid`. `UserPromptSubmit` DOES
support `additionalContext`, so the same goal — make the post-compaction
prompt aware of carried-over session state — is achieved by detecting the
compaction marker in the transcript tail and injecting once on the first
prompt after it.

### Trigger criteria

The advisory fires when **all** are true:

1. The hook payload carries `transcript_path`, `session_id`, and `cwd`.
2. The transcript tail (last N lines, default 100) contains at least one
   `{"type": "user", "isCompactSummary": true, "uuid": <str>, ...}` entry.
3. The most-recent matching entry's `uuid` differs from the
   `last_compact_uuid_emitted` field in the per-session state file — i.e.
   no prior `UserPromptSubmit` in this session has already injected this
   compaction.

When all three hold, the hook emits the `additionalContext` JSON and
updates the state file. On the next prompt the dedup gate keeps the hook
silent.

### Context payload

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

Compaction marker uuid=<uuid> timestamp=<ts>. This context is injected once
per compaction event; subsequent prompts will not repeat it.
```

When a source is unavailable (no PR, no strikes, detached HEAD) the field
degrades gracefully — `(none for current branch)` / `0/3` /
`(detached/unknown)` — instead of being dropped.

### Configuration

| Env var | Default | Scope | Effect |
| --------- | --------- | ------- | -------- |
| `PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT` | unset | hook | When `1`, exits silently before reading stdin |
| `PRAXIS_POSTCOMPACT_CONTEXT_FILE` | unset | hook | Explicit dedup state file path (test override) |
| `PRAXIS_POSTCOMPACT_TAIL_LINES` | `100` | hook | Transcript tail line count to scan; non-int / <1 falls back to default |
| `PRAXIS_STATE_DIR` | `~/.praxis/state` | external lookup only | Strike-counter state directory the hook *reads* (host-neutral default, #527; falls back to the legacy `~/.claude/state/praxis` when unset and the new location is absent); does NOT redirect this hook's own dedup state file |

### State file

```
${TMPDIR:-/tmp}/praxis-postcompact-context-${session_id}.json
```

Shape: `{"last_compact_uuid_emitted": "<uuid>"}`.

Path resolution priority:

1. `PRAXIS_POSTCOMPACT_CONTEXT_FILE` env var (explicit override, used by tests).
2. `session_id` from the payload — stable across hook invocations within a
   single Claude Code session, same field consumed by `strike-counter` and
   `session-intent`.

The sibling `session-intent` hook keeps a PPID fallback to support direct
CLI / test invocation without a payload; this hook does not, because
`main()` rejects any payload with missing `session_id` before reaching
`resolve_state_path`, making the fallback unreachable. Tests use the
explicit env override instead.

### Response format

Success path:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "<context body>"
  }
}
```

exit 0. Every other path (bypass, missing payload field, no compaction in
tail, already injected, infrastructure error) is **silent** — no stdout, no
stderr, exit 0. The hook never blocks the prompt.

### Fail-open contract

- bypass env set → silent
- malformed JSON stdin → silent
- missing `transcript_path` / `session_id` / `cwd` → silent
- transcript file unreadable / absent → silent
- no `isCompactSummary` in tail → silent
- compaction record missing `uuid` → silent
- state file unreadable / unwritable → emit/skip best-effort, never crash
- `git` / `gh` absent or non-zero → field degrades, hook continues
- uncaught exception in inner logic → swallowed, exit 0

### Host filtering

`hosts: ["claude"]` in `hooks/manifest.json`. The compaction mechanic and
the `transcript_path` payload field are Claude Code-specific; other hosts
(Codex, Cursor, OpenCode, Gemini) have different session-shrinking
semantics, so the hook is not emitted on their platforms.

### Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `strike-counter` (SessionStart) | re-emits strike count when session resumes | Complementary — strike-counter fires on session boot; this hook fires after mid-session compaction |
| `session-intent` (UserPromptSubmit) | classifies first-prompt read-intent | None — disjoint signal; session-intent reads `prompt`, this hook reads `transcript_path` |
| `codex-review-route` (UserPromptSubmit) | warn on multi-worktree `/codex:review` | None — different trigger surface |
| `path-probe-gate` | guard Edit/Write nested path | Complementary — that hook addresses the consequence (post-compaction path-guessing); this hook addresses the upstream signal |

### Known limitations

| Case | Behaviour |
| ------ | ----------- |
| Compaction summary older than `PRAXIS_POSTCOMPACT_TAIL_LINES` lines | Silent — the dedup ensures correctness; the cost is "first prompt after compaction injects nothing if tail window is too small". Tune via env var on very chatty sessions |
| Multiple compactions between prompts | Only the **most recent** is injected. Older compactions are considered already-superseded |
| Non-Claude transcript schema variant | Silent — defensive `dict` / type checks degrade to no-op |
| Branch with no open PR | `active PR : (none for current branch)` — explicit absence rather than dropped field |
| `gh` not installed / not authenticated | PR field reads `(none for current branch)` — fail-open |
| Worktree with detached HEAD | `branch : (detached/unknown)`; PR lookup skipped |
| State file path permission denied (read-only `$TMPDIR`) | Hook still emits the context but cannot dedup; next prompt re-emits. Worst case is duplicate context, never crash |

### Tests

```bash
bash tests/hooks/advisory-nudge/test_postcompact_context.sh
```

Cases cover: emit on synthetic compaction record, silent when no compaction
in tail, dedup on second invocation with same uuid, re-emit when uuid
changes, fail-open on malformed/missing payload fields, bypass env, custom
tail-lines env var, fixture transcript with multiple compactions, strike
state integration, branch / PR field degradation.
