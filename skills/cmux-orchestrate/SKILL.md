---
name: cmux-orchestrate
description: >
  Group delegated cmux workspaces into a Run and answer, after the fact, how far
  that work got. Triggers on "run ledger", "orchestrate", "위임 진행 상황",
  "그 작업 어디까지", "횡단 조회".
verified-against-runtime: true
runtime-verified-at: 2026-08-14
runtime-verified-note: "cmux 0.64.22 — workspace-group create/add and set-status/set-progress carry no agent prohibition, unlike `cmux todo`, whose --help reserves the checklist for the user."
---

# cmux-orchestrate

## Overview

`cmux-delegate` hands one task to one workspace and forgets it. Once
`--distribute` scatters N of them, nothing holds the N together: asking "how far
did that get" means reading N sidebar tabs by eye, and the only completion
signal is a report file that exists or does not.

This skill adds the layer above: a Run groups the delegations, and each bound
workspace carries a task state that distinguishes not-started from in-flight
from blocked from done.

**Core principle:** the ledger is the record and cmux is a view. Every cmux call
here can fail without costing an answer.

## When to Use

- A `--distribute` delegation is in flight and you want one answer instead of N tabs
- A delegation was handed off across a session boundary and its state has to be recovered
- Something is stuck and the question is *which* worker is waiting on a human

## Process

### Step 1: Create a Run

```bash
RL="${CLAUDE_PLUGIN_ROOT}/skills/cmux-orchestrate/run-ledger.sh"
eval "$(sh "$RL" create "P1~P5 에러 조사")"
echo "$run"      # 1786600000-41234
```

`cmux-delegate --distribute` does this on its own (its Step 3.6) and reports the
id. Call it directly only when grouping delegations you are launching by hand.

### Step 2: Bind Workspaces

```bash
sh "$RL" bind "$RUN" "$WS_UUID" "P1 조사"
```

The selector is the workspace **UUID**, not `workspace:N` — the ref numbering's
stability across close/open has never been confirmed, and the UUID does not
raise the question. `cmux-delegate` stashes it in a `.ws` file at launch, which
is the one moment it is known.

### Step 3: Record Transitions

```bash
sh "$RL" start "$RUN" "$WS_UUID" "조사 착수"
sh "$RL" done  "$RUN" "$WS_UUID" "보고서 검증 통과"
sh "$RL" block "$RUN" "$WS_UUID" "모달 프롬프트 대기"
```

Blocked is a current condition, not a scar: a task that was blocked and later
finished reads as done. A run with any blocked task reads as blocked, because
the run state exists to surface what needs a human and two workers progressing
must not paint over the third.

### Step 4: Query

```bash
eval "$(sh "$RL" summary "$RUN")"
echo "$state $done/$total"     # blocked 2/3
```

| `state` | 뜻 | 위임자가 할 일 |
| --- | --- | --- |
| `pending` | 묶였으나 아무도 착수 안 함 | 워커가 실제로 떴는지 본다 |
| `running` | 진행 중 | 기다린다 |
| `blocked` | 하나 이상이 사람을 기다림 | 그 워크스페이스를 연다 |
| `complete` | 전부 done | 결과를 수거한다 |
| `closed` | 명시적으로 닫힘 | 없음 — 이력으로만 읽는다 |
| `empty` | Run 은 있으나 바인딩 0건 | `done == total` 이 0 == 0 으로 참이 되는 경우. `complete` 와 구분된다 |

`sh "$RL" list` enumerates run ids newest first.

### Step 5: Close

```bash
sh "$RL" close "$RUN"
```

`closed` outranks the task counts — a run closed with work outstanding reports
`closed`, not `blocked`. Closing is the statement that no one is watching any more.

## What This Deliberately Does Not Touch

`cmux todo` is not used, and no code here should start using it. Its own
`--help` reserves the checklist for the user:

> Note for coding agents: this checklist belongs to the user. Do not add, edit,
> complete, remove, or replace items on your own initiative — only manage it
> when the user explicitly asks you to.

The sidebar is painted with `set-status praxis_run <state>` and
`set-progress <done/total>`, neither of which carries that prohibition. Issue
#982's body asked for the `todo` route; that checkbox is unadopted for this
reason rather than unfinished.

## Error Handling

| Error | Recovery |
|-------|----------|
| cmux absent or a cmux call fails | Swallowed. The ledger is written and the answer is unaffected |
| State dir unwritable | Swallowed, exit 0. A ledger that fails a delegation is worse than no ledger |
| Unknown run id | `total=0 state='empty'`, exit 0 — not an error, an answer |
| A torn or corrupt event line | That line is skipped; the rest of the history still answers |
| Malformed argv | usage on stderr, **exit 2**. The one failure not swallowed: the caller has a bug |

## Limitations

- The group is anchor-based and dies with the cmux process; only the ledger
  survives a restart. This is why the ledger is the record.
- `done` in a `--distribute` delegation is not recorded per worker — one report
  file is shared by N workers (#903), so folding it into N completions would
  answer backwards. Those tasks stay `running` until #903 is resolved.
- Task labels are free text and never deduplicated. Two workers given the same
  label are two tasks, distinguished only by workspace UUID.
