---
name: cmux-session-manager
description: cmux 세션 일상 관리 자동화. status(대시보드)/cleanup(정리+재조직) 수동 커맨드와 init(hook)/report(schedule) 자동화. Triggers on "cmux session", "세션 관리", "세션 정리", "cmux status", "cmux cleanup", "cmux tidy".
---

# cmux Session Manager

## Overview

cmux에서 Claude Code 세션이 무제한 누적되는 문제를 해결하는 **일상 관리 자동화 스킬**.
세션의 전체 라이프사이클(생성 → 관리 → 정리)을 커버한다.

> **역할 분리**: 이 스킬은 "일상 관리"용. 크래시/정전 후 세션 복구는 `recover-sessions-cmux` 스킬을 사용.

## Commands

### `status` — 세션 대시보드

전체 cmux 세션의 상태를 테이블로 표시한다.

**실행 방법:**
1. 사용자가 `cmux-session status` 또는 `cmux status`를 요청
2. 다음 스크립트를 실행:
```bash
bash "$(dirname "$0")/cmux-session-status"
```
3. 출력 결과를 사용자에게 그대로 보여준다

**출력 내용:**
- 상단 요약: Active / Waiting / Idle / Crashed / Orphaned 카운트
- 세션 테이블: 상태 아이콘, 카테고리, 이름, 브랜치
- 고아 섹션: auto-removable / named / unsafe 분류

### `cleanup [--dry-run]` — 정리 + 재조직

3-Phase 정리를 수행한다. `--dry-run` 플래그로 실행 없이 계획만 볼 수 있다.

**실행 방법:**
1. 사용자가 `cmux-session cleanup` 또는 `cmux cleanup`을 요청
2. dry-run 여부 확인 후 스크립트 실행:
```bash
bash "$(dirname "$0")/cmux-session-cleanup" [--dry-run]
```
3. 스크립트는 3개의 JSON 블록을 `---PHASE_SEPARATOR---`로 구분하여 출력한다
4. 각 Phase의 JSON을 파싱하여 아래 Data Handoff Protocol에 따라 처리한다

## Data Handoff Protocol

cleanup 스크립트는 3개의 JSON 블록을 stdout으로 출력한다.
Claude는 이를 파싱하여 사용자 인터랙션을 처리한다.

### Phase 1: `auto_cleanup` (자동 — 사용자 확인 불필요)
```json
{"phase":"auto_cleanup","actions":[
  {"action":"kill_orphan","session":"12","type":"safe_numeric","executed":true},
  {"action":"close_crashed","ref":"workspace:69","name":"...","executed":true},
  {"action":"report_orphan","session":"psm_...","type":"safe_named","auto_delete":false}
]}
```
- `executed: true` 항목은 이미 실행됨 — 결과를 사용자에게 보고
- `auto_delete: false` 항목은 참고용 — "이 세션은 자동 삭제하지 않았음" 알림

### Phase 2: `idle_cleanup` (사용자 확인 필요)
```json
{"phase":"idle_cleanup","sessions":[
  {"ref":"workspace:43","name":"...","state":"IDLE","branch":"main","category":"[TMP]"},
  {"ref":"workspace:67","name":"...","state":"WAITING","branch":"main","category":"[OPS]"}
]}
```
**처리 방법:**
1. sessions 배열이 비어있으면 "정리할 유휴 세션이 없습니다" 출력 후 Phase 3로
2. sessions가 있으면 `AskUserQuestion` (multiSelect: true)로 표시:
   - 각 옵션의 label: `[STATE] [CAT] name (branch)`
   - 사용자가 선택한 세션에 대해 실행:
   ```bash
   cmux close-workspace --workspace <ref>
   ```

### Phase 3: `reorganize` (사용자 확인 필요)
```json
{"phase":"reorganize","single_window_warning":true,"changes":[
  {"ref":"workspace:30","current_name":"...","proposed_name":"[DEV] ...","category":"[DEV]","target_window":"Active Dev"}
]}
```
**처리 방법:**
1. `single_window_warning: true`이면 경고 표시:
   > "현재 모든 워크스페이스가 1개 window에 있습니다. 재조직하면 카테고리별 window(Active Dev, Ops/Debug, Research)가 생성됩니다."
2. changes 배열이 비어있으면 "재조직할 세션이 없습니다" 출력
3. changes가 있으면 미리보기 테이블 표시 후 `AskUserQuestion`로 확인:
   - "Yes, reorganize" / "Skip reorganization"
4. 확인되면 각 change에 대해:
   ```bash
   cmux rename-workspace --workspace <ref> "<proposed_name>"
   cmux set-status category "<category>" --workspace <ref>
   ```
5. Window 이동은 `get_or_create_window` 로직 적용:
   - 해당 카테고리의 다른 워크스페이스가 이미 존재하는 window를 찾아 이동
   - 없으면 새 window 생성 후 이동:
   ```bash
   cmux move-workspace-to-window --workspace <ref> --window <window_ref>
   ```

## State Detection

`cmux sidebar-state`의 `claude_code=` 필드를 primary signal로 사용:

| `claude_code=` | State | Cleanup 대상 |
|----------------|-------|-------------|
| `Running` | ACTIVE | No |
| `Needs input` | WAITING | Phase 2 (선택적) |
| `Idle` | IDLE 또는 CRASHED | Phase 2 (IDLE) / Phase 1 (CRASHED) |
| (없음) | UNKNOWN | No |

Idle 워크스페이스는 `read-screen`으로 crash 시그니처 (`bun.report`, `segfault` 등) 추가 확인.

## Category Classification

우선순위 (높은 순):
1. **[DEV]**: 브랜치가 `hub-N-feat-*`, `hub-N-refactor-*` 등
2. **[OPS]**: 이름에 `failure`, `debug`, `error`, `fix`, `incident` 등
3. **[RES]**: 이름에 `analyze`, `investigate`, `compare`, `check`, `research` 등
4. **[TMP]**: 위 모두 해당 없음

## Init Hook (자동화)

새 세션 생성 시 자동으로 카테고리를 적용하려면 cmux hook을 설정:

```bash
# cmux hook 설정 (session-start 시 카테고리 태깅)
cmux set-hook session-start 'bash -c "
  WS=\$CMUX_WORKSPACE_ID
  NAME=\$(cmux read-screen --workspace \$WS --lines 1 2>/dev/null | head -1)
  # classify and rename logic here
"'
```

또는 Claude Code `settings.json`에 session-start hook을 추가하여
`cmux-session-lib`의 `classify_category`를 호출하고 자동 rename + set-status.

## Report Schedule (자동화)

매일 마감 시 자동 리포트를 받으려면 `/schedule` 스킬 사용:

```
/schedule "매일 18:00에 cmux-session status 실행하고 결과를 Slack으로 전송"
```

report는 status 출력 + `sidebar-state`의 `pr=` 필드로 PR 상태 + 정리 대상 추천을 포함.

## Troubleshooting

| 문제 | 원인 | 해결 |
|------|------|------|
| "cmux is not running" | cmux 앱 미실행 | cmux 앱 실행 |
| "jq is required" | jq 미설치 | `brew install jq` |
| 세션 상태 UNKNOWN | sidebar-state에 claude_code 없음 | 해당 세션은 Claude Code가 아닐 수 있음 |
| 고아 0개 | 모든 tmux 세션이 cmux 소유 | 정상 — 정리할 것 없음 |
