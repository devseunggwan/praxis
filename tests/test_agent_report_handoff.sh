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
# Issue #997 re-keyed the report on the WORKSPACE rather than the worktree:
# `--distribute` opens N workers in one worktree, so a worktree key gave N
# workers one file and per-worker completion was unanswerable. §6 exercises the
# shipped helper from both halves of the protocol under the new key.
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
  '결정론적으로 "미완료" 이고'

assert_present \
  "reader confirms the report was written where the work happened" \
  "불일치 = 다른 작업의 보고서"

# The defect #997 fixed: one report per WORKER, so the check sits inside the
# per-workspace loop rather than once outside it.
assert_present \
  "the report check is per-worker, inside the workspace loop" \
  "보고서는 워커 하나당 하나입니다"

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
# 4. Honest scope (issue #842, narrowed by #981)
#
# #842 could only DETECT silence, so it declared the cause out of scope
# entirely. #981 classifies that cause, which retires the blanket disclaimer —
# but not the honesty requirement behind it. What remains uncertain has to stay
# on the page, so these assertions now pin the narrower limits rather than the
# retired sentence: the two states the stream cannot separate, and the value
# that means "no answer" instead of "dead".
# ---------------------------------------------------------------------------

assert_present \
  "tool-execution and thinking are declared indistinguishable" \
  "툴 실행 중인지"

assert_present \
  "the reason PostToolUse cannot close that gap is stated" \
  "agent.hook.PostToolUse\` 가 중계되지 않아"

assert_present \
  "limitation restated in the Limitations section" \
  "툴 실행 중과 사고 중은 구분 불가"

assert_present \
  "a failed lookup is not allowed to read as death" \
  "부재를 사망으로 읽지 않는다"

# The selector is stashed at Step 5 rather than re-derived at Step 7, because
# neither re-derivation path is sound: `short_task` is truncated to 30 chars so
# titles collide, and `--session` never creates a titled workspace at all.
# Pinned here because both halves live in one file and a title-lookup rewrite
# would silently reopen the gap.

assert_present \
  "Step 7 reads the selectors Step 5 stashed" \
  "find /tmp/ -maxdepth 1 -name 'cmux-delegate-{timestamp}-*.ws'"

assert_present \
  "the new-session path stashes one file per workspace" \
  'echo "$WS_ID" > "/tmp/cmux-delegate-{timestamp}-${WS_REF#workspace:}.ws"'

assert_present \
  "the existing-session path stashes it the same way" \
  'echo "$WS_ID" > "/tmp/cmux-delegate-{timestamp}-${TARGET#workspace:}.ws"'

# A failed lookup still lets `head` succeed, so redirecting the pipeline itself
# leaves a zero-byte file — which the consumer would read as "a worker exists"
# and then probe with an empty argument, getting usage/rc=2 and no state.
assert_present \
  "the stash is gated on a non-empty lookup" \
  'if [ -n "$WS_ID" ]; then'

assert_present \
  "the consumer skips an empty stash instead of counting it" \
  '[ -s "$WS_FILE" ] || { echo "$WS_FILE 비어 있음 — 건너뜀"; continue; }'

assert_present \
  "state is reset per iteration so one worker cannot inherit another's verdict" \
  "직전 워커의 값"

# `/tmp` is a symlink to `private/tmp` on macOS, so a bare `find /tmp` returns
# nothing whether or not the files exist — a dead oracle that reads as "no
# workers to check". The trailing slash is what makes find follow it.
assert_present \
  "the trailing slash that keeps find from silently finding nothing is explained" \
  "find 는 기본적으로 링크를 따라가지 않아"

assert_present \
  "the reason the title lookup is only a fallback is stated" \
  "30자 절단이 동명 워크스페이스를"

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

# The delegator addresses a worker by the UUID it stashed at launch; the worker
# is addressed by its own environment. Both go through the shipped helper.
report_for_delegator() { sh "$HELPER" --workspace "$1"; }
report_for_worker() { CMUX_WORKSPACE_ID="$1" sh "$HELPER"; }
worktree_for() { sh "$HELPER" --worktree "$1"; }

