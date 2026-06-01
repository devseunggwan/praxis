#!/usr/bin/env bash
# test_merge_menu_review_options.sh — coverage for
# hooks/advisory-nudge/merge-menu-review-options-advisory/impl.py
#
# Synthesizes Claude Code PreToolUse(AskUserQuestion) payloads and asserts:
#   advisory → exit 0 + stderr non-empty  (default mode)
#   block    → exit 2 + stderr non-empty  (PRAXIS_MERGE_MENU_REVIEW_STRICT=1)
#   pass     → exit 0 + stderr empty
#
# The hook inspects only options[].label — no transcript is needed.
#
# Usage: bash tests/hooks/advisory-nudge/test_merge_menu_review_options.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/merge-menu-review-options-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# Build a JSON payload for an AskUserQuestion tool call from an options array.
# $1 = options JSON array (e.g., '["squash 머지", "대기"]')
build_payload() {
  local options_json="$1"
  python3 - "$options_json" <<'PY'
import json, sys
options = json.loads(sys.argv[1])
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "Merge?",
                "header": "Merge",
                "multiSelect": False,
                "options": [{"label": opt, "description": "test desc"} for opt in options],
            }
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PY
}

# run_case name expected mode payload
#   expected: advisory | block | pass
#   mode:     default            → no STRICT env set
#             strict             → PRAXIS_MERGE_MENU_REVIEW_STRICT=1
#             strictval=<value>  → PRAXIS_MERGE_MENU_REVIEW_STRICT=<value>
run_case() {
  local name="$1" expected="$2" mode="$3" payload="$4"
  local err_file rc

  err_file=$(mktemp)
  case "$mode" in
    strict)
      echo "$payload" | PRAXIS_MERGE_MENU_REVIEW_STRICT=1 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    strictval=*)
      echo "$payload" | PRAXIS_MERGE_MENU_REVIEW_STRICT="${mode#strictval=}" "$HOOK" >/dev/null 2>"$err_file"
      ;;
    *)
      echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
      ;;
  esac
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    advisory) [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0 ;;
    block)    [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0 ;;
    pass)     [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0 ;;
    *) echo "INTERNAL: unknown expected '$expected'" >&2; ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# (a) ADVISORY — merge-decision menu, no review/debate option
# ---------------------------------------------------------------------------
run_case "KO merge token, no review"        advisory default "$(build_payload '["squash 머지 + 정리", "대기"]')"
run_case "EN merge token, no review"        advisory default "$(build_payload '["squash merge + cleanup", "hold"]')"
run_case "EN squash token, no review"       advisory default "$(build_payload '["squash and merge", "wait"]')"
run_case "mixed-script squash 머지"          advisory default "$(build_payload '["squash 머지", "대기"]')"
run_case "uppercase EN merge token"          advisory default "$(build_payload '["MERGE NOW", "HOLD"]')"
run_case "TitleCase Squash Merge"            advisory default "$(build_payload '["Squash Merge", "Hold"]')"

# ---------------------------------------------------------------------------
# (b) PASS — review/debate option already present
# ---------------------------------------------------------------------------
run_case "review present: codex review (KO)"   pass default "$(build_payload '["squash 머지", "codex review 재실행", "대기"]')"
run_case "review present: code-reviewer (EN)"  pass default "$(build_payload '["merge", "re-run code-reviewer", "hold"]')"
run_case "review present: critic"              pass default "$(build_payload '["머지", "critic 토론", "대기"]')"
run_case "review present: 리뷰 (KO)"            pass default "$(build_payload '["머지", "리뷰 한번 더", "대기"]')"
run_case "review present: 검토 (KO)"            pass default "$(build_payload '["merge", "추가 검토", "hold"]')"

# ---------------------------------------------------------------------------
# (c) PASS — no merge-decision option (not a merge gate)
# ---------------------------------------------------------------------------
run_case "no merge token"                  pass default "$(build_payload '["Plan A", "Plan B"]')"
run_case "false-positive: merged (not merge)" pass default "$(build_payload '["view merged PRs", "cancel"]')"
run_case "false-positive: merger"          pass default "$(build_payload '["contact the merger", "skip"]')"
run_case "empty options"                   pass default "$(build_payload '[]')"

# ---------------------------------------------------------------------------
# (d) BLOCK — strict mode, merge menu without review option
# ---------------------------------------------------------------------------
run_case "strict: KO merge, no review"     block strict "$(build_payload '["squash 머지", "대기"]')"
run_case "strict: EN merge, no review"     block strict "$(build_payload '["merge now", "hold"]')"
run_case "strict: review present → pass"   pass  strict "$(build_payload '["머지", "codex 재실행", "대기"]')"

# strict-env value contract: ONLY `=1` (after strip) activates strict. Any
# disable-intent or non-1 value falls back to advisory (documented `=1` contract).
run_case "strict=1 with whitespace → block"  block "strictval= 1 " "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=no → advisory (not strict)"  advisory "strictval=no" "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=0 → advisory"                advisory "strictval=0" "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=false → advisory"            advisory "strictval=false" "$(build_payload '["squash 머지", "대기"]')"
run_case "strict=true → advisory (only 1)"    advisory "strictval=true" "$(build_payload '["squash 머지", "대기"]')"

# ---------------------------------------------------------------------------
# (e) PASS — non-AskUserQuestion tool / malformed payload (fail-open)
# ---------------------------------------------------------------------------
run_case "non-AskUserQuestion tool"        pass default '{"tool_name":"Bash","tool_input":{"command":"git merge"}}'
run_case "malformed JSON payload"          pass default 'not-json-at-all'
run_case "tool_input not a dict"           pass default '{"tool_name":"AskUserQuestion","tool_input":"oops"}'
run_case "questions missing"               pass default '{"tool_name":"AskUserQuestion","tool_input":{}}'

# ---------------------------------------------------------------------------
# (f) multi-question payload — merge token in 2nd question, no review
# ---------------------------------------------------------------------------
MULTIQ='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q1","header":"a","options":[{"label":"Plan A","description":"d"}]},{"question":"q2","header":"b","options":[{"label":"squash 머지","description":"d"},{"label":"대기","description":"d"}]}]}}'
run_case "multi-question, merge in q2, no review" advisory default "$MULTIQ"

# ---------------------------------------------------------------------------
# (g) documented-guarantee pins (spec "Known limitations")
# ---------------------------------------------------------------------------
# label-only detection: review intent in the *description* is NOT counted →
# the menu still nudges (review option must live in the label).
DESC_ONLY_REVIEW='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q","header":"h","options":[{"label":"squash 머지","description":"or run a codex review first"},{"label":"대기","description":"d"}]}]}}'
run_case "review only in description → still nudges" advisory default "$DESC_ONLY_REVIEW"

# preview contains the substring 'review' → suppresses (documented safe direction)
run_case "preview substring suppresses (documented)" pass default "$(build_payload '["merge after preview", "hold"]')"

# KO inflected non-gate label 머지된 still triggers (documented bare-token cost)
run_case "KO inflected 머지된 triggers (documented)"  advisory default "$(build_payload '["머지된 PR 목록 보기", "취소"]')"

# ---------------------------------------------------------------------------
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
