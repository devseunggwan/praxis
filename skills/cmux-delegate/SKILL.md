---
name: cmux-delegate
description: Delegate a task to an independent Claude Code session in a new cmux workspace with auto-collected context. Triggers on "delegate", "cmux delegate", "new session".
---

# cmux-delegate

## Overview

현재 대화의 작업 맥락을 자동 수집하여, 새로운 cmux workspace에서 독립 Claude Code 세션을 열어 범용 작업(리뷰, 디버깅, 구현 등)을 위임합니다.

**Core principle:** 프롬프트는 반드시 파일 기반 전달. 인라인 `-p` 절대 사용 금지 (shell escaping 문제 회피).

## When to Use

- 현재 작업의 독립 리뷰/검수가 필요할 때
- 디버깅이나 구현을 별도 세션에 위임할 때
- 현재 컨텍스트의 편향 없이 fresh eyes가 필요할 때
- Triggers: "delegate", "cmux delegate", "new session", "별도 세션"

## Inputs

사용자가 위임할 작업을 설명합니다:

```
/cmux-delegate 전체 코드 검수 요청 --model opus
/cmux-delegate "PR #78, #137, #7502 크로스-레포 일관성 검증"
/cmux-delegate debug auth token refresh failure --model sonnet
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `<task>` | (required) | 위임할 작업 설명 |
| `--model` | `sonnet` | Claude 모델 (opus/sonnet/haiku) |
| `--cwd` | current dir | 새 세션의 작업 디렉토리 |
| `--max-budget-usd` | (none) | 최대 예산 한도 |

## Process

### Step 1: Parse Arguments

`{{ARGUMENTS}}`에서 인자를 파싱합니다:

```
args = parse("{{ARGUMENTS}}")
model = args.model || "sonnet"
cwd = args.cwd || $(pwd)
budget = args.budget || ""
task = args.task (remaining text after flags)
short_task = task[:30], sanitized to [a-zA-Z0-9가-힣 -] only (for cmux workspace name)
timestamp = epoch seconds + PID (e.g., 1744163800-12345) to avoid collision
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
BASE_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")
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

### Step 4: Generate Wrapper Script

`/tmp/cmux-delegate-{timestamp}.sh`를 생성합니다:

```bash
#!/bin/bash
PROMPT_FILE="/tmp/cmux-delegate-{timestamp}.md"
SCRIPT_FILE="/tmp/cmux-delegate-{timestamp}.sh"

# Cleanup temp files on exit
trap 'rm -f "$PROMPT_FILE" "$SCRIPT_FILE"' EXIT

cat "$PROMPT_FILE" | claude \
  --model {model} \
  --output-format stream-json \
  --permission-mode auto \
  {budget_flag}

# Notify on completion
cmux notify --title "cmux-delegate" --body "Task completed: {short_task}" 2>/dev/null || true
```

`{budget_flag}`는 budget이 지정된 경우에만 `--max-budget-usd {budget}`로 치환합니다.

**이 파일도 `Write` 도구로 생성합니다.** 단, 파일 내용 자체에 shell 변수(`$PROMPT_FILE` 등)가 포함되므로 이는 의도된 것입니다 — 중요한 것은 사용자 프롬프트가 이 스크립트를 거치지 않는다는 점입니다.

### Step 5: Launch cmux Workspace

```bash
WS_RAW=$(cmux new-workspace \
  --name "[delegate] {short_task}" \
  --cwd {cwd} \
  --command "bash /tmp/cmux-delegate-{timestamp}.sh")

# Validate workspace creation
if [[ "$WS_RAW" != OK* ]]; then
  echo "Error: workspace 생성 실패 — $WS_RAW"
  echo "수동 실행: bash /tmp/cmux-delegate-{timestamp}.sh"
  exit 1
fi

WS_REF=$(echo "$WS_RAW" | sed 's/^OK //')
```

### Step 6: Report

스킬 실행 결과를 사용자에게 보고합니다:

```
Delegated to {WS_REF}
  Task: {short_task}
  Model: {model}
  Prompt: /tmp/cmux-delegate-{timestamp}.md
  CWD: {cwd}

cmux에서 {WS_REF} 탭을 확인하세요.
완료 시 cmux notify로 알림이 전송됩니다.
```

## Error Handling

| Error | Recovery |
|-------|----------|
| `cmux` not found | "cmux가 설치되어 있지 않습니다. cmux.app을 설치해주세요." 출력 후 중단 |
| git 명령 실패 | 해당 맥락 항목을 "unavailable"로 채우고 계속 진행 |
| `gh` 명령 실패 | PR 정보를 "no PR found"로 채우고 계속 진행 |
| workspace 생성 실패 | 에러 메시지 출력. 프롬프트 파일 경로를 안내하여 수동 실행 가능하게 함 |

## Architecture

```
사용자: /cmux-delegate "전체 검수" --model opus
  │
  ├── Step 2: 맥락 수집 (git, gh)
  │     └── git branch, log, diff, gh pr
  │
  ├── Step 3: 프롬프트 .md 생성 (Write tool)
  │     └── /tmp/cmux-delegate-{ts}.md
  │
  ├── Step 4: wrapper .sh 생성 (Write tool)
  │     └── /tmp/cmux-delegate-{ts}.sh
  │           └── cat .md | claude --model opus
  │           └── trap cleanup EXIT
  │           └── cmux notify on completion
  │
  └── Step 5: cmux new-workspace --command "bash .sh"
        └── workspace:{N} → 독립 Claude 세션
```

## Why Wrapper Script?

`claude -p "..."` 패턴은 프롬프트에 `$`, `{}`, `` ` `` 등이 포함되면 shell이 해석하여 프롬프트가 깨집니다 (Hub #1001 크리마 검수에서 실제 경험).

`cat file | claude` 패턴은 프롬프트가 shell을 한 번도 거치지 않으므로 모든 특수문자가 안전합니다.

## Limitations (v1)

- 결과 파일 자동 수집/보고 미지원 → 사용자가 cmux에서 직접 확인
- 다중 세션 병렬 위임 미지원 → cmux-orchestrator 사용
- 작업 유형별 템플릿 미지원 → 사용자가 프롬프트에 직접 명시