WS_NORMAL=AAAAAAAA-1111-2222-3333-444444444444
WS_FABRICATED=BBBBBBBB-1111-2222-3333-444444444444
WS_TRUNCATED=CCCCCCCC-1111-2222-3333-444444444444
WS_MISPLACED=DDDDDDDD-1111-2222-3333-444444444444

run_documented_gate() {  # $1 = workspace UUID, $2 = path the delegator holds
  local report worktree
  report="$(report_for_delegator "$1")" || return 1
  worktree="$(worktree_for "$2")" || return 1
  [ -f "$report" ] || { echo "미완료: 완료 보고서 부재 ($report)"; return 1; }
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$report" 2>/dev/null \
    || { echo "미완료: JSON 파싱 실패"; return 1; }
  [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("worktree",""))' "$report")" = "$worktree" ] \
    || { echo "미완료: 보고서의 worktree 불일치"; return 1; }
  return 0
}

WT_NORMAL="$GATE_TMP/normal"
WT_TRUNCATED="$GATE_TMP/truncated"
WT_MISPLACED="$GATE_TMP/misplaced"

cat > "$(report_for_delegator "$WS_NORMAL")" <<JSON
{"worktree": "$WT_NORMAL", "branch": "issue-1-x", "head_sha": "abc", "pushed": true,
 "pr_url": null, "tests": {"command": "true", "passed": 1, "failed": 0},
 "completed_at": "2026-07-28T00:00:00Z"}
JSON
# fabricated: the agent said "PR created, all done" and wrote nothing.
printf '{"worktree": "%s", "head_sha"' "$WT_TRUNCATED" > "$(report_for_delegator "$WS_TRUNCATED")"
# misplaced: a well-formed report whose work happened somewhere else entirely.
cat > "$(report_for_delegator "$WS_MISPLACED")" <<JSON
{"worktree": "$GATE_TMP/somewhere-else", "branch": "issue-2-y", "head_sha": "def",
 "pushed": true, "pr_url": null, "tests": {"command": "true", "passed": 1, "failed": 0},
 "completed_at": "2026-07-28T00:00:00Z"}
JSON

# The property the shared helper exists to guarantee: the worker writing from
# inside its workspace and the delegator holding the stashed UUID land on one
# file, without either side transcribing the derivation.
_assert_record "writer and reader derive the same path" \
  "$([ "$(report_for_worker "$WS_NORMAL")" = "$(report_for_delegator "$WS_NORMAL")" ] && echo 1 || echo 0)" \
  "the worker's own-environment path differs from the delegator's --workspace path"

# The defect this file's §6 was rewritten for (#997): N workers in ONE worktree.
_assert_record "two workers in the same worktree get different reports" \
  "$([ "$(report_for_delegator "$WS_NORMAL")" != "$(report_for_delegator "$WS_FABRICATED")" ] && echo 1 || echo 0)" \
  "distinct workspaces collided on one report file"

# No fallback to a worktree key: outside cmux the helper refuses rather than
# writing a file nobody reads now and an unrelated delegation finds later.
if env -u CMUX_WORKSPACE_ID sh "$HELPER" >/dev/null 2>&1; then
  _assert_record "no workspace is a hard failure, not a fallback" 0 "helper answered with no workspace id"
else
  _assert_record "no workspace is a hard failure, not a fallback" 1 ""
fi

# The delegator handing over an empty `.ws` must not be answered from the
# delegator's OWN environment — that reads one worker's completion as another's.
if CMUX_WORKSPACE_ID="$WS_NORMAL" sh "$HELPER" --workspace "" >/dev/null 2>&1; then
  _assert_record "an empty --workspace does not fall through to the environment" 0 \
    "empty value was answered from CMUX_WORKSPACE_ID"
else
  _assert_record "an empty --workspace does not fall through to the environment" 1 ""
fi

# The UUID becomes a filename and arrives from `cat`-ing a file.
if sh "$HELPER" --workspace "../../etc/passwd" >/dev/null 2>&1; then
  _assert_record "a non-UUID workspace is rejected" 0 "traversal-shaped value was accepted"
