---
name: cmux-resume-sessions
description: >
  JSON 스냅샷에서 cmux 워크스페이스를 복원.
  cmux-save-sessions가 저장한 스냅샷을 입력으로 사용.
  Triggers on "resume sessions", "세션 복원", "세션 복구", "cmux resume", "restore sessions".
---

# cmux Resume Sessions

## Overview

`cmux-save-sessions`가 저장한 JSON 스냅샷에서 cmux 워크스페이스를 복원하는 스킬.
워크스페이스 구조(이름, cwd)를 복원하며, Claude Code 프로세스는 사용자가 수동으로 시작한다.

> **역할 분리**:
> - `cmux-resume-sessions`: JSON 스냅샷 기반 의도적 복원 (파일 기반)
> - `cmux-recover-sessions`: 크래시/정전 후 tmux 세션 기반 복구 (프로세스 기반)

## The Iron Law

```
RESUME RESTORES STRUCTURE AND CONTINUES CONVERSATIONS.
```

Resume은 워크스페이스 구조(이름, cwd)를 복원하고, `claude --continue`로 각 디렉토리의 최근 대화를 이어간다.
실행 중이던 명령어나 세션의 runtime state는 복원하지 않는다.

## Commands

### `resume [snapshot]` — 스냅샷에서 세션 복원

**실행 방법:**
1. 사용자가 "resume sessions", "세션 복원" 등을 요청
2. 스냅샷 선택:
   - 인자 없음 → 가장 최근 스냅샷 사용
   - 파일명 또는 전체 경로 지정 → 해당 스냅샷 사용
3. 다음 스크립트 실행:
```bash
bash "$(dirname "$0")/cmux-resume-sessions" [snapshot-file]
```
4. 출력 결과를 사용자에게 보여준다

**복원 내용:**
- 각 세션에 대해 cmux 워크스페이스 생성 (`--cwd` 옵션으로 작업 디렉토리 설정)
- 워크스페이스 이름을 저장 시점의 이름으로 설정
- 각 워크스페이스에서 `claude --continue` 자동 실행 (해당 cwd의 최근 대화 이어가기)
- cwd가 존재하지 않는 세션은 스킵 (경고 출력)

**플래그:**
- `--no-claude`: Claude Code 자동 시작을 스킵 (워크스페이스 구조만 복원)

**복원하지 않는 것:**
- 실행 중이던 명령어
- 세션의 runtime state (git 상태, 편집 중이던 파일 등)

## Output Example

```
Resuming from: sessions-20260407-143000.json
  Saved at: 2026-04-07T14:30:00+0900 | Host: macbook-pro.local | Sessions: 7

  ✓ Review PR comments → workspace:150 (/Users/nathan.song/projects/hub)
  ✓ Fix auth bug → workspace:151 (/Users/nathan.song/projects/backend)
  ⚠ SKIP: Old worktree task (cwd not found: /tmp/wt-deleted)
  ✗ FAIL: Broken session

Done. Created: 2 | Skipped: 1 | Failed: 1
```

## Integration

- **cmux-save-sessions**: 이 스킬의 입력 데이터를 생성하는 스킬
- **cmux-session-manager**: 복원 후 `status`로 결과 확인 가능
- **cmux-orchestrator**: 복원된 워크스페이스에서 워커 재시작 가능

## Troubleshooting

| 문제 | 원인 | 해결 |
|------|------|------|
| "cmux is not running" | cmux 앱 미실행 | cmux 앱 실행 |
| "jq is required" | jq 미설치 | `brew install jq` |
| "cwd not found" | 저장 시점의 디렉토리가 삭제됨 | 해당 세션은 자동 스킵 |
| "No snapshots" | 저장된 스냅샷 없음 | `cmux-save-sessions`로 먼저 저장 |
| 세션이 중복 생성됨 | 이미 열린 세션과 겹침 | 복원 전 기존 세션 확인 필요 |
