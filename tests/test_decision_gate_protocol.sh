#!/usr/bin/env bash
# tests/test_decision_gate_protocol.sh — cmux-delegate decision gate (#984)
#
# The executable behaviour is pinned in tests/test_decision_gate.py. What this
# file pins is the SKILL.md text, because the gate only works if the delegated
# worker reads its prompt and obeys it: the code can refuse correctly and the
# delegation still ends with an unsupervised mutation if the prompt fails to
# say that anything other than `approved` means stop.
#
# Run:  bash tests/test_decision_gate_protocol.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/cmux-delegate/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. The worker's half — published in the prompt the worker actually receives
# ---------------------------------------------------------------------------

assert_present \
  "the worker is told to publish the question instead of choosing" \
  "판정이 올 때까지 막습니다"

assert_present \
  "the gate is invoked through the shipped helper" \
  'sh "$GATE" ask'

assert_present \
  "only approval is a go signal" \
  '`approved` 만이 진행 신호입니다'

# Three refusals with three different causes. Each is named, because a worker
# that cannot tell them apart reports "declined" for a question nobody read.
assert_present "rejection is a stop" "**하지 않는다.**"
assert_present "timeout is distinguished from rejection" "거부와 다르다"
assert_present "no cmux is not permission" "승인 없이 진행하는 것과 같다"

# ---------------------------------------------------------------------------
# 2. The delegator's half — read at Step 7, with no new supervisory loop
# ---------------------------------------------------------------------------

assert_present \
  "the pending question surfaces where the delegator already stands" \
  "상주 감시자는 두지 않습니다"

assert_present \
  "status is read inside the per-workspace loop" \
  'eval "$(sh "$GATE" status "$WS_ID"'

assert_present \
  "a blocked worker is not misread as a working one" \
  "일하는 중으로 오독되고"

assert_present \
  "rejection carries a reason" \
  "거부에는 사유를 답니다"

# The one thing a delegator can do that silently breaks the NEXT decision.
assert_present \
  "re-resolving does not re-signal" \
  "신호는 다시 가지 않습니다"

# ---------------------------------------------------------------------------
# 3. Contracts this feature must not displace
# ---------------------------------------------------------------------------

assert_present \
  "the completion report is still the oracle for finishing" \
  "이 파일이 완료 보고의 정본이고"

assert_present \
  "the report is still keyed on the workspace (#997)" \
  "보고서는 워커 하나당 하나입니다"

assert_lib_summary
