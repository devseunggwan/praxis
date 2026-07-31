---
name: cmux-delegate
description: Delegate a task to an independent Claude Code session in a new cmux workspace with auto-collected context. Triggers on "cmux delegate", "delegate task", "delegate to new session", "별도 세션", "세션에 위임".
verified-against-runtime: true
runtime-verified-at: 2026-06-17
runtime-verified-note: "cmux/gh/claude/codex/gemini --help probe — cmux new-workspace exists, gh exposes pr/issue commands, claude supports --model/--permission-mode, codex exposes exec, and gemini supports -p/--prompt."
---

# cmux-delegate

## Overview

현재 대화의 작업 맥락(git 메타데이터 + 대화에서 합성한 추론 맥락)을 자동 수집하여, cmux workspace에서 독립 Claude Code 세션을 열어 범용 작업(리뷰, 디버깅, 구현 등)을 위임합니다. 기존 세션 재사용, 별도 계정 프로필, 다중 항목 병렬 분산을 지원합니다.

**Core principles:**
- 프롬프트는 반드시 파일 기반 전달. 인라인 `-p` 절대 사용 금지 (shell escaping 문제 회피).
- 유저가 세션명/계정을 명시하면 글자 그대로 따른다. 자의적 재해석 금지.

## When to Use

- 현재 작업의 독립 리뷰/검수가 필요할 때
- 디버깅이나 구현을 별도 세션에 위임할 때
- 현재 컨텍스트의 편향 없이 fresh eyes가 필요할 때
- 다중 독립 항목을 병렬로 조사/실행할 때

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
| ---------- | --------- | ------------- |
| `<task>` | (required) | 위임할 작업 설명 |
| `--model` | `sonnet` | Provider:model notation. `opus`/`sonnet`/`haiku` = claude. Also supports `claude`, `claude:opus`, `codex`, `codex:o3`, `gemini`, `gemini:flash`. See project `ARCHITECTURE.md` Provider Routing. |
| `--cwd` | current dir | 새 세션의 작업 디렉토리 |
| `--max-budget-usd` | (none) | 최대 예산 한도 |
| `--account` | (기본 계정) | Claude 계정 프로필 (예: `claude-2` → `CLAUDE_CONFIG_DIR=~/.claude-2`) |
| `--session` | (신규 생성) | 기존 워크스페이스에 전달 (이름 또는 workspace ref) |
| `--distribute` | false | 독립 항목별 병렬 분산 실행 |
| `--permission-mode` | `auto` | Claude 권한 모드 (acceptEdits/auto/bypassPermissions/default/dontAsk/plan) |

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
permission_mode = args["permission-mode"] || "auto"
task = args.task (remaining text after flags)
short_task = task[:30], sanitized to [a-zA-Z0-9가-힣 -] only (for cmux workspace name)
timestamp = epoch seconds + PID (e.g., 1744163800-12345) to avoid collision

# Provider resolution (from project ARCHITECTURE.md Provider Resolution Logic)
if model matches /^(codex|gemini)(?::(.+))?$/:
  provider = match[1]           # "codex" or "gemini"
  sub_model = match[2] || ""    # "" or "o3" or "flash" (colon stripped)
elif model in ["opus", "sonnet", "haiku"]:
  provider = "claude"
  sub_model = model
elif model matches /^claude(?::(.+))?$/:
  provider = "claude"
  sub_model = match[1] || ""
else:
  provider = "claude"
  sub_model = model

# Pre-flight: verify provider CLI is available
if ! command -v "$provider" &>/dev/null:
  warn "⚠ ${provider} CLI not found, falling back to claude:sonnet"
  provider = "claude"
  sub_model = "sonnet"
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

### Step 2.5: Synthesize Conversation Handoff

Step 2 의 raw git/PR 메타데이터는 *무엇이 바뀌었는지*만 전달합니다. 위임 세션이 이전 대화 없이도 진행하려면 *왜·어떻게* 의 추론 맥락이 필요합니다. 오케스트레이터(이 스킬을 실행하는 에이전트)는 현재 대화를 이미 보유하므로, **별도 LLM 호출 없이** 합성 결과를 직접 작성합니다 (Step 3 에서 `Write` 도구로 프롬프트 파일에 포함).

**합성 항목** (해당하는 것만, 없으면 생략):

