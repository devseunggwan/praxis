#!/usr/bin/env bash
# test_output_block_falsify_advisory.sh — coverage for output-block-falsify-advisory hook
#
# Synthesizes Claude Code PreToolUse payloads and asserts:
#   advisory → exit 0 + stderr non-empty (contains advisory keyword)
#   pass     → exit 0 + stderr empty + stdout empty (issue #787 round-6: a
#              deny/ask decision also exits 0 with empty stderr, so stdout
#              must be checked too or a real block would misreport as pass)
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

# Test isolation (issue #787 codex round-6 P2): dozens of run_case calls
# below trigger T1/T2 deny/ask decisions, each of which writes a RICH
# telemetry record. Without a default override, those records land in the
# REAL ~/.praxis/telemetry/fire-events-*.jsonl store this suite runs
# against — polluting the exact block-rate signal issue #787 added
# telemetry to measure accurately. Point every hook invocation in this
# script at a throwaway file by default; the telemetry-content-checking
# cases further down still override PRAXIS_FIRE_TELEMETRY_FILE per-call to
# their own TEL_DIR paths, which takes precedence over this export for
# that single invocation. Cleaned up by the trap registered alongside
# TEL_DIR below (a single EXIT trap covers both).
DEFAULT_TEL_FILE="$(mktemp)"
export PRAXIS_FIRE_TELEMETRY_FILE="$DEFAULT_TEL_FILE"

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload
#   expectation:
#     "advisory:<substring>" — exit 0 + stderr contains <substring>
#     "ask:<substring>"       — exit 0 + stdout JSON has permissionDecision:ask + substring
#     "ask:<substring>"      — exit 0 + stdout JSON has permissionDecision:ask + substring  (issue #899)
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
      local decision reason
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
      reason=$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    h = d.get('hookSpecificOutput', {})
    reason = h.get('permissionDecisionReason', '')
    print(reason if isinstance(reason, str) else '')
except Exception:
    print('')
" "$out" 2>/dev/null)
      case "$reason" in
        *"$needle"*) ;;
        *) ok=0 ;;
      esac
      ;;
    pass)
      # Issue #787 codex round-6 P2: deny/ask decisions also exit 0 with
      # empty stderr (they signal via a stdout JSON permissionDecision, not
      # stderr) — checking stderr alone cannot distinguish a real silent
      # pass from an undetected deny/ask. stdout must be empty too.
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      [ -z "$out" ]   || ok=0
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
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>} stdout=${out:-<empty>}"
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

run_case "AskUserQuestion: (Recommended) English — no Falsified: — T1 gates (issue #899)" \
  "ask:Falsified:" \
  "$(make_ask_payload '["Option A (Recommended)", "Option B"]')"

run_case "AskUserQuestion: (추천) Korean — no Falsified: — T1 gates (issue #899)" \
  "ask:Falsified:" \
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
run_case "AskUserQuestion: (Recommended) + no Falsified: → T1 ask (issue #393)" \
  "ask:A '(Recommended)' marker is present" \
  "$(make_ask_payload '["Best option (Recommended)", "Alternative"]')"

# Case 2b (issue #828): Falsified: line in the triggering option's OWN
# description field (not the question body) also satisfies T1 — the
# question field itself stays short, which is the trigger-reduction this
# issue exists for.
run_case "AskUserQuestion: (Recommended) + Falsified: in option description (not body) → pass (issue #828)" \
  pass \
  "$(make_ask_payload_with_descriptions \
      '["Option A (Recommended)"]' \
      '["Falsified: Option A (Recommended) — probe: grep for existing PR → none found; premise survives because no in-flight PR covers this."]' \
      'Which approach?')"

# Case 2c (issue #828 regression): per-label coverage still enforced when
# evidence is supplied via description — 2 triggering options, only one
# covered (via its own description), the other has no Falsified: line
# anywhere → still gates.
run_case "AskUserQuestion: 2x (Recommended), only one covered via description → still T1 gates (issue #828)" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      '["Falsified: Option A (Recommended) — probe: grep → none found; premise survives because none.", "plain description, no evidence"]' \
      'Which approach?')"

# Case 2d (issue #828): T2 (confidence-anchoring) also satisfied via a
# Falsified: line placed in the anchoring option's own description field
# (the line must still start at column 0 within the field, same contract
# as the question-body path — a leading newline puts it there).
run_case "T2: 'safer' + Falsified: line in same description field → pass (issue #828)" \
  pass \
  "$(make_ask_payload_with_descriptions \
      '["Option A", "Option B"]' \
      "$(python3 -c 'import json; print(json.dumps(["safer choice.\nFalsified: Option A — probe: ran both, no measurable difference; premise survives because none.", "Alternative"]))')" \
      'Which approach?')"

# Case 2e (issue #828 codex review P2 — self-referential label bypass):
# a single (Recommended) option whose own LABEL is crafted to read as a
# clean `Falsified:` line (no `description` at all) must NOT satisfy the
# gate. Only `description` is a valid evidence source — `label` is the
# same attacker/model-controlled field the (Recommended) marker itself
# lives in, so it must never double as the evidence proving that marker's
# premise. Regression for a real bypass caught by codex review (single-
# label mode: "any clean Falsified: line anywhere" previously included
# `collect_option_texts()`'s label text).
run_case "AskUserQuestion: label itself crafted as a Falsified: line, no description → still T1 gates (issue #828 codex P2)" \
  "ask:Falsified:" \
  "$(make_ask_payload \
      '["Falsified: Option A (Recommended) (Recommended) — probe: fake → fake; premise survives because fake"]')"

# Case 2f (issue #828 codex review round-2 P2 — cross-option description
# bypass): a single triggering (Recommended) option with NO description of
# its own must NOT be satisfied by a DIFFERENT, non-triggering option's
# unrelated `Falsified:` line. Before this fix, adding every option's
# description unconditionally to q_texts let single-label mode ("any clean
# line anywhere satisfies") accept evidence that never addressed the
# actual triggering option at all — the triggering option's premise was
# never falsified. Only a triggering option's OWN description counts.
run_case "AskUserQuestion: non-triggering option's unrelated Falsified: description does not cover a different triggering option → still T1 gates (issue #828 codex round-2 P2)" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["Option A (Recommended)", "Option B"]' \
      '[null, "Falsified: totally unrelated evidence about something else — probe: n/a → n/a; premise survives because n/a"]' \
      'Which approach?')"

