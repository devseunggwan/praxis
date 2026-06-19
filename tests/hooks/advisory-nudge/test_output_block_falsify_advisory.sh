#!/usr/bin/env bash
# test_output_block_falsify_advisory.sh — coverage for output-block-falsify-advisory hook
#
# Synthesizes Claude Code PreToolUse payloads and asserts:
#   advisory → exit 0 + stderr non-empty (contains advisory keyword)
#   pass     → exit 0 + stderr empty
#
# Usage: bash tests/test_output_block_falsify_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/output-block-falsify-advisory/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload
#   expectation:
#     "advisory:<substring>" — exit 0 + stderr contains <substring>
#     "ask:<substring>"       — exit 0 + stdout JSON has permissionDecision:ask + substring
#     "deny:<substring>"      — exit 0 + stdout JSON has permissionDecision:deny + substring  (issue #393)
#     "pass"                  — exit 0 + stderr empty
run_case() {
  local name="$1" expectation="$2" payload="$3"

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  printf '%s' "$payload" | "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    advisory:*)
      local needle="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$needle"*) ;;
        *) ok=0 ;;
      esac
      ;;
    ask:*|deny:*)
      local expected_decision="${expectation%%:*}"
      local needle="${expectation#*:}"
      [ "$rc" -eq 0 ] || ok=0
      local decision
      decision=$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    h = d.get('hookSpecificOutput', {})
    print(h.get('permissionDecision', ''))
except Exception:
    print('')
" "$out" 2>/dev/null)
      [ "$decision" = "$expected_decision" ] || ok=0
      case "$out" in
        *"$needle"*) ;;
        *) ok=0 ;;
      esac
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

