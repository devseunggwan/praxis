---
name: recover-sessions
description: Bulk recover Claude Code sessions after power loss or tmux crash. Scans recent sessions and distributes them across Ghostty tabs with tmux 2-pane splits. Triggers on "recover", "session recovery", "restore sessions", "power recovery".
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

- `~/.local/bin/claude-recover` script must be installed
- tmux must be installed (`brew install tmux`)
- Ghostty terminal (tab-based workflow)

## Process

### Step 1: Verify Script Installation

```bash
which claude-recover || echo "NOT INSTALLED"
```

If the script is missing, inform the user:

> `claude-recover` is not installed. Would you like me to install it?

If installation is needed, create the script at `~/.local/bin/claude-recover` and run `chmod +x`.

### Step 2: Scan Recovery Targets

Ask the user for the recovery time range:

```
How far back should I scan for sessions?
1. Last 1 day (default)
2. Last 3 days
3. Last 7 days
4. Custom
```

After selection, run the scan:

```bash
claude-recover --list --days <N>
```

Show the output to the user and confirm which sessions to recover.

### Step 3: Create tmux Sessions

Once the user confirms recovery:

```bash
claude-recover --days <N>
```

This command:
1. Scans recent sessions (auto-filters schedule/auto sessions)
2. Pairs sessions into groups of 2 and creates tmux sessions (`cr-1`, `cr-2`, ...)
3. Each tmux session: vertical split, top/bottom panes run `claude --resume <session-id>`

### Step 4: Ghostty Tab Distribution Guide

After tmux sessions are created, guide the user:

```
═══════════════════════════════════════════════
 tmux sessions created successfully.

 Open a new Ghostty tab (Cmd+T) for each and run:

   tmux a -t cr-1
   tmux a -t cr-2
   tmux a -t cr-3
   ...

 Or auto-open Ghostty windows:
   claude-recover --days <N> --windows
═══════════════════════════════════════════════
```

### Step 5: Verify Recovery

Check recovery status:

```bash
tmux ls 2>/dev/null | grep "^cr-"
```

## Mode Reference

| Mode | Command | Behavior |
|------|---------|----------|
| List | `claude-recover --list --days N` | Show targets only (no tmux creation) |
| Create | `claude-recover --days N` | Create tmux sessions + show attach instructions |
| Auto-attach | `claude-recover --days N --attach` | Create + auto-attach to cr-1 |
| Auto-windows | `claude-recover --days N --windows` | Open all sessions in Ghostty windows |

## Session Identification

The script identifies sessions using:

- **Path**: Extracts real project path from `progress.cwd` field inside the jsonl
- **Content**: Shows first user message (`type=user`, `message.content`) to identify session purpose
- **Filtering**: Auto-excludes sessions <50K and known schedule patterns ("SLA deep analysis", "Collect today", etc.)
- **Size**: Larger files indicate more conversation = more important sessions

## Prevention: Session Naming Convention

Prevention beats recovery. Always name sessions at startup:

```bash
claude --name "hub-700-feat-xyz"
claude --name "dag-v3-kakao-moment"
```

Named sessions can be instantly recovered with `claude --resume "hub-700"` (fuzzy match).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "No sessions to recover" | No sessions in time range | Increase `--days` value |
| tmux session creation fails | tmux not running | Verify tmux is installed |
| Session starts in wrong directory | cwd extraction failed | Check progress.cwd in jsonl file |
| Ghostty windows don't open | Ghostty not running | Launch Ghostty first, then use `--windows` |

## Integration

**Workflow position:** System recovery (runs before any other skill)

```
[Power loss / Reboot] → [recover-sessions] → [Resume daily work]
```