# Case 2g (issue #828 codex review round-3 P2 — same-normalized-label
# cross-option collision): two DIFFERENT options can share the same
# normalized label (e.g. "Option  A" double-space vs "Option A"). Before
# this fix, evidence collection matched by `_normalize_label(label) in
# triggering_norm` (a string set) rather than option identity, so a
# non-triggering option's unrelated `Falsified:` description satisfied a
# DIFFERENT, T2-triggering option that merely shared the same normalized
# label and carried no evidence of its own. Index-based matching
# (`_t1_triggering_indices` / `_t2_triggering_indices`) closes this: two
# distinct options never share the same index.
run_case "AskUserQuestion: non-triggering option sharing a normalized label with the triggering option does not supply its evidence → still T2 ask (issue #828 codex round-3 P2)" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["Option  A", "Option A"]' \
      '["safer path overall", "Falsified: totally unrelated evidence about something else — probe: n/a → n/a; premise survives because n/a"]' \
      'Which approach?')"

# Case 3 (updated by issue #369): (Recommended) in description-only is now
# scanned by T2 (bare `recommended` token, label OR description). Original
# expectation `pass` upgraded to `ask` — false-positive guard was over-
# conservative and let confidence-anchoring framing bypass the gate.
run_case "AskUserQuestion: (Recommended) in description only — T2 escalates to ask (issue #369)" \
  "ask:Falsified:" \
  "$(make_ask_payload_description_only)"

# Case 4 (updated by issue #393): fixed guidance remains English, while the
# copy-paste-ready scaffold preserves the user-supplied Korean label verbatim.
run_case "AskUserQuestion: (추천) Korean label + no Falsified: → T1 ask (issue #393)" \
  "ask:Falsified: 권장 방법 (추천) — probe:" \
  "$(make_ask_payload '["권장 방법 (추천)", "대안"]')"

# Case 5: Non-recommended option — no (Recommended) label — silent pass (no advisory)
run_case "AskUserQuestion: non-recommended option labels — silent pass" \
  pass \
  "$(make_ask_payload '["Option A", "Option B", "Option C"]')"

# Case 6 (regression for P2 fix; updated by issue #393): multi-question payload
# where Q1 has (Recommended) but no Falsified:, and Q2 has Falsified: in its own
# question text — Q1 still fires T1 → DENY (block).
run_case "AskUserQuestion: multi-question — Falsified: in Q2 does not cover Q1 (Recommended) → T1 ask" \
  "ask:Falsified:" \
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
  "ask:An option label or description contains a confidence-anchoring framing token" \
  "$(make_ask_payload '["가장 안전한 옵션", "alt"]')"

# T1 precedence over T2 — when label has literal (Recommended) AND
# description has confidence-anchoring, T1 fires first.
# Issue #393 raised T1 to deny; issue #899 restored it to ask. T1 still
# takes precedence over T2, so the T1 message
# (ASK_MSG content, "Self-Falfify" marker) is still emitted, but the
# is the one emitted.
run_case "T1>T2 precedence: literal (Recommended) + anchoring desc → T1 ask (issue #393)" \
  "ask:Self-Falsify" \
  "$(make_ask_payload_with_descriptions \
      '["X (Recommended)", "Y"]' \
      '["가장 안전한 path", "alt"]' \
      'Which?')"

# Multi-question T2-then-T1 precedence — Q1 fires T2 (confidence-anchoring),
# Q2 fires T1 (literal Recommended). Pre-fix bug: loop broke on Q1's T2 and
# emitted ask, allowing user to bypass the T1 hard block. Post-fix: T2 records
# but keeps scanning; Q2's T1 takes precedence over the T2 message.
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
run_case "T2→T1 multi-question: Q1 anchoring, Q2 literal (Recommended) → ask (issue #393 fix, downgraded #899)" \
  "ask:Self-Falsify" \
  "$(make_ask_payload_t2_then_t1)"

# ---------------------------------------------------------------------------
# Line-start hint cases — issue #598
# ---------------------------------------------------------------------------

# T1: (Recommended) label + Falsified: appears MID-LINE (not at column 0)
# → hook must still gate (ask) AND the message must contain the line-start hint.
# Needle uses the ASCII word "startswith" which appears literally in the JSON output
# (Korean is Unicode-escaped by json.dumps, so Korean needle won't match in shell).
run_case "T1: Falsified: mid-line does not satisfy startswith check — still gates + hint" \
  "ask:startswith" \
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
# Exact predicate guidance — issue #910
# ---------------------------------------------------------------------------

# Two triggering labels switch _has_falsified_line into multi-trigger mode.
# A generic column-0 line is not enough; the reason must expose the exact
# per-label contract instead of asking for another format-guessing retry.
run_case "T1 (issue #910): multi-trigger prefix mismatch → reason exposes per-label contract" \
  "ask:multi-trigger: one line per normalized full option label" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: checked all options against open PRs — none found.
Which one?")"

# A near-match that drops the marker suffix must remain blocked, and the
# reason must say that marker suffixes are part of the full label.
run_case "T1 (issue #910): missing marker suffix → reason names suffix requirement" \
  "ask:including marker suffixes" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A — probe: gh pr list --search a → none found
Falsified: Option B — probe: gh pr list --search b → none found
Which one?")"

# T2 uses the same _has_falsified_line predicate, so its message must expose
# the same exact label-boundary rule rather than drifting from T1.
run_case "T2 (issue #910): multi-trigger mismatch → reason exposes label boundary" \
  "ask:followed by end-of-line or" \
  "$(make_ask_payload_with_descriptions \
      '["A safer rollout", "B safest rollout"]' \
      '["safer for staged release", "safest for direct release"]' \
      "Falsified: A safer rollout later — probe: checked A → no match
