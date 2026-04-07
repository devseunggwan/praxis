---
name: cmux-save-sessions
description: >
  cmux 세션 리스트를 JSON 스냅샷으로 저장. 현재 세션은 기본 제외.
  save(저장)/list(목록) 커맨드 지원.
  Triggers on "save sessions", "세션 저장", "세션 스냅샷", "cmux save", "list snapshots", "스냅샷 목록".
---

# cmux Save Sessions

## Overview

cmux 워크스페이스의 현재 상태를 JSON 스냅샷으로 저장하는 스킬.
스냅샷은 세션 복구, 히스토리 기록, 공유, 타 스킬 입력 데이터로 활용된다.

> **역할 분리**:
> - `cmux-save-sessions`: 현재 상태를 JSON으로 캡처 (저장)
> - `cmux-resume-sessions`: JSON 스냅샷에서 워크스페이스 복원 (복원)
> - `cmux-recover-sessions`: 크래시/정전 후 tmux 기반 복구 (비상)
> - `cmux-session-manager`: 실시간 상태 + 정리 (일상)

## The Iron Law

```
SAVE CAPTURES TRUTH. CURRENT SESSION IS EXCLUDED BY DEFAULT.
```

Save는 현재 시점의 정확한 상태를 기록한다.
스크립트를 실행하는 세션(관리자 세션)은 기본적으로 제외된다 — 작업 세션만 저장한다.

## Commands

### `save` — 세션 스냅샷 저장

**실행 방법:**
1. 사용자가 "save sessions", "세션 저장" 등을 요청
2. 다음 스크립트 실행:
```bash
bash "$(dirname "$0")/cmux-save-sessions"
```
3. 출력 결과를 사용자에게 보여준다
4. **저장 후 닫기 확인** — `AskUserQuestion`으로 물어본다:

> "N개 세션이 저장되었습니다. 저장된 세션을 닫을까요?"
> - **전체 닫기**: 저장된 모든 워크스페이스를 닫는다
> - **선택 닫기**: 사용자가 닫을 세션을 선택한다 (multiSelect)
> - **유지**: 아무것도 닫지 않는다

5. 닫기 선택 시, 저장된 JSON에서 ref를 읽어 실행:
```bash
cmux close-workspace --workspace <ref>
```

> **tmux 세션은 직접 종료하지 않는다** — `cmux close-workspace`가 backing terminal을 자체 정리한다.
> 수동 `tmux kill-session`은 다른 워크스페이스의 terminal을 깨뜨릴 수 있다.
> 고아 tmux 세션 정리가 필요하면 `cmux-session-manager`의 cleanup 커맨드를 사용한다.

> ⚠️ **현재 세션(관리자 세션)은 절대 닫지 않는다** — 닫으면 Claude Code가 종료된다.
> `--include-self`로 저장했더라도 현재 세션은 닫기 대상에서 제외한다.

**플래그:**
- `--include-self`: 현재 세션도 포함하여 저장

**저장 위치:** `~/.cmux/sessions/sessions-YYYYMMDD-HHMMSS.json`

**캡처 데이터:**
- workspace ref, name, state (Active/Idle/Waiting/Crashed/Unknown)
- git branch, PR status, category ([DEV]/[OPS]/[RES]/[TMP])
- working directory (cwd)

### `list` — 저장된 스냅샷 목록

**실행 방법:**
1. 사용자가 "list snapshots", "스냅샷 목록" 등을 요청
2. `~/.cmux/sessions/` 디렉토리의 파일을 나열:
```bash
for f in $(ls -t ~/.cmux/sessions/sessions-*.json 2>/dev/null | head -20); do
  saved_at=$(jq -r '.saved_at' "$f")
  total=$(jq -r '.total' "$f")
  echo "  $(basename "$f") | $saved_at | $total sessions"
done
```

## Output Format

### JSON 스냅샷 구조
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

- **cmux-resume-sessions**: 이 스킬이 저장한 JSON을 입력으로 사용
- **cmux-recover-sessions**: 크래시 복구에 스냅샷을 참조 데이터로 활용 가능
- **cmux-session-manager**: status와 유사하지만 영속적 기록
- **cmux-orchestrator**: 워커 구성을 스냅샷으로 저장

## Troubleshooting

| 문제 | 원인 | 해결 |
|------|------|------|
| "cmux is not running" | cmux 앱 미실행 | cmux 앱 실행 |
| "jq is required" | jq 미설치 | `brew install jq` |
| 0 sessions saved | 현재 세션만 존재 | `--include-self` 플래그 사용 |