make_ask_payload() {
  # $1 = JSON array of option label strings (already JSON-encoded)
  python3 -c "
import json, sys
labels = json.loads(sys.argv[1])
options = [{'label': l} for l in labels]
payload = {
    'session_id': 'test-session',
    'tool_name': 'AskUserQuestion',
    'tool_input': {
        'questions': [
            {
                'question': 'What should we do?',
                'options': options,
            }
        ]
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1"
}

make_ask_payload_with_question() {
  # $1 = JSON array of option label strings (already JSON-encoded)
  # $2 = question text (plain string)
  python3 -c "
import json, sys
labels = json.loads(sys.argv[1])
question_text = sys.argv[2]
options = [{'label': l} for l in labels]
payload = {
    'session_id': 'test-session',
    'tool_name': 'AskUserQuestion',
    'tool_input': {
        'questions': [
            {
                'question': question_text,
                'options': options,
            }
        ]
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1" "$2"
}

make_ask_payload_multi_question_bypass() {
  # Q1 has (Recommended) + no Falsified:; Q2 has Falsified: in its question text.
  # A per-question check must still escalate to ask for Q1.
  python3 - <<'PYEOF'
import json
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "Which approach?",
                "options": [{"label": "Option A (Recommended)"}, {"label": "Option B"}],
            },
            {
                "question": "Falsified: no existing PR found.\nAnother question?",
                "options": [{"label": "Yes"}, {"label": "No"}],
            },
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PYEOF
}

make_ask_payload_description_only() {
  # Payload where (Recommended) appears in options[].description, NOT in label.
  # Issue #369: T2 (confidence-anchoring) now scans description too, so this
  # payload triggers an ask via the bare `recommended` token in description.
  python3 -c "
import json
payload = {
    'session_id': 'test-session',
    'tool_name': 'AskUserQuestion',
    'tool_input': {
        'questions': [
            {
                'question': 'Which approach?',
                'options': [
                    {'label': 'Option A', 'description': 'This is the (Recommended) path.'},
                    {'label': 'Option B', 'description': 'Alternative'},
                ],
            }
        ]
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
"
}

make_ask_payload_with_descriptions() {
  # $1 = JSON array of option label strings
  # $2 = JSON array of option description strings (same length as labels)
  # $3 = question text
  python3 -c "
import json, sys
labels = json.loads(sys.argv[1])
descs = json.loads(sys.argv[2])
question_text = sys.argv[3]
options = [{'label': l, 'description': d} for l, d in zip(labels, descs)]
payload = {
    'session_id': 'test-session',
    'tool_name': 'AskUserQuestion',
    'tool_input': {
        'questions': [
            {
                'question': question_text,
                'options': options,
            }
        ]
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1" "$2" "$3"
}

make_bash_payload() {
  # $1 = command string
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session',
    'tool_name': 'Bash',
    'tool_input': {
        'command': sys.argv[1],
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1"
}

# ---------------------------------------------------------------------------
# AskUserQuestion positive cases
# ---------------------------------------------------------------------------

run_case "AskUserQuestion: (Recommended) English — no Falsified: — T1 blocks (issue #393)" \
  "deny:Falsified:" \
  "$(make_ask_payload '["Option A (Recommended)", "Option B"]')"

run_case "AskUserQuestion: (추천) Korean — no Falsified: — T1 blocks (issue #393)" \
  "deny:Falsified:" \
  "$(make_ask_payload '["옵션 A (추천)", "옵션 B"]')"

run_case "AskUserQuestion: (recommended) lowercase — T2 escalates to ask (issue #369)" \
  "ask:Falsified:" \
  "$(make_ask_payload '["use existing approach (recommended)"]')"

# ---------------------------------------------------------------------------
# AskUserQuestion negative cases
# ---------------------------------------------------------------------------

run_case "AskUserQuestion: no marker — silent pass" \
  pass \
  "$(make_ask_payload '["Option A", "Option B", "Option C"]')"

run_case "AskUserQuestion: empty options — silent pass" \
  pass \
  "$(make_ask_payload '[]')"

# ---------------------------------------------------------------------------
# Bash positive cases
# ---------------------------------------------------------------------------

run_case "Bash: merge all — English bulk phrase fires" \
  "advisory:output-block-falsify-advisory" \
  "$(make_bash_payload 'gh pr merge --all  # merge all open PRs')"

run_case "Bash: close all — English bulk phrase fires" \
  "advisory:output-block-falsify-advisory" \
  "$(make_bash_payload 'close all open issues via gh cli')"

run_case "Bash: delete all — English bulk phrase fires" \
  "advisory:output-block-falsify-advisory" \
  "$(make_bash_payload 'aws s3 rm s3://bucket/ --recursive # delete all objects')"

run_case "Bash: 모두 삭제 — Korean substring fires" \
  "advisory:output-block-falsify-advisory" \
  "$(make_bash_payload 'gh issue list | xargs gh issue close  # 모두 삭제')"

run_case "Bash: 전부 머지 — Korean substring fires" \
  "advisory:output-block-falsify-advisory" \
  "$(make_bash_payload '# 전부 머지 처리')"

run_case "Bash: 다 머지 — Korean substring fires" \
  "advisory:output-block-falsify-advisory" \
  "$(make_bash_payload 'echo "다 머지 할게요"')"

# ---------------------------------------------------------------------------
# Bash negative cases
# ---------------------------------------------------------------------------

run_case "Bash: git status — silent pass" \
  pass \
  "$(make_bash_payload 'git status')"

run_case "Bash: gh pr list — read-only, no bulk mutation — silent pass" \
  pass \
  "$(make_bash_payload 'gh pr list --state open')"

run_case "Bash: git log --all — --all flag but not a bulk mutation — silent pass" \
  pass \
  "$(make_bash_payload 'git log --all --oneline')"

# Codex #225 P3: word-boundary regression — `disclose all` / `enclose all`
# must NOT match the `close all` substring.
run_case "Bash: disclose all — word-boundary regression — silent pass" \
  pass \
  "$(make_bash_payload 'echo we will disclose all findings')"

run_case "Bash: enclose all — word-boundary regression — silent pass" \
  pass \
  "$(make_bash_payload 'echo enclose all attachments in the email')"

# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

run_case "Edge: malformed JSON stdin — silent pass" \
  pass \
  'not valid json at all'

run_case "Edge: empty JSON object — silent pass" \
  pass \
  '{}'

run_case "Edge: unknown tool_name — silent pass" \
  pass \
  "$(python3 -c 'import json; print(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}}))')"

# Codex #225 P2: fail-open on non-string command (number instead of string).
# Hook contract: advisory hooks NEVER break tool execution on malformed payloads.
run_case "Edge: non-string command (int) — fail-open silent pass" \
  pass \
  '{"tool_name":"Bash","tool_input":{"command":123}}'

run_case "Edge: non-string command (null) — fail-open silent pass" \
  pass \
  '{"tool_name":"Bash","tool_input":{"command":null}}'

# ---------------------------------------------------------------------------
# (Recommended) escalation cases — issue #290
# ---------------------------------------------------------------------------

# Case 1: (Recommended) label + Falsified: line present → PASS (silent)
run_case "AskUserQuestion: (Recommended) + Falsified: line in question body → pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B"]' \
      "Falsified: checked no existing PR for this — none found.
What should we do?")"

# Case 2 (updated by issue #393): (Recommended) label + no Falsified: → DENY (block)
run_case "AskUserQuestion: (Recommended) + no Falsified: → T1 deny (issue #393)" \
  "deny:Falsified:" \
  "$(make_ask_payload '["Best option (Recommended)", "Alternative"]')"

# Case 3 (updated by issue #369): (Recommended) in description-only is now
# scanned by T2 (bare `recommended` token, label OR description). Original
# expectation `pass` upgraded to `ask` — false-positive guard was over-
# conservative and let confidence-anchoring framing bypass the gate.
run_case "AskUserQuestion: (Recommended) in description only — T2 escalates to ask (issue #369)" \
  "ask:Falsified:" \
  "$(make_ask_payload_description_only)"

# Case 4 (updated by issue #393): (추천) Korean label + no Falsified: → DENY (block)
run_case "AskUserQuestion: (추천) Korean label + no Falsified: → T1 deny (issue #393)" \
  "deny:Falsified:" \
  "$(make_ask_payload '["권장 방법 (추천)", "대안"]')"

# Case 5: Non-recommended option — no (Recommended) label — silent pass (no advisory)
run_case "AskUserQuestion: non-recommended option labels — silent pass" \
  pass \
  "$(make_ask_payload '["Option A", "Option B", "Option C"]')"

# Case 6 (regression for P2 fix; updated by issue #393): multi-question payload
# where Q1 has (Recommended) but no Falsified:, and Q2 has Falsified: in its own
# question text — Q1 still fires T1 → DENY (block).
run_case "AskUserQuestion: multi-question — Falsified: in Q2 does not cover Q1 (Recommended) → T1 deny" \
  "deny:Falsified:" \
  "$(make_ask_payload_multi_question_bypass)"

# ---------------------------------------------------------------------------
# T2 confidence-anchoring framing cases — issue #369
# ---------------------------------------------------------------------------

# In-vivo regression: description has "가장 안전한" (the exact framing that
# bypassed T1 in a real session — anchoring framing placed in description).
run_case "T2: KO '가장 안전한' in description — no Falsified → ask" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["S1 only", "S1+S2"]' \
      '["가장 안전한 1회 변경", "Faster but more scope"]' \
      'Phase 1?')"

# EN single-word anchoring in label.
run_case "T2: EN 'safer' in label — no Falsified → ask" \
  "ask:Falsified:" \
  "$(make_ask_payload '["A safer rollout", "B aggressive"]')"

# EN single-word anchoring in label.
run_case "T2: EN 'safest' in label — no Falsified → ask" \
  "ask:Falsified:" \
  "$(make_ask_payload '["Option A — safest", "Option B"]')"

# KO single-word anchoring in description.
run_case "T2: KO '자연스러운' in description → ask" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["Approach A", "Approach B"]' \
      '["자연스러운 진행", "주류 패턴"]' \
      'Pick one?')"

# EN multi-word anchoring in label.
run_case "T2: EN 'prefer this' in label — no Falsified → ask" \
  "ask:Falsified:" \
  "$(make_ask_payload '["X (prefer this)", "Y"]')"

# EN 'obvious choice' multi-word anchoring.
run_case "T2: EN 'obvious choice' in description → ask" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["A", "B"]' \
      '["the obvious choice for new repos", "alt"]' \
      'Setup?')"

# Mixed Hangul/ASCII — ASCII lookaround must not match across boundary.
run_case "T2: mixed Hangul/ASCII 'prefer this 옵션' → ask" \
  "ask:Falsified:" \
  "$(make_ask_payload '["prefer this 옵션을 사용", "대안"]')"

# T2 satisfaction by Falsified: line — silent pass.
run_case "T2: KO '가장 안전한' + Falsified: line → pass" \
  pass \
  "$(make_ask_payload_with_descriptions \
      '["S1 only", "S1+S2"]' \
      '["가장 안전한 1회 변경", "Faster"]' \
      'Falsified: critic spawn check confirmed — no anchoring bypass.
Phase 1?')"

# T2 negative — token-like substring inside an unrelated word must not fire.
# `disclosure` contains 'closur' but no anchoring tokens; ensure no spurious
# match on words that happen to share letters with anchoring tokens.
run_case "T2: negative — 'preferential treatment' not in token list — pass" \
  pass \
  "$(make_ask_payload '["preferential treatment of edge cases", "alternative"]')"

# T2 negative — `safe` alone is not in the token set (only `safer`/`safest`).
run_case "T2: negative — bare 'safe' not in token set — pass" \
  pass \
  "$(make_ask_payload '["safe path verified", "untested"]')"

# T2 word-boundary regression — `unsafer` must not match `safer`.
run_case "T2: word-boundary — 'unsafer' is not safer — pass" \
  pass \
  "$(make_ask_payload '["an unsafer path", "the other"]')"

# T2 ANCHORING_ASK_MSG content (verify the new message variant is emitted).
run_case "T2: KO '안전한' triggers ANCHORING_ASK_MSG (not ASK_MSG)" \
  "ask:confidence-anchoring" \
  "$(make_ask_payload '["가장 안전한 옵션", "alt"]')"

# T1 precedence over T2 — when label has literal (Recommended) AND
# description has confidence-anchoring, T1 fires first.
# Updated by issue #393: T1 now emits deny (not ask). The T1 message
# (ASK_MSG content, "Self-Falfify" marker) is still emitted, but the
# decision is deny instead of ask.
run_case "T1>T2 precedence: literal (Recommended) + anchoring desc → T1 deny (issue #393)" \
  "deny:Self-Falsify" \
  "$(make_ask_payload_with_descriptions \
      '["X (Recommended)", "Y"]' \
      '["가장 안전한 path", "alt"]' \
      'Which?')"

# Multi-question T2-then-T1 precedence — Q1 fires T2 (confidence-anchoring),
# Q2 fires T1 (literal Recommended). Pre-fix bug: loop broke on Q1's T2 and
# emitted ask, allowing user to bypass the T1 hard block. Post-fix: T2 records
# but keeps scanning; Q2's T1 upgrades the decision to deny.
make_ask_payload_t2_then_t1() {
  python3 - <<'PYEOF'
import json
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "Q1: which approach?",
                "options": [
                    {"label": "Option A (safer)", "description": "natural choice"},
                    {"label": "Option B", "description": "alt"},
                ],
            },
            {
                "question": "Q2: which path?",
                "options": [
                    {"label": "Path X (Recommended)", "description": "T1 marker"},
                    {"label": "Path Y", "description": "alt"},
                ],
            },
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PYEOF
}
run_case "T2→T1 multi-question: Q1 anchoring, Q2 literal (Recommended) → deny (issue #393 fix)" \
  "deny:Self-Falsify" \
  "$(make_ask_payload_t2_then_t1)"

# ---------------------------------------------------------------------------
# Line-start hint cases — issue #598
# ---------------------------------------------------------------------------

# T1: (Recommended) label + Falsified: appears MID-LINE (not at column 0)
# → hook must still block (deny) AND the message must contain the line-start hint.
# Needle uses the ASCII word "startswith" which appears literally in the JSON output
# (Korean is Unicode-escaped by json.dumps, so Korean needle won't match in shell).
run_case "T1: Falsified: mid-line does not satisfy startswith check — still deny + hint" \
  "deny:startswith" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B"]' \
      "이 선택이 A가 B의 선행. Falsified: checked prior PRs — none exist. 계속?")"

# T2: confidence-anchoring label + Falsified: appears MID-LINE
# → hook must still block (ask) AND the message must contain the line-start hint.
run_case "T2: Falsified: mid-line does not satisfy startswith check — still ask + hint" \
  "ask:startswith" \
  "$(make_ask_payload_with_descriptions \
      '["S1 only", "S1+S2"]' \
      '["가장 안전한 1회 변경", "Faster"]' \
      "단계별로 진행. Falsified: no duplicate PR found. 어떻게 할까요?")"

# ---------------------------------------------------------------------------
# Template-level guidance cases — issue #682
# ---------------------------------------------------------------------------

# T1: (Recommended) without Falsified: → deny message must contain the
# [pre-author-template] marker, proving the message is template-level
# (instructs Claude to fix the compose template, not just this instance).
run_case "T1 (issue #682): (Recommended) + no Falsified: → deny contains pre-author-template" \
  "deny:pre-author-template" \
  "$(make_ask_payload '["Option A (Recommended)", "Option B"]')"

# T2: confidence-anchoring without Falsified: → ask message must contain the
# [pre-author-template] marker for the same template-level reason.
run_case "T2 (issue #682): safer label + no Falsified: → ask contains pre-author-template" \
  "ask:pre-author-template" \
  "$(make_ask_payload '["A safer rollout", "B aggressive"]')"

# Regression: a compliant call (Recommended) + column-0 Falsified: — must
# still silent-pass (the template-level message change must not break this).
run_case "T1 (issue #682): (Recommended) + column-0 Falsified: → silent pass (regression)" \
  pass \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B"]' \
      "Falsified: checked existing PRs — none found.
What should we do?")"

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------

# main() opts into the shared @fail_open guard; verify the decorator is
# applied (fail-open behaviour itself is covered in tests/test_hook_runtime.sh).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ ${#FAILED_NAMES[@]} -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