Falsified: B safest rollout later — probe: checked B → no match
Which one?")"

# ---------------------------------------------------------------------------
# Template-level guidance cases — issue #682
# ---------------------------------------------------------------------------

# T1: (Recommended) without Falsified: → gate message must contain the
# [pre-author-template] marker, proving the message is template-level
# (instructs Claude to fix the compose template, not just this instance).
run_case "T1 (issue #682): (Recommended) + no Falsified: → ask contains pre-author-template" \
  "ask:pre-author-template" \
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
# Ready-to-fill scaffold cases — issue #787
# ---------------------------------------------------------------------------

# T1: gate message must embed a copy-paste-ready Falsified: line seeded with
# the triggering option label, not just the generic instruction text.
run_case "T1 (issue #787): (Recommended) + no Falsified: → ask embeds ready-to-fill scaffold" \
  "ask:Falsified: Best option (Recommended)" \
  "$(make_ask_payload '["Best option (Recommended)", "Alternative"]')"

run_case "T1 (issue #787): scaffold section is marked with [scaffold]" \
  "ask:[scaffold]" \
  "$(make_ask_payload '["Best option (Recommended)", "Alternative"]')"

# T2: ask message must embed the same ready-to-fill scaffold, seeded with the
# anchoring-token-carrying label.
run_case "T2 (issue #787): safer label + no Falsified: → ask embeds ready-to-fill scaffold" \
  "ask:Falsified: A safer rollout" \
  "$(make_ask_payload '["A safer rollout", "B aggressive"]')"

# Structural check: 2 options both carrying the (Recommended) marker must
# produce 2 scaffold lines (one per triggering label), not just 1. Piped
# straight into python3's stdin (not embedded as a string literal) because
# the message text itself contains single quotes that would break a
# triple-quoted Python literal.
_scaffold_multi_result=$(make_ask_payload '["Option A (Recommended)", "Option B (Recommended)"]' | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
reason = d.get('hookSpecificOutput', {}).get('permissionDecisionReason', '')
# Assert the two label-seeded scaffold lines directly. Counting every
# instructional 'Falsified:' mention couples this behavior test to prose.
a_line = 'Falsified: Option A (Recommended) — probe: '
b_line = 'Falsified: Option B (Recommended) — probe: '
ok = reason.count(a_line) == 1 and reason.count(b_line) == 1
print('ok' if ok else 'fail_scaffold_lines')
" 2>/dev/null)
if [ "$_scaffold_multi_result" = "ok" ]; then
  echo "  PASS  2 triggering labels -> 2 scaffold Falsified: lines (issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  2 triggering labels -> 2 scaffold Falsified: lines (issue #787) ($_scaffold_multi_result)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("2 triggering labels -> 2 scaffold Falsified: lines")
fi

# Regression: no scaffold section when Falsified: is already present (pass path).
run_case "T1 (issue #787): (Recommended) + Falsified: present → no scaffold needed (silent pass)" \
  pass \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B"]' \
      "Falsified: checked no existing PR — none found.
What should we do?")"

# Anti-bypass regression (codex round-2 P1 fix): copy-pasting the scaffold
# line VERBATIM (placeholders unfilled) into the question body must NOT
# silently satisfy the gate — the placeholder line itself starts with
# "Falsified:", so a naive prefix check would let a probe-free retry through.
run_case "T1 (issue #787 round-2 P1): unfilled scaffold copy-paste does not satisfy gate — still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Best option (Recommended)", "Alternative"]' \
      "Falsified: Best option (Recommended) — probe: <command> → <observed>; premise survives because <...>
What should we do?")"

# Same anti-bypass check for T2 ask.
run_case "T2 (issue #787 round-2 P1): unfilled scaffold copy-paste does not satisfy gate — still ask" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["A safer rollout", "B aggressive"]' \
      '["", ""]' \
      "Falsified: A safer rollout — probe: <command> → <observed>; premise survives because <...>
Which one?")"

# Regression: once placeholders are actually filled in with real content
# (no literal <command>/<observed>/<...> left), the line DOES satisfy the
# gate — the fix must not make legitimate filled-in evidence unsatisfiable.
run_case "T1 (issue #787 round-2): scaffold filled in with real probe output → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Best option (Recommended)", "Alternative"]' \
      "Falsified: Best option (Recommended) — probe: gh pr list --search feature → no existing PR; premise survives because none found
What should we do?")"

# Anti-bypass regression (codex round-3 P1 fix): ONE question with 2
# triggering options -> 2 scaffold lines. Filling in only the FIRST one and
# leaving the second with unfilled placeholders must NOT satisfy the gate —
# _has_falsified_line's old "return True on first clean line" let a sibling
# option's fake evidence ride along unchecked.
run_case "T1 (issue #787 round-3 P1): 1 of 2 scaffold lines filled, other still placeholder → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Falsified: Option B (Recommended) — probe: <command> → <observed>; premise survives because <...>
Which one?")"

# Regression: BOTH scaffold lines filled in for the same 2-option question ->
# silent pass (the round-3 fix must not require MORE than "no placeholder
# tokens anywhere", it must still accept a fully-filled multi-line case).
run_case "T1 (issue #787 round-3): both scaffold lines fully filled → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Falsified: Option B (Recommended) — probe: gh pr list --search b → none found; premise survives because none found
Which one?")"

# Anti-bypass regression (codex round-4 P1 fix): ONE question with 2
# triggering options -> 2 scaffold lines required. Providing evidence for
# ONLY the first option and OMITTING the second line entirely (no
# placeholder text left anywhere) must NOT satisfy the gate — the round-3
# fix only rejected leftover placeholder tokens, but a deleted line has no
# placeholder to reject, so clean_count (1) must be compared against the
# number of triggering labels (2), not treated as boolean-satisfied.
run_case "T1 (issue #787 round-4 P1): 1 of 2 lines provided, other omitted → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Which one?")"

