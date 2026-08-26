---
name: cmux-delegate
description: Delegate a task to an independent Claude Code session in a new cmux workspace with auto-collected context. Triggers on "cmux delegate", "delegate task", "delegate to new session", "별도 세션", "세션에 위임".
verified-against-runtime: true
runtime-verified-at: 2026-08-20
runtime-verified-note: "cmux 0.64.22 — the wrapper passes the prompt as argv (`claude \"$(cat file)\"`) and keeps stdin on the terminal, so the worker runs as an ordinary interactive session and answers its own permission and folder-trust prompts; `--max-budget-usd` stays print-mode only."
---

# cmux-delegate

## Overview

작업 중에 새로 발생한 **독립 이슈**를 다른 세션으로 넘기는 스킬입니다. 현재 대화의 작업 맥락(git 메타데이터 + 대화에서 합성한 추론 맥락)을 자동 수집해 cmux workspace 에 독립 Claude Code 세션을 열고, 그 세션이 issue→worktree→PR 까지 혼자 완주합니다. 기존 세션 재사용, 별도 계정 프로필, 다중 이슈 병렬 분산을 지원합니다.

**Core principles:**
- 프롬프트는 반드시 파일 기반 전달. 인라인 `-p` 절대 사용 금지 (shell escaping 문제 회피).
- 유저가 세션명/계정을 명시하면 글자 그대로 따른다. 자의적 재해석 금지.
- **위임 단위는 독립 이슈 하나입니다.** 이슈로 설 수 있으면 위임하고, 못 서면 위임 대상이 아니라 이 세션에서 끝낼 조각입니다.
- **Fire-and-forget.** 위임하면 끝입니다 — 워커는 위임자에게 아무것도 보고하지 않고, 위임자는 워커를 감시하거나 판정하지 않습니다. 결과는 사용자가 해당 cmux 탭에서 직접 봅니다.

## When to Use

작업 도중 **지금 하던 일과 별개인 문제**가 튀어나왔을 때 씁니다.

- 구현 중에 무관한 버그를 발견해 별건으로 떼어낼 때
- 리뷰에서 나온 지적이 이 PR 범위 밖이라 후속 이슈로 갈 때
- 그렇게 쌓인 이슈 여러 건을 각각 다른 세션으로 보낼 때 (`--distribute`)

### 위임 대상 판별

**그 항목이 독립 이슈 하나로 설 수 있는가** — 이것 하나로 판별합니다.

| 판별 | 처리 |
| --- | --- |
| 이슈로 선다 (자체 재현·범위·완료 조건이 있고, 제 작업이 안 끝나도 진행 가능) | 위임 |
| 이슈로 못 선다 (제 현재 작업의 조각이라, 결과가 돌아와야 제 작업이 완성됨) | **위임하지 않고 이 세션에서 끝냅니다** |

