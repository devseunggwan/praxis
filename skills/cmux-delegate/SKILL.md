---
name: cmux-delegate
description: Hand off an existing independent issue that surfaced mid-task to its own Claude Code session in a new cmux workspace, with auto-collected context; that session runs issue→worktree→PR alone. Not for splitting the current task.
when_to_use: Triggers on "cmux delegate", "delegate issue", "delegate to new session", "별도 세션", "세션에 위임", "별건으로 빼서".
verified-against-runtime: true
runtime-verified-at: 2026-09-04
runtime-verified-note: "cmux 0.64.22 — the selected workspace's `list-workspaces` row is prefixed `* `, so field 1 without the strip is `*` and `cmux send --workspace '*'` fails with `Invalid workspace handle`; stripped, `--session` resolves and `send` returns `OK`. The legacy-alias notice goes to stderr, so it cannot reach the grep."
---

# cmux-delegate

## Overview

A skill that hands an **independent issue** that surfaced mid-task off to a
different session. It auto-collects the current conversation's working context
(git metadata + reasoning context synthesized from the conversation), opens an
independent Claude Code session in a cmux workspace, and that session runs
issue→worktree→PR to completion on its own. Reusing an existing session,
separate account profiles, and parallel multi-issue distribution are supported.

**Core principles:**

- The prompt is always delivered via a file. Inline `-p` is strictly forbidden
  (avoids shell-escaping problems).
- When the user names a session/account explicitly, follow it to the letter.
  No arbitrary reinterpretation.
- **The unit of delegation is one independent issue.** If it can stand as an
  issue, delegate it; if it cannot, it is not a delegation target but a piece
  to finish in this session.
- **Fire-and-forget.** Delegating is the end — the worker reports nothing back
  to the delegator, and the delegator neither monitors nor judges the worker.
  The user sees the result directly in that cmux tab.

## When to Use

Use this when **a problem separate from what you are doing right now** pops up
mid-task.

- You found an unrelated bug during implementation and want to split it off
- A review finding is out of this PR's scope and goes to a follow-up issue
- Several issues piled up that way and each should go to a different session
  (`--distribute`)

### Delegation eligibility

**Can that item stand as one independent issue** — this single test decides.

| Verdict | Handling |
| --- | --- |
| Stands as an issue (has its own reproduction, scope, and completion criteria, and can proceed even if my work is unfinished) | Delegate |
| Cannot stand as an issue (it is a piece of my current work, and my work is only complete once its result comes back) | **Do not delegate — finish it in this session** |