# Regression: a SINGLE triggering label with its one clean Falsified: line
# still satisfies the gate (round-4's required_count must default to 1 for
# the common single-option case, not silently demand more).
run_case "T1 (issue #787 round-4): single triggering label, 1 clean line → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Best option (Recommended)", "Alternative"]' \
      "Falsified: Best option (Recommended) — probe: gh pr list --search feature → no existing PR; premise survives because none found
What should we do?")"

# Anti-false-positive regression (codex round-5 P2 fix): when the OPTION
# LABEL ITSELF happens to contain a literal placeholder-looking substring
# (e.g. "<command>"), the scaffold seeds that label verbatim into the
# Falsified: line's prefix. The old whole-line scan flagged this forever,
# even after the actual probe/observed evidence past "— probe:" was
# genuinely filled in — a legitimate retry could never pass. Scoping the
# placeholder scan to the content after "— probe:" fixes this without
# weakening the anti-bypass guard (verified by the next case).
run_case "T1 (issue #787 round-5 P2): label contains literal <command>, evidence filled in → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Run <command> manually (Recommended)", "Alternative"]' \
      "Falsified: Run <command> manually (Recommended) — probe: gh pr list --search feature → no existing PR found; premise survives because none found.
What should we do?")"

# Regression: the anti-bypass guard must still fire when the EVIDENCE
# region (after "— probe:") is the unfilled part, even though the label
# prefix also happens to contain a placeholder-looking substring — proves
# round-5's fix narrowed the scan window without disabling it.
run_case "T1 (issue #787 round-5): label contains <command> AND evidence unfilled → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Run <command> manually (Recommended)", "Alternative"]' \
      "Falsified: Run <command> manually (Recommended) — probe: <command> → <observed>; premise survives because <...>
What should we do?")"

# Anti-false-positive regression (codex round-6 P2 fix): a LEGACY free-form
# Falsified line — no "— probe:" marker at all — whose own genuine wording
# happens to contain a literal placeholder-looking substring. Round-5 only
# scoped the scan window when the marker WAS present; a marker-less line
# still fell back to scanning the whole line, so this case still perma-
# blocked. Marker-less lines are not scaffold-shaped, so round-6 excludes
# them from placeholder scanning entirely (the real scaffold copy-paste
# bypass always contains the marker, since it is emitted verbatim).
run_case "T1 (issue #787 round-6 P2): legacy free-form line, no marker, contains <command> → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Run <command> manually (Recommended)", "Alternative"]' \
      "Falsified: checked how <command> is invoked here, does not apply to this case.
What should we do?")"

# Anti-bypass regression (codex round-6 P2 fix): a 2-option question where
# BOTH triggering options exist, but the SAME clean Falsified line is
# pasted TWICE instead of providing distinct evidence for the second
# option. required_count counted raw lines, so clean_count==2 satisfied
# the check without ever addressing Option B. Falsified lines are now
# deduped by exact text before counting, so the duplicate contributes
# nothing and the question still denies.
run_case "T1 (issue #787 round-6 P2): same clean line pasted twice for 2 options → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Which one?")"

# Regression: 2 DISTINCT clean lines for 2 DISTINCT triggering options must
# still silent-pass (round-6's dedup must not require exact label-text
# matching — it only rejects exact line-text duplicates).
run_case "T1 (issue #787 round-6): 2 distinct clean lines for 2 options → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Falsified: Option B (Recommended) — probe: gh pr list --search b → none found; premise survives because none found
Which one?")"

# Anti-bypass regression (codex round-7 P2 fix): ONE question mixes a T1
# option (exact "(Recommended)" marker) with a SEPARATE T2 option (a
# "safer" description, no marker). The old if/elif skipped the T2 check
# entirely once T1 matched for the question, so the T2 option's label
# never entered required_count — a retry providing evidence for ONLY the
# T1 option silent-passed with the T2 option's claim never verified.
run_case "T1 (issue #787 round-7 P2): mixed T1+T2 in one question, only T1 evidence → still gates" \
  "ask:Falsified: Option B" \
  "$(make_ask_payload_with_descriptions \
      '["Option A (Recommended)", "Option B"]' \
      '["", "This is the safer choice overall"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Which one?")"

# Regression: the SAME mixed question, with evidence for BOTH the T1 and
# T2 option, must silent-pass (round-7's fix must not require MORE than
# one clean line per genuinely distinct triggering label).
run_case "T1 (issue #787 round-7): mixed T1+T2 in one question, both evidence lines → silent pass" \
  pass \
  "$(make_ask_payload_with_descriptions \
      '["Option A (Recommended)", "Option B"]' \
      '["", "This is the safer choice overall"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Falsified: Option B — probe: checked B directly → not actually safer for this case; premise survives because verified
Which one?")"

# Regression (codex round-7 self-catch): T2's bare recommend(?:ed|s)?
# pattern also matches the literal word inside "(Recommended)", so BOTH
# options in a pure-T1 2-option question also match T2's confidence-
# anchoring check. Without excluding t1_triggering labels from
# t2_triggering, this double-counted each label into both t1_labels and
# t2_labels, producing 4 scaffold lines (2 duplicated) instead of 2 in the
# gate message for a case that has no genuine T2-only option at all.
_scaffold_no_dup_result=$(make_ask_payload '["Option A (Recommended)", "Option B (Recommended)"]' | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
reason = d.get('hookSpecificOutput', {}).get('permissionDecisionReason', '')
ok = reason.count('Falsified: Option A (Recommended)') == 1 and reason.count('Falsified: Option B (Recommended)') == 1
print('ok' if ok else 'a=' + str(reason.count('Falsified: Option A (Recommended)')) + ' b=' + str(reason.count('Falsified: Option B (Recommended)')))
" 2>/dev/null)
if [ "$_scaffold_no_dup_result" = "ok" ]; then
  echo "  PASS  pure-T1 2-option question -> no T2 double-count duplication in scaffold (issue #787 round-7)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  pure-T1 2-option question -> no T2 double-count duplication in scaffold (issue #787 round-7) ($_scaffold_no_dup_result)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("pure-T1 2-option question -> no T2 double-count duplication")
fi

# Anti-false-positive regression (codex round-8 P2 fix): an option LABEL
# that itself contains the literal "— probe:" delimiter substring and a
# placeholder-looking token (e.g. quoting another probe's shape) used to
# shift the scan to that label-internal, leftmost marker occurrence via
# find(), pulling trailing label text — including the placeholder token —
# into evidence_region and permanently hard-gating even after real
# evidence was filled in after the actual (rightmost) scaffold delimiter.
# rfind() now anchors on the real, last-inserted delimiter.
run_case "T1 (issue #787 round-8 P2): label itself embeds marker+placeholder, real evidence after real delimiter → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Investigate — probe: <command> approach (Recommended)", "Alternative"]' \
      "Falsified: Investigate — probe: <command> approach (Recommended) — probe: gh pr list --search x → none found; premise survives because none found
