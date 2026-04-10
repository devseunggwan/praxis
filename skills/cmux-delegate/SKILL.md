---
name: cmux-delegate
description: Delegate a task to an independent Claude Code session in a new cmux workspace with auto-collected context. Triggers on "delegate", "cmux delegate", "new session".
---

# cmux-delegate

## Overview

현재 대화의 작업 맥락을 자동 수집하여, cmux workspace에서 독립 Claude Code 세션을 열어 범용 작업(리뷰, 디버깅, 구현 등)을 위임합니다. 기존 세션 재사용, 별도 계정 프로필, 다중 항목 병렬 분산을 지원합니다.

**Core principles:**
- 프롬프트는 반드시 파일 기반 전달. 인라인 `-p` 절대 사용 금지 (shell escaping 문제 회피).
- 유저가 세션명/계정을 명시하면 글자 그대로 따른다. 자의적 재해석 금지.

## When to Use

- 현재 작업의 독립 리뷰/검수가 필요할 때
- 디버깅이나 구현을 별도 세션에 위임할 때
- 현재 컨텍스트의 편향 없이 fresh eyes가 필요할 때
- 다중 독립 항목을 병렬로 조사/실행할 때
- Triggers: "delegate", "cmux delegate", "new session", "별도 세션"

## Inputs

사용자가 위임할 작업을 설명합니다:

```
/cmux-delegate 전체 코드 검수 요청 --model opus
/cmux-delegate "PR #78, #137, #7502 크로스-레포 일관성 검증" --account claude-2
/cmux-delegate debug auth token refresh failure --session claude-2
/cmux-delegate "P1~P5 에러 조사" --account claude-2 --distribute
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `<task>` | (required) | 위임할 작업 설명 |
| `--model` | `sonnet` | Claude 모델 (opus/sonnet/haiku) |
| `--cwd` | current dir | 새 세션의 작업 디렉토리 |
| `--max-budget-usd` | (none) | 최대 예산 한도 |
| `--account` | (기본 계정) | Claude 계정 프로필 (예: `claude-2` → `CLAUDE_CONFIG_DIR=~/.claude-2`) |
| `--session` | (신규 생성) | 기존 워크스페이스에 전달 (이름 또는 workspace ref) |
| `--distribute` | false | 독립 항목별 병렬 분산 실행 |

## Process

### Step 1: Parse Arguments

`{{ARGUMENTS}}`에서 인자를 파싱합니다:

```
args = parse("{{ARGUMENTS}}")
model = args.model || "sonnet"
cwd = args.cwd || $(pwd)
budget = args["max-budget-usd"] || ""
account = args.account || ""
session = args.session || ""
distribute = args.distribute || false
task = args.task (remaining text after flags)
short_task = task[:30], sanitized to [a-zA-Z0-9가-힣 -] only (for cmux workspace name)
timestamp = epoch seconds + PID (e.g., 1744163800-12345) to avoid collision
```

### Step 1.5: Session Resolution

기존 세션 사용 여부를 결정합니다.

```
if session is specified:
  1. cmux list-workspaces → 이름 또는 ref로 매칭
  2. 매칭 성공 → cmux send 모드 (Step 5b)
  3. 매칭 실패 → 에러: "세션 '{session}'을 찾을 수 없습니다" 출력 후 중단
else:
  → 기존 동작 (new-workspace, Step 5a)
```

### Step 1.6: Account Resolution

계정 프로필을 결정합니다.

```
if account is specified:
  # 계정 프로필은 CLAUDE_CONFIG_DIR 환경변수로 지정
  # 예: --account claude-2 → CLAUDE_CONFIG_DIR=~/.claude-2
  claude_env = "CLAUDE_CONFIG_DIR=~/.{account}"
  
  # 검증: 해당 config 디렉토리 존재 여부 확인
  if not exists(~/.{account}):
    에러: "계정 프로필 디렉토리 ~/.{account}이 없습니다" 출력 후 중단
else:
  claude_env = ""  # 기본 계정 사용
```

### Step 2: Collect Context

현재 대화와 프로젝트의 맥락을 자동 수집합니다. 각 명령은 실패해도 계속 진행합니다 (`2>/dev/null`).

수집할 정보:

```bash
# 1. Git 상태
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
COMMITS=$(git log --oneline -5 2>/dev/null || echo "no git history")
DIFF_STAT=$(git diff --stat HEAD 2>/dev/null || echo "no changes")

# 2. 변경 파일 목록 (base branch 대비)
BASE_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
BASE_BRANCH=${BASE_BRANCH:-main}
CHANGED_FILES=$(git diff --name-only $(git merge-base HEAD "origin/$BASE_BRANCH" 2>/dev/null || echo HEAD~5)..HEAD 2>/dev/null || echo "unknown")

# 3. PR 정보 (있으면)
PR_INFO=$(gh pr list --head "$BRANCH" --json number,title,url 2>/dev/null || echo "no PR")