An item whose result must come back to be merged is not a delegation target.
That is a subagent's job, and using this skill for it means demanding a report
from the worker — a demand that historically brought in completion-report
collection (#894) and the decision gate (#984). Both were removed in #1130.

## Inputs

Specify the **issue** to delegate by its issue number.

```
/cmux-delegate "#1140 auth 토큰 갱신 실패" --model opus
/cmux-delegate "#1141 리뷰에서 나온 후속 항목" --session claude-2
/cmux-delegate "별건 3개: #1140, #1141, #1142" --account claude-2 --distribute
```

**The issue must already exist at delegation time.** If there is no issue yet,
create it in this session before delegating — issue creation is a procedure
that requires approval, so it is not handed to the worker. If only a
description is passed without a number, the worker cannot know which issue to
attach the PR to.

### Arguments

| Argument | Default | Description |
| ---------- | --------- | ------------- |
| `<task>` | (required) | Description of the task to delegate |
| `--model` | `sonnet` | Provider:model notation. `opus`/`sonnet`/`haiku` = claude. Also supports `claude`, `claude:opus`, `codex`, `codex:o3`, `gemini`, `gemini:flash`. See project `ARCHITECTURE.md` Provider Routing. |
| `--cwd` | current dir | Working directory for the new session |
| `--max-budget-usd` | — | **Unsupported (#1054).** A print-mode-only flag, so it cannot be used with an interactive worker. If given, do not ignore it silently — tell the user |
| `--account` | (default account) | Claude account profile (e.g. `claude-2` → `CLAUDE_CONFIG_DIR=~/.claude-2`) |
| `--session` | (create new) | Deliver into an existing workspace (name or workspace ref) |
| `--distribute` | false | Parallel distribution at issue granularity. Not sharding of a single task |
| `--permission-mode` | — | **Removed (#1054).** The delegated worker is an ordinary session, so it uses the user's ordinary defaults |

## Process

### Step 1: Parse Arguments

Parse the arguments from `{{ARGUMENTS}}`:

```
args = parse("{{ARGUMENTS}}")
model = args.model || "sonnet"
cwd = args.cwd || $(pwd)
# Accept the budget flag but do not forward it; tell the user it was received (#1054).
# Dropping it silently lets the user delegate believing a cap is in place.
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

Decide whether to use an existing session.

```
if session is specified:
  1. cmux list-workspaces → match by name or ref
  2. match found → cmux send mode (Step 5b)
  3. no match → print error "세션 '{session}'을 찾을 수 없습니다" and stop
else:
  → existing behavior (new-workspace, Step 5a)
```

### Step 1.6: Account Resolution

Decide the account profile.

```
if account is specified:
  # The account profile is set via the CLAUDE_CONFIG_DIR environment variable
  # e.g. --account claude-2 → CLAUDE_CONFIG_DIR=~/.claude-2
  claude_env = "CLAUDE_CONFIG_DIR=~/.{account}"
  
  # Validation: check that the config directory exists
  if not exists(~/.{account}):
    print error "계정 프로필 디렉토리 ~/.{account}이 없습니다" and stop
else:
  claude_env = ""  # use the default account
```

### Step 2: Collect Context

Auto-collect the current conversation's and project's context. Each command
continues even on failure (`2>/dev/null`).

Information to collect:

```bash
# 1. Git state
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
COMMITS=$(git log --oneline -5 2>/dev/null || echo "no git history")
DIFF_STAT=$(git diff --stat HEAD 2>/dev/null || echo "no changes")

# 2. Changed file list (vs base branch)
BASE_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
BASE_BRANCH=${BASE_BRANCH:-main}
CHANGED_FILES=$(git diff --name-only $(git merge-base HEAD "origin/$BASE_BRANCH" 2>/dev/null || echo HEAD~5)..HEAD 2>/dev/null || echo "unknown")

# 3. PR info (if any)
PR_INFO=$(gh pr list --head "$BRANCH" --json number,title,url 2>/dev/null || echo "no PR")

# 4. PR review comments (if any)
REVIEW_COMMENTS="0"
PR_NUM=$(gh pr list --head "$BRANCH" --json number -q '.[0].number' 2>/dev/null || echo "")
if [ -n "$PR_NUM" ]; then
  REVIEW_COMMENTS=$(gh api "repos/$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null)/pulls/$PR_NUM/comments" --jq 'length' 2>/dev/null || echo "0")
fi
```

### Step 2.5: Synthesize Conversation Handoff

Step 2's raw git/PR metadata conveys only *what changed*. For the delegated
session to proceed without the prior conversation, it needs the *why and how*
reasoning context. The orchestrator (the agent running this skill) already
holds the current conversation, so it writes the synthesis directly, **without
a separate LLM call** (included in the prompt file via the `Write` tool in
Step 3).

**Synthesis items** (only those that apply; omit when absent):

- **Decisions** — decisions made and their reasons
- **Findings** — constraints and facts discovered (saves the delegated
  session re-investigation cost)
- **Relevant files** — files read or discussed in the conversation (including
  ones git diff does not capture)
- **Next task** — a self-contained description of the next task

**Task-type branching** — the handoff intensity is adjusted by the delegation
*intent*:

| Delegation type | Handoff intensity |
| ---------- | ------------- |
| review / audit / fresh-eyes (unbiased re-examination) | Neutral *facts* only (`### Findings` / `### Relevant files`). The orchestrator's conclusions and opinions (`### Decisions` / `### Next task`) are **excluded** — prevents bias injection into fresh eyes |
| continue-work / implement / debug (continuing the work) | Rich — include all 4 subsections |

Fact-vs-opinion boundary example: fact = "file X returns an empty response on
a cache miss" / opinion = "file X's cache logic is wrong".

**Applicability:** Step 2.5 is the step immediately before Step 3 (prompt
`.md` generation) and runs identically in **all** of new-session,
existing-session (`--session`), and distribute modes — because all three
modes consume the same `.md`.

**Graceful degradation:** if the conversation context is thin (e.g. a one-off
direct delegation with no prior context), skip the synthesis and omit the
`## Handoff` section entirely.

### Step 3: Generate Prompt File

Synthesize the collected context and the user prompt, and save it to
`/tmp/cmux-delegate-{timestamp}.md`.

**Two names, defined here and used by every later step:** `{prompt_file}` is
that `.md`, and `{script_file}` is the wrapper `.sh` Step 4 generates from it.
In new-session and existing-session mode they are
`/tmp/cmux-delegate-{timestamp}.md` and `.sh`. In distribute mode Step 3.5
gives each item its own pair, so every later step substitutes the pair
belonging to the worker it is launching — never the base names.

**Any question answerable before delegating gets its answer baked into the
prompt.** A worker asking mid-run costs one round-trip and a human's
attention, no matter how good the channel is. While authoring, whenever "would
the worker ask about this" comes to mind, write the answer in on the spot —
things like which branch to cut from, which account to push with, and how far
to run the tests.

Prompt file structure:

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

{Full-omission condition: only when the conversation context is thin.
 review/audit/fresh-eyes is not full omission — exclude only ### Decisions /
 ### Next task below and keep the neutral facts (### Findings / ### Relevant files).
 continue-work/implement/debug includes all 4 subsections — follow the Step 2.5 task-type branching}

### Findings
{constraints and facts discovered}

### Relevant files
{files read or discussed in the conversation}

### Decisions  — continue-work/implement/debug only
{decisions made and their reasons}

### Next task  — continue-work/implement/debug only
{a self-contained next task}

## Instructions

{task description from user}

---
Report results in Korean.
```

**CRITICAL:** the prompt file is created with the `Write` tool (no shell
involved). Creating the file through the shell — `echo`, `cat <<EOF`,
`printf`, etc. — is strictly forbidden: special characters get interpreted.

### Step 3.5: Distribute Mode (--distribute)

When the `--distribute` flag is given, split the prompt **at issue
granularity**. This is not a feature for sharding one lump of work — it sends
N issues that are already mutually independent, each on its own.

**Split criteria:**

- Every single item must pass the `When to Use` test — if an item that cannot
  stand as an independent issue is mixed in, drop it from the split and keep
  it in this session
- If items are delimited by issue numbers (`#1130`) or issue titles, split on
  those boundaries
- Section headers such as `## P1` or `### 항목 1` are only boundary
  **candidates**. Do not split just because sections exist — a document that
  divides one task under subheadings is the most common cause of bad splits
- If the split yields 1 item, ignore distribute (single session)

**Split process:**

1. Split on the **issue boundaries** confirmed by the criteria above →
   generate an individual .md file for each, named
   `/tmp/cmux-delegate-{timestamp}-{n}.md` with `{n}` counting from 1. A
   section header is not itself a boundary — if one issue is written under
   several headers, those headers are bundled into one file
2. The Context section is included in every split file
3. Generate an individual wrapper .sh for each file, named
   `/tmp/cmux-delegate-{timestamp}-{n}.sh` for the same `{n}`. Item `{n}`'s
   pair is what `{prompt_file}` and `{script_file}` mean for that worker;
   reusing the base names would have every worker read one prompt and every
   trap delete one script
4. Routing: If `--model` is explicit, apply uniformly. Otherwise, auto-assign by task type (see project `ARCHITECTURE.md` Task-Type Routing):
   - Code implementation/fix → `codex` (if CLI available) or `claude:sonnet`
   - Search/analysis/large context → `gemini` (if CLI available) or `claude:sonnet`
   - Design/security/review → `claude:opus`
   - Data lookup/status check → `claude:haiku`

### Step 4: Generate Wrapper Script

Generate `{script_file}`, substituting this worker's own pair:

```bash
#!/bin/bash
PROMPT_FILE="{prompt_file}"
SCRIPT_FILE="{script_file}"

# Cleanup: delete only the .sh. The .md is preserved (another workspace may
# reference it)
trap 'rm -f "$SCRIPT_FILE"' EXIT

# If the prompt file cannot be read, stop here. There is no `set -e`, so if
# `wc` fails the script keeps going, and then `[ "" -gt N ]` errors out as
# non-true, the guard is passed, and an empty argv reaches claude — the
# worker sits as a session with nothing to do and rc is 0. That is the same
# shape as the false completion #1054 set out to fix, hence fail-closed.
if ! PROMPT_BYTES=$(wc -c < "$PROMPT_FILE" 2>/dev/null) || [ "$PROMPT_BYTES" -eq 0 ]; then
  echo "프롬프트 파일을 읽을 수 없거나 비어 있습니다: $PROMPT_FILE" >&2
  cmux notify --title "cmux-delegate" --body "Failed to start: prompt unreadable" 2>/dev/null || true
  exit 1
fi

# Passing via argv means the prompt consumes the kernel's argument limit. If
# it overflows, exec fails with E2BIG, and from the outside that failure is
# indistinguishable from "the worker did nothing", so it is caught here
# first. No fallback is provided — the moment this reverts to stdin, #1054
# comes straight back.
#
# Why two limits: `ARG_MAX` is the **combined** argv+envp limit, counting the
# environment variables too, so only a quarter of it is used. Linux
# separately has a **per-string** limit — `execve(2)`: "the limit per string
# is 32 pages (the kernel constant MAX_ARG_STRLEN)", 128KiB with 4KiB pages.
# Linux's ARG_MAX is typically 2MiB, so ARG_MAX/4 = 512KiB is 4x that limit,
# and unless the smaller of the two is used, a prompt that passes the guard
# is rejected at execve. Measured prompts run 6-10KB, so this is not yet an
# observed incident.
#
# On macOS this line narrows nothing — pages are 16KiB, so 32 pages is
# 512KiB, larger than ARG_MAX/4 (256KiB), and min picks the latter. Verified
# by measurement.
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
    # The prompt goes through argv and stdin stays empty (#1054). With a
    # pipe, the moment `cat` exits the worker's fd 0 closes, and after that
    # no channel remains to answer a permission prompt on, and none for
    # `cmux send` to reach — in measurement the worker printed
    # "Awaiting your confirmation" and exited 0.
    # stdout is not piped either: block buffering kicked in and the running
    # pane looked entirely empty.
    #
    # The shell-interpretation risk is the `-p "…inline literal…"` shape;
    # with `"$(cat file)"` the shell does not re-interpret the substitution
    # result, so special characters are preserved as-is. But
    # **trailing newlines are stripped** — command substitution's defined
    # behavior and the single point where it differs from the pipe. Interior
    # newlines and every other character survive, so the prompt's meaning is
    # unaffected, but do not read this as end-of-file bytes being preserved.
    #
    # `--permission-mode` is not passed. The delegated worker is an ordinary
    # session, so it uses the ordinary defaults. `dontAsk` denies without
    # asking, so the worker dies the same way, and the only mode where tools
    # run, `bypassPermissions`, disarms every gate.
    #
    # `{budget_flag}` is not passed either. `--max-budget-usd` is print-mode
    # only, and this shape of worker is interactive, so there is no print
    # mode to begin with. If Step 1 received a budget, do not drop it
    # silently here — tell the user.
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

# The notification reflects the exit code. Without looking at rc, a worker
# killed by a permission denial, a crash, or E2BIG is also reported as
# "Task completed", indistinguishable from success for the delegator and the
# user — that is #1054's original symptom.
#
# In the claude branch, though, these lines are not run at task completion.
# With argv + TTY stdin the worker is an ordinary interactive session, so
# when the task ends it returns to the prompt and does not exit (measured:
# the wrapper process stayed alive after task completion and no rc file was
# created). This point is reached only when a person leaves the session, and
# the rc then describes how the session closed, not whether the task
# succeeded. This notification is not a completion verdict — the
# user checks the result directly in cmux.
case "{provider}" in
  claude)
    # Reaching here means only that the session closed, not that the task
    # finished. A person can leave mid-task with rc=0, so this does not say
    # "completed".
    cmux notify --title "cmux-delegate" --body "Claude session exited (rc=$rc): {short_task}" 2>/dev/null || true
    ;;
  *)
    # codex/gemini are non-interactive, so rc genuinely reports task success
    # or failure.
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
`{budget_flag}` is no longer substituted (#1054). `--max-budget-usd` is
print-mode only and cannot be used with an interactive worker — and
codex/gemini do not support budget caps in the first place, so this skill has
no path to deliver a budget at all.

**The claude branch pipes neither stdin nor stdout.** That is the point of
this shape, and it must not be reverted even for convenience — piping stdin
kills the worker at a permission prompt, and piping stdout empties the running
pane so a person cannot see what is happening. If a log copy is needed, pull
it from the pane with `cmux read-screen --scrollback` instead of re-inserting
`tee`.

The codex/gemini branches stay as they are. gemini takes argv via `-p` to
begin with, and `codex exec` is non-interactive by design, so a
waiting-for-an-answer state does not exist.

**This file is also created with the `Write` tool.** Note that the file
content itself contains shell variables (`$PROMPT_FILE`, etc.) — that is
intended. What matters is that the user prompt never passes through this
script.

**CRITICAL — do not delete the .md file in the trap.** The trap runs when the
workspace closes, and another workspace may reference the same .md file
(distribute mode, retries, etc.).

### Step 5a: Launch cmux Workspace (new session)

When `--session` is not specified:

```bash
WS_RAW=$(cmux new-workspace \
  --name "[delegate] {short_task}" \
  --cwd "{cwd}" \
  --command "bash {script_file}")

# Validate workspace creation
if [[ "$WS_RAW" != OK* ]]; then
  echo "Error: workspace 생성 실패 — $WS_RAW"
  echo "수동 실행: bash {script_file}"
  exit 1
fi

WS_REF=$(echo "$WS_RAW" | sed 's/^OK //')
```

**In distribute mode, repeat this once per split item**, substituting that
item's `{prompt_file}` / `{script_file}` pair each time.

### Step 5b: Send to Existing Session (existing session)

When `--session` is specified:

```bash
# 1. Match the workspace
# `-v f=1` rather than a literal field number: the skill loader rewrites a
# `$<digit>` in this body into the invocation's arguments, so the literal form
# reaches the model already corrupted. The field split itself stays awk's: rows
# are leading-space indented, so `cut -d' ' -f1` returns an empty first field.
TARGET=$(cmux list-workspaces | grep "{session}" | sed 's/^\* //' | head -1 | awk -v f=1 '{print $f}')

# The selected workspace's row starts with `* `, so its first field is the
# marker: strip it, or the ref is unreachable through `--session`. The prefix
# test then rejects the non-empty garbage an emptiness guard waves through.
if [ "${TARGET#workspace:}" = "$TARGET" ]; then
  echo "Error: 세션 '{session}'의 workspace ref 를 얻지 못했습니다 (추출값: '$TARGET')"
  cmux list-workspaces
  exit 1
fi

# 2. Deliver the prompt file path
cmux send --workspace "$TARGET" \
  "{prompt_file} 파일을 읽고 조사해주세요."
cmux send-key --workspace "$TARGET" Enter
```

### Step 6: Report

Report the skill execution result to the user:

**Single-session mode:**

```text
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

**Distribute mode:**

```text
Distributed to {N} workspaces:
  | Workspace | Task | Provider | Model | Account |
  |-----------|------|----------|-------|---------|
  | {ws_ref}  | {item_title} | {provider} | {sub_model} | {account} |
  ...

각 cmux 탭에서 진행 상황을 확인하세요.
결과 확인은 탭마다 직접 합니다. claude 워커의 완료 알림은 오지 않습니다 —
위와 같은 이유입니다.
```

**Existing-session mode:**

```text
Sent to {TARGET} ({session_name})
  Task: {short_task}
  Prompt: /tmp/cmux-delegate-{timestamp}.md

cmux에서 {session_name} 탭을 확인하세요.
```

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| `cmux` not found | Print "cmux가 설치되어 있지 않습니다. cmux.app을 설치해주세요." and stop |
| git command fails | Fill that context item with "unavailable" and continue |
| `gh` command fails | Fill the PR info with "no PR found" and continue |
| workspace creation fails | Print the error message. Point at the prompt file path so it can be run manually |
| `--session` match fails | Show the list of available workspaces and stop |
| `--account` directory missing | Print the error message and stop |
| distribute split fails | If the split is impossible, fall back to a single session and notify the user |

## Architecture

### Single session (default)

```text
user: /cmux-delegate "#1140 auth 토큰 갱신 실패" --model claude:opus --account claude-2
  │
  ├── Step 1.6: Account Resolution
  │     └── CLAUDE_CONFIG_DIR=~/.claude-2
  │
  ├── Step 2: Context collection (git, gh)
  │     └── git branch, log, diff, gh pr
  │
  ├── Step 2.5: Conversation-synthesis handoff (written directly by the agent, no LLM call)
  │     └── Findings / Relevant files (+ Decisions / Next task if continue-work)
  │           (thin context → omit entirely)
  │
  ├── Step 3: Prompt .md generation (Write tool)
  │     └── /tmp/cmux-delegate-{ts}.md
  │
  ├── Step 4: wrapper .sh generation (Write tool)
  │     └── /tmp/cmux-delegate-{ts}.sh
  │           └── CLAUDE_CONFIG_DIR=~/.claude-2 claude --model opus "$(cat .md)"
  │           └── trap: delete .sh only (.md preserved)
  │           └── cmux notify: only on startup failure / session exit (not a completion verdict)
  │
  └── Step 5a: cmux new-workspace --command "bash .sh"
        └── workspace:{N} → independent Claude session (claude-2 account)
```

### Delivery to an existing session

```
user: /cmux-delegate "에러 조사" --session claude-2
  │
  ├── Step 1.5: Session Resolution
  │     └── cmux list-workspaces → match "claude-2"
  │
  ├── Step 2.5: Conversation-synthesis handoff (rich if continuing work)
  │
  ├── Step 3: Prompt .md generation
  │
  └── Step 5b: cmux send --workspace {matched} "prompt file path"
        └── message delivered into the existing session
```

### Parallel distribution (distribute)

```text
user: /cmux-delegate "작업 중 나온 별건 3개: #1140 토큰 갱신 실패, #1141 로그 유실, #1142 문서 오타" --account claude-2 --distribute
  │
  ├── Step 2.5: Conversation-synthesis handoff (once) → included in the shared Context block
  │
  ├── Step 3.5: Distribute — split at issue granularity (Handoff copied into every split)
  │     ├── /tmp/cmux-delegate-{ts}-1.md (#1140)
  │     ├── /tmp/cmux-delegate-{ts}-2.md (#1141)
  │     └── /tmp/cmux-delegate-{ts}-3.md (#1142)
  │
  ├── Step 4: generate 3 wrapper .sh files (CLAUDE_CONFIG_DIR applied to each)
  │
  └── Step 5a: cmux new-workspace × 3 (parallel)
        ├── workspace:{N}   → [#1140] (claude-2 account)
        ├── workspace:{N+1} → [#1141] (claude-2 account)
        └── workspace:{N+2} → [#1142] (claude-2 account)

Each workspace runs its one issue through issue→worktree→PR to completion.
Nothing comes back to the delegator.
```

## Why Wrapper Script?

The wrapper exists to keep the prompt **in a file**. If the prompt text lands
as a literal in the script body or on the command line, `$`, `{}`, and `` ` ``
get interpreted by the shell and break (observed when a prompt containing
backticks and `${…}` was passed as a shell literal).

**Keeping it in a file and passing it via stdin are separate things.** The two
were bundled together for a long time, but what broke was `-p "…literal…"`,
not argv itself. `"$(cat file)"` is exactly as safe as the pipe because the
shell does not re-interpret the substitution result:

```text
$ claude --model haiku "$(cat p4.md)"
cost is $COST `whoami` ${HOME} {a,b} "quoted" 'single' \n   ← returned verbatim
```

That is why Step 4 uses argv — it keeps the special-character safety and
takes stdin back. With stdin alive the worker is an ordinary session, and a
person can answer the permission prompts and the workspace trust dialog. The
pipe-era hunt for `-p`/redirect exemptions to bypass that dialog was a problem
created by there being nobody to answer it.

`--max-budget-usd` is still print-mode only and cannot be used in this shape.
That was equally true in the pipe era, so it is not a regression.

## Limitations

- **No automatic result-file collection/reporting** → the user checks
  directly in cmux. Delegation is fire-and-forget, and the delegator does not
  monitor the worker
- Consequently a completion claim and actual completion are
  indistinguishable — if the worker says it created a PR, the user verifies
  directly with `gh pr view`
- No task-type templates → the user specifies details in the prompt directly
- The distribute split judges issue boundaries by human reading —
  unstructured prompts need manual splitting
- The delegation-unit test (does it stand as an issue) is not structurally
  enforced — nothing prevents violations beyond reading and following this
  document
- **Handoff synthesis quality depends on the orchestrator conversation**
  (Step 2.5) — with thin conversation context only raw git context is
  delivered, and for fresh-eyes delegation it is deliberately minimized to
  prevent bias
- **codex write constraint**: `codex exec` can exit without error even when
  file writes fail due to its sandboxed environment — after completion,
  always check for actual changes with `git status`. On an empty diff,
  immediately re-delegate with the `claude` fallback.