Which one?")"

# Anti-bypass regression (codex round-8 P2 fix): a 2-option question where
# the SAME real evidence line is pasted twice, with only a trailing-
# whitespace difference on the second copy. Exact-text dedup treated the
# two as DISTINCT, satisfying required_count=2 while Option B still had
# zero real evidence. Lines are now whitespace-normalized before dedup, so
# the whitespace-only copy still collapses to 1 clean line and the
# question still denies.
run_case "T1 (issue #787 round-8 P2): whitespace-only duplicate line does not satisfy 2nd option → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise  survives because none found
Falsified: Option A (Recommended) — probe: gh pr list --search a → none found; premise survives because none found
Which one?")"

# Anti-bypass regression (codex round-9 P1 fix): 2 DIFFERENTLY-WORDED clean
# lines that both address Option A, with Option B never addressed at all.
# The old count-only check treated 2 distinct lines as satisfying
# required_count=2 for a 2-option question regardless of which option each
# line actually verified — Option B's claim silently went unverified.
# Each clean line is now matched to a specific triggering label by its
# "Falsified: {label}" prefix, so 2 lines both prefixed with "Option A"
# leave Option B uncovered and the question still denies.
run_case "T1 (issue #787 round-9 P1): 2 differently-worded lines, both Option A, Option B uncovered → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a -> none found; premise survives (first check)
Falsified: Option A (Recommended) — probe: gh issue list --search a -> also none found; premise survives (second check, still Option A only)
Which one?")"

# Regression: the SAME 2-option question with each option addressed by its
# OWN, differently-worded line must still silent-pass — the round-9 P1 fix
# maps lines to labels by prefix match, it does not require identical
# wording or line count beyond one-per-label.
run_case "T1 (issue #787 round-9 P1): each option addressed by its own distinctly-worded line → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Option B (Recommended)"]' \
      "Falsified: Option A (Recommended) — probe: gh pr list --search a -> none found; premise survives (first check)
Falsified: Option B (Recommended) — probe: gh issue list --search b -> also none found; premise survives (second, distinct check)
Which one?")"

# Anti-bypass regression (codex round-9 P2 fix): the EVIDENCE text itself
# (legitimately placed after the real scaffold delimiter) contains a
# second literal "— probe:" substring, with an unfilled "<command>"
# placeholder sitting BEFORE that spurious marker but still within the
# real evidence region. round-8's rfind() anchored on the LAST "— probe:"
# occurrence — the spurious one — so the leading placeholder fell outside
# the scanned window and silently passed. The delimiter is now found via
# find() seeded right after the matched label prefix, so the FIRST
# "— probe:" after the label (the real, scaffold-inserted one) is used
# regardless of how many more "— probe:"-shaped substrings appear later in
# the evidence text — the leading placeholder is caught.
run_case "T1 (issue #787 round-9 P2): evidence embeds a second marker, unfilled placeholder before it → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Alternative"]' \
      "Falsified: Option A (Recommended) — probe: <command> ran — probe: actually done
What should we do?")"

# Regression: the SAME evidence-embedded-second-marker shape, but with the
# real evidence genuinely filled in (no placeholder anywhere before the
# spurious marker) must still silent-pass — the round-9 P2 fix narrows the
# anchor point, it does not forbid legitimate evidence from containing the
# word sequence "— probe:" again.
run_case "T1 (issue #787 round-9 P2): evidence embeds a second marker, no placeholder anywhere → silent pass" \
  pass \
  "$(make_ask_payload_with_question \
      '["Option A (Recommended)", "Alternative"]' \
      "Falsified: Option A (Recommended) — probe: ran gh pr list — probe: actually done, no PR found
What should we do?")"

# Anti-bypass regression (codex round-10 P2 fix): a bare (no-marker) T2
# label ("Rerun") happens to be a literal STRING PREFIX of unrelated
# evidence text ("Rerunning without confirmation") on the OTHER
# triggering option's line. round-9's `line.startswith(f"Falsified:
# {label}")` matched "Rerun" against "Rerunning..." (since "Runtime" ...
# "Rerunning" both continue the shorter label with more letters), wrongly
# crediting "Rerun" as covered even though that line never actually
# addresses it — the real "Rerun" claim stayed unverified.
run_case "T1+T2 (issue #787 round-10 P2): bare label is a string-prefix of unrelated evidence text → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["Merge (Recommended)", "Rerun"]' \
      '["", "This is the safer choice overall"]' \
      "Falsified: Merge (Recommended) — probe: gh pr list --search x -> none found; premise survives
Falsified: Rerunning without confirmation — probe: gh issue list -> none found; premise survives
Which one?")"

# Regression: the SAME bare-label-is-a-prefix shape, but the second line
# genuinely addresses "Rerun" as its own whole-token label (the marker's
# leading space forms the required boundary right after the label) must
# still silent-pass — the round-10 fix requires a boundary after the
# label, it does not forbid the label from ever matching.
run_case "T1+T2 (issue #787 round-10 P2): bare label genuinely addressed by its own line → silent pass" \
  pass \
  "$(make_ask_payload_with_descriptions \
      '["Merge (Recommended)", "Rerun"]' \
      '["", "This is the safer choice overall"]' \
      "Falsified: Merge (Recommended) — probe: gh pr list --search x -> none found; premise survives