결과를 받아서 합쳐야 하는 항목은 위임 대상이 아닙니다. 그건 서브에이전트가 할 일이고,
이 스킬로 그걸 하면 워커에게 보고를 요구하게 되며 — 그 요구가 과거에 완료 보고서 회수
(#894)와 decision gate(#984)를 불러들였습니다. 둘 다 #1130 에서 제거됐습니다.

## Inputs

위임할 **이슈**를 이슈 번호로 지정합니다.

```
/cmux-delegate "#1140 auth 토큰 갱신 실패" --model opus
/cmux-delegate "#1141 리뷰에서 나온 후속 항목" --session claude-2
/cmux-delegate "별건 3개: #1140, #1141, #1142" --account claude-2 --distribute
```

**위임 시점에 이슈가 이미 존재해야 합니다.** 아직 이슈가 없으면 위임 전에 이 세션에서
만드세요 — 이슈 생성은 승인이 필요한 절차라 워커에게 넘기지 않습니다. 번호 없이 설명만
넘기면 워커는 어느 이슈에 PR 을 걸어야 할지 알 수 없습니다.

### Arguments

| Argument | Default | Description |
| ---------- | --------- | ------------- |
| `<task>` | (required) | 위임할 작업 설명 |
| `--model` | `sonnet` | Provider:model notation. `opus`/`sonnet`/`haiku` = claude. Also supports `claude`, `claude:opus`, `codex`, `codex:o3`, `gemini`, `gemini:flash`. See project `ARCHITECTURE.md` Provider Routing. |
| `--cwd` | current dir | 새 세션의 작업 디렉토리 |
| `--max-budget-usd` | — | **미지원 (#1054).** print 모드 전용 플래그라 대화형 워커에 쓸 수 없습니다. 주어지면 무시하지 말고 그 사실을 알리세요 |
| `--account` | (기본 계정) | Claude 계정 프로필 (예: `claude-2` → `CLAUDE_CONFIG_DIR=~/.claude-2`) |
| `--session` | (신규 생성) | 기존 워크스페이스에 전달 (이름 또는 workspace ref) |
| `--distribute` | false | 이슈 단위 병렬 분산 실행. 한 작업의 샤딩이 아닙니다 |
| `--permission-mode` | — | **제거됨 (#1054).** 위임 워커는 평소 세션이므로 사용자의 평소 기본값을 씁니다 |

## Process

### Step 1: Parse Arguments

`{{ARGUMENTS}}`에서 인자를 파싱합니다:

```
args = parse("{{ARGUMENTS}}")
model = args.model || "sonnet"
cwd = args.cwd || $(pwd)
# 예산 플래그는 받되 전달하지 않고, 받았다는 사실을 사용자에게 알립니다 (#1054).
# 조용히 버리면 사용자는 한도가 걸린 줄 알고 위임합니다.
if args["max-budget-usd"]: warn("--max-budget-usd 는 대화형 워커에 적용되지 않습니다 (#1054)")
account = args.account || ""
session = args.session || ""
distribute = args.distribute || false
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

**위임 전에 답할 수 있는 질문은 프롬프트에 답으로 박아 넣습니다.** 워커가 도중에
묻는 것은 아무리 채널이 좋아도 왕복 한 번과 사람의 주의를 삽니다. 저작하면서 "이걸
워커가 물어올까"가 떠오르면 그 자리에서 답을 적어 넣으세요 — 어느 브랜치에서
자를지, 어느 계정으로 push 할지, 테스트를 어디까지 돌릴지 같은 것들입니다.

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

---
Report results in Korean.
```

**CRITICAL:** 프롬프트 파일은 `Write` 도구로 생성합니다 (shell 미경유). `echo`, `cat <<EOF`, `printf` 등 shell을 통한 파일 생성은 절대 금지 — 특수문자가 해석됩니다.

### Step 3.5: Distribute Mode (--distribute)

`--distribute` 플래그가 지정된 경우, 프롬프트를 **이슈 단위로** 분할합니다. 한 덩어리 작업을
샤딩하는 기능이 아닙니다 — 이미 서로 독립인 이슈 N 건을 각자 보내는 기능입니다.

**분할 기준:**
- 항목 하나하나가 `When to Use` 의 판별을 통과해야 합니다 — 독립 이슈로 서지 못하는 항목이
  섞여 있으면 그 항목은 분할에서 빼고 이 세션에 남깁니다
- 이슈 번호(`#1130`)나 이슈 제목으로 항목이 구분되면 그 경계로 분할
- `## P1`, `### 항목 1` 등 섹션 헤더는 경계 **후보**일 뿐입니다. 섹션이 있다는 이유만으로
  쪼개지 마세요 — 한 작업을 소제목으로 나눈 문서가 가장 흔한 오분할 원인입니다
- 분할 결과가 1개면 distribute 무시 (단일 세션)

**분할 프로세스:**
1. 위 기준으로 확정한 **이슈 경계**로 분리 → 각각 개별 .md 파일 생성. 섹션 헤더가 곧 경계는
   아닙니다 — 한 이슈가 여러 헤더로 쓰여 있으면 그 헤더들은 한 파일로 묶습니다
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

# 프롬프트 파일을 못 읽으면 여기서 끝냅니다. `set -e` 가 없으므로 `wc` 가 실패해도
# 스크립트는 계속 가고, 그러면 `[ "" -gt N ]` 이 에러를 내며 참이 아니게 되어 가드를
# 지나친 뒤 빈 argv 가 claude 로 넘어갑니다 — 워커는 할 일 없는 세션으로 앉아 있고
# rc 는 0 입니다. #1054 가 고치려던 거짓 완료와 같은 모양이므로 fail-closed 입니다.
if ! PROMPT_BYTES=$(wc -c < "$PROMPT_FILE" 2>/dev/null) || [ "$PROMPT_BYTES" -eq 0 ]; then
  echo "프롬프트 파일을 읽을 수 없거나 비어 있습니다: $PROMPT_FILE" >&2
  cmux notify --title "cmux-delegate" --body "Failed to start: prompt unreadable" 2>/dev/null || true
  exit 1
fi

# argv 로 넘기므로 프롬프트가 커널의 인자 한도를 먹습니다. 넘치면 exec 이 E2BIG
# 로 실패하는데, 그 실패는 바깥에서 "워커가 아무 일도 안 했다"와 구별되지 않으므로
# 여기서 먼저 잡습니다. 폴백은 두지 않습니다 — stdin 으로 되돌리는 순간 #1054 가
# 그대로 돌아옵니다.
#
# 한도가 둘인 이유: `ARG_MAX` 는 argv+envp **총합** 한도라 환경변수까지 함께 세므로
# 4분의 1만 씁니다. 리눅스에는 그와 별개로 **인자 하나당** 한도가 있습니다 —
# `execve(2)`: "the limit per string is 32 pages (the kernel constant
# MAX_ARG_STRLEN)", 4KiB 페이지 기준 128KiB. 리눅스의 ARG_MAX 는 보통 2MiB 라
# ARG_MAX/4 = 512KiB 로 이 한도의 4배가 되어, 둘 중 작은 쪽을 쓰지 않으면 가드를
# 통과한 프롬프트가 execve 에서 거부됩니다. 실측 프롬프트는 6~10KB 대라 아직
# 관측된 사고는 아닙니다.
#
# macOS 에서는 이 줄이 아무것도 좁히지 않습니다 — 페이지가 16KiB 라 32페이지가
# 512KiB 가 되어 ARG_MAX/4(256KiB)보다 크고, min 이 후자를 고릅니다. 실측 확인함.
ARG_LIMIT=$(( $(getconf ARG_MAX) / 4 ))
STR_LIMIT=$(( 32 * $(getconf PAGE_SIZE) ))
[ "$STR_LIMIT" -lt "$ARG_LIMIT" ] && ARG_LIMIT=$STR_LIMIT
if [ "$PROMPT_BYTES" -gt "$ARG_LIMIT" ]; then
  echo "프롬프트가 너무 큽니다: ${PROMPT_BYTES}B > ${ARG_LIMIT}B" >&2
  cmux notify --title "cmux-delegate" --body "Failed to start: prompt too large" 2>/dev/null || true
  exit 1
fi

# Provider-specific invocation (from project ARCHITECTURE.md Provider CLI Spec)
case "{provider}" in
  claude)
    # 프롬프트는 argv 로 넘기고 stdin 은 비워 둡니다 (#1054). 파이프로 넘기면
    # `cat` 이 끝나는 순간 워커의 fd 0 이 닫히고, 그 뒤로는 권한 프롬프트에
    # 답할 통로도 `cmux send` 가 도달할 통로도 남지 않습니다 — 실측에서 워커는
    # "Awaiting your confirmation" 을 출력하고 exit 0 으로 종료했습니다.
    # stdout 도 파이프로 물리지 않습니다: 블록 버퍼링이 걸려 실행 중인 페인이
    # 통째로 비어 보였습니다.
    #
    # 셸 해석 위험은 `-p "…인라인 리터럴…"` 형태의 문제이고, `"$(cat file)"` 은
    # 셸이 치환 결과를 재해석하지 않으므로 특수문자가 그대로 보존됩니다. 단
    # **후행 개행은 잘립니다** — 명령 치환의 정의된 동작이고 파이프와 다른 유일한
    # 지점입니다. 내부 개행과 나머지 문자는 그대로이므로 프롬프트 의미에는 영향이
    # 없지만, 파일 끝 바이트가 보존된다고 읽으면 안 됩니다.
    #
    # `--permission-mode` 는 넘기지 않습니다. 위임 워커는 평소 세션이므로 평소
    # 기본값을 씁니다. `dontAsk` 는 묻지 않고 거부라 워커가 똑같이 죽고, 유일하게
    # 툴이 도는 `bypassPermissions` 는 모든 게이트를 무력화합니다.
    #
    # `{budget_flag}` 도 넘기지 않습니다. `--max-budget-usd` 는 print 모드 전용인데
    # 이 형태의 워커는 대화형이라 애초에 print 모드가 아닙니다. Step 1 이 예산을
    # 받았으면 여기서 조용히 버리지 말고 그 사실을 사용자에게 알립니다.
    {claude_env} claude \
      --model {sub_model} \
      "$(cat "$PROMPT_FILE")"
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
rc=$?

# 알림은 종료 코드를 반영합니다. rc 를 보지 않으면 권한 거부·크래시·E2BIG 로
# 죽은 워커도 "Task completed" 로 보고되고, 위임자와 사용자에게는 성공과
# 구별되지 않습니다 — 그게 #1054 의 원래 증상입니다.
#
# 다만 claude 분기에서 이 줄들은 **작업 완료 시점에 실행되지 않습니다**. argv +
# TTY stdin 이면 워커는 평소 대화형 세션이라 작업이 끝나도 프롬프트로 돌아가고
# 종료하지 않기 때문입니다 (실측: 작업 완료 후에도 래퍼 프로세스 생존, rc 파일
# 미생성). 여기 도달하는 것은 사람이 세션을 나갔을 때뿐이고, 그때의 rc 는
# 작업 성패가 아니라 세션 종료 방식을 말합니다. 이 알림은 완료 판정이
# 아닙니다 — 결과는 사용자가 cmux 에서 직접 확인합니다.
case "{provider}" in
  claude)
    # 여기 도달했다는 것은 세션이 닫혔다는 뜻일 뿐, 작업이 끝났다는 뜻이 아닙니다.
    # 사람은 작업 도중에도 rc=0 으로 나갈 수 있으므로 "completed" 라고 쓰지 않습니다.
    cmux notify --title "cmux-delegate" --body "Claude session exited (rc=$rc): {short_task}" 2>/dev/null || true
    ;;
  *)
    # codex/gemini 는 비대화형이라 rc 가 실제로 작업 성패를 말합니다.
    if [ "$rc" -eq 0 ]; then
      cmux notify --title "cmux-delegate" --body "Task completed: {short_task}" 2>/dev/null || true
    else
      cmux notify --title "cmux-delegate" --body "Task FAILED (exit $rc): {short_task}" 2>/dev/null || true
    fi
    ;;
