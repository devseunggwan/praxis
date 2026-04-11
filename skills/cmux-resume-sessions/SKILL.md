---
name: cmux-resume-sessions
description: >
  Restore cmux workspaces from a JSON snapshot saved by cmux-save-sessions.
  Use this when you want to rehydrate an intentionally saved layout with NO crash context.
  Crash routing override: if the request mentions a crash, power loss, OOM, or "살려야",
  route to cmux-recover-sessions instead — even when the user also mentions a snapshot,
  because the snapshot may be stale and .jsonl scanning reflects the real latest state.
  Triggers on "resume sessions", "session resume", "session restore", "restore sessions", "cmux resume", "restore from snapshot", "rehydrate sessions", "세션 복원", "스냅샷 복구", "스냅샷 복원".
---

# cmux Resume Sessions

> ⚠️ **Wrong skill?** If your sessions died from a crash / power loss / OOM kill,
> use **`cmux-recover-sessions`** instead. That skill scans `.jsonl` files on
> disk and finds sessions you never explicitly saved. Resume only works on a
> JSON snapshot you produced earlier with `cmux-save-sessions`.

## Overview

Restores cmux workspaces from a JSON snapshot saved by `cmux-save-sessions`.
Restores workspace structure (name, cwd) and continues Claude Code conversations automatically.

> **Role separation**:
> - `cmux-resume-sessions` (this skill): Intentional restore from a JSON snapshot you saved on purpose (file-based)
> - `cmux-recover-sessions`: Post-crash/power-loss recovery from `.jsonl` files Claude Code persists automatically (process-based)

## The Iron Law

```
RESUME RESTORES STRUCTURE AND CONTINUES CONVERSATIONS.
```

Resume restores workspace structure (name, cwd) and runs `claude --continue` to pick up the most recent conversation in each directory.
It does NOT restore runtime state of previously running commands or sessions.

## When to Use

- Restore a workspace layout from a `cmux-save-sessions` snapshot
- Rehydrate yesterday's working set at the start of a new day
- Move a session layout to another machine (snapshot → transfer → resume)
- Triggers: "resume sessions", "session restore", "session resume", "cmux resume", "restore sessions"

> **Not for crash recovery** — after a power loss, use `cmux-recover-sessions` (scans `.jsonl` files directly).

## Commands

### `resume [snapshot]` — Restore sessions from snapshot

**How to run:**
1. User requests "resume sessions", "session restore", etc.
2. Snapshot selection:
   - No argument: use the most recent snapshot
   - Filename or full path specified: use that snapshot
3. Execute:
```bash
bash "$(dirname "$0")/cmux-resume-sessions" [snapshot-file]
```
4. Show output to the user

**What gets restored:**
- Creates a cmux workspace per session (with `--cwd` for working directory)
- Sets workspace name to match the saved name
- Runs `claude --resume <session-id>` if session ID is available, otherwise `claude --continue` (continues the most recent conversation for that cwd)
- Sessions with non-existent cwd are skipped (with warning)
- Duplicate workspaces (same name already exists) are skipped automatically

**Flags:**
- `--no-claude`: Skip auto-starting Claude Code (restore workspace structure only)

**What is NOT restored:**
- Previously running commands
- Session runtime state (git status, open editors, etc.)

> ⚠️ **Resumed sessions render from the first message.** Claude Code re-renders
> a resumed conversation starting at the oldest message, so a workspace will
> *look* like it reverted to its earliest state.
>
> Which command fires for each workspace depends on what the snapshot
> captured:
> - `claude --resume <session-id>` — used when the snapshot carries a
>   concrete session id. This *usually* reopens that exact transcript,
>   but it is not a guarantee (stale session id, partial flush at save
>   time, or a truncated tail can all surface as unexpected context).
> - `claude --continue` — the fallback when the snapshot omitted a
>   session id. This attaches to the cwd's most recent conversation for
>   that working directory, which may be a completely different chain
>   from the one you saved. See the *Rationalization Prevention* section
>   at the bottom for the exact failure mode.
>
> Always verify each restored workspace before trusting it:
> - scroll the viewport to the bottom, or
> - ask the model directly: *"what was the last thing we worked on?"*

## Output Example

```
Resuming from: sessions-20260407-143000.json
  Saved at: 2026-04-07T14:30:00+0900 | Host: macbook-pro.local | Sessions: 7

  ✓ Review PR comments → workspace:150 (/Users/nathan.song/projects/hub)
  ✓ Fix auth bug → workspace:151 (/Users/nathan.song/projects/backend)
  ⚠ SKIP: Old worktree task (cwd not found: /tmp/wt-deleted)
  ✗ FAIL: Broken session

Done. Created: 2 | Skipped: 1 | Failed: 1
```

## Integration

- **cmux-save-sessions**: Produces the input data for this skill
- **cmux-session-manager**: Use `status` to verify results after restore
- **cmux-orchestrator**: Can restart workers in restored workspaces

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| "cmux is not running" | cmux app not running | Start cmux app |
| "jq is required" | jq not installed | `brew install jq` |
| "cwd not found" | Directory was deleted since save | Session is auto-skipped |
| "No snapshots" | No saved snapshots exist | Save first with `cmux-save-sessions` |
| Duplicate sessions created | Overlap with already-open sessions | Check existing sessions before restore |

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Restore every snapshot at once" | Old snapshots point to cwd paths that no longer exist. Restore the most recent that's still valid. |
| "Skip `--no-claude`, always auto-start Claude" | If the target cwd's recent conversation is stale, auto-continue lands in the wrong context. Use `--no-claude` when in doubt. |
| "Ignore duplicate warnings" | Duplicate workspaces are noise at best, collision at worst. Inspect existing sessions first. |
| "Restore before verifying the snapshot's host" | Cross-machine restore may succeed with stale paths. Check `hostname` in the JSON before restoring on a new host. |
