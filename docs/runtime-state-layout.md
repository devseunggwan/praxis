# Praxis runtime state layout

Praxis hooks and skills write four kinds of runtime files. Since praxis is
multi-platform (Claude, Codex, Cursor, Gemini, OpenCode), these live under a
**host-neutral** `~/.praxis` root rather than the Claude-nested legacy location.
The resolver is [`hooks/_lib/_paths.py`](../hooks/_lib/_paths.py), mirrored for
pure-shell hooks by [`hooks/_lib/_paths.sh`](../hooks/_lib/_paths.sh) — the two
must stay in agreement, since the writer and reader halves of a protocol can sit
on opposite sides of that split.

## Roots

| Root                       | Purpose                                            | Resolver                                     | Override                                    |
| -------------------------- | -------------------------------------------------- | -------------------------------------------- | ------------------------------------------- |
| `~/.praxis/state/`         | Durable, cross-session state                       | `praxis_state_dir()`                         | `PRAXIS_STATE_DIR` (base), `PRAXIS_HOME`    |
| `~/.praxis/cache/`         | Regenerable, session-scoped caches / dedup markers | `praxis_cache_dir()`, `resolve_cache_file()` | `PRAXIS_HOME`, per-file env                 |
| `~/.praxis/logs/`          | Diagnostics                                        | `resolve_writable("logs", …)`                | `PRAXIS_HOME`, per-file env                 |
| `~/.praxis/agent-reports/` | cmux-delegate completion reports                   | `skills/cmux-delegate/agent-report-path.sh`  | `PRAXIS_HOME`                               |
| `~/.praxis/telemetry/`     | fire / bypass event ledgers (daily rotation)       | `hooks/_lib/_fire_ledger.py`                 | `PRAXIS_FIRE_TELEMETRY_FILE`, `PRAXIS_HOME` |
| `~/.praxis/scope-confirm/` | Stop-gate block logs                               | `praxis_resolve_writable scope-confirm …`    | `PRAXIS_HOME`                               |

`PRAXIS_HOME` relocates the whole tree — that is the single knob, and every
runtime path praxis writes goes through one of the resolvers above so it stays
true. `resolve_writable` falls back to `${TMPDIR}/praxis-<file>` when the home
dir is not writable, and never raises.

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

## Volatile caches (`~/.praxis/cache/`)

Session-scoped dedup and state files. All of these lived under `${TMPDIR}`
before #903, which meant `PRAXIS_HOME` did not move them:

| Entry                                                              | Producer                                                                                     |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| `session-intent-<sid>.json`                                        | `preflight-gate/session-intent`                                                              |
| `retrospect-active-<sid>.json`, `retrospect-candidates-<sid>.json` | `preflight-gate/retrospect-active-marker` (read by `completion-verify/retrospect-mix-check`) |
| `md-read-history-<sid>.json`                                       | `postuse-correction/pre-edit-md-escape-advisory`                                             |
| `postcompact-context-<sid>.json`                                   | `advisory-nudge/postcompact-context`                                                         |
| `jq-config-advisory-<sid>.json`                                    | `advisory-nudge/jq-config-empty-dict-advisory`                                               |
| `path-probe-gate/`                                                 | `advisory-nudge/path-probe-gate`                                                             |
| `pre-output-falsification-gate/`                                   | `advisory-nudge/pre-output-falsification-gate`                                               |
| `bulk-write-checkpoint/`                                           | `advisory-nudge/bulk-write-memory-checkpoint`                                                |
| `bash-worktree-advisory/`                                          | `advisory-nudge/bash-worktree-existence-advisory`                                            |
| `gh-json-<sid>/`                                                   | `preflight-gate/gh-json-validator`                                                           |
| `worktree-prune-snapshot-<sid>.json`                               | `preflight-gate/worktree-prune-snapshot-gate`                                                |

### Sweeping

`${TMPDIR}` came with an OS janitor; `~/.praxis/cache/` does not, and these
entries are session-keyed — one per session, indefinitely. `prune_stale` runs
opportunistically from `resolve_writable("cache", …)` and drops entries past
`PRAXIS_CACHE_TTL_DAYS` (default 7; `0` disables).

### Back-compat (#903)

`resolve_cache_file()` performs a one-time move of a pre-#903
`${TMPDIR}/praxis-<name>` file into the cache root. Without it, a session that
was already retrospect-active or intent-anchored when the upgrade landed would
read the new path as "no state" and silently disarm its gate mid-session.

## Agent reports (`~/.praxis/agent-reports/`)

`cmux-delegate` writes one completion report per delegated task, named
`<sha1(worktree absolute path)>.json`. Before #903 this was `.agent-report.json`
at the worktree root — inside whatever repository the task happened to target.
The delegator derives the same path from `{cwd}` via the shared helper and
checks the report's own `worktree` field before trusting it.

Reports are deliberately **not** swept by `prune_stale`. Absence of a report is
the deterministic "incomplete" signal, so deleting one a delegator has not yet
collected would manufacture exactly the false negative the file-based handoff
exists to eliminate. They are small and one per delegated task.