- **Decisions** — 내려진 결정과 그 이유
- **Findings** — 발견한 제약·사실 (위임 세션의 재조사 비용 절감)
- **Relevant files** — 대화에서 읽거나 논의한 파일 (git diff 에 안 잡히는 것 포함)
- **Next task** — self-contained 한 다음 작업 서술

**Task-type 분기** — 위임 *의도*에 따라 handoff 강도를 조절합니다:

| 위임 유형 | Handoff 강도 |
| ---------- | ------------- |
| review / audit / fresh-eyes (편향 없는 재검토) | 중립적 *사실*만 (`### Findings` / `### Relevant files`). 오케스트레이터의 결론·의견(`### Decisions` / `### Next task`)은 **배제** — fresh eyes 의 편향 주입 방지 |
| continue-work / implement / debug (작업 이어가기) | 풍부하게 — 4개 하위 섹션 모두 포함 |

사실 vs 의견 경계 예: 사실 = "파일 X는 캐시 미스 시 빈 응답을 반환함" / 의견 = "파일 X의 캐시 로직이 잘못됨".

**적용 범위:** Step 2.5 는 Step 3(프롬프트 `.md` 생성) 직전 단계로, 신규 세션·기존 세션(`--session`)·distribute 모드 **모두**에서 동일하게 실행됩니다 — 세 모드가 같은 `.md` 를 소비하기 때문입니다.

**Graceful degradation:** 대화 맥락이 빈약하면(사전 맥락 없는 일회성 직접 위임 등) 합성을 생략하고 `## Handoff` 섹션 자체를 넣지 않습니다.

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

## Handoff (conversation synthesis)

{전체 생략 조건: 대화 맥락 빈약일 때만. review/audit/fresh-eyes 는 전체 생략이 아니라
 아래 ### Decisions / ### Next task 만 제외하고 중립 사실(### Findings / ### Relevant files)은 유지.
 continue-work/implement/debug 는 4개 하위 섹션 모두 포함 — Step 2.5 task-type 분기를 따름}

### Findings
{발견한 제약·사실}

### Relevant files
{대화에서 읽거나 논의한 파일}

### Decisions  — continue-work/implement/debug 한정
{내려진 결정과 이유}

### Next task  — continue-work/implement/debug 한정
{self-contained 한 다음 작업}

## Instructions

{task description from user}

## Completion protocol (REQUIRED)

작업을 마치면 완료 보고서를 씁니다. 이 파일이 완료 보고의 정본이고,
채팅 메시지는 포인터일 뿐입니다. 경로는 아래 명령으로 구합니다 — worktree
루트가 아니라 `PRAXIS_HOME` 아래입니다. worktree 는 대개 남의 repo 이고,
praxis 가 거기에 파일을 남길 이유가 없습니다.

```bash
ARP="${CLAUDE_PLUGIN_ROOT}/skills/cmux-delegate/agent-report-path.sh"
WORKTREE="$(sh "$ARP" --worktree "$PWD")"   # ← 보고서의 worktree 필드에 그대로
REPORT="$(sh "$ARP" "$PWD")"
```

`$PWD` 가 worktree 하위 디렉터리여도 됩니다 — 헬퍼가 worktree 루트로
정규화하므로 위임자와 같은 경로가 나옵니다.

    {
      "worktree": "/abs/path/to/worktree",
      "branch": "issue-123-feat-x",
      "head_sha": "0123456789abcdef0123456789abcdef01234567",
      "pushed": true,
      "pr_url": "https://github.com/owner/repo/pull/456",
      "tests": {"command": "./scripts/run-tests.sh", "passed": 507, "failed": 0},
      "completed_at": "2026-07-28T09:00:00Z"
    }


- `worktree` 는 위 `$WORKTREE` 값을 그대로 넣습니다. 파일명이 해시라서 이
  필드가 없으면 위임자가 자기 것인지 확인할 수 없습니다.
- `pushed` 는 `git push` 가 **성공한 뒤에만** true. 커밋만 했으면 false.
- `pr_url` 은 실제로 생성된 PR 이 없으면 `null` — 빈 문자열이나 예상 URL 금지.
- `tests` 의 숫자는 실행한 명령의 출력에서 그대로 옮깁니다. 추정 금지.
- 작업을 끝내지 못했으면 **파일을 쓰지 마세요.** 파일 부재 = 미완료입니다.

마지막 메시지에는 그 보고서의 절대 경로만 남깁니다.

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
4. Routing: If `--model` is explicit, apply uniformly. Otherwise, auto-assign by task type (see project `ARCHITECTURE.md` Task-Type Routing):
   - Code implementation/fix → `codex` (if CLI available) or `claude:sonnet`
   - Search/analysis/large context → `gemini` (if CLI available) or `claude:sonnet`
   - Design/security/review → `claude:opus`
   - Data lookup/status check → `claude:haiku`

