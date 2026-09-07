---
name: cmux-session-manager
description: Automate daily cmux session management. Manual commands for status (dashboard) and cleanup (tidy + reorganize), plus init (hook) and report (schedule) automation.
when_to_use: Triggers on "cmux session", "session management", "session cleanup", "cmux status", "cmux cleanup", "cmux tidy".
verified-against-runtime: true
runtime-verified-at: 2026-06-16
runtime-verified-note: "mock cmux/tmux probe of cmux-session-cleanup --dry-run — it emits three JSON phases split by ---PHASE_SEPARATOR---, keeps safe_named orphans report-only, and routes idle workspaces into idle_cleanup/reorganize."
---

# cmux Session Manager

## Overview

Solves the problem of unlimited session accumulation in cmux with **daily management automation**.
Covers the full session lifecycle: creation, management, and cleanup.

> **Role separation**: This skill is for "daily management". For post-crash/power-loss session recovery, use the `cmux-recover-sessions` skill.

## The Iron Law

```
READ BEFORE CLOSE. USER CONFIRMATION REQUIRED FOR NON-AUTO CLEANUP.
```

Phase 1 `auto_cleanup` (safe orphans, crashed sessions) runs without prompting.
Phase 2 `idle_cleanup` and Phase 3 `reorganize` MUST be confirmed by the user before executing —
never close or rename sessions without explicit approval, because a still-running task may look idle.

## When to Use

- End-of-day session hygiene (status dashboard, cleanup, reorganize)
- Too many orphaned or idle sessions accumulated in cmux
- Before starting new work — verify no stale workspaces are consuming resources

> **Not for crash recovery** — use `cmux-recover-sessions` after a power loss or cmux crash.

## Commands

### `status` — Session Dashboard

Displays the status of all cmux sessions in a table.

**How to run:**
1. User requests `cmux-session status` or `cmux status`
2. Execute the following script:
```bash
bash "${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/skills/cmux-session-manager/cmux-session-status"
```
3. Show the output to the user as-is

If `CLAUDE_PLUGIN_ROOT` is unset the `:?` guard aborts with `praxis plugin root
not set`; resolve it from the installed-plugins manifest
(`jq -r '.plugins["praxis@praxis"][0].installPath // empty' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"`),
export it, and re-run — do not fall back to a relative path.

**Output includes:**
- Summary header: Active / Waiting / Idle / Crashed / Orphaned counts
- Session table: status icon, category, name, branch
- Orphan section: classified as auto-removable / named / idle / unsafe

### `cleanup [--dry-run]` — Tidy + Reorganize

Performs 3-phase cleanup. Use `--dry-run` flag to preview the plan without executing.

**How to run:**
1. User requests `cmux-session cleanup` or `cmux cleanup`
2. Check dry-run preference, then execute:
```bash
bash "${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/skills/cmux-session-manager/cmux-session-cleanup" [--dry-run]
```
3. The script outputs 3 JSON blocks separated by `---PHASE_SEPARATOR---`
4. Parse each phase's JSON and process according to the Data Handoff Protocol below

## Data Handoff Protocol

The cleanup script outputs 3 JSON blocks to stdout.
Claude parses them and handles user interaction.

### Phase 1: `auto_cleanup` (automatic — no user confirmation needed)
```json
{"phase":"auto_cleanup","actions":[
  {"action":"kill_orphan","session":"12","type":"safe_numeric","executed":true},
  {"action":"close_crashed","ref":"workspace:69","name":"...","executed":true},
  {"action":"report_orphan","session":"psm_...","type":"safe_named","auto_delete":false},
  {"action":"report_orphan","session":"41","type":"idle_numeric","commands":"2.1.241",
   "idle_evidence":"startup prompt on screen in 1 pane(s), no output for 91380s","auto_delete":false}
]}
```
- `executed: true` items are already done — report results to user
- `auto_delete: false` items are informational — notify "this session was not auto-deleted"
- `idle_evidence` is present on `idle_*` types — show it verbatim when asking the
  user whether to remove that session; it is the whole reason the session is
  separated from `unsafe_*`

### Phase 2: `idle_cleanup` (user confirmation required)
```json
{"phase":"idle_cleanup","sessions":[
  {"ref":"workspace:43","name":"...","state":"IDLE","branch":"main","category":"[TMP]"},
  {"ref":"workspace:67","name":"...","state":"WAITING","branch":"main","category":"[OPS]"}
]}
```
**How to process:**
1. If sessions array is empty, output "No idle sessions to clean up" and proceed to Phase 3
2. If sessions exist, show via `AskUserQuestion` (multiSelect: true):
   - Each option label: `[STATE] [CAT] name (branch)`
   - For user-selected sessions, execute:
   ```bash
   cmux close-workspace --workspace <ref>
   ```

### Phase 3: `reorganize` (user confirmation required)
```json
{"phase":"reorganize","single_window_warning":true,"changes":[
  {"ref":"workspace:30","current_name":"...","proposed_name":"[DEV] ...","category":"[DEV]","target_window":"Active Dev"}
]}
```
**How to process:**
1. If `single_window_warning: true`, show warning:
   > "All workspaces are currently in a single window. Reorganizing will create category-based windows (Active Dev, Ops/Debug, Research)."
2. If changes array is empty, output "No sessions to reorganize"
3. If changes exist, show preview table then confirm via `AskUserQuestion`:
   - "Yes, reorganize" / "Skip reorganization"
