---
name: cmux-resume-sessions
description: >
  Restore cmux workspaces from a JSON snapshot saved by cmux-save-sessions.
  Use this when you want to rehydrate an intentionally saved layout with NO crash context.
  Crash routing override: if the request mentions a crash, power loss, OOM, or 살려야,
  route to cmux-recover-sessions instead — even when the user also mentions a snapshot,
  because the snapshot may be stale and .jsonl scanning reflects the real latest state.
when_to_use: >
  Triggers on "resume sessions", "session resume", "session restore", "cmux resume", "restore from snapshot", "rehydrate sessions", "세션 복원", "스냅샷 복구", "스냅샷 복원".
verified-against-runtime: true
runtime-verified-at: 2026-05-28
runtime-verified-note: "jq 1.x // empty + bash hostname (cmux 1.x snapshot schema: top-level .hostname) — silent-proceed on missing field / null / empty string; emits real value when present; matches against $(hostname). 4 synthetic + 1 real-snapshot cases probed."
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

Resume restores workspace structure (name, cwd) and, per session, runs `claude --resume <session-id>` when the snapshot carries a session id — falling back to `claude --continue` (the cwd's most recent conversation) when it does not — to pick up the prior conversation in each directory.
It does NOT restore runtime state of previously running commands or sessions.

## When to Use

- Restore a workspace layout from a `cmux-save-sessions` snapshot
- Rehydrate yesterday's working set at the start of a new day
- Move a session layout to another machine (snapshot → transfer → resume)

> **Not for crash recovery** — after a power loss, use `cmux-recover-sessions` (scans `.jsonl` files directly).

## Commands

### `resume [snapshot]` — Restore sessions from snapshot

**How to run:**
1. User requests "resume sessions", "session restore", etc.
2. Snapshot resolution — resolve `snapshot` to a full path before the gate, mirroring the CLI's logic at `cmux-resume-sessions` lines 5-13, 23-30 so the gate sees the same file the CLI will open. Mirror the CLI parser exactly: consume `--no-claude` (the only recognized flag), and treat **every other token** — including unknown `--flag` strings — as a positional candidate, taking the first one as the snapshot candidate:
```bash
SAVE_DIR="$HOME/.cmux/sessions"
arg=""
for a in "$@"; do
  case "$a" in
    --no-claude) ;;  # only recognized flag — skip (mirror of CLI line 10)
    *) arg="$a"; break ;;  # every other token, including unknown --flag, is positional (mirror of CLI line 11)
  esac
done
# arg is now "" (no positional) or a bare filename / full path / a literal "--flag" the CLI would also pass through
if [[ -z "$arg" ]]; then
  snapshot=$(ls -t "$SAVE_DIR"/sessions-*.json | head -1)
elif [[ -f "$arg" ]]; then
  snapshot="$arg"
elif [[ -f "$SAVE_DIR/$arg" ]]; then
  snapshot="$SAVE_DIR/$arg"
else
  echo "ERROR: snapshot '$arg' not found in cwd or $SAVE_DIR" >&2; exit 1
fi
```
3. **Hostname mismatch gate** — verify the snapshot's saved host matches the current machine before restoring:
```bash
saved_host=$(jq -r '.hostname // empty' "$snapshot")
current_host=$(hostname)
```
   - If `${saved_host}` is empty (legacy snapshot without the field): proceed silently.
   - If `${saved_host}` equals `${current_host}`: proceed silently.
   - If `${saved_host}` differs from `${current_host}`: surface `AskUserQuestion`:
     - Question: `"이 스냅샷은 다른 머신 (${saved_host}) 에서 저장되었습니다. 현재 머신은 ${current_host} 입니다. cwd 경로가 맞지 않을 수 있어 세션 다수가 'cwd not found' 로 스킵될 수 있습니다. 계속할까요?"`
     - Options: `"계속 진행"` / `"취소"`
   - If user selects `"취소"`: abort with `"Resume 취소됨 — 호스트 불일치 (${saved_host} → ${current_host})."`
4. Execute (only after the hostname gate passes). Pass through all original args (`"$@"`) so flags like `--no-claude` survive:
```bash
bash "${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/skills/cmux-resume-sessions/cmux-resume-sessions" "$@"
```
5. Show output to the user

If `CLAUDE_PLUGIN_ROOT` is unset the `:?` guard aborts with `praxis plugin root
not set`; resolve it from the installed-plugins manifest
(`jq -r '.plugins["praxis@praxis"][0].installPath // empty' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"`),
export it, and re-run — do not fall back to a relative path.

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

  ✓ Review PR comments → workspace:150 (/Users/dev/projects/my-repo)
  ✓ Fix auth bug → workspace:151 (/Users/dev/projects/backend)
  ⚠ SKIP: Old worktree task (cwd not found: /tmp/wt-deleted)
  ✗ FAIL: Broken session

Done. Created: 2 | Skipped: 1 | Failed: 1
```

## Integration

- **cmux-save-sessions**: Produces the input data for this skill
- **cmux-session-manager**: Use `status` to verify results after restore

## Troubleshooting

| Problem | Cause | Fix |
| --------- | ------- | ----- |
| "cmux is not running" | cmux app not running | Start cmux app |
| "jq is required" | jq not installed | `brew install jq` |
| "cwd not found" | Directory was deleted since save | Session is auto-skipped |
| "No snapshots" | No saved snapshots exist | Save first with `cmux-save-sessions` |
| Duplicate sessions created | Overlap with already-open sessions | Check existing sessions before restore |

## Rationalization Prevention

| Excuse | Reality |
| -------- | --------- |
| "Restore every snapshot at once" | Old snapshots point to cwd paths that no longer exist. Restore the most recent that's still valid. |
| "Skip `--no-claude`, always auto-start Claude" | If the target cwd's recent conversation is stale, auto-continue lands in the wrong context. Use `--no-claude` when in doubt. |
| "Ignore duplicate warnings" | Duplicate workspaces are noise at best, collision at worst. Inspect existing sessions first. |
| "Restore before verifying the snapshot's host" | Cross-machine restore may succeed with stale paths. The hostname mismatch gate (Step 3 in `resume` "How to run") surfaces an `AskUserQuestion` when `jq -r '.hostname'` differs from `$(hostname)` — do not click through without inspecting the cwd paths. |