### Step 4: Generate Wrapper Script

`/tmp/cmux-delegate-{timestamp}.sh`를 생성합니다:

```bash
#!/bin/bash
PROMPT_FILE="/tmp/cmux-delegate-{timestamp}.md"
SCRIPT_FILE="/tmp/cmux-delegate-{timestamp}.sh"

# Cleanup: .sh만 삭제. .md는 보존 (다른 워크스페이스가 참조할 수 있음)
trap 'rm -f "$SCRIPT_FILE"' EXIT

# Provider-specific invocation (from project ARCHITECTURE.md Provider CLI Spec)
case "{provider}" in
  claude)
    cat "$PROMPT_FILE" | {claude_env} claude \
      --model {sub_model} \
      --permission-mode {permission_mode} \
      {budget_flag}
    ;;
  codex)
    cat "$PROMPT_FILE" | codex exec \
      {sub_model:+-m {sub_model}}
    ;;
  gemini)
    gemini -p "$(cat "$PROMPT_FILE")" \
      --approval-mode yolo \
      {sub_model:+-m {sub_model}}
    ;;
esac

# Notify on completion
cmux notify --title "cmux-delegate" --body "Task completed: {short_task}" 2>/dev/null || true
```

`{provider}` and `{sub_model}` are substituted from the provider resolution result in Step 1.
`{claude_env}` is substituted with `CLAUDE_CONFIG_DIR=~/.{account}` when account is specified (claude provider only).
`{budget_flag}` is substituted with `--max-budget-usd {budget}` when budget is specified (claude provider only — codex/gemini do not support budget limits).

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
  Provider: {provider}
  Model: {sub_model || "default"}
  Account: {account || "default"}
  Prompt: /tmp/cmux-delegate-{timestamp}.md
  CWD: {cwd}

cmux에서 {WS_REF} 탭을 확인하세요.
완료 시 cmux notify로 알림이 전송됩니다.
```

**distribute 모드:**
```
Distributed to {N} workspaces:
  | Workspace | Task | Provider | Model | Account |
  |-----------|------|----------|-------|---------|
  | {ws_ref}  | {item_title} | {provider} | {sub_model} | {account} |
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

### Step 7: Collect the Report (file, not prose)

위임한 작업의 완료를 판정할 때는 **에이전트의 메시지를 읽지 않고** 보고서
파일을 읽습니다. 경로는 writer 와 **같은 헬퍼**로 구합니다 — 스니펫을 양쪽에
복붙하면 한쪽만 고쳐질 때 판정이 조용히 깨집니다. 이 단계는 위임 직후가
아니라, 결과를 소비하기 직전(다음 단계 진입, 사용자 보고, 머지 판단) 에
실행합니다.

**왜 파일인가.** prose 채널은 양극단으로 고장난 이력이 있습니다 —
존재하지 않는 PR 을 생성 완료로 보고한 fabrication, 지시 2회에 무응답인
silence. 어느 쪽도 메시지만 읽어서는 구분되지 않습니다. 파일 부재는
결정론적으로 "미완료" 이고, 파일 존재는 필드별 재검증의 대상입니다.

```bash
ARP="${CLAUDE_PLUGIN_ROOT}/skills/cmux-delegate/agent-report-path.sh"
WORKTREE="$(sh "$ARP" --worktree "{cwd}")"
REPORT="$(sh "$ARP" "{cwd}")"
[ -f "$REPORT" ] || { echo "미완료: 완료 보고서 부재 ($REPORT)"; exit 1; }
[ "$(jq -r '.worktree // ""' "$REPORT")" = "$WORKTREE" ] \
  || { echo "미완료: 보고서의 worktree 가 $WORKTREE 와 불일치"; exit 1; }
```

읽은 값은 **그대로 믿지 않고** 필드마다 fresh 하게 재확인합니다. 보고서는
에이전트가 쓴 것이므로 그 자체로는 증거가 아닙니다 — 아래 명령의 출력이
증거입니다.

