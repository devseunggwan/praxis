#!/usr/bin/env bash
# tests/test_agent_report_handoff.sh — cmux-delegate file-based report handoff
#
# Regression test for issue #842:
#   A delegated agent's completion report travelled the prose channel only,
#   which failed in both directions — one agent reported a PR it had never
#   created (and had not even pushed the branch), another went silent through
#   two direct instructions. Neither is distinguishable by reading the message.
#   The skill must therefore (a) make the agent write .agent-report.json,
#   (b) make the orchestrator read that file rather than the message, and
#   (c) treat file absence as incomplete.
#
# All assertions are static document checks against SKILL.md, mirroring
# tests/test_worktree_merge_cleanup.sh.
#
# Run:  bash tests/test_agent_report_handoff.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/cmux-delegate/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. Pre-existing contracts the handoff must not displace
# ---------------------------------------------------------------------------

assert_present \
  "file-based prompt delivery still mandated" \
  "인라인 \`-p\` 절대 사용 금지"

assert_present \
  "conversation handoff synthesis retained" \
  "Synthesize Conversation Handoff"

# ---------------------------------------------------------------------------
# 2. Agent side — the report file and every required field (issue #842)
# ---------------------------------------------------------------------------

assert_present \
  "completion protocol section exists" \
  "Completion protocol"

assert_present \
  "report file named" \
  ".agent-report.json"

for field in branch head_sha pushed pr_url tests completed_at; do
  assert_present \
    "report field documented: $field" \
    "\"$field\""
done

assert_present \
  "pushed is gated on a successful push, not on committing" \
  "git push\` 가 **성공한 뒤에만** true"

assert_present \
  "absent PR must be null, not a guessed URL" \
  "빈 문자열이나 예상 URL 금지"

assert_present \
  "test counts are transcribed, not estimated" \
  "추정 금지"

assert_present \
  "unfinished work writes no file" \
  "파일을 쓰지 마세요"

# ---------------------------------------------------------------------------
# 3. Orchestrator side — read the file, not the message (issue #842)
# ---------------------------------------------------------------------------

assert_present \
  "collect-report step exists" \
  "Step 7: Collect the Report"

assert_present \
  "orchestrator is told not to read the agent's message" \
  "에이전트의 메시지를 읽지 않고"

assert_present \
  "file absence is deterministic incompletion" \
  "미완료: .agent-report.json 부재"

assert_present \
  "report values are re-verified, not trusted" \
  "그대로 믿지 않고"

assert_present \
  "push claim re-verified against the remote" \
  "git ls-remote origin"

assert_present \
  "PR claim re-verified against gh" \
  "gh pr view <url> --json state,headRefOid"

assert_present \
  "test counts re-verified by re-running" \
  "같은 명령을 직접 재실행"

assert_present \
  "partial completion (pushed:false) is not a failure" \
  "정상적인 부분 완료"

# ---------------------------------------------------------------------------
# 4. Honest scope — detection only, not diagnosis (issue #842)
# ---------------------------------------------------------------------------

assert_present \
  "silence cause is declared out of scope" \
  "silence 를 *탐지* 할 뿐 원인을 진단하지"

assert_present \
  "limitation restated in the Limitations section" \
  "silence 는 *탐지* 만 가능하고"

# ---------------------------------------------------------------------------
# 5. Error-handling rows for the three failure shapes
# ---------------------------------------------------------------------------

assert_present \
  "missing-report row present" \
  "부재 | **미완료로 취급**"

assert_present \
  "malformed-report row keeps the file for inspection" \
  "부분 기록일 수 있으므로 삭제 금지"

assert_present \
  "mismatch row prefers the re-verification output" \
  "재검증 출력을 채택하고 보고서 값은 폐기"

# ---------------------------------------------------------------------------
# 6. The documented gate actually separates the two failure shapes
#
# Static presence checks prove the text is there, not that the procedure it
# describes discriminates. Lift the gate verbatim out of Step 7 and run it
# against both states the issue names: a normal completion and a fabrication
# (a completion CLAIM with no report file). The prose channel cannot tell
# these apart — this gate must.
# ---------------------------------------------------------------------------

run_documented_gate() {  # $1 = cwd under test
  local report="$1/.agent-report.json"
  [ -f "$report" ] || { echo "미완료: .agent-report.json 부재"; return 1; }
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$report" 2>/dev/null \
    || { echo "미완료: JSON 파싱 실패"; return 1; }
  return 0
}

GATE_TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
mkdir -p "$GATE_TMP/normal" "$GATE_TMP/fabricated" "$GATE_TMP/truncated"
cat > "$GATE_TMP/normal/.agent-report.json" <<'JSON'
{"branch": "issue-1-x", "head_sha": "abc", "pushed": true,
 "pr_url": null, "tests": {"command": "true", "passed": 1, "failed": 0},
 "completed_at": "2026-07-28T00:00:00Z"}
JSON
# fabricated/: the agent said "PR created, all done" and wrote nothing.
printf '{"branch": "issue-1-x", "head_sha"' > "$GATE_TMP/truncated/.agent-report.json"

run_documented_gate "$GATE_TMP/normal" >/dev/null
_assert_record "gate accepts a normal completion" "$([ $? -eq 0 ] && echo 1 || echo 0)" \
  "documented gate rejected a valid report"

run_documented_gate "$GATE_TMP/fabricated" >/dev/null
_assert_record "gate rejects a completion claim with no file" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted a missing report"

run_documented_gate "$GATE_TMP/truncated" >/dev/null
_assert_record "gate rejects a truncated report" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted malformed JSON"

if [ -f "$GATE_TMP/truncated/.agent-report.json" ]; then
  _assert_record "gate leaves a malformed report on disk for inspection" 1 ""
else
  _assert_record "gate leaves a malformed report on disk for inspection" 0 "file removed"
fi

rm -rf "$GATE_TMP"

assert_lib_summary
