#!/bin/bash
# Stop hook: block assistant completion claims without same-turn verification evidence.
# Contract: reads JSON from stdin, emits {"decision":"block"} or exit 0 pass.
#
# Strict same-turn enforcement (issue #138, PR #144):
#   When CLAIM_PATTERNS matches in the last 10 lines of the last assistant message,
#   pass only if ALL of these hold within the current turn (since the last real user input):
#     L1. A Bash tool_use exists.
#     L3. Its tool_result.content matches EVIDENCE_PATTERNS.
#     L2. At least one EVIDENCE_PATTERNS-matching span from that tool_result
#         is paste'd as a substring of the assistant message text — i.e. the
#         specific "12 passed" / "tests passed" / "lint clean" / "✅" / etc.
#         token that triggered L3 must appear verbatim in the message.
#   Otherwise, block with a {decision: block, reason: ...} JSON payload.

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // ""')
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')

[ "$STOP_HOOK_ACTIVE" = "true" ] && exit 0
[ ! -f "$TRANSCRIPT_PATH" ] && exit 0

CLAIM_PATTERNS='(모두 완료(했)?|완료했습니다[.!。?…]?\s*$|작업 완료[.!。?…]?\s*$|완료[.!。?…]?\s*$|\bdone\b[.!?]?\s*$|\bfinished\b[.!?]?\s*$|cleanup (is |was )?finished|implementation complete|all done)'
EVIDENCE_PATTERNS='(tests? passed|\bPASS\b|exit code 0|\b[1-9][0-9]* tests? (ran|passed)|\b[1-9][0-9]* passed\b|0 errors|build successful|lint clean|성공적으로|테스트.*통과|✅)'

# Single jq pass: extract last assistant text + Bash tool_result texts in current turn.
# Current turn boundary = events after the last real user input (string content, or
# array containing any non-tool_result block). Tool-result-only user messages are
# tool replies and do not reset the turn. [PR #144]
TURN_JSON=$(tail -n 400 "$TRANSCRIPT_PATH" | jq -sc '
  ([
    to_entries[]
    | select(
        .value.message.role == "user"
        and (.value.isSidechain // false) == false
        and (
          (.value.message.content | type) == "string"
          or (
            (.value.message.content | type) == "array"
            and ((.value.message.content // []) | map(select(.type != "tool_result")) | length > 0)
          )
        )
      )
    | .key
  ] | last) as $user_idx
  | (if $user_idx == null then 0 else $user_idx + 1 end) as $start
  | (.[$start:]) as $turn
  | ([$turn[]
       | select(.message.role == "assistant" and (.isSidechain // false) == false)]
     | last
     | (.message.content // [])
     | map(select(.type == "text") | .text)
     | join("\n")) as $last_text
  | ([$turn[]
       | select(.message.role == "assistant" and (.isSidechain // false) == false)
       | (.message.content // [])[]
       | select(.type == "tool_use" and .name == "Bash")
       | .id]) as $bash_ids
  | ([$turn[]
       | select(.message.role == "user")
       | (.message.content // [])[]
       | select(.type == "tool_result"
                and (.tool_use_id as $t | $bash_ids | any(. == $t)))
       | (if (.content | type) == "string" then .content
          elif (.content | type) == "array" then
            (.content | map(select(.type == "text") | .text) | join("\n"))
          else "" end)]
     | join("\n---\n")) as $bash_outputs
  | {last_text: $last_text, bash_outputs: $bash_outputs}
' 2>/dev/null)

[ -z "$TURN_JSON" ] && exit 0

LAST_TEXT=$(printf '%s' "$TURN_JSON" | jq -r '.last_text // ""')
BASH_OUTPUTS=$(printf '%s' "$TURN_JSON" | jq -r '.bash_outputs // ""')

[ -z "$LAST_TEXT" ] && exit 0

# Check last 10 lines only — avoids false positives from mid-message 완료 mentions
LAST_LINES=$(printf '%s\n' "$LAST_TEXT" | tail -10)
if ! printf '%s' "$LAST_LINES" | grep -qiE "$CLAIM_PATTERNS"; then
  exit 0
fi

# Claim detected — verify L1+L3+L2 in this turn.
block_reason=""

if [ -z "$BASH_OUTPUTS" ]; then
  block_reason="No Bash verification command was run in this turn. Run a real verify command (test/lint/build) and paste its output BEFORE declaring completion."
elif ! printf '%s' "$BASH_OUTPUTS" | grep -qE "$EVIDENCE_PATTERNS"; then
  block_reason="Bash output present but lacks a verification signal (e.g., 'tests passed', 'exit code 0', 'lint clean'). Re-run an actual verify command."
else
  # Paste check: each EVIDENCE_PATTERNS-matching span in tool_result must
  # appear verbatim in the assistant message. Span-based (not line-based)
  # so decorated output like '======== 12 passed in 0.85s ========' counts
  # when the assistant cites '12 passed in 0.85s'.
  paste_detected=false
  evidence_spans=$(printf '%s' "$BASH_OUTPUTS" | grep -oE "$EVIDENCE_PATTERNS")
  while IFS= read -r span; do
    [ -z "$span" ] && continue
    if printf '%s' "$LAST_TEXT" | grep -qF -e "$span"; then
      paste_detected=true
      break
    fi
  done <<< "$evidence_spans"

  if [ "$paste_detected" = "false" ]; then
    block_reason="Bash output has a verification signal but the evidence span (e.g. 'X passed', 'lint clean', '✅') was not quoted in your message. Paste the verify token verbatim into your reply."
  fi
fi

if [ -n "$block_reason" ]; then
  mkdir -p ~/.claude/scope-confirm
  echo "$(date -Iseconds) session=$SESSION_ID blocked_completion_without_evidence" >> ~/.claude/scope-confirm/stop-triggered.log

  REASON="Completion claim detected without same-turn verification evidence. ${block_reason} See AGENTS.md Verification section."
  jq -n --arg r "$REASON" '{decision: "block", reason: $r}'
  exit 0
fi

exit 0