| 필드 | 재검증 명령 | 불일치 시 |
| --- | --- | --- |
| `worktree` | 위 `jq` 비교 | 불일치 = 다른 작업의 보고서. 미완료로 취급 |
| `head_sha` + `pushed: true` | `git ls-remote origin refs/heads/<branch>` | remote SHA 가 없거나 다르면 push 미완료 — 보고서의 `pushed` 를 무시 |
| `pr_url` | `gh pr view <url> --json state,headRefOid` | 조회 실패 = PR 부재(fabrication), `headRefOid` 불일치 = 보고 이후 커밋 존재 |
| `tests` | 같은 명령을 직접 재실행 | 숫자 불일치 = 보고서 수치 신뢰 불가 |

`pushed: false` 는 실패가 아니라 **정상적인 부분 완료** 입니다 — 커밋은
있으나 push 는 남았다는 뜻이므로, 위임자가 push 를 이어받거나 에이전트에
재지시합니다.

**커버리지 한계 (명기).** 이 단계는 silence 를 *탐지* 할 뿐 원인을 진단하지
않습니다 — 세션 crash 인지 장시간 도구 호출 대기인지는 파일 부재만으로
구분되지 않으며, 그 판별은 이 스킬의 범위 밖입니다.

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| `cmux` not found | "cmux가 설치되어 있지 않습니다. cmux.app을 설치해주세요." 출력 후 중단 |
| git 명령 실패 | 해당 맥락 항목을 "unavailable"로 채우고 계속 진행 |
| `gh` 명령 실패 | PR 정보를 "no PR found"로 채우고 계속 진행 |
| workspace 생성 실패 | 에러 메시지 출력. 프롬프트 파일 경로를 안내하여 수동 실행 가능하게 함 |
| `--session` 매칭 실패 | 사용 가능한 워크스페이스 목록을 보여주고 중단 |
| `--account` 디렉토리 미존재 | 에러 메시지 출력 후 중단 |
| distribute 분할 실패 | 분할 불가 시 단일 세션으로 fallback, 유저에게 알림 |
| 완료 보고서 부재 | **미완료로 취급** — 완료 주장 메시지가 있어도 마찬가지. 에이전트에 재지시하거나 위임자가 인수 |
| 완료 보고서 JSON 파싱 실패 | 미완료로 취급. 파일 내용을 그대로 보여주고 중단 (부분 기록일 수 있으므로 삭제 금지) |
| 보고서 필드 ↔ 재검증 불일치 | 재검증 출력을 채택하고 보고서 값은 폐기. 어긋난 필드를 사용자에게 명시 |

## Architecture

### 단일 세션 (기본)

```
user: /cmux-delegate "full code review" --model claude:opus --account claude-2
  │
  ├── Step 1.6: Account Resolution
  │     └── CLAUDE_CONFIG_DIR=~/.claude-2
  │
  ├── Step 2: 맥락 수집 (git, gh)
  │     └── git branch, log, diff, gh pr
  │
  ├── Step 2.5: 대화 합성 handoff (에이전트 직접 작성, LLM 호출 없음)
  │     └── Findings / Relevant files (+ continue-work 면 Decisions / Next task)
  │           (빈약한 맥락 → 전체 생략)
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
  ├── Step 2.5: 대화 합성 handoff (작업 이어가기면 풍부하게)
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
  ├── Step 2.5: 대화 합성 handoff (1회) → 공통 Context 블록에 포함
  │
  ├── Step 3.5: Distribute — 프롬프트 분할 (Handoff 는 모든 분할에 공통 복사)
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

- 완료 판정은 완료 보고서 파일로 결정론적이지만, **작업 산출물 자체의 자동 수집은 미지원** → 사용자가 cmux에서 직접 확인
- silence 는 *탐지* 만 가능하고 원인(세션 crash vs 장시간 도구 대기)은 판별 불가 — 파일 부재만으로는 구분되지 않음
- 작업 유형별 템플릿 미지원 → 사용자가 프롬프트에 직접 명시
- distribute 모드의 자동 분할은 섹션 헤더 기반 — 비정형 프롬프트는 수동 분할 필요
- **Handoff 합성 품질은 오케스트레이터 대화에 의존** (Step 2.5) — 대화 맥락이 빈약하면 raw git 맥락만 전달되고, fresh-eyes 위임에서는 편향 방지를 위해 의도적으로 최소화됨
- **codex 쓰기 제약**: `codex exec`는 샌드박스 환경으로 인해 파일 쓰기가 실패해도 오류 없이 종료될 수 있음 — 완료 후 반드시 `git status`로 실제 변경 여부를 확인할 것. 빈 diff가 나오면 즉시 `claude` fallback으로 재위임.