# 4. PR 리뷰 코멘트 (있으면)
REVIEW_COMMENTS="0"
PR_NUM=$(gh pr list --head "$BRANCH" --json number -q '.[0].number' 2>/dev/null || echo "")
if [ -n "$PR_NUM" ]; then
  REVIEW_COMMENTS=$(gh api "repos/$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null)/pulls/$PR_NUM/comments" --jq 'length' 2>/dev/null || echo "0")
fi
```

### Step 3: Generate Prompt File

수집된 맥락과 사용자 프롬프트를 합성하여 `/tmp/cmux-delegate-{timestamp}.md`에 저장합니다.

프롬프트 파일 구조:

```markdown
# Task: {task}

## Context (auto-collected)

- **Branch:** {BRANCH}
- **Base branch:** {BASE_BRANCH}
- **Recent commits:**
{COMMITS}

- **Changed files:**
{CHANGED_FILES}

- **Diff summary:**
{DIFF_STAT}

- **PR:** {PR_INFO}
- **Review comments:** {REVIEW_COMMENTS} pending

## Instructions

{task description from user}

---
Report results in Korean.
```

**CRITICAL:** 프롬프트 파일은 `Write` 도구로 생성합니다 (shell 미경유). `echo`, `cat <<EOF`, `printf` 등 shell을 통한 파일 생성은 절대 금지 — 특수문자가 해석됩니다.

### Step 3.5: Distribute Mode (--distribute)

`--distribute` 플래그가 지정된 경우, 프롬프트를 독립 항목별로 분할합니다.

**자동 분할 기준:**
- 프롬프트에 `## P1`, `## P2`, `### 항목 1` 등 독립 섹션이 있으면 섹션별 분할
- 번호 리스트(`1.`, `2.` 등)로 구분된 독립 작업이 있으면 항목별 분할
- 분할 결과가 1개면 distribute 무시 (단일 세션)

**분할 프로세스:**
1. 프롬프트를 섹션별로 분리 → 각각 개별 .md 파일 생성
2. Context 섹션은 모든 분할 파일에 공통 포함
3. 각 파일에 대해 개별 래퍼 .sh 생성
4. 모델 라우팅: `--model`이 명시적이면 전체 동일, 없으면 복잡도 기반 자동 배정
   - 데이터 조회/상태 확인 → haiku
   - 분석/구현 → sonnet
   - 설계/보안 → opus

### Step 4: Generate Wrapper Script

`/tmp/cmux-delegate-{timestamp}.sh`를 생성합니다:

```bash
#!/bin/bash
PROMPT_FILE="/tmp/cmux-delegate-{timestamp}.md"
SCRIPT_FILE="/tmp/cmux-delegate-{timestamp}.sh"

# Cleanup: .sh만 삭제. .md는 보존 (다른 워크스페이스가 참조할 수 있음)
trap 'rm -f "$SCRIPT_FILE"' EXIT

cat "$PROMPT_FILE" | {claude_env} claude \
  --model {model} \
  --permission-mode auto \
  {budget_flag}

# Notify on completion
cmux notify --title "cmux-delegate" --body "Task completed: {short_task}" 2>/dev/null || true
```

`{claude_env}`는 account가 지정된 경우 `CLAUDE_CONFIG_DIR=~/.{account}`로 치환합니다.
`{budget_flag}`는 budget이 지정된 경우에만 `--max-budget-usd {budget}`로 치환합니다.

**이 파일도 `Write` 도구로 생성합니다.** 단, 파일 내용 자체에 shell 변수(`$PROMPT_FILE` 등)가 포함되므로 이는 의도된 것입니다 — 중요한 것은 사용자 프롬프트가 이 스크립트를 거치지 않는다는 점입니다.

**CRITICAL — trap에서 .md 파일을 삭제하지 않습니다.** 워크스페이스가 닫힐 때 trap이 실행되는데, 다른 워크스페이스가 동일 .md 파일을 참조할 수 있기 때문입니다 (distribute 모드, 재시도 등).

### Step 5a: Launch cmux Workspace (신규 세션)

`--session`이 지정되지 않은 경우:

```bash
WS_RAW=$(cmux new-workspace \
  --name "[delegate] {short_task}" \
  --cwd "{cwd}" \
  --command "bash /tmp/cmux-delegate-{timestamp}.sh")

# Validate workspace creation
if [[ "$WS_RAW" != OK* ]]; then
  echo "Error: workspace 생성 실패 — $WS_RAW"
  echo "수동 실행: bash /tmp/cmux-delegate-{timestamp}.sh"
  exit 1
fi

WS_REF=$(echo "$WS_RAW" | sed 's/^OK //')
```

**distribute 모드에서는 분할된 항목 수만큼 반복 실행합니다.**

### Step 5b: Send to Existing Session (기존 세션)

`--session`이 지정된 경우:

```bash
# 1. 워크스페이스 매칭
TARGET=$(cmux list-workspaces | grep "{session}" | head -1 | awk '{print $1}')

if [ -z "$TARGET" ]; then
  echo "Error: 세션 '{session}'을 찾을 수 없습니다"
  cmux list-workspaces
  exit 1
fi

# 2. 프롬프트 파일 경로를 전달
cmux send --workspace "$TARGET" \
  "{prompt_file_path} 파일을 읽고 조사해주세요."
cmux send-key --workspace "$TARGET" Enter
```

