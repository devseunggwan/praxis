---
name: recover-sessions
description: Bulk recover Claude Code sessions after power loss or tmux crash. Interactive interview to determine recovery scope, layout, and execution mode. Triggers on "recover", "session recovery", "restore sessions", "power recovery".
---

# Recover Sessions

## Overview

Bulk recover Claude Code sessions after power loss or tmux server crash.

**Core principle:** Claude Code conversations are safely persisted to disk as `.jsonl` files. Recovery = find saved sessions and arrange them in tmux panes.

## When to Use

- After a Mac power loss when all tmux sessions are gone
- After tmux server crash that killed all running sessions
- After reboot when previous work sessions need to be restored
- Triggers: "recover", "session recovery", "restore sessions", "power recovery"

## Prerequisites

- `claude-recover` script in `skills/recover-sessions/claude-recover` (symlinked to `~/.local/bin/`)
- tmux installed (`brew install tmux`)
- Any terminal app (Ghostty, iTerm2, Terminal.app — auto-detected)

## Process

### Step 1: Verify Script Installation

```bash
which claude-recover || echo "NOT INSTALLED"
```

If missing, create symlink:

```bash
ln -sf ~/projects/praxis/skills/recover-sessions/claude-recover ~/.local/bin/claude-recover
```

### Step 2: Interview — Recovery Scope

Ask the user via `AskUserQuestion`:

**Q1: When did the crash happen?**

```
When did the power loss / crash occur?
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
claude-recover --list --from <start> --to <end>
```

Present the results to the user. If 0 sessions found, suggest widening the range.

### Step 4: Interview — Session Selection

Ask the user via `AskUserQuestion`:

**Q2: Which sessions to recover?**

Offer filtering options first, then manual selection:

```
Found N sessions. How to filter?
1. All (recover everything)
2. Date filter (narrow to specific day)
3. Implementation/dev sessions only
4. Large sessions only (>1M)
5. Let me pick by number
```

Multiple filters can be combined. Show calculated count after filtering.

### Step 5: Interview — Layout

Ask the user via `AskUserQuestion`:

**Q3: How should sessions be arranged in tmux?**

```
How many panes per tmux window?
1. 1x2 — 2 panes (top/bottom, default)
2. 2x1 — 2 panes (left/right)
3. 1x3 — 3 panes (top/middle/bottom)
4. 2x2 — 4 panes (2x2 grid)
5. 3x2 — 6 panes (3 columns, 2 rows)
6. Custom (enter CxR)
```

Show calculated window count: "N sessions ÷ P panes = W windows"

### Step 6: Interview — Execution Mode

Ask the user via `AskUserQuestion`:

**Q4: How to open the recovered sessions?**

```
How should the recovery session be opened?
1. Manual — show attach command, I'll open it myself
2. Auto-attach — attach to tmux session immediately
3. New window — open in a new terminal window
```

### Step 7: Final Confirmation

Present the recovery plan summary and ask for **explicit approval** before executing:

```
═══════════════════════════════════════════════
 Recovery Plan
═══════════════════════════════════════════════

 Date range:  03-18 ~ 03-20
 Sessions:    22 (filtered from 85 total)
 Layout:      1x3 (3 panes per window)
 Windows:     8 (in single tmux session 'cr')
 Mode:        Auto-attach

 Command to execute:
   claude-recover --from 03-18 --to 03-20 --layout 1x3 --attach

═══════════════════════════════════════════════

Proceed with recovery?
1. Yes, execute
2. Change settings
3. Cancel
```

- Option 1 → Execute the command
- Option 2 → Return to the relevant interview step
- Option 3 → Abort

### Step 8: Execute Recovery

Run the approved command:

```bash
claude-recover --from <start> --to <end> --layout <CxR> [--attach|--windows]
```

### Step 9: Verify and Guide

After execution, verify the tmux session was created:

```bash
tmux ls 2>/dev/null | grep "^cr"
tmux list-windows -t cr 2>/dev/null
```

Show navigation instructions:

```
tmux a -t cr              # attach to recovery session
Ctrl+B n                  # next window
Ctrl+B p                  # previous window
Ctrl+B 0-N                # jump to window by number
```

## Script Reference

### CLI Options

| Option | Description |
|--------|-------------|
| `--days N` | Scan last N days |
| `--from DATE` | Start date (YYYY-MM-DD or MM-DD) |
| `--to DATE` | End date (default: yesterday) |
| `--layout CxR` | Pane grid per window (default: 1x2) |
| `--list` | List only, don't create tmux session |
| `--attach` | Create + auto-attach |
| `--windows` | Create + open in new terminal window |

### Architecture

The script creates a **single tmux session** named `cr` with **multiple windows**. Each window contains a pane grid (e.g., 1x3 = 3 panes). User attaches once with `tmux a -t cr` and navigates windows with `Ctrl+B n/p/0-N`.

```
tmux session "cr"
  ├─ window 0 (1x3): session 1, 2, 3
  ├─ window 1 (1x3): session 4, 5, 6
  ├─ window 2 (1x3): session 7, 8, 9
  └─ window 3 (1x3): session 10, 11, 12
```

### Filtering Pipeline

The script automatically excludes:

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

The script scans all `~/.claude*/projects/` directories automatically. For non-default config homes (e.g., `~/.claude-5x`), the resume command uses `CLAUDE_CONFIG_DIR` to ensure correct settings are loaded. Symlinked `projects/` directories are deduplicated.

### Layout Examples

```
1x2 = ┌───┐    1x3 = ┌───┐    2x2 = ┌───┬───┐    3x2 = ┌───┬───┬───┐
      │ 1 │          │ 1 │          │ 1 │ 2 │          │ 1 │ 2 │ 3 │
      ├───┤          ├───┤          ├───┼───┤          ├───┼───┼───┤
      │ 2 │          │ 2 │          │ 3 │ 4 │          │ 4 │ 5 │ 6 │
      └───┘          ├───┤          └───┴───┘          └───┴───┴───┘
                     │ 3 │
                     └───┘
```

Layout selection:
- `1xN` → `even-vertical` (equal-height horizontal splits)
- `Nx1` → `even-horizontal` (equal-width vertical splits)
- `NxM` → `tiled` (grid)

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
| Fewer sessions than expected | Previous recovery changed mtime | Sessions resumed earlier now have today's mtime |
| tmux creation fails | tmux not installed | `brew install tmux` |
| Wrong directory | cwd extraction failed | Check progress.cwd in jsonl |
| Terminal window doesn't open | Terminal app not detected | Use `--attach` or manual mode instead |

## Integration

**Workflow position:** System recovery (runs before any other skill)

```
[Power loss / Reboot] → [recover-sessions] → [Resume daily work]
```
