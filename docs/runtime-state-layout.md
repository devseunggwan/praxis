# Praxis runtime state layout

Praxis hooks and skills write runtime files under five roots. Since praxis is
multi-platform (Claude, Codex, Cursor), these live under a
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
| `~/.praxis/telemetry/`     | fire / bypass ledgers (daily; gzip + 30d sweep)    | `hooks/_lib/_fire_ledger.py`                 | `PRAXIS_FIRE_TELEMETRY_FILE`, `PRAXIS_HOME` |
| `~/.praxis/docs/specs/`    | Feature specs ([`spec-store.md`](spec-store.md))   | `praxis_specs_dir()`                         | `PRAXIS_HOME`                               |

The spec store is the one root praxis does not write: a person authors those
files and `praxis:spec-drift` only reads them. It is here because it is
relocated by the same knob and must resolve identically from both halves of the
resolver, not because praxis generates it.

`PRAXIS_HOME` relocates the whole tree — that is the single knob, and every
runtime path praxis writes goes through one of the resolvers above so it stays
true. `resolve_writable` falls back to `${TMPDIR}/praxis-<file>` when the home
dir is not writable, and never raises.

## Durable state (`~/.praxis/state/`)

| File                                                | Producer                                                                                                     | Consumers                                                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `strikes/<sid>.json`, `strikes/.current-session`, … | [`strike-counter`](../hooks/completion-verify/strike-counter/spec.md)                                        | strike-counter; read by [`postcompact-context`](../hooks/advisory-nudge/postcompact-context/spec.md) |
| `phantom-path/<hash>`                               | [`external-write-path-existence-check`](../hooks/advisory-nudge/external-write-path-existence-check/spec.md) | itself (dedup; swept past the cache TTL, #1241)                                                      |

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
  [`hooks/_lib/_hook_runtime.py`](../hooks/_lib/_hook_runtime.py). Rotated by
  size (issue #1282): past 5 MiB (`PRAXIS_HOOK_ERROR_LOG_MAX_BYTES` overrides,
  `0` disables) the file becomes `hook-errors.jsonl.1` and a fresh one starts;
  one predecessor is kept.
- `stop-triggered.log` / `retrospect-mix-blocked.log` — Stop-gate block logs
  from [`completion-verify`](../hooks/completion-verify/completion-verify/spec.md)
  and [`retrospect-mix-check`](../hooks/completion-verify/retrospect-mix-check/spec.md).
  Best-effort appends, rotated to `<name>.1` past 1 MiB by
  `praxis_rotate_log` in [`hooks/_lib/_paths.sh`](../hooks/_lib/_paths.sh)
  (issue #1282) — this directory has no TTL sweep, unlike `cache/` and
  `telemetry/`, so each writer bounds its own file. Before #1182 these lived under an undocumented
  `~/.praxis/scope-confirm/` root; old files are not migrated and a legacy
  `scope-confirm/` directory may linger harmlessly. #1182 was a relocation
  only; until #1282 these append-only files grew without bound.

Fire/bypass telemetry is **not** under `logs/` — it lives at
`~/.praxis/telemetry/` (see [`bypass-telemetry.md`](bypass-telemetry.md)).

## Volatile caches (`~/.praxis/cache/`)

Session-scoped dedup and state files, plus the per-repo gh-label cache.
The session-scoped entries lived under `${TMPDIR}` before #903, and the
gh-label cache in the XDG cache dir until #1182 — in both eras `PRAXIS_HOME`
did not move them:

| Entry                                                              | Producer                                                                                     |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| `session-intent-<sid>.json`                                        | `preflight-gate/session-intent`                                                              |
| `retrospect-active-<sid>.json`, `retrospect-candidates-<sid>.json` | `preflight-gate/retrospect-active-marker` (read by `completion-verify/retrospect-mix-check`) |
| `md-read-history-<sid>.json`                                       | `postuse-correction/pre-edit-md-escape-advisory`                                             |
| `jq-config-advisory-<sid>.json`                                    | `advisory-nudge/jq-config-empty-dict-advisory`                                               |
| `path-probe-gate/`                                                 | `advisory-nudge/path-probe-gate`                                                             |
| `pre-output-falsification-gate/`                                   | `advisory-nudge/pre-output-falsification-gate`                                               |
| `bulk-write-checkpoint/`                                           | `advisory-nudge/bulk-write-memory-checkpoint`                                                |
| `bash-worktree-advisory/`                                          | `advisory-nudge/bash-worktree-existence-advisory`                                            |
| `gh-json-<sid>/`                                                   | `preflight-gate/gh-json-validator`                                                           |
| `worktree-prune-snapshot-<sid>.json`                               | `preflight-gate/worktree-prune-snapshot-gate`                                                |
| `poll-loop-waiters-<sid>.json`                                     | `preflight-gate/foreground-poll-loop-guard`                                                  |
| `approval-premise-ack-<sid>.json`                                  | `preflight-gate/approval-premise-reread-gate` (consumed on read)                              |
| `gh-label-cache.json`                                              | `preflight-gate/gh-label-verify` (per-repo label sets, #1182 — pre-#1182 XDG location not migrated) |
| `stop-scan-<hook>-<sid>.json`                                      | Stop gates' resumable reduction cursors (`reduce_transcript_resumable`, #1237)               |
| `scan-<hook>-<part>-<sid>.json`                                    | Resumable scan cursors of the three commit-path scanners and `retrospect-mix-check` (#1280)  |

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