### Step 6: Report

스킬 실행 결과를 사용자에게 보고합니다:

**단일 세션 모드:**
```
Delegated to {WS_REF}
  Task: {short_task}
  Model: {model}
  Account: {account || "default"}
  Prompt: /tmp/cmux-delegate-{timestamp}.md
  CWD: {cwd}

cmux에서 {WS_REF} 탭을 확인하세요.
완료 시 cmux notify로 알림이 전송됩니다.
```

**distribute 모드:**
```
Distributed to {N} workspaces:
  | Workspace | Task | Model | Account |
  |-----------|------|-------|---------|
  | {ws_ref}  | {item_title} | {model} | {account} |
  ...

각 cmux 탭에서 진행 상황을 확인하세요.
완료 시 cmux notify로 개별 알림이 전송됩니다.
```

**기존 세션 모드:**
```
Sent to {TARGET} ({session_name})
  Task: {short_task}
  Prompt: /tmp/cmux-delegate-{timestamp}.md

cmux에서 {session_name} 탭을 확인하세요.
```

## Error Handling

| Error | Recovery |
|-------|----------|
| `cmux` not found | "cmux가 설치되어 있지 않습니다. cmux.app을 설치해주세요." 출력 후 중단 |
| git 명령 실패 | 해당 맥락 항목을 "unavailable"로 채우고 계속 진행 |
| `gh` 명령 실패 | PR 정보를 "no PR found"로 채우고 계속 진행 |
| workspace 생성 실패 | 에러 메시지 출력. 프롬프트 파일 경로를 안내하여 수동 실행 가능하게 함 |
| `--session` 매칭 실패 | 사용 가능한 워크스페이스 목록을 보여주고 중단 |
| `--account` 디렉토리 미존재 | 에러 메시지 출력 후 중단 |
| distribute 분할 실패 | 분할 불가 시 단일 세션으로 fallback, 유저에게 알림 |

## Architecture

### 단일 세션 (기본)

```
사용자: /cmux-delegate "전체 검수" --model opus --account claude-2
  │
  ├── Step 1.6: Account Resolution
  │     └── CLAUDE_CONFIG_DIR=~/.claude-2
  │
  ├── Step 2: 맥락 수집 (git, gh)
  │     └── git branch, log, diff, gh pr
  │
  ├── Step 3: 프롬프트 .md 생성 (Write tool)
  │     └── /tmp/cmux-delegate-{ts}.md
  │
  ├── Step 4: wrapper .sh 생성 (Write tool)
  │     └── /tmp/cmux-delegate-{ts}.sh
  │           └── cat .md | CLAUDE_CONFIG_DIR=~/.claude-2 claude --model opus
  │           └── trap: .sh만 삭제 (.md 보존)
  │           └── cmux notify on completion
  │
  └── Step 5a: cmux new-workspace --command "bash .sh"
        └── workspace:{N} → 독립 Claude 세션 (claude-2 계정)
```

### 기존 세션 전달

```
사용자: /cmux-delegate "에러 조사" --session claude-2
  │
  ├── Step 1.5: Session Resolution
  │     └── cmux list-workspaces → "claude-2" 매칭
  │
  ├── Step 3: 프롬프트 .md 생성
  │
  └── Step 5b: cmux send --workspace {matched} "프롬프트 파일 경로"
        └── 기존 세션에 메시지 전달
```

### 병렬 분산 (distribute)

```
사용자: /cmux-delegate "P1~P5 에러 조사" --account claude-2 --distribute
  │
  ├── Step 3.5: Distribute — 프롬프트 분할
  │     ├── /tmp/cmux-delegate-{ts}-1.md (P1)
  │     ├── /tmp/cmux-delegate-{ts}-2.md (P2)
  │     ├── /tmp/cmux-delegate-{ts}-3.md (P3)
  │     └── /tmp/cmux-delegate-{ts}-4.md (P4+P5)
  │
  ├── Step 4: 래퍼 .sh 4개 생성 (각각 CLAUDE_CONFIG_DIR 적용)
  │
  └── Step 5a: cmux new-workspace × 4 (병렬)
        ├── workspace:{N}   → [P1] (claude-2 계정)
        ├── workspace:{N+1} → [P2] (claude-2 계정)
        ├── workspace:{N+2} → [P3] (claude-2 계정)
        └── workspace:{N+3} → [P4+P5] (claude-2 계정)
```

## Why Wrapper Script?

`claude -p "..."` 패턴은 프롬프트에 `$`, `{}`, `` ` `` 등이 포함되면 shell이 해석하여 프롬프트가 깨집니다 (Hub #1001 크리마 검수에서 실제 경험).

`cat file | claude` 패턴은 프롬프트가 shell을 한 번도 거치지 않으므로 모든 특수문자가 안전합니다.

## Limitations

- 결과 파일 자동 수집/보고 미지원 → 사용자가 cmux에서 직접 확인
- 작업 유형별 템플릿 미지원 → 사용자가 프롬프트에 직접 명시
- distribute 모드의 자동 분할은 섹션 헤더 기반 — 비정형 프롬프트는 수동 분할 필요
