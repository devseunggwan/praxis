---
name: cmux-save-sessions
description: >
  Save cmux session list as a JSON snapshot. Current session excluded by default.
  Supports save and list commands.
  Triggers on "save sessions", "session save", "session snapshot", "cmux save", "list snapshots", "snapshot list".
---

# cmux Save Sessions

## Overview

Saves the current state of cmux workspaces as a JSON snapshot.
Snapshots are used for session recovery, history recording, sharing, and as input data for other skills.

> **Role separation**:
> - `cmux-save-sessions`: Capture current state as JSON (save)
> - `cmux-resume-sessions`: Restore workspaces from JSON snapshot (restore)
> - `cmux-recover-sessions`: Post-crash/power-loss recovery from tmux sessions (emergency)
> - `cmux-session-manager`: Real-time status + cleanup (daily)

## The Iron Law

```
SAVE CAPTURES TRUTH. CURRENT SESSION IS EXCLUDED BY DEFAULT.
```

Save records the exact state at the current moment.
The session running this script (the manager session) is excluded by default — only work sessions are saved.

## When to Use

- Capture the current workspace layout before a system reboot or cleanup
- Create a shareable snapshot of a multi-workspace setup for another machine
- Record daily work state for later `cmux-resume-sessions` restore
- Feed input data to other skills that need a session list
- Triggers: "save sessions", "session save", "session snapshot", "cmux save", "list snapshots", "snapshot list"

> **Not for crash recovery** — after a power loss, use `cmux-recover-sessions` (reads `.jsonl` files directly).

## Commands

### `save` — Save session snapshot

**How to run:**
1. User requests "save sessions", "session save", etc.
2. Execute:
```bash
bash "$(dirname "$0")/cmux-save-sessions"
```
3. Show output to the user
4. **Post-save close prompt** — ask via `AskUserQuestion`:

> "N sessions saved. Would you like to close the saved sessions?"
> - **Close all**: Close all saved workspaces
> - **Select to close**: User picks which sessions to close (multiSelect)
> - **Keep**: Don't close anything

5. If close is selected, read refs from the saved JSON and execute:
```bash
cmux close-workspace --workspace <ref>
```

> **Never terminate tmux sessions directly** — `cmux close-workspace` handles backing terminal cleanup internally.
> Manual `tmux kill-session` can break other workspaces' terminals.
> For orphan tmux session cleanup, use `cmux-session-manager`'s cleanup command.

> **Never close the current session (manager session)** — closing it would terminate Claude Code.
> Even if saved with `--include-self`, the current session is excluded from close targets.

**Flags:**
- `--include-self`: Include the current session in the snapshot

**Save location:** `~/.cmux/sessions/sessions-YYYYMMDD-HHMMSS.json`

**Captured data:**
- workspace ref, name, state (Active/Idle/Waiting/Crashed/Unknown)
- git branch, PR status, category ([DEV]/[OPS]/[RES]/[TMP])
- working directory (cwd)

### `list` — List saved snapshots

**How to run:**
1. User requests "list snapshots", "snapshot list", etc.
2. List files in `~/.cmux/sessions/`:
```bash
for f in $(ls -t ~/.cmux/sessions/sessions-*.json 2>/dev/null | head -20); do
  saved_at=$(jq -r '.saved_at' "$f")
  total=$(jq -r '.total' "$f")
  echo "  $(basename "$f") | $saved_at | $total sessions"
done
```

## Output Format

### JSON Snapshot Structure
```json
{
  "saved_at": "2026-04-07T14:30:00+0900",
  "hostname": "macbook-pro.local",
  "total": 7,
  "summary": {
    "active": 2,
    "waiting": 1,
    "idle": 1,
    "crashed": 0,
    "unknown": 1
  },
  "sessions": [
    {
      "ref": "workspace:147",
      "name": "Session name",
      "state": "ACTIVE",
      "branch": "main",
      "pr": "none",
      "category": "[DEV]",
      "cwd": "/path/to/project"
    }
  ]
}
```

## Integration

- **cmux-resume-sessions**: Consumes JSON saved by this skill
- **cmux-recover-sessions**: Can reference snapshots as recovery data during crash recovery
- **cmux-session-manager**: Similar to status but provides persistent records
- **cmux-orchestrator**: Save worker configurations as snapshots

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| "cmux is not running" | cmux app not running | Start cmux app |
| "jq is required" | jq not installed | `brew install jq` |
| 0 sessions saved | Only current session exists | Use `--include-self` flag |

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Include the current session too" | The manager session would be in the snapshot, and `--include-self` can accidentally close the session you are in. |
| "Skip saving, I'll remember the layout" | You won't. Multi-workspace layouts vanish on reboot. Save is cheap. |
| "Overwrite the previous snapshot" | Timestamped files are the history. Overwriting discards recovery points. |
| "Close all sessions after save" | Read the list first. An ACTIVE session may still be doing meaningful work. |
