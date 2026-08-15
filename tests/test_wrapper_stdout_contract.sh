#!/usr/bin/env bash
# tests/test_wrapper_stdout_contract.sh — Step 4 stdout contract, prose half (#981)
#
# The executable behaviour is pinned in tests/test_wrapper_stdout_contract.py.
# What this file pins is the SKILL.md text, for two reasons the code cannot
# cover:
#
#   1. `| tee` looks like logging. A future reader who reads it as logging will
#      delete it as redundant, and the ARCHITECTURE.md obligation silently
#      lapses. The comment saying it is contract compliance is the load-bearing
#      part, so it is asserted like code.
#   2. This is documented-contract drift, NOT a repaired failure — the owner's
#      4-run control on claude 2.1.232 showed the prompt is not actually lost.
#      If that disclaimer goes missing, the next reader concludes a real bug
#      once existed here and reasons from a fiction.
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
# 1. The obligation is named where the call site lives
# ---------------------------------------------------------------------------

assert_present \
  "the redirect is justified as contract compliance, not logging" \
  "로그 적재가 목적이 아니라 계약 이행입니다"

assert_present \
  "the obligation is traced to its source document" \
  "stdout 을 리다이렉트하거나"

assert_present \
  "the competing-readers mechanism is stated, not just the rule" \
  "같은 스트림을 두고 경쟁합니다"

# ---------------------------------------------------------------------------
# 2. The disclaimer — the single sentence that stops a future misreading
# ---------------------------------------------------------------------------

assert_present \
  "the file says plainly that no bug was fixed here" \
  "**이것은 버그 수정이 아닙니다.**"

assert_present \
  "the owner's control run is cited by version" \
  "소유자의 4회 대조 실행(claude 2.1.232)"

assert_present \
  "the reader is told how NOT to read the line" \
  "예전에 프롬프트가 깨졌었다"

# ---------------------------------------------------------------------------
# 3. Why tee and not the two nearby forms that also satisfy the contract
# ---------------------------------------------------------------------------

assert_present \
  "the narrowest-redirect choice is argued against a full redirect" \
  "가장 좁은 리다이렉트이기 때문입니다"

assert_present \
  "the operator-visibility cost of a full redirect is named" \
  "페인을 비웁니다"

assert_present \
  "the log is marked as a byproduct, so deleting it does not look free" \
  "파이프 자체는 남겨두어야 합니다"

# ---------------------------------------------------------------------------
# 4. Contracts this change must not displace
# ---------------------------------------------------------------------------

# "Why Wrapper Script?" records a real, previously-experienced failure with
# `claude -p "..."`. The new prose must reinforce that exclusion, not reopen it.
assert_present \
  "the -p exclusion is restated as still binding" \
  '`-p` 를 붙이는 순간 프롬프트가 shell 을 거쳐 위 실패가 그대로 돌아옵니다'

assert_present \
  "the budget flag is not allowed to argue -p back in" \
  "print 모드 전용인 것도"

# The must-stay-silent direction, mirrored from the .py control: the wrapper
# must still create the .sh only via Write, and the trap must still spare .md.
assert_present \
  "the trap still deletes only the .sh" \
  "trap 'rm -f \"\$SCRIPT_FILE\"' EXIT"

assert_absent \
  "the stdin path is still shell-free — no -p form reintroduced" \
  'claude -p "$(cat'

assert_lib_summary
