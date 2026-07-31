#!/usr/bin/env bash
# tests/test_agent_report_handoff.sh — cmux-delegate file-based report handoff
#
# Regression test for issue #842:
#   A delegated agent's completion report travelled the prose channel only,
#   which failed in both directions — one agent reported a PR it had never
#   created (and had not even pushed the branch), another went silent through
#   two direct instructions. Neither is distinguishable by reading the message.
#   The skill must therefore (a) make the agent write a completion report,
#   (b) make the orchestrator read that file rather than the message, and
#   (c) treat file absence as incomplete.
#
# Issue #903 moved the report out of the worktree root into
# <PRAXIS_HOME>/agent-reports/<sha1(worktree)>.json, so §6 additionally
# exercises the shipped path helper both halves of the protocol call.
#
# Assertions are static document checks against SKILL.md (mirroring
# tests/test_worktree_merge_cleanup.sh) plus an executable gate in §6.
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
  "report path comes from the shared helper, not a literal path (#903)" \
  "agent-report-path.sh"

assert_present \
  "report no longer lands in the worktree root (#903)" \
  "worktree 루트가 아니라"

for field in worktree branch head_sha pushed pr_url tests completed_at; do
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
  "미완료: 완료 보고서 부재"

assert_present \
  "reader confirms the report belongs to its own worktree (#903)" \
  "보고서의 worktree 가"

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
  "완료 보고서 부재 | **미완료로 취급**"

assert_present \
  "malformed-report row keeps the file for inspection" \
  "부분 기록일 수 있으므로 삭제 금지"

assert_present \
  "mismatch row prefers the re-verification output" \
  "재검증 출력을 채택하고 보고서 값은 폐기"

# ---------------------------------------------------------------------------
# 6. The documented gate actually separates the failure shapes
#
# Static presence checks prove the text is there, not that the procedure it
# describes discriminates. Run the gate against every state the issues name: a
# normal completion, a fabrication (a completion CLAIM with no report file), a
# truncated write, and — since #903 keys reports by worktree hash rather than
# location — a report belonging to a different worktree.
#
# The path comes from the shipped helper, not a transcription of it. A
# transcription passes even when writer and reader have drifted apart, which
# is the failure the shared helper exists to prevent.
# ---------------------------------------------------------------------------

HELPER="$ROOT_DIR/skills/cmux-delegate/agent-report-path.sh"
GATE_TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
export PRAXIS_HOME="$GATE_TMP/praxis-home"

report_path_for() { sh "$HELPER" "$1"; }
worktree_for() { sh "$HELPER" --worktree "$1"; }

run_documented_gate() {  # $1 = path the delegator holds (worktree or a subdir)
  local report worktree
  report="$(report_path_for "$1")" || return 1
  worktree="$(worktree_for "$1")" || return 1
  [ -f "$report" ] || { echo "미완료: 완료 보고서 부재 ($report)"; return 1; }
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$report" 2>/dev/null \
    || { echo "미완료: JSON 파싱 실패"; return 1; }
  [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("worktree",""))' "$report")" = "$worktree" ] \
    || { echo "미완료: 보고서의 worktree 불일치"; return 1; }
  return 0
}

WT_NORMAL="$GATE_TMP/normal"
WT_FABRICATED="$GATE_TMP/fabricated"
WT_TRUNCATED="$GATE_TMP/truncated"
WT_FOREIGN="$GATE_TMP/foreign"

cat > "$(report_path_for "$WT_NORMAL")" <<JSON
{"worktree": "$WT_NORMAL", "branch": "issue-1-x", "head_sha": "abc", "pushed": true,
 "pr_url": null, "tests": {"command": "true", "passed": 1, "failed": 0},
 "completed_at": "2026-07-28T00:00:00Z"}
JSON
# fabricated: the agent said "PR created, all done" and wrote nothing.
printf '{"worktree": "%s", "head_sha"' "$WT_TRUNCATED" > "$(report_path_for "$WT_TRUNCATED")"
# foreign: a well-formed report that belongs to some other worktree.
cat > "$(report_path_for "$WT_FOREIGN")" <<JSON
{"worktree": "$GATE_TMP/somewhere-else", "branch": "issue-2-y", "head_sha": "def",
 "pushed": true, "pr_url": null, "tests": {"command": "true", "passed": 1, "failed": 0},
 "completed_at": "2026-07-28T00:00:00Z"}
JSON

# Writer and reader independently derive the same path — the property the
# shared helper exists to guarantee.
_assert_record "writer and reader derive the same path" \
  "$([ "$(report_path_for "$WT_NORMAL")" = "$(report_path_for "$WT_NORMAL/")" ] && echo 1 || echo 0)" \
  "trailing slash changed the derived report path"

case "$(report_path_for "$WT_NORMAL")" in
  "$PRAXIS_HOME"/agent-reports/*) _assert_record "report lands under PRAXIS_HOME" 1 "" ;;
  *) _assert_record "report lands under PRAXIS_HOME" 0 "path escaped PRAXIS_HOME" ;;
esac

_assert_record "report does not land in the worktree" \
  "$([ ! -e "$WT_NORMAL" ] && echo 1 || echo 0)" \
  "the worktree dir was created/written to"

run_documented_gate "$WT_NORMAL" >/dev/null
_assert_record "gate accepts a normal completion" "$([ $? -eq 0 ] && echo 1 || echo 0)" \
  "documented gate rejected a valid report"

run_documented_gate "$WT_FABRICATED" >/dev/null
_assert_record "gate rejects a completion claim with no file" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted a missing report"

run_documented_gate "$WT_TRUNCATED" >/dev/null
_assert_record "gate rejects a truncated report" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted malformed JSON"

run_documented_gate "$WT_FOREIGN" >/dev/null
_assert_record "gate rejects a report from another worktree" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted a foreign report"

if [ -f "$(report_path_for "$WT_TRUNCATED")" ]; then
  _assert_record "gate leaves a malformed report on disk for inspection" 1 ""
else
  _assert_record "gate leaves a malformed report on disk for inspection" 0 "file removed"
fi

# The case that motivated the normalization: the agent writes from a package
# subdirectory while the delegator holds the path it passed to `--cwd`. Hashing
# the raw argument would send the two to different files, and a finished task
# would read as incomplete.
GIT_WT="$GATE_TMP/real-repo"
mkdir -p "$GIT_WT/pkg/nested"
git -C "$GIT_WT" init -q 2>/dev/null

_assert_record "agent in a subdir derives the delegator's report path" \
  "$([ "$(report_path_for "$GIT_WT/pkg/nested")" = "$(report_path_for "$GIT_WT")" ] && echo 1 || echo 0)" \
  "subdirectory hashed to a different report than the worktree root"

cat > "$(report_path_for "$GIT_WT/pkg/nested")" <<JSON
{"worktree": "$(worktree_for "$GIT_WT/pkg/nested")", "branch": "issue-3-z",
 "head_sha": "abc", "pushed": true, "pr_url": null,
 "tests": {"command": "true", "passed": 1, "failed": 0},
 "completed_at": "2026-07-28T00:00:00Z"}
JSON

run_documented_gate "$GIT_WT" >/dev/null
_assert_record "gate accepts a report written from a subdirectory" \
  "$([ $? -eq 0 ] && echo 1 || echo 0)" \
  "gate rejected a report the agent wrote from a package subdir"

unset PRAXIS_HOME
rm -rf "$GATE_TMP"

assert_lib_summary