4. If confirmed, for each change:
   ```bash
   cmux rename-workspace --workspace <ref> "<proposed_name>"
   cmux set-status category "<category>" --workspace <ref>
   ```
5. Apply `get_or_create_window` logic for window moves:
   - Find a window where another workspace of the same category already exists
   - If none found, create a new window and move:
   ```bash
   cmux move-workspace-to-window --workspace <ref> --window <window_ref>
   ```

## State Detection

Uses the `claude_code=` field from `cmux sidebar-state` as the primary signal:

| `claude_code=` | State | Cleanup target |
| ---------------- | ------- | --------------- |
| `Running` | ACTIVE | No |
| `Needs input` | WAITING | Phase 2 (optional) |
| `Idle` | IDLE or CRASHED | Phase 2 (IDLE) / Phase 1 (CRASHED) |
| (absent) | UNKNOWN | No |

Idle workspaces are further checked via `read-screen` for crash signatures (`bun.report`, `segfault`, etc.).

## Orphan Classification

An orphan tmux session — one no cmux workspace owns — is typed by what its panes
are running, and the type decides who removes it.

| Type | Condition | Handling |
| ------ | ----------- | ---------- |
| `safe_*` | every pane runs a bare shell | `safe_numeric` is killed by Phase 1 without prompting; `safe_named` is reported |
| `idle_*` | a non-shell pane, and the idleness oracle finds the process parked | reported with its evidence — never auto-killed |
| `unsafe_*` | a non-shell pane the oracle cannot show is parked | reported as running processes, as before |

The oracle answers "parked or working?" by requiring **both** signals:

- the session's newest `#{window_activity}` is older than `CMUX_IDLE_ACTIVITY_SECS`
  (default 1800), and
- every non-shell pane's screen shows a startup prompt at the start of a line.

Either signal alone misreads a case the other catches: a quiet window is also
what a long silent build looks like, and a prompt on screen is also what a
session someone just opened looks like. Any uncertain input — no activity clock,
an unreadable pane, no non-shell pane at all — produces no verdict, so the
session keeps the stricter type.

`idle_*` is deliberately report-only. The oracle can be wrong, and the cost of a
wrong `idle` that auto-kills is a working session lost without a prompt, while
the cost of a wrong `idle` that only reports is one line the operator dismisses.

## Category Classification

Priority (highest first):
1. **[DEV]**: Branch matches `issue-N-<type>-*` (feat, refactor, docs, test, chore, fix, perf, ci, build)
2. **[OPS]**: Name contains `failure`, `debug`, `error`, `fix`, `incident`, etc.
3. **[RES]**: Name contains `analyze`, `investigate`, `compare`, `check`, `research`, etc.
4. **[TMP]**: None of the above

## Init Hook (Automation)

To auto-apply categories when new sessions are created, set up a cmux hook:

```bash
# cmux hook setup (category tagging on session-start)
cmux set-hook session-start 'bash -c "
  WS=\$CMUX_WORKSPACE_ID
  NAME=\$(cmux read-screen --workspace \$WS --lines 1 2>/dev/null | head -1)
  # classify and rename logic here
"'
```

Or add a session-start hook to Claude Code `settings.json`
to call `cmux-session-lib`'s `classify_category` for auto rename + set-status.

## Report Schedule (Automation)

Praxis ships no scheduler. For a daily end-of-day report, run
`cmux-session-status` from an external scheduler (cron, launchd) and route its
output wherever you read reports.

The report includes status output + PR status from `sidebar-state`'s `pr=` field + cleanup recommendations.

## Troubleshooting

| Problem | Cause | Fix |
| --------- | ------- | ----- |
| "cmux is not running" | cmux app not running | Start cmux app |
| "jq is required" | jq not installed | `brew install jq` |
| Session state UNKNOWN | No claude_code in sidebar-state | Session may not be Claude Code |
| 0 orphans | All tmux sessions owned by cmux | Normal — nothing to clean |

## Rationalization Prevention

| Excuse | Reality |
| -------- | --------- |
| "I'll approve the cleanup without looking, it's probably fine" | An IDLE session may be a long-running task paused on input. Read the category/branch first. |
| "Skip the dry-run, just execute" | Dry-run takes 2 seconds and prevents closing a session you actually wanted to keep. |
| "The session looks crashed, auto-close it" | Only `auto_cleanup` (Phase 1) kills sessions automatically. Anything in Phase 2 needs your eyes. |
| "It is typed `idle`, so it is safe to kill" | `idle` means an oracle found no sign of work, not that there is none. It is evidence to show the user, never a licence to remove the session for them. |
| "Reorganize everything into one window" | Category windows make parallel work scannable. One-window mode is a regression. |

## Integration

**Workflow position:** Daily session lifecycle management — runs in between work sessions.

```
[morning: status] → [work: many sessions] → [evening: cleanup] → [snapshot if needed]
```

**Related skills:**
- **cmux-save-sessions**: Capture current state before running cleanup (recovery point)
- **cmux-resume-sessions**: Restore a saved layout if cleanup removes too much
- **cmux-recover-sessions**: Post-crash recovery (different scope — not for daily use)

**Automation:**
- Init hook: auto-classify on `session-start` (see "Init Hook" section)
- Report schedule: daily status via an external scheduler (see "Report Schedule" section)
