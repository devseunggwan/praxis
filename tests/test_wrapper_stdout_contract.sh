#!/usr/bin/env bash
# tests/test_wrapper_stdout_contract.sh — Step 4 stdio contract, prose half (#1054)
#
# The executable behaviour is pinned in tests/test_wrapper_stdout_contract.py.
# What this file pins is the SKILL.md text, for reasons the code cannot cover:
#
#   1. Piping the prompt looks harmless and reads as the safer form — it is the
#      shape most callers reach for. The comment saying what it costs (fd 0, and
#      with it every permission prompt and `cmux send`) is the load-bearing part,
#      so it is asserted like code.
#   2. `"$(cat file)"` looks like the shell-interpolation failure the skill spent
#      years excluding. The sentence separating an inline literal from a quoted
#      command substitution is what stops a future reader from "restoring" the
#      pipe on that reasoning.
#   3. A notify that ignores the exit code reports a dead worker as a finished
#      one. That is #1054's original symptom, so the rc gating is asserted here
#      too — deleting it silently reopens the bug.
#
# Run:  bash tests/test_wrapper_stdout_contract.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/cmux-delegate/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. Why the prompt is not piped — the cost is named, not just the rule
# ---------------------------------------------------------------------------

assert_present \
  "the call site says the prompt goes through argv" \
  "프롬프트는 argv 로 넘기고 stdin 은 비워 둡니다"

assert_present \
  "the mechanism is stated: cat exiting is what closes fd 0" \
  "cat\` 이 끝나는 순간 워커의 fd 0 이 닫히고"

assert_present \
  "the measured consequence is cited, not just asserted" \
  "exit 0 으로 종료했습니다"

assert_present \
  "the stdout half names its own cost — the pane goes blank" \
  "블록 버퍼링이 걸려 실행 중인 페인이"

# ---------------------------------------------------------------------------
# 2. The distinction that keeps the pipe from being "restored"
# ---------------------------------------------------------------------------

# "Why Wrapper Script?" records a real failure with an inline literal prompt
# (Hub #1001). The new prose must keep that exclusion while separating it from
# the argv form, or a future reader re-derives the pipe from the same story.
assert_present \
  "the inline-literal exclusion is restated as still binding" \
  "스크립트 본문이나 명령줄에 리터럴로 들어가면"

assert_present \
  "the two are separated: a quoted substitution is not re-expanded" \
  "셸이 치환 결과를 재해석하지 않으므로"

assert_present \
  "the separation is backed by a transcribed run, not an argument" \
  'cost is $5 `whoami` ${HOME}'

# ---------------------------------------------------------------------------
# 3. The completion signal must follow the exit code
# ---------------------------------------------------------------------------

assert_present \
  "the notify branches on rc rather than firing unconditionally" \
  'if [ "$rc" -eq 0 ]; then'

assert_present \
  "the failure branch carries the exit code to the reader" \
  "Task FAILED (exit \$rc)"

assert_present \
  "why rc gating exists is stated, not left to be re-derived" \
  "그게 #1054 의 원래 증상입니다"

# Measured after the switch: an argv+TTY worker stays interactive, so the rc
# branch is never reached on task completion. Leaving that unsaid re-creates
# #1054's false-completion expectation with a different mechanism.
assert_present \
  "the notify is disclaimed as a completion signal on the claude branch" \
  "작업 완료 시점에 실행되지 않습니다"

assert_present \
  "the real completion oracle is named — the Step 7 report file" \
  "완료 판정의 정본은 Step 7 이"

# ---------------------------------------------------------------------------
# 4. The argv size limit is guarded, and the guard refuses a fallback
# ---------------------------------------------------------------------------

assert_present \
  "the prompt is measured against the kernel argument limit" \
  "ARG_LIMIT=\$(( \$(getconf ARG_MAX) / 4 ))"

assert_present \
  "the guard fails loudly instead of falling back to the pipe" \
  "폴백은 두지 않습니다"

# ---------------------------------------------------------------------------
# 5. Contracts this change must not displace
# ---------------------------------------------------------------------------

assert_present \
  "the trap still deletes only the .sh" \
  "trap 'rm -f \"\$SCRIPT_FILE\"' EXIT"

assert_absent \
  "the prompt is never piped into claude again" \
  'cat "$PROMPT_FILE" | {claude_env} claude'

assert_absent \
  "no -p form is reintroduced" \
  'claude -p "$(cat'

assert_absent \
  "the worker is not handed a permission-mode override" \
  "--permission-mode {permission_mode}"

assert_lib_summary