Falsified: Rerun — probe: checked rerun path -> safe; premise survives
Which one?")"

# Anti-bypass regression (codex round-11 P2 fix): an option LABEL itself
# contains a literal embedded newline. `_falsified_scaffold` previously
# interpolated the raw label verbatim, so the gate message's scaffold hint
# split across two printed lines — a verbatim copy-paste of that unfilled
# scaffold then had its placeholder tokens (`<command>` etc.) land on the
# SECOND physical line, which does not start with `Falsified:` and is
# invisible to the per-line scan, silently passing. Labels are now
# whitespace-normalized (embedded newlines collapsed to spaces) at the
# point they enter the triggering pipeline, so the gate message's scaffold
# is guaranteed to stay a single physical line regardless of what the raw
# option label contains — verified here by checking the scaffold segment
# contains the newline-joined label as ONE line, with no bare "Falsified:
# Multi" line missing its own evidence marker.
_newline_label_scaffold_result=$(make_ask_payload '["Multi\nline label (Recommended)", "Alternative"]' | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
reason = d.get('hookSpecificOutput', {}).get('permissionDecisionReason', '')
lines = reason.split('[scaffold]', 1)[-1].splitlines()
falsified_lines = [l for l in lines if l.startswith('Falsified:')]
ok = (
    len(falsified_lines) == 1
    and 'Multi line label (Recommended)' in falsified_lines[0]
    and '— probe:' in falsified_lines[0]
)
print('ok' if ok else 'falsified_lines=' + repr(falsified_lines))
")
if [ "$_newline_label_scaffold_result" = "ok" ]; then
  echo "  PASS  option label with embedded newline -> scaffold stays a single physical line (issue #787 round-11 P2)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  option label with embedded newline -> scaffold stays a single physical line (issue #787 round-11 P2) ($_newline_label_scaffold_result)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("option label with embedded newline -> single-line scaffold")
fi

# Regression: the SAME newline-bearing label, with the (now-guaranteed
# single-line) scaffold copy-pasted verbatim and left unfilled, must still
# gate — the round-11 fix must not accidentally make the placeholder guard
# unreachable for labels that originally contained a newline.
run_case "T1 (issue #787 round-11 P2): newline-bearing label, unfilled single-line scaffold copy-paste → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_question \
      '["Multi\nline label (Recommended)", "Alternative"]' \
      "Falsified: Multi line label (Recommended) — probe: <command> → <observed>; premise survives because <...>
What should we do?")"

# Anti-bypass regression (codex round-12 P2 fix): round-10's non-
# alphanumeric boundary was still too weak — a genuinely different,
# unrelated evidence line that happens to share the triggering label as
# its own prefix, separated by a space (e.g. triggering "Run" vs.
# evidence text "Run now"), also has a non-alphanumeric boundary. The
# "Run" option's real claim is never addressed even though the line
# gets credited toward it.
run_case "T1+T2 (issue #787 round-12 P2): near-miss label prefix (triggering \"Run\" vs. evidence \"Run now\") → still gates" \
  "ask:Falsified:" \
  "$(make_ask_payload_with_descriptions \
      '["Merge (Recommended)", "Run"]' \
      '["", "This is the safer choice overall"]' \
      "Falsified: Merge (Recommended) — probe: gh pr list --search x -> none found; premise survives
Falsified: Run now — probe: gh issue list -> none found; premise survives
Which one?")"

# Regression: the SAME near-miss shape, but "Run" is genuinely addressed
# by its own line ending exactly at the scaffold's own delimiter → still
# silent-pass — the round-12 fix must not forbid the label from ever
# matching, only close the near-miss-prefix collision.
run_case "T1+T2 (issue #787 round-12 P2): label genuinely addressed via the exact scaffold delimiter → silent pass" \
  pass \
  "$(make_ask_payload_with_descriptions \
      '["Merge (Recommended)", "Run"]' \
      '["", "This is the safer choice overall"]' \
      "Falsified: Merge (Recommended) — probe: gh pr list --search x -> none found; premise survives
Falsified: Run — probe: checked run path -> safe; premise survives
Which one?")"

# Anti-bypass regression (PR #796 CodeRabbit review): the "— probe:" marker
# is present, but nothing (or only whitespace) follows it on the same
# line — deleting everything after the marker, or leaving only trailing
# whitespace, satisfied the gate with zero probe content since the
# placeholder-token scan finds nothing in an empty evidence_region. The
# trailing space after "probe:" is built via a Python string literal (not
# left trailing at end-of-line in this file) so it survives edit-tool
# whitespace normalization.
_empty_evidence_decision=$(python3 - <<'PYEOF' | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
print(d.get('hookSpecificOutput', {}).get('permissionDecision', 'pass'))
"
import json
question_text = "Falsified: Option A (Recommended) — probe: " + "\nWhat should we do?"
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": question_text,
                "options": [{"label": "Option A (Recommended)"}, {"label": "Alternative"}],
            }
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PYEOF
)
if [ "$_empty_evidence_decision" = "ask" ]; then
  echo "  PASS  marker present, empty evidence after it -> still gates (PR #796 CodeRabbit review)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  marker present, empty evidence after it -> still gates (PR #796 CodeRabbit review) (got '$_empty_evidence_decision')"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("marker present, empty evidence after it -> still gates")
fi

# Regression variant: whitespace-only evidence (not merely zero-length)
# must also still gate — the fix strips the evidence region before
# checking emptiness, not just checking for a zero-length string.
_whitespace_evidence_decision=$(python3 - <<'PYEOF' | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
print(d.get('hookSpecificOutput', {}).get('permissionDecision', 'pass'))
"
import json
question_text = "Falsified: Option A (Recommended) — probe: " + "   " + "\nWhat should we do?"
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": question_text,
                "options": [{"label": "Option A (Recommended)"}, {"label": "Alternative"}],
            }
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PYEOF
)
if [ "$_whitespace_evidence_decision" = "ask" ]; then
  echo "  PASS  marker present, whitespace-only evidence -> still gates (PR #796 CodeRabbit review)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  marker present, whitespace-only evidence -> still gates (PR #796 CodeRabbit review) (got '$_whitespace_evidence_decision')"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("marker present, whitespace-only evidence -> still gates")
