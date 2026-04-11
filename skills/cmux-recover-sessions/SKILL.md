---
name: cmux-recover-sessions
description: >
  Bulk recover Claude Code sessions after a crash, power loss, OOM kill, or reboot
  by scanning the .jsonl files Claude Code persists automatically. Interactive
  interview chooses recovery scope and layout. Use this when sessions died and
  you need them back, NOT for restoring an intentionally saved layout.
  Priority: crash context wins. If the request mentions a crash/power loss/OOM,
  prefer this skill even when the user also mentions a snapshot — the snapshot
  may be stale, and .jsonl scan reflects the real final state.
  Only defer to cmux-resume-sessions when there is NO crash context and the user
  explicitly wants to rehydrate a saved snapshot.
  Triggers on "터졌다", "크래시 복구", "크래시 복원", "전원 꺼짐 복구", "OOM 복구", "세션 살려야", "recover cmux", "crash recovery", "power loss recovery", "cmux session recovery".
---

# Recover Sessions (cmux)

> ⚠️ **Wrong skill?** If you have a JSON snapshot you previously saved with
> `cmux-save-sessions` and just want to rehydrate that exact layout, use
> **`cmux-resume-sessions`** instead. Recover scans the on-disk `.jsonl` files
> Claude Code persists automatically — useful precisely *because* you never
> got a chance to save anything before the crash.

## Overview

Bulk recover Claude Code sessions after crash or power loss into cmux workspaces.
cmux variant of `recover-sessions` — replaces tmux backend with cmux workspace/split API.

**Core principle:** Claude Code conversations are safely persisted to disk as `.jsonl` files. Recovery = find saved sessions and open them in cmux workspaces.

## The Iron Law

```
RECOVERY IS NEVER DESTRUCTIVE. LIVE WORKSPACES MUST NOT BE TOUCHED.
```

Recovery reads `.jsonl` files and re-opens them in new cmux workspaces. It must never:
- Close or replace any currently running cmux workspace
- Overwrite or delete saved conversation files
- Modify working directories — `cwd` is informational only

## When to Use

- After a Bun segfault crash that killed a Claude Code session
- After a Mac power loss when all cmux workspaces are gone
- After reboot when previous work sessions need to be restored
- Triggers: "recover cmux", "cmux session recovery", "cmux restore sessions"

## Prerequisites

- `cmux-recover-sessions` script in `skills/cmux-recover-sessions/cmux-recover-sessions` (symlinked to `~/.local/bin/`)
- cmux running (`cmux ping` should succeed) — not required for `--plain` and `--list` modes

## Process

### Step 1: Verify Script Installation

```bash
which cmux-recover-sessions || echo "NOT INSTALLED"
```

If missing, create symlink:

```bash
# Replace PRAXIS_REPO with your local praxis clone path (e.g., ~/projects/praxis)
PRAXIS_REPO=~/projects/praxis
ln -sf "$PRAXIS_REPO/skills/cmux-recover-sessions/cmux-recover-sessions" ~/.local/bin/cmux-recover-sessions
```

Also verify cmux is running:

```bash
cmux ping
```

### Step 2: Interview — Recovery Scope

Ask the user via `AskUserQuestion`:

**Q1: When did the crash happen?**

```
When did the crash occur?
1. Today (recover sessions from yesterday)
2. Yesterday
3. Last Friday (weekend crash)
4. Custom date range
```

- Option 1 → `--from <yesterday> --to <yesterday>`
- Option 2 → `--from <2 days ago> --to <yesterday>`
- Option 3 → `--from <last Monday> --to <last Friday>`
- Option 4 → Ask follow-up for start/end dates (MM-DD or YYYY-MM-DD)

### Step 3: Scan and Present Results

Run the scan with determined date range:

```bash
cmux-recover-sessions --list --from <start> --to <end>
```

Present the results to the user. If 0 sessions found, suggest widening the range.

### Step 4: Interview — Session Selection

Ask the user via `AskUserQuestion`:

**Q2: Which sessions to recover?**

```
Found N sessions. How to filter?
1. All (recover everything)
2. Date filter (narrow to specific day)
3. Implementation/dev sessions only
4. Large sessions only (>1M)
5. Let me pick by number
```

### Step 5: Interview — Layout

Ask the user via `AskUserQuestion`:

**Q3: How should sessions be arranged in cmux?**

```
How should sessions be opened?
1. Tabs — 1 workspace per session (default, recommended)
2. Split 1x2 — 2 sessions per workspace (top/bottom)
3. Split 2x1 — 2 sessions per workspace (left/right)
4. Split 2x2 — 4 sessions per workspace (grid)
5. Custom split (enter CxR)
6. Plain — output resume commands only (no workspace creation)
```

Show calculated workspace count: "N sessions ÷ P panes = W workspaces"

### Step 6: Final Confirmation

Present the recovery plan summary and ask for **explicit approval** before executing:

```
═══════════════════════════════════════════════
 Recovery Plan (cmux)
═══════════════════════════════════════════════

 Date range:  03-23 ~ 03-25
 Sessions:    12 (filtered from 50 total)
 Layout:      tabs (1 session per workspace)
 Workspaces:  12

 Command to execute:
   cmux-recover-sessions --from 03-23 --to 03-25 --tabs

═══════════════════════════════════════════════

Proceed with recovery?
1. Yes, execute
2. Change settings
3. Cancel
```

### Step 7: Execute Recovery

Run the approved command:

```bash
cmux-recover-sessions --from <start> --to <end> [--tabs|--split CxR|--plain]
```

### Step 8: Verify and Guide

After execution, verify workspaces were created:

```bash
cmux list-workspaces
```

Show navigation instructions:

```
cmux workspaces are now open. Navigate with:
  Cmd+1-9         jump to workspace by number
  Cmd+Shift+]     next workspace
  Cmd+Shift+[     previous workspace

⚠️ Note: Claude Code re-renders a resumed conversation from the first
   message, so the visible viewport looks like the session "reverted to
   its earliest state".
   
   Recovery always launches each workspace with `claude --resume <uuid>`,
   pointing at the exact .jsonl discovered on disk. In most cases that
   loads the intended transcript, but it is not a guarantee — a bad or
   stale session id, a partial flush at crash time, or a truncated tail
   can all surface as "wrong" context. Always confirm the state in each
   restored workspace before trusting it:
     - scroll the viewport to the bottom, or
     - ask the model directly: "what was the last thing we worked on?"
```

## Script Reference

### CLI Options

| Option | Description |
|--------|-------------|
| `--days N` | Scan last N days |
| `--from DATE` | Start date (YYYY-MM-DD or MM-DD) |
| `--to DATE` | End date (default: yesterday) |
| `--tabs` | 1 session per workspace tab (default) |
| `--split CxR` | CxR grid per workspace |
| `--plain` | Output resume commands only (no workspace creation) |
| `--list` | List only, don't create workspaces |
| `--rename` | Auto-rename workspaces with session info |

### Architecture

cmux uses workspaces as the primary unit. Each workspace is a tab.

**Tabs mode (default):**
```
cmux window
  ├─ workspace 1 (tab): claude --resume session-1
  ├─ workspace 2 (tab): claude --resume session-2
  ├─ workspace 3 (tab): claude --resume session-3
  └─ workspace 4 (tab): claude --resume session-4
```

**Plain mode:**
```
$ cmux-recover-sessions --from 03-25 --plain

# Session 1: Check zombie locks
cd "/Users/.../my-project"
claude --resume abc123

# Session 2: code-review for ...
cd "/Users/.../my-project"
claude --resume def456
```

**Split mode (e.g., 1x2):**
```
cmux window
  ├─ workspace 1: ┌─────────────┐
  │               │  session-1   │
  │               ├─────────────┤
  │               │  session-2   │
  │               └─────────────┘
  └─ workspace 2: ┌─────────────┐
                   │  session-3   │
                   ├─────────────┤
                   │  session-4   │
                   └─────────────┘
```

### Filtering Pipeline

Same as `recover-sessions` — automatically excludes:

| Filter | What it removes |
|--------|----------------|
| Subagent paths | `/subagents/` directory sessions |
| Teammate sessions | `<teammate-message>` (omc team workers) |
| Team orchestrators | `oh-my-claudecode:team` command sessions |
| Command-only | Skill auto-invocations with ≤5 user messages |
| Short sessions | Less than 4 user messages |
| Schedule/auto | Known prefixes (SLA, daily commit, morning briefing, etc.) |
| Exited sessions | `/exit` or `/quit` detected in last 15 lines |
| No content | Sessions with no identifiable user message |

### Multi Config Home Support

Scans all `~/.claude*/projects/` directories automatically. For non-default config homes (e.g., `~/.claude-5x`), the resume command uses `CLAUDE_CONFIG_DIR`.

## Prevention: Session Naming

Prevention beats recovery. Always name sessions at startup:

```bash
claude --name "issue-42-feat-xyz"
```

Named sessions recover instantly: `claude --resume "issue-42"` (fuzzy match).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "No sessions to recover" | No sessions in range | Widen `--from`/`--to` range |
| "cmux not reachable" | cmux not running | Start cmux app first |
| Workspace creation fails | Socket auth issue | Check `CMUX_SOCKET_PASSWORD` |
| Wrong directory | cwd extraction failed | Check progress.cwd in jsonl |

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Ignore the Bun segfault, re-run directly" | The crash may be deterministic. Recover first, then diagnose the crash log. |
| "Widen the scan to all time" | Massive scans surface months-old throwaway sessions. Use a reasonable `--from`/`--to` window. |
| "Skip naming sessions, I'll remember which is which" | Unnamed sessions recover as "session_<id>". After 10 recoveries they're indistinguishable. Always `claude --name`. |
| "Re-run recovery if it fails partway" | Re-running changes mtimes and can hide already-recovered sessions. Diagnose the failure first. |

## Integration

**Workflow position:** System recovery (runs before any other skill)

```
[Crash / Reboot] → [cmux-recover-sessions] → [Resume daily work]
```
