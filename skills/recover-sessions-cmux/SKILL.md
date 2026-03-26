---
name: recover-sessions-cmux
description: Bulk recover Claude Code sessions after crash or power loss into cmux workspaces. Interactive interview to determine recovery scope and layout. Triggers on "recover cmux", "cmux session recovery", "cmux restore sessions".
---

# Recover Sessions (cmux)

## Overview

Bulk recover Claude Code sessions after crash or power loss into cmux workspaces.
cmux variant of `recover-sessions` — replaces tmux backend with cmux workspace/split API.

**Core principle:** Claude Code conversations are safely persisted to disk as `.jsonl` files. Recovery = find saved sessions and open them in cmux workspaces.

## When to Use

- After a Bun segfault crash that killed a Claude Code session
- After a Mac power loss when all cmux workspaces are gone
- After reboot when previous work sessions need to be restored
- Triggers: "recover cmux", "cmux session recovery", "cmux restore sessions"

## Prerequisites

- `claude-recover-cmux` script in `skills/recover-sessions-cmux/claude-recover-cmux` (symlinked to `~/.local/bin/`)
- cmux running (`cmux ping` should succeed)

## Process

### Step 1: Verify Script Installation

```bash
which claude-recover-cmux || echo "NOT INSTALLED"
```

If missing, create symlink:

```bash
ln -sf ~/projects/my-skills/skills/recover-sessions-cmux/claude-recover-cmux ~/.local/bin/claude-recover-cmux
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
claude-recover-cmux --list --from <start> --to <end>
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
   claude-recover-cmux --from 03-23 --to 03-25 --tabs

═══════════════════════════════════════════════

Proceed with recovery?
1. Yes, execute
2. Change settings
3. Cancel
```

### Step 7: Execute Recovery

Run the approved command:

```bash
claude-recover-cmux --from <start> --to <end> [--tabs|--split CxR]
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
claude --name "hub-700-feat-xyz"
```

Named sessions recover instantly: `claude --resume "hub-700"` (fuzzy match).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "No sessions to recover" | No sessions in range | Widen `--from`/`--to` range |
| "cmux not reachable" | cmux not running | Start cmux app first |
| Workspace creation fails | Socket auth issue | Check `CMUX_SOCKET_PASSWORD` |
| Wrong directory | cwd extraction failed | Check progress.cwd in jsonl |

## Integration

**Workflow position:** System recovery (runs before any other skill)

```
[Crash / Reboot] → [recover-sessions-cmux] → [Resume daily work]
```