else
  _assert_record "a non-UUID workspace is rejected" 1 ""
fi

# An old `... "$PWD"` call site must fail loudly rather than have its argument
# ignored while it addresses a different file than the other half.
if CMUX_WORKSPACE_ID="$WS_NORMAL" sh "$HELPER" "$WT_NORMAL" >/dev/null 2>&1; then
  _assert_record "the retired positional call form is rejected" 0 "path argument was silently ignored"
else
  _assert_record "the retired positional call form is rejected" 1 ""
fi

case "$(report_for_delegator "$WS_NORMAL")" in
  "$PRAXIS_HOME"/agent-reports/*) _assert_record "report lands under PRAXIS_HOME" 1 "" ;;
  *) _assert_record "report lands under PRAXIS_HOME" 0 "path escaped PRAXIS_HOME" ;;
esac

_assert_record "report does not land in the worktree" \
  "$([ ! -e "$WT_NORMAL" ] && echo 1 || echo 0)" \
  "the worktree dir was created/written to"

run_documented_gate "$WS_NORMAL" "$WT_NORMAL" >/dev/null
_assert_record "gate accepts a normal completion" "$([ $? -eq 0 ] && echo 1 || echo 0)" \
  "documented gate rejected a valid report"

run_documented_gate "$WS_FABRICATED" "$GATE_TMP/fabricated" >/dev/null
_assert_record "gate rejects a completion claim with no file" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted a missing report"

run_documented_gate "$WS_TRUNCATED" "$WT_TRUNCATED" >/dev/null
_assert_record "gate rejects a truncated report" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted malformed JSON"

run_documented_gate "$WS_MISPLACED" "$WT_MISPLACED" >/dev/null
_assert_record "gate rejects a report whose work happened elsewhere" \
  "$([ $? -ne 0 ] && echo 1 || echo 0)" "documented gate accepted a misplaced report"

if [ -f "$(report_for_delegator "$WS_TRUNCATED")" ]; then
  _assert_record "gate leaves a malformed report on disk for inspection" 1 ""
else
  _assert_record "gate leaves a malformed report on disk for inspection" 0 "file removed"
fi

# `--worktree` still normalizes a package subdir to the worktree root: the agent
# commonly works from `<worktree>/pkg` while the delegator holds `--cwd`, and the
# report's `worktree` field must match across that gap.
GIT_WT="$GATE_TMP/real-repo"
mkdir -p "$GIT_WT/pkg/nested"
git -C "$GIT_WT" init -q 2>/dev/null

_assert_record "a subdir normalizes to the worktree root" \
  "$([ "$(worktree_for "$GIT_WT/pkg/nested")" = "$(worktree_for "$GIT_WT")" ] && echo 1 || echo 0)" \
  "subdirectory did not normalize to the worktree root"

# And it must keep working with no workspace at all — the field it feeds has to
# be obtainable on a host with no cmux. Q3 of #997 depends on this ordering.
if env -u CMUX_WORKSPACE_ID sh "$HELPER" --worktree "$GIT_WT" >/dev/null 2>&1; then
  _assert_record "--worktree works outside a workspace" 1 ""
else
  _assert_record "--worktree works outside a workspace" 0 "the workspace check ran before the --worktree return"
fi

WS_SUBDIR=EEEEEEEE-1111-2222-3333-444444444444
cat > "$(report_for_delegator "$WS_SUBDIR")" <<JSON
{"worktree": "$(worktree_for "$GIT_WT/pkg/nested")", "branch": "issue-3-z",
 "head_sha": "abc", "pushed": true, "pr_url": null,
 "tests": {"command": "true", "passed": 1, "failed": 0},
 "completed_at": "2026-07-28T00:00:00Z"}
JSON

run_documented_gate "$WS_SUBDIR" "$GIT_WT" >/dev/null
_assert_record "gate accepts a report written from a subdirectory" \
  "$([ $? -eq 0 ] && echo 1 || echo 0)" \
  "gate rejected a report the agent wrote from a package subdir"

unset PRAXIS_HOME
rm -rf "$GATE_TMP"

assert_lib_summary
