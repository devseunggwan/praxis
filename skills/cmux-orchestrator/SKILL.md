---
name: cmux-orchestrator
description: Dispatch and supervise multiple Claude Code workers in cmux workspaces. Crash-resilient, model-routed, file-based coordination. Triggers on "orchestrate", "dispatch", "오케스트레이터", "cmux workers".
---

# cmux Orchestrator

## Overview

Dispatches tasks to independent Claude Code workers running in cmux workspaces.
Each worker is a separate process — master crash does NOT kill workers.

**Core principle:** Workers are independent. Coordination is file-based. Crashes are recoverable.

**Chains:** `turbo-setup` → execute → `turbo-deliver` as a full pipeline per worker.

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
  claude    claude    claude   ├─ w1.jsonl
  --sonnet  --haiku   --opus   ├─ w2.jsonl
  task-1    task-2    task-3   └─ w3.jsonl
```

## When to Use

- Multiple independent tasks to execute in parallel
- Batch processing (code reviews, test runs, migrations)
- Any work that benefits from crash isolation
- Triggers: "orchestrate", "dispatch", "오케스트레이터", "cmux workers"

## Inputs

User provides a task list:

```
다음 작업들을 병렬로 처리해주세요:
1. laplace-airflow-dags PR #7042 코드 리뷰
2. laplace-web-v2 issue #300 구현
3. Hub #789 상태 확인
```

Or a structured task file:

```json
// /tmp/orchestrator/tasks.json
[
  {"description": "PR #7042 코드 리뷰", "complexity": "medium", "cwd": "/path/to/repo"},
  {"description": "issue #300 구현", "complexity": "medium", "cwd": "/path/to/repo"},
  {"description": "Hub #789 상태 확인", "complexity": "low", "cwd": "/path/to/repo"}
]
```

## Process

### Step 1: Parse Tasks + Route Models

For each task, determine complexity and assign model:

```
Task → Complexity Router → Model Assignment
  "코드 리뷰"     → medium  → sonnet
  "구현"           → medium  → sonnet
  "상태 확인"      → low     → haiku
  "아키텍처 설계"  → high    → opus
```

**Routing rules (from CLAUDE.md Model Routing Rules):**
- `find`, `search`, `list`, `status`, `check` → **haiku**
- `implement`, `fix`, `test`, `review`, `refactor` → **sonnet**
- `architect`, `design`, `security`, `incident`, `debug` → **opus**
- Default: **sonnet**

Present the dispatch plan and ask for confirmation:

```
═══════════════════════════════════════════════
 Dispatch Plan
═══════════════════════════════════════════════

 #  Task                          Model    Budget
 1  PR #7042 코드 리뷰            sonnet   $0.50
 2  issue #300 구현               sonnet   $1.00
 3  Hub #789 상태 확인            haiku    $0.10

 Total budget: $1.60
 Max concurrent workers: 3

 Proceed? (y/n)
═══════════════════════════════════════════════
```

### Step 2: Initialize Orchestrator State

```bash
ORCH_DIR="/tmp/cmux-orchestrator-$(date +%s)"
mkdir -p "$ORCH_DIR/logs" "$ORCH_DIR/results"
```

Create state files:

```bash
# queue.json — pending tasks
# workers.json — active workers
# completed.json — finished results
```

### Step 3: Dispatch Workers

For each task, create a cmux workspace:

```bash
dispatch_worker() {
  local task_id="$1" description="$2" model="$3" cwd="$4" budget="$5"
  local log_file="$ORCH_DIR/logs/${task_id}.jsonl"

  local cmd="claude -p '${description}' \
    --output-format stream-json --verbose \
    --permission-mode auto \
    --model ${model} \
    --max-budget-usd ${budget} \
    2>&1 | tee ${log_file}; \
    echo '===WORKER_DONE===' >> ${log_file}"

  local raw=$(cmux new-workspace --cwd "$cwd" --command "$cmd")
  local ws=$(echo "$raw" | sed 's/^OK //')

  cmux rename-workspace --workspace "$ws" "[w${task_id}] ${description:0:25}"

  # Get surface ref for monitoring
  local surface=$(cmux list-pane-surfaces --workspace "$ws" 2>/dev/null \
    | grep -oP 'surface:\d+' | head -1)

  # Register worker
  echo "{\"id\":\"${task_id}\",\"ws\":\"${ws}\",\"surface\":\"${surface}\",\"status\":\"running\",\"log\":\"${log_file}\"}"
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

  # Extract final result from stream-json
  python3 -c "
import json
result = None
cost = 0
with open('${log}') as f:
    for line in f:
        try:
            obj = json.loads(line)
            if obj.get('type') == 'result':
                result = obj.get('result', '')
                cost = obj.get('total_cost_usd', 0)
        except: pass
json.dump({'task_id': '${task_id}', 'result': result, 'cost': cost}, 
          open('${result_file}', 'w'), ensure_ascii=False, indent=2)
print(f'Task ${task_id}: \${cost:.4f} — {(result or \"\")[:80]}')
  "
}
```

### Step 6: Report Summary

```
═══════════════════════════════════════════════
 Orchestration Complete
═══════════════════════════════════════════════

 #  Task                    Status     Cost     Result
 1  PR #7042 리뷰           ✅ done    $0.24    "3 issues found, 1 critical..."
 2  issue #300 구현         ✅ done    $0.45    "Feature implemented, tests pass..."
 3  Hub #789 확인           ✅ done    $0.03    "Status: all tasks completed..."

 Total: 3 tasks, 0 failed, $0.72 total cost
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
  cmux list-workspaces 2>/dev/null | grep '\[w' | while read line; do
    local ws=$(echo "$line" | grep -oP 'workspace:\d+')
    local screen=$(cmux read-screen --workspace "$ws" --lines 3 2>/dev/null)

    if echo "$screen" | grep -q "===WORKER_DONE==="; then
      echo "COMPLETED: $ws"
    elif echo "$screen" | grep -q "❯"; then
      echo "IDLE: $ws (may need re-dispatch)"
    else
      echo "RUNNING: $ws"
    fi
  done
}
```

## Full Pipeline Mode

Chain turbo-setup → execute → turbo-deliver per worker:

```bash
# Each worker runs the full lifecycle
full_pipeline_cmd() {
  local task="$1" model="$2" budget="$3"
  echo "claude -p '
    Phase 1: Run /turbo-setup for: ${task}
    Phase 2: Implement the task
    Phase 3: Run /turbo-deliver
  ' --output-format stream-json --verbose \
    --permission-mode auto \
    --model ${model} \
    --max-budget-usd ${budget}"
}
```

## Integration

**Workflow position:**
```
[user provides task list]
  → [cmux-orchestrator]
     ├─ [worker 1] turbo-setup → execute → turbo-deliver
     ├─ [worker 2] turbo-setup → execute → turbo-deliver
     └─ [worker 3] turbo-setup → execute → turbo-deliver
  → [results summary]
```

**Depends on:**
- `turbo-setup` (per-worker setup automation)
- `turbo-deliver` (per-worker delivery automation)
- Model Routing Rules (from CLAUDE.md)
- cmux CLI (`new-workspace`, `read-screen`, `send-key`, `list-workspaces`)

**Environment:**
- cmux must be running (`cmux ping`)
- Claude Code CLI available (`claude -p`)
- `/tmp/cmux-orchestrator-*` for state files
