---
name: cmux-orchestrator
description: Dispatch and supervise multiple Claude Code workers in cmux workspaces. Crash-resilient, model-routed, file-based coordination. Triggers on "orchestrate", "dispatch", "cmux workers".
---

# cmux Orchestrator

## Overview

Dispatches tasks to independent Claude Code workers running in cmux workspaces.
Each worker is a separate process — master crash does NOT kill workers.

**Core principle:** Workers are independent. Coordination is file-based. Crashes are recoverable.

**Chains:** `turbo-setup` → execute → `turbo-completion` as a full pipeline per worker.

## The Iron Law

```
WORKERS ARE INDEPENDENT. MASTER CRASH MUST NOT KILL WORKERS.
RESULT COLLECTION IS FILE-BASED — NEVER IN-MEMORY.
```

Every worker runs as its own cmux workspace process.
State lives on disk (`/tmp/cmux-orchestrator-*/`) so the orchestrator can be restarted and re-attach to in-flight workers.
Never pipe worker output directly into the orchestrator process — the master crash would lose everything.

## Architecture

```
┌──────────────────────────────────────────────┐
│            Orchestrator (this skill)          │
│                                              │
│  Queue ──▶ Dispatch ──▶ Supervise ──▶ Collect│
│  (.json)   (cmux API)   (poll loop)  (.jsonl)│
└──────────────┬───────────────┬───────────────┘
               │               │
     ┌─────────┼─────────┐    │
     ▼         ▼         ▼    ▼
  [ws:1]    [ws:2]    [ws:3]  results/
  claude    codex     gemini   ├─ w1.jsonl
  :sonnet   (default) (default)├─ w2.jsonl
  review    implement analyze  └─ w3.jsonl
```

## When to Use

- Multiple independent tasks to execute in parallel
- Batch processing (code reviews, test runs, migrations)
- Any work that benefits from crash isolation
- Triggers: "orchestrate", "dispatch", "cmux workers"

## Inputs

User provides a task list:

```
Please process these tasks in parallel:
1. my-project PR #42 code review
2. frontend issue #100 implementation
3. Issue #55 status check
```

Or a structured task file:

```json
// /tmp/orchestrator/tasks.json
[
  {"description": "PR #42 code review", "complexity": "medium", "cwd": "/path/to/repo"},
  {"description": "issue #100 implementation", "complexity": "medium", "cwd": "/path/to/repo"},
  {"description": "issue #55 status check", "complexity": "low", "cwd": "/path/to/repo"}
]
```

## Process

### Step 1: Parse Tasks + Route Provider & Model

Two-phase routing (from CLAUDE.md Provider Routing):

**Phase 1 — Task type → Provider:**

```
Task → Provider Router
  "implement", "fix", "refactor", "code generation"  → codex
  "search", "analyze", "summarize", "large context"   → gemini
  "review", "design", "architecture", "security"      → claude
  "status", "check", "list"                           → claude
  Default                                              → claude
```

**Phase 2 — Provider + Complexity → Model:**

```
claude + low    → haiku     (find, search, list, status, check)
claude + medium → sonnet    (review, test, refactor)
claude + high   → opus      (architect, design, security, debug)
codex           → default   (or explicit: codex:o3)
gemini          → default   (or explicit: gemini:flash)
```

**Pre-flight availability check:**

```bash
for provider in codex gemini; do
  command -v "$provider" >/dev/null 2>&1 || {
    echo "⚠ ${provider} CLI not found — tasks will fall back to claude:sonnet"
    FALLBACK["$provider"]=true
  }
done
```

If a provider is unavailable, tasks assigned to it fall back to `claude:sonnet` with a warning in the dispatch plan.

Present the dispatch plan and ask for confirmation:

```
═══════════════════════════════════════════════
 Dispatch Plan
═══════════════════════════════════════════════

 #  Task                          Provider  Model      Budget
 1  PR #42 code review             claude    sonnet     $0.50
 2  issue #100 implementation     codex     (default)  $1.00
 3  codebase search + analysis    gemini    (default)  $0.30

 Total budget: $1.80
 Max concurrent workers: 3

 Proceed? (y/n)
═══════════════════════════════════════════════
```

### Step 2: Initialize Orchestrator State

```bash
ORCH_DIR="/tmp/cmux-orchestrator-$(date +%s)"
mkdir -p "$ORCH_DIR/logs" "$ORCH_DIR/results" "$ORCH_DIR/prompts"
```

Create state files:

```bash
# queue.json — pending tasks
# workers.json — active workers
# completed.json — finished results
```

### Step 3: Dispatch Workers

For each task, create a cmux workspace with provider-specific CLI invocation:

```bash
dispatch_worker() {
  local task_id="$1" description="$2" provider="$3" model="$4" cwd="$5" budget="$6"
  local log_file="$ORCH_DIR/logs/${task_id}.jsonl"
  local prompt_file="$ORCH_DIR/prompts/${task_id}.md"

  # Write prompt to file (avoid shell escaping — file-based delivery)
  echo "${description}" > "$prompt_file"

  # Construct provider-specific command (from CLAUDE.md Provider CLI Spec)
  local cmd
  case "$provider" in
    claude)
      cmd="cat '${prompt_file}' | claude \
        --output-format stream-json --verbose \
        --permission-mode auto \
        --model ${model} \
        ${budget:+--max-budget-usd ${budget}} \
        2>&1 | tee ${log_file}; \
        echo '===WORKER_DONE===' >> ${log_file}"
      ;;
    codex)
      # `-o` writes only the final agent message to a separate file, bypassing
      # verbose stdout logs. `collect_result()` prefers this file over log parsing.
      local codex_result_file="$ORCH_DIR/results/${task_id}.codex.last.txt"
      cmd="cat '${prompt_file}' | codex exec \
        ${model:+-m ${model}} \
        -o '${codex_result_file}' \
        2>&1 | tee ${log_file}; \
        echo '===WORKER_DONE===' >> ${log_file}"
      ;;
    gemini)
      cmd="gemini -p \"\$(cat '${prompt_file}')\" \
        --approval-mode yolo \
        ${model:+-m ${model}} \
        2>&1 | tee ${log_file}; \
        echo '===WORKER_DONE===' >> ${log_file}"
      ;;
  esac

  local raw=$(cmux new-workspace --cwd "$cwd" --command "$cmd")
  local ws=$(echo "$raw" | sed 's/^OK //')

  cmux rename-workspace --workspace "$ws" "[w${task_id}:${provider}] ${description:0:20}"

  # Get surface ref for monitoring
  local surface=$(cmux list-pane-surfaces --workspace "$ws" 2>/dev/null \
    | sed -n 's/.*surface:\([0-9]*\).*/\1/p' | head -1)

  # Register worker (provider field for result parsing and crash recovery)
  echo "{\"id\":\"${task_id}\",\"ws\":\"${ws}\",\"surface\":\"${surface}\",\"provider\":\"${provider}\",\"model\":\"${model}\",\"status\":\"running\",\"log\":\"${log_file}\"}"
}
```

### Step 4: Supervise (Poll Loop)

Monitor all workers until completion:

```bash
supervise() {
  local poll_interval=10
  local stuck_threshold=300  # 5 min

  while true; do
    local all_done=true
    local running=0
    local completed=0
    local failed=0

    for worker in $(jq -c '.[]' "$ORCH_DIR/workers.json"); do
      local ws=$(echo "$worker" | jq -r '.ws')
      local task_id=$(echo "$worker" | jq -r '.id')
      local status=$(echo "$worker" | jq -r '.status')

      [ "$status" = "completed" ] || [ "$status" = "failed" ] && {
        completed=$((completed + 1)); continue
      }

      all_done=false
      running=$((running + 1))

      # Read screen to detect state
      local screen=$(cmux read-screen --workspace "$ws" --lines 5 2>/dev/null)

      if echo "$screen" | grep -q "===WORKER_DONE==="; then
        update_status "$task_id" "completed"
        collect_result "$task_id"
        completed=$((completed + 1))

      elif echo "$screen" | grep -q "Do you want to proceed"; then
        cmux send-key --workspace "$ws" Enter 2>/dev/null

      elif echo "$screen" | grep -q "Error\|FAILED\|panic"; then
        update_status "$task_id" "failed"
        failed=$((failed + 1))
      fi
    done

    # Status line
    echo -ne "\r[orchestrator] running:${running} completed:${completed} failed:${failed}  "

    $all_done && break
    sleep $poll_interval
  done
}
```

### Step 5: Collect Results

```bash
collect_result() {
  local task_id="$1"
  local log="$ORCH_DIR/logs/${task_id}.jsonl"
  local result_file="$ORCH_DIR/results/${task_id}.json"

  # Read provider from workers.json
  local provider=$(jq -r ".[] | select(.id==\"${task_id}\") | .provider" "$ORCH_DIR/workers.json")

  # Extract result — provider-aware parsing
  python3 -c "
import json

provider = '${provider}'
log_file = '${log}'
task_id = '${task_id}'

result = None
cost = 0

if provider == 'claude':
    with open(log_file) as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get('type') == 'result':
                    result = obj.get('result', '')
                    cost = obj.get('total_cost_usd', 0)
            except: pass

elif provider == 'codex':
    # Codex: prefer dedicated --output-last-message file (only the final agent
    # message, no verbose logs). Fall back to log scan only if the file is
    # missing or empty — in that case accept JSONL 'content' but never overwrite
    # a previously parsed JSON result with a stray plain text log line.
    import os
    codex_last = log_file.replace('/logs/', '/results/').replace('.jsonl', '.codex.last.txt')
    if os.path.exists(codex_last):
        with open(codex_last) as f:
            text = f.read().strip()
        if text:
            result = text
    if result is None:
        parsed_json = False
        with open(log_file) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    if 'content' in obj:
                        result = obj['content']
                        parsed_json = True
                except:
                    if not parsed_json and line.strip() and '===WORKER_DONE===' not in line:
                        result = line.strip()

elif provider == 'gemini':
    with open(log_file) as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get('type') == 'result' or 'result' in obj:
                    result = obj.get('result', obj.get('text', ''))
            except:
                if line.strip() and '===WORKER_DONE===' not in line:
                    result = line.strip()

json.dump({
    'task_id': task_id,
    'provider': provider,
    'result': result,
    'cost': cost
}, open('$ORCH_DIR/results/${task_id}.json', 'w'), ensure_ascii=False, indent=2)
print(f'Task {task_id} [{provider}]: \${cost:.4f} -- {(result or \"\")[:80]}')
  "
}
```