esac
exit "$rc"
```

`{provider}` and `{sub_model}` are substituted from the provider resolution result in Step 1.
`{claude_env}` is substituted with `CLAUDE_CONFIG_DIR=~/.{account}` when account is specified (claude provider only).
`{budget_flag}` 는 더 이상 치환되지 않습니다 (#1054). `--max-budget-usd` 가 print
모드 전용이라 대화형 워커에는 쓸 수 없기 때문입니다 — codex/gemini 는 애초에 예산
한도를 지원하지 않으므로, 이 스킬 전체에 예산을 전달할 경로가 없습니다.

**claude 분기는 stdin·stdout 어느 쪽도 파이프에 물리지 않습니다.** 이것이 이 형태의
요점이라 편의를 위해서라도 되돌리면 안 됩니다 — stdin 을 물리면 워커가 권한
프롬프트에서 죽고, stdout 을 물리면 실행 중 페인이 비어 사람이 무슨 일이 일어나는지
볼 수 없습니다. 로그 사본이 필요하면 `tee` 를 다시 끼우는 대신 `cmux read-screen
--scrollback` 으로 페인에서 꺼냅니다.

codex/gemini 분기는 그대로 둡니다. gemini 는 애초에 `-p` 로 argv 를 받고,
`codex exec` 는 설계상 비대화형이라 답을 기다리는 상태 자체가 없습니다.

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
결과 확인은 사용자가 직접 합니다 — claude 워커는 작업을 마쳐도 세션이 살아 있어
완료 알림이 오지 않습니다. 알림이 온다면 기동 실패이거나 사람이 세션을 나간
것입니다.
```