fi

# Cross-question label preservation (codex round-3 P2 fix): 2 DIFFERENT
# questions each present an option with the IDENTICAL label text. Global
# dedup across the whole payload previously collapsed this to 1 scaffold
# line — but _has_falsified_line checks each question's own text
# independently, so fixing only the first occurrence would leave the
# second question blocked on retry.
make_ask_payload_two_questions_same_label() {
  python3 - <<'PYEOF'
import json
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {"question": "Q1?", "options": [{"label": "Fix now (Recommended)"}, {"label": "Q1 alt"}]},
            {"question": "Q2?", "options": [{"label": "Fix now (Recommended)"}, {"label": "Q2 alt"}]},
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PYEOF
}
_same_label_result=$(make_ask_payload_two_questions_same_label | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
reason = d.get('hookSpecificOutput', {}).get('permissionDecisionReason', '')
ok = reason.count('Falsified: Fix now (Recommended)') == 2
print('ok' if ok else 'fail_count=' + str(reason.count('Falsified: Fix now (Recommended)')))
")
if [ "$_same_label_result" = "ok" ]; then
  echo "  PASS  2 questions with identical label -> 2 scaffold lines, not deduped away (issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  2 questions with identical label -> 2 scaffold lines, not deduped away (issue #787) ($_same_label_result)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("2 questions with identical label -> 2 scaffold lines")
fi

# Multi-question aggregation (codex round-1 P2 fix): 2 separate questions,
# each with its OWN T1 violation, no Falsified: in either. Pre-fix, the loop
# broke on the first violating question and only Q1's label reached the
# scaffold — fixing Q1 alone would still hit a T1 block on Q2 at retry.
make_ask_payload_two_t1_questions() {
  python3 - <<'PYEOF'
import json
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {"question": "Q1?", "options": [{"label": "Q1 choice (Recommended)"}, {"label": "Q1 alt"}]},
            {"question": "Q2?", "options": [{"label": "Q2 choice (추천)"}, {"label": "Q2 alt"}]},
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PYEOF
}
_two_t1_result=$(make_ask_payload_two_t1_questions | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
reason = d.get('hookSpecificOutput', {}).get('permissionDecisionReason', '')
# Korean text is \u-escaped by json.dump, so only the ASCII label prefix
# ('Q2 choice', not the '(추천)' suffix) survives as a literal substring.
ok = 'Q1 choice (Recommended)' in reason and 'Q2 choice' in reason
print('ok' if ok else 'fail_' + reason[-300:])
")
if [ "$_two_t1_result" = "ok" ]; then
  echo "  PASS  2 separate T1-violating questions -> both labels in scaffold (issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  2 separate T1-violating questions -> both labels in scaffold (issue #787) ($_two_t1_result)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("2 separate T1-violating questions -> both labels in scaffold")
fi

# Multi-question aggregation: 2 separate questions, each with its OWN T2-only
# (anchoring) violation, neither carrying a T1 marker. Pre-fix, only the
# first question's label made it into the scaffold (tier_decision is None
# gate blocked the second).
make_ask_payload_two_t2_questions() {
  python3 - <<'PYEOF'
import json
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {"question": "Q1?", "options": [{"label": "Q1 safer path"}, {"label": "Q1 alt"}]},
            {"question": "Q2?", "options": [{"label": "Q2 option", "description": "자연스러운 진행"}, {"label": "Q2 alt"}]},
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PYEOF
}
_two_t2_result=$(make_ask_payload_two_t2_questions | "$HOOK" 2>/dev/null | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
h = d.get('hookSpecificOutput', {})
reason = h.get('permissionDecisionReason', '')
ok = h.get('permissionDecision') == 'ask' and 'Q1 safer path' in reason and 'Q2 option' in reason
print('ok' if ok else 'fail_decision=' + str(h.get('permissionDecision')) + '_reason_tail=' + reason[-200:])
")
if [ "$_two_t2_result" = "ok" ]; then
  echo "  PASS  2 separate T2-violating questions -> both labels in scaffold (issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  2 separate T2-violating questions -> both labels in scaffold (issue #787) ($_two_t2_result)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("2 separate T2-violating questions -> both labels in scaffold")
fi

# ---------------------------------------------------------------------------
# Block-event telemetry cases — issue #787
# ---------------------------------------------------------------------------

TEL_DIR="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TEL_DIR"; rm -f "$DEFAULT_TEL_FILE"' EXIT

rich_records() {
  # $1 = telemetry file. Prints one JSON object per RICH line found.
  local file="$1"
  [ -f "$file" ] || return 0
  python3 -c "
import json
with open('$file') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get('granularity') == 'rich':
            print(json.dumps(r))
"
}

# T1 ask fires -> 1 RICH record, decision=ask, correct hook/role/session/tool.
TEL_T1="$TEL_DIR/t1.jsonl"
make_ask_payload '["Best option (Recommended)", "Alternative"]' \
  | PRAXIS_FIRE_TELEMETRY_FILE="$TEL_T1" "$HOOK" >/dev/null 2>&1
_t1_tel=$(rich_records "$TEL_T1" | python3 -c "
import json, sys
lines = [json.loads(l) for l in sys.stdin if l.strip()]
ok = (
    len(lines) == 1
    and lines[0].get('decision') == 'ask'
    and lines[0].get('hook') == 'output-block-falsify-advisory'
    and lines[0].get('role') == 'advisory-nudge'
    and lines[0].get('tool') == 'AskUserQuestion'
    and lines[0].get('session_id') == 'test-session'
)
print('ok' if ok else 'fail_' + str(lines))
")
if [ "$_t1_tel" = "ok" ]; then
  echo "  PASS  T1 ask -> 1 RICH telemetry record (decision=ask, issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  T1 ask -> 1 RICH telemetry record (decision=ask, issue #787) ($_t1_tel)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("T1 ask -> 1 RICH telemetry record")
fi

# T1 ask -> the automatic COARSE "pass" duplicate from @fail_open must be
# suppressed (issue #787 codex round-1 P2): without suppression the file
# would have 2 lines (RICH block + COARSE pass), corrupting aggregate_fires()
# block-rate counts. Reusing the same TEL_T1 file/call from the case above.
_t1_total_lines=$(wc -l < "$TEL_T1" 2>/dev/null | tr -d ' ')
if [ "${_t1_total_lines:-0}" -eq 1 ]; then
  echo "  PASS  T1 ask -> coarse duplicate suppressed (1 total line, issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  T1 ask -> coarse duplicate suppressed (1 total line, issue #787) (got $_t1_total_lines lines)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("T1 ask -> coarse duplicate suppressed")
fi

# T2 ask fires -> 1 RICH record, decision=ask.
TEL_T2="$TEL_DIR/t2.jsonl"
make_ask_payload '["A safer rollout", "B aggressive"]' \
  | PRAXIS_FIRE_TELEMETRY_FILE="$TEL_T2" "$HOOK" >/dev/null 2>&1
_t2_tel=$(rich_records "$TEL_T2" | python3 -c "
import json, sys
lines = [json.loads(l) for l in sys.stdin if l.strip()]
ok = len(lines) == 1 and lines[0].get('decision') == 'ask'
print('ok' if ok else 'fail_' + str(lines))
")
if [ "$_t2_tel" = "ok" ]; then
  echo "  PASS  T2 ask -> 1 RICH telemetry record (decision=ask, issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  T2 ask -> 1 RICH telemetry record (decision=ask, issue #787) ($_t2_tel)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("T2 ask -> 1 RICH telemetry record")
fi

# T2 ask -> same coarse-duplicate suppression as T1 above.
_t2_total_lines=$(wc -l < "$TEL_T2" 2>/dev/null | tr -d ' ')
if [ "${_t2_total_lines:-0}" -eq 1 ]; then
  echo "  PASS  T2 ask -> coarse duplicate suppressed (1 total line, issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  T2 ask -> coarse duplicate suppressed (1 total line, issue #787) (got $_t2_total_lines lines)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("T2 ask -> coarse duplicate suppressed")
fi

# Silent pass -> no RICH record (nothing to count).
TEL_PASS="$TEL_DIR/pass.jsonl"
make_ask_payload '["Option A", "Option B"]' \
  | PRAXIS_FIRE_TELEMETRY_FILE="$TEL_PASS" "$HOOK" >/dev/null 2>&1
_pass_tel_count=$(rich_records "$TEL_PASS" | wc -l | tr -d ' ')
if [ "${_pass_tel_count:-0}" -eq 0 ]; then
  echo "  PASS  silent pass -> no RICH telemetry record (issue #787)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  silent pass -> no RICH telemetry record (issue #787) (got $_pass_tel_count)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("silent pass -> no RICH telemetry record")
fi

# Missing session_id -> the ask decision still fires (stdout unaffected). PR #796
# CodeRabbit review: previously this early-returned before the RICH write,
# dropping the decision from aggregate_fires()'s fires/block/ask totals
# entirely. record_session_fire coerces a non-str session_id to "", and
# aggregate_fires only adds a session to its per-session set when it is a
# non-empty string, so the RICH record is now written unconditionally with
# session_id="" — the decision still counts, only per-session attribution
# is lost.
make_ask_payload_no_session() {
  python3 -c "
import json, sys
labels = json.loads(sys.argv[1])
options = [{'label': l} for l in labels]
payload = {
    'tool_name': 'AskUserQuestion',
    'tool_input': {'questions': [{'question': 'What should we do?', 'options': options}]},
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1"
}
TEL_NOSESS="$TEL_DIR/nosession.jsonl"
make_ask_payload_no_session '["Best option (Recommended)", "Alternative"]' \
  | PRAXIS_FIRE_TELEMETRY_FILE="$TEL_NOSESS" "$HOOK" >/dev/null 2>&1
_nosess_tel=$(rich_records "$TEL_NOSESS" | python3 -c "
import json, sys
lines = [json.loads(l) for l in sys.stdin if l.strip()]
ok = (
    len(lines) == 1
    and lines[0].get('decision') == 'ask'
    and lines[0].get('hook') == 'output-block-falsify-advisory'
    and lines[0].get('session_id') == ''
)
print('ok' if ok else 'fail_' + str(lines))
")
if [ "$_nosess_tel" = "ok" ]; then
  echo "  PASS  missing session_id -> RICH record still written with session_id='' (PR #796 review)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  missing session_id -> RICH record still written with session_id='' (PR #796 review) ($_nosess_tel)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("missing session_id -> RICH record still written with session_id=''")
fi

# Missing session_id -> the COARSE "pass" duplicate must still be suppressed
# even though the RICH record is now written — otherwise the same call
# would produce both a RICH "block" and a COARSE "pass" record, corrupting
# aggregate_fires() the same way the original T1/T2 coarse-duplicate bug did
# (issue #787 codex round-1 P2). Total line count in the file must be
# exactly 1 (the RICH record only).
if [ -f "$TEL_NOSESS" ]; then
  _nosess_total_lines=$(wc -l < "$TEL_NOSESS" | tr -d ' ')
else
  _nosess_total_lines=0
fi
if [ "${_nosess_total_lines:-0}" -eq 1 ]; then
  echo "  PASS  missing session_id -> coarse duplicate suppressed (1 total line = RICH only, PR #796 review)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  missing session_id -> coarse duplicate suppressed (1 total line = RICH only, PR #796 review) (got $_nosess_total_lines lines)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("missing session_id -> coarse duplicate suppressed")
fi

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