### Step 6: Report Summary

```
═══════════════════════════════════════════════
 Orchestration Complete
═══════════════════════════════════════════════

 #  Task                    Provider  Status     Cost     Result
 1  PR #42 review            claude    ✅ done    $0.24    "3 issues found..."
 2  issue #100 impl         codex     ✅ done    $0.45    "Feature implemented..."
 3  codebase analysis       gemini    ✅ done    $0.03    "Architecture uses..."

 Total: 3 tasks, 0 failed, $0.72 total cost
 Providers: claude×1, codex×1, gemini×1
 Duration: 4m 32s
═══════════════════════════════════════════════
```

## Crash Recovery

If the orchestrator (or master Claude session) crashes:

```bash
# 1. Workers continue running (independent processes)
# 2. Restart orchestrator
# 3. Rescan cmux workspaces for [w*] prefixed names
# 4. Re-attach to workers via workspace refs
# 5. Collect completed results, resume supervision

recover_orchestrator() {
  local orch_dir="$1"

  # Find worker workspaces still alive
  # Provider is encoded in workspace name: [w{id}:{provider}]
  # If workers.json survives, provider is also recoverable from disk
  cmux list-workspaces 2>/dev/null | grep '\[w' | while read line; do
    local ws=$(echo "$line" | sed -n 's/.*workspace:\([^ ]*\).*/\1/p')
    local provider=$(echo "$line" | grep -oE 'claude|codex|gemini' | head -1 || echo "claude")
    local screen=$(cmux read-screen --workspace "$ws" --lines 3 2>/dev/null)

    if echo "$screen" | grep -q "===WORKER_DONE==="; then
      echo "COMPLETED [$provider]: $ws"
    elif echo "$screen" | grep -q "❯"; then
      echo "IDLE [$provider]: $ws (may need re-dispatch)"
    else
      echo "RUNNING [$provider]: $ws"
    fi
  done
}
```

## Full Pipeline Mode

Chain turbo-setup → execute → turbo-completion per worker:

```bash
# Each worker runs the full lifecycle
# Full pipeline always uses claude (turbo-* skills require Claude Code)
# Provider routing applies only to standalone task dispatch (Step 3)
full_pipeline_cmd() {
  local task="$1" model="$2" budget="$3"
  echo "claude -p '
    Phase 1: Run /turbo-setup for: ${task}
    Phase 2: Implement the task (use /cmux-delegate --model codex if pure implementation)
    Phase 3: Run /turbo-completion
  ' --output-format stream-json --verbose \
    --permission-mode auto \
    --model ${model:-sonnet} \
    --max-budget-usd ${budget}"
}
```

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Just run workers in-process, it's simpler" | Master crash = all workers lost. File-based state is the only safe path. |
| "Skip provider routing, use claude for everything" | Codex generates code faster. Gemini handles large context better. Route by task type. |
| "Skip model routing, use opus for all workers" | Wastes 80% of budget on tasks haiku can do. Route by complexity. |
| "Don't need a poll loop, workers will notify on done" | Notifications get lost on crash. Polling is boring and reliable. |
| "Skip result collection, I'll read logs manually" | Results scattered across N log files. Collect into `results/` for downstream use. |
| "Kill stuck workers immediately" | A worker may be waiting on user input ("Do you want to proceed"). Send-key first, kill only if truly stuck. |

## Integration

**Workflow position:**
```
[user provides task list]
  → [cmux-orchestrator]
     ├─ [worker 1] turbo-setup → execute → turbo-completion
     ├─ [worker 2] turbo-setup → execute → turbo-completion
     └─ [worker 3] turbo-setup → execute → turbo-completion
  → [results summary]
```

**Depends on:**
- `turbo-setup` (per-worker setup automation)
- `turbo-completion` (per-worker delivery automation)
- Model Routing Rules (from CLAUDE.md)
- cmux CLI (`new-workspace`, `read-screen`, `send-key`, `list-workspaces`)

**Environment:**
- cmux must be running (`cmux ping`)
- Claude Code CLI available (`claude -p`) — required
- Codex CLI available (`codex exec`) — optional, for code generation tasks
- Gemini CLI available (`gemini -p`) — optional, for analysis tasks
- `/tmp/cmux-orchestrator-*` for state files