**distribute 모드:**
```
Distributed to {N} workspaces:
  | Workspace | Task | Provider | Model | Account |
  |-----------|------|----------|-------|---------|
  | {ws_ref}  | {item_title} | {provider} | {sub_model} | {account} |
  ...

각 cmux 탭에서 진행 상황을 확인하세요.
결과 확인은 탭마다 직접 합니다. claude 워커의 완료 알림은 오지 않습니다 —
위와 같은 이유입니다.
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
| ------- | ---------- |
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
user: /cmux-delegate "#1140 auth 토큰 갱신 실패" --model claude:opus --account claude-2
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
  │           └── CLAUDE_CONFIG_DIR=~/.claude-2 claude --model opus "$(cat .md)"
  │           └── trap: .sh만 삭제 (.md 보존)
  │           └── cmux notify: 기동 실패 / 세션 종료 시에만 (완료 판정 아님)
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
사용자: /cmux-delegate "작업 중 나온 별건 3개: #1140 토큰 갱신 실패, #1141 로그 유실, #1142 문서 오타" --account claude-2 --distribute
  │
  ├── Step 2.5: 대화 합성 handoff (1회) → 공통 Context 블록에 포함
  │
  ├── Step 3.5: Distribute — 이슈 단위 분할 (Handoff 는 모든 분할에 공통 복사)
  │     ├── /tmp/cmux-delegate-{ts}-1.md (#1140)
  │     ├── /tmp/cmux-delegate-{ts}-2.md (#1141)
  │     └── /tmp/cmux-delegate-{ts}-3.md (#1142)
  │
  ├── Step 4: 래퍼 .sh 3개 생성 (각각 CLAUDE_CONFIG_DIR 적용)
  │
  └── Step 5a: cmux new-workspace × 3 (병렬)
        ├── workspace:{N}   → [#1140] (claude-2 계정)
        ├── workspace:{N+1} → [#1141] (claude-2 계정)
        └── workspace:{N+2} → [#1142] (claude-2 계정)

각 워크스페이스는 자기 이슈 하나를 issue→worktree→PR 까지 완주합니다. 위임자에게
돌아오는 것은 없습니다.
```

