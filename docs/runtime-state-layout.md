# Praxis runtime state layout

Praxis hooks write three kinds of runtime files. Since praxis is multi-platform
(Claude, Codex, Cursor, Gemini, OpenCode), these live under a **host-neutral**
`~/.praxis` root rather than the Claude-nested legacy location. The resolver is
[`hooks/_lib/_paths.py`](../hooks/_lib/_paths.py).

## Roots

| Root               | Purpose                                            | Resolver                      | Override                                 |
| ------------------ | -------------------------------------------------- | ----------------------------- | ---------------------------------------- |
| `~/.praxis/state/` | Durable, cross-session state                       | `praxis_state_dir()`          | `PRAXIS_STATE_DIR` (base), `PRAXIS_HOME` |
| `~/.praxis/cache/` | Regenerable, session-scoped caches / dedup markers | `praxis_cache_dir()`          | `PRAXIS_HOME`                            |
| `~/.praxis/logs/`  | Diagnostics                                        | `resolve_writable("logs", …)` | `PRAXIS_HOME`, per-file env              |

`PRAXIS_HOME` relocates the whole tree (used by tests for isolation).
`resolve_writable` falls back to `${TMPDIR}/praxis-<file>` when the home dir is
not writable, and never raises.

## Durable state (`~/.praxis/state/`)

| File                                                | Producer                                                                                                     | Consumers                                                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `strikes/<sid>.json`, `strikes/.current-session`, … | [`strike-counter`](../hooks/completion-verify/strike-counter/spec.md)                                        | strike-counter; read by [`postcompact-context`](../hooks/advisory-nudge/postcompact-context/spec.md) |
| `phantom-path/<hash>`                               | [`external-write-path-existence-check`](../hooks/advisory-nudge/external-write-path-existence-check/spec.md) | itself (dedup)                                                                                       |

### Back-compat and migration (#527)

- An explicit `PRAXIS_STATE_DIR` override always wins — pre-#527 deployments that
  set it keep their existing location.
- When no override is set, the default moved from `~/.claude/state/praxis` to
  `~/.praxis/state`. To preserve continuity:
  - **strike-counter** performs a one-time copy of
    `~/.claude/state/praxis/strikes` into the new location on first run if the
    new location is absent.
  - **postcompact-context** read-falls-back to the legacy location when the new
    one has no entry for the session.
  - **phantom-path** markers are regenerable dedup state, so no fallback is
    needed — a relocated marker simply lets one advisory re-fire once.

## Logs (`~/.praxis/logs/`)

- `hook-errors.jsonl` — swallowed-exception log from the shared `@fail_open`
  guard (`PRAXIS_HOOK_ERROR_LOG` overrides). See
  [`hooks/_lib/_hook_runtime.py`](../hooks/_lib/_hook_runtime.py).
- bypass telemetry — see [`bypass-telemetry.md`](bypass-telemetry.md).

## Volatile caches (`~/.praxis/cache/`) — follow-up

The session-scoped `${TMPDIR}/praxis-*` dedup files (jq-config, path-probe,
bulk-write, pre-output-falsification, bash-worktree, session-intent,
postcompact-context, md-read-history, gh-json) are tracked for migration onto
`praxis_cache_dir()` / `resolve_writable("cache", …)` in #527's follow-up. The
foundation (`praxis_cache_dir()`) is in place; the consumers are swept
separately to keep each hook's dedup-behaviour change reviewable in isolation.