## Why Wrapper Script?

래퍼가 필요한 이유는 프롬프트를 **파일에 두기 위해서**입니다. 프롬프트 텍스트가
스크립트 본문이나 명령줄에 리터럴로 들어가면 `$`, `{}`, `` ` `` 이 셸에 해석되어
깨집니다 (Hub #1001 크리마 검수에서 실제 경험).

**파일에 두는 것과 stdin 으로 넘기는 것은 별개입니다.** 오래 이 둘이 한 덩어리로
묶여 있었는데, 깨진 것은 `-p "…리터럴…"` 이지 argv 자체가 아닙니다. `"$(cat file)"`
은 셸이 치환 결과를 재해석하지 않으므로 파이프와 똑같이 안전합니다:

```text
$ claude --model haiku "$(cat p4.md)"
cost is $5 `whoami` ${HOME} {a,b} "quoted" 'single' \n      ← 원문 그대로 복귀
```

그래서 Step 4 는 argv 를 씁니다 — 특수문자 안전성은 그대로 두고 stdin 을 돌려받는
쪽입니다. stdin 이 살아 있으면 워커는 평소 세션이 되고, 권한 프롬프트와 워크스페이스
신뢰 대화상자에 사람이 답할 수 있습니다. 파이프 시절 이 대화상자를 우회하려고
`-p`/리다이렉트 면제를 찾아다녔던 것은, 답할 사람이 없었기 때문에 생긴 문제였습니다.

`--max-budget-usd` 는 여전히 print 모드 전용이라 이 형태에서 쓸 수 없습니다.
파이프 시절에도 마찬가지였으므로 회귀는 아닙니다.

## Limitations

- **결과 파일 자동 수집/보고 미지원** → 사용자가 cmux 에서 직접 확인. 위임은 fire-and-forget 이고, 위임자는 워커를 감시하지 않습니다
- 그래서 완료 주장과 실제 완료는 구별되지 않습니다 — 워커가 PR 을 만들었다고 말하면 사용자가 `gh pr view` 로 직접 확인합니다
- 작업 유형별 템플릿 미지원 → 사용자가 프롬프트에 직접 명시
- distribute 분할은 이슈 경계를 사람이 읽어 판단합니다 — 비정형 프롬프트는 수동 분할 필요
- 위임 단위 판별(이슈로 서는가)은 구조적으로 강제되지 않습니다 — 이 문서를 읽고 지키는 것 외에 막는 장치가 없습니다
- **Handoff 합성 품질은 오케스트레이터 대화에 의존** (Step 2.5) — 대화 맥락이 빈약하면 raw git 맥락만 전달되고, fresh-eyes 위임에서는 편향 방지를 위해 의도적으로 최소화됨
- **codex 쓰기 제약**: `codex exec`는 샌드박스 환경으로 인해 파일 쓰기가 실패해도 오류 없이 종료될 수 있음 — 완료 후 반드시 `git status`로 실제 변경 여부를 확인할 것. 빈 diff가 나오면 즉시 `claude` fallback으로 재위임.
