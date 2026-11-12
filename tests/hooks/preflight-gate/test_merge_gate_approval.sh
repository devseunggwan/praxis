#!/bin/bash
# test_merge_gate_approval.sh — coverage for hooks/preflight-gate/merge-gate/approval.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   ask    → exit 0 + stdout JSON has permissionDecision "ask"
#   silent → exit 0 + stdout empty (no JSON, no permissionDecision)
#
# Usage: bash tests/test_merge_gate_approval.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/merge-gate/approval.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation delegate_env payload_json
#   expectation:
#     "ask"    — stdout JSON has permissionDecision "ask", rc=0
#     "silent" — stdout empty, rc=0
#   delegate_env:
#     "delegate"    — run with CMUX_DELEGATE=1 set. Since #1055 this grants no
#                     exemption; the cases below assert it is inert.
#     "no-delegate" — run with CMUX_DELEGATE unset
unset PRAXIS_MERGE_ASK_TRAILER_REGEX

run_case() {
  local name="$1" expectation="$2" delegate="$3" payload="$4"

  local out_file
  out_file=$(mktemp)

  if [ "$delegate" = "delegate" ]; then
    echo "$payload" | CMUX_DELEGATE=1 python3 "$HOOK" >"$out_file" 2>/dev/null
  else
    echo "$payload" | env -u CMUX_DELEGATE python3 "$HOOK" >"$out_file" 2>/dev/null
  fi
  local rc=$?
  local out
  out=$(cat "$out_file")
  rm -f "$out_file"

  local ok=1
  case "$expectation" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    decision = d.get('hookSpecificOutput', {}).get('permissionDecision', '')
    sys.exit(0 if decision == 'ask' else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$out" ] && echo "        stdout: $out" | head -c 400
  fi
}

echo "test_pre_merge_approval_gate"

# --- Merge in any session → ASK

run_case "direct session: gh pr merge --squash (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1 --squash"}}'

run_case "direct session: gh pr merge --merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 42 --merge"}}'

run_case "direct session: gh pr merge --delete-branch (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 99 --squash --delete-branch"}}'

# --- #1055: CMUX_DELEGATE=1 no longer exempts anything → ASK

run_case "CMUX_DELEGATE=1 session: gh pr merge (ask, exemption removed)" \
  "ask" "delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1"}}'

run_case "CMUX_DELEGATE=1 session: gh pr merge --squash (ask, exemption removed)" \
  "ask" "delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1 --squash --delete-branch"}}'

# --- R4-F1: marker removed — agent-attachable bypass cannot exist → ASK

run_case "direct session: merge-approval:ack marker no longer bypasses (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1 --squash # merge-approval:ack"}}'

# --- Non-merge gh commands → SILENT

run_case "direct session: gh pr view (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr view 1"}}'

run_case "direct session: gh pr list (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr list --state open"}}'

run_case "direct session: gh pr create (silent, not merge)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title foo --body bar"}}'

run_case "direct session: git commit with merge in message (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"merge note\""}}'

# --- Chained commands: scan beyond first segment → ASK

run_case "direct session: chained gh issue list && gh pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list && gh pr merge 1 --squash"}}'

run_case "direct session: echo prep; gh pr merge 5 (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"echo prep; gh pr merge 5"}}'

# --- Quoted body containing 'gh pr merge' text → SILENT (not executed as cmd)

run_case "direct session: quoted body with gh pr merge text (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 1 --body \"next step: gh pr merge\""}}'

# --- Env-var prefix on the command line → ASK
# No environment variable exempts a merge (#1055), and an inline prefix would
# not reach the hook process anyway.

run_case "inline env CMUX_DELEGATE=1 gh pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"env CMUX_DELEGATE=1 gh pr merge 1 --squash"}}'

# --- Non-Bash tool → SILENT

run_case "non-Bash tool (Read) → silent" \
  "silent" "no-delegate" \
  '{"tool_name":"Read","tool_input":{"file_path":"/tmp/foo"}}'

run_case "non-Bash tool (Write) → silent" \
  "silent" "no-delegate" \
  '{"tool_name":"Write","tool_input":{"file_path":"/tmp/bar","content":"x"}}'

# --- Malformed JSON → SILENT (fail-open)

run_case "malformed JSON → silent" \
  "silent" "no-delegate" \
  'not-json'

# --- Empty / blank command → SILENT

run_case "empty command → silent" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":""}}'

# --- gh global flags between `gh` and subcommand → ASK (F1 regression)

run_case "direct session: gh -R owner/repo pr merge --squash (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo pr merge 1 --squash"}}'

run_case "direct session: gh --repo owner/repo pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh --repo owner/repo pr merge 1"}}'

run_case "direct session: gh --hostname github.com pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh --hostname github.com pr merge 1"}}'

run_case "direct session: gh -R=owner/repo pr merge (= form, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R=owner/repo pr merge 1"}}'

run_case "direct session: gh -R owner/repo pr list (read, not merge, silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo pr list"}}'

run_case "direct session: gh -R repo pr merge + ack marker no longer bypasses (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo pr merge 1 # merge-approval:ack"}}'

run_case "inline CMUX_DELEGATE=1 gh -R owner/repo pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"CMUX_DELEGATE=1 gh -R owner/repo pr merge 1"}}'

# --- #511: path-prefix / command-wrapper bypass must NOT slip past the gate → ASK
# `argv[0] == "gh"` exact match previously let `/usr/bin/gh pr merge` and
# `command gh pr merge` through silently. _is_gh_binary (basename) + the
# command/builtin peel in strip_prefix close both.

run_case "direct session: /usr/bin/gh pr merge 5 (path-prefix, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh pr merge 5"}}'

run_case "direct session: command gh pr merge 5 (command wrapper, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"command gh pr merge 5"}}'

run_case "direct session: builtin command gh pr merge 5 (builtin wrapper, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"builtin command gh pr merge 5"}}'

run_case "direct session: /usr/bin/gh -R owner/repo pr merge (path-prefix + global flag, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh -R owner/repo pr merge 5"}}'

run_case "direct session: /usr/bin/gh pr list (path-prefix read, not merge, silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh pr list"}}'

# --- #985: help and heredoc bodies are not merges ---------------------------
# A gate that asks on `gh pr merge --help` blocks the one command run to learn
# the flags; a heredoc body is data, so a commit message quoting the command is
# not the command. The last two cases keep the fix from becoming a bypass.

run_case "direct session: gh pr merge --help (usage only, silent)" \
  "silent" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "gh pr merge --help"}}'

run_case "direct session: gh pr merge -h (usage only, silent)" \
  "silent" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "gh pr merge -h"}}'

run_case "direct session: commit message quoting the merge (heredoc body, silent)" \
  "silent" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "git commit -m \"$(cat <<'EOF'\ngh pr merge is only quoted here\nEOF\n)\""}}'

run_case "direct session: -h as a --subject value (still a merge, ask)" \
  "ask" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "gh pr merge 985 --squash --subject -h"}}'

run_case "direct session: merge after a heredoc terminator (still a merge, ask)" \
  "ask" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "cat <<'EOF'\nbody\nEOF\ngh pr merge 985 --squash"}}'

# Bulk approval: the chat answer to a per-PR-sectioned briefing replaces the
# prompt for each listed PR (the briefing check's bulk_merge_approved
# decides). Anything it does not release still asks.
FIXTURES_DIR="$ROOT_DIR/tests/fixtures"
bulk_payload() {
  python3 -c 'import json, sys; print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[2]}, "transcript_path": sys.argv[1]}))' \
    "$FIXTURES_DIR/$1" "$2"
}
run_case "bulk: listed PR after a bulk answer (silent)" \
  "silent" "no-delegate" \
  "$(bulk_payload momentum-merge-bulk-all-second.jsonl 'gh pr merge 999 --squash')"
run_case "bulk: AskUserQuestion bulk answer (silent)" \
  "silent" "no-delegate" \
  "$(bulk_payload momentum-merge-bulk-ask-answer.jsonl 'gh pr merge 999 --squash')"
run_case "bulk: PR outside the approved set (ask)" \
  "ask" "no-delegate" \
  "$(bulk_payload momentum-merge-bulk-all-second.jsonl 'gh pr merge 1001 --squash')"
run_case "bulk: combined block without per-PR sections (ask)" \
  "ask" "no-delegate" \
  "$(bulk_payload momentum-merge-bulk-combined.jsonl 'gh pr merge 999 --squash')"
run_case "bulk: chained merges (ask)" \
  "ask" "no-delegate" \
  "$(bulk_payload momentum-merge-bulk-all-first.jsonl 'gh pr merge 833 --squash && gh pr merge 999 --squash')"
run_case "bulk: unreadable transcript (ask)" \
  "ask" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "gh pr merge 999 --squash"}, "transcript_path": "/nonexistent/t.jsonl"}'

# Single-PR chat approval (issue #1654): the user's own answer or order for
# this PR replaces the prompt; a merge the user has not approved still asks.
M999='gh pr merge 999 --repo o/r --squash'
run_case "chat: 'merge' answering the briefed ask (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl "$M999")"
run_case "chat: '머지 승인' answering the briefed ask (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-answered-ko.jsonl "$M999")"
run_case "chat: user ordered '#999 머지해' (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-ordered.jsonl "$M999")"
run_case "chat: AskUserQuestion pick '머지 승인' (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-ask-answer.jsonl "$M999")"
run_case "chat: retry after a merge that failed (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-failed-merge.jsonl "$M999")"
run_case "chat: --repo given after a cd (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-cd.jsonl "$M999")"
run_case "chat: briefing with no answer yet (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-unanswered.jsonl "$M999")"
run_case "chat: 'ok' to a non-merge question (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-unrelated-ok.jsonl "$M999")"
run_case "chat: order then '머지 보류' (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-held.jsonl "$M999")"
run_case "chat: injected isMeta order (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-injected.jsonl "$M999")"
run_case "chat: answer already spent by a merge (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-spent.jsonl "$M999")"
run_case "chat: another PR than the approved one (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl 'gh pr merge 1001 --repo o/r --squash')"
run_case "chat: --repo other than the probed repo (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl 'gh pr merge 999 --repo x/y --squash')"
run_case "chat: no --repo after a cd (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-cd.jsonl 'gh pr merge 999 --squash')"
run_case "chat: chained merge (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl "$M999 && echo done")"
run_case "chat: numberless merge (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl 'gh pr merge --repo o/r --squash')"
run_case "chat: looped merge (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl "for i in 1; do $M999; done")"
run_case "chat: gh api repos/o/r probe names the repo (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-api-probe.jsonl "$M999")"
run_case "chat: two --repo selectors (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl 'gh pr merge 999 --repo o/r --repo x/y --squash')"
run_case "chat: same owner/name on another host (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-answered.jsonl 'gh pr merge 999 --repo ghe.example.com/o/r --squash')"
run_case "chat: later probe moves to another repo, merge follows it (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-later-probe.jsonl 'gh pr merge 999 --repo x/y --squash')"
run_case "chat: later probe naming no repo, text names o/r#999 (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-unnamed-probe.jsonl "$M999")"
run_case "chat: later probe of another repo, merge stays on o/r (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-later-probe.jsonl "$M999")"
run_case "chat: pull URL reported before the order names the repo (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-url-named.jsonl "$M999")"
run_case "chat: pull URL repo differs from the merge (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-url-named.jsonl 'gh pr merge 999 --repo x/y --squash')"
run_case "chat: text names #999 in two repos (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-two-repos-named.jsonl "$M999")"
run_case "chat: text names #999 without its repo (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-no-repo-named.jsonl "$M999")"
run_case "chat: '#999 머지 보류. ok' (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-hold-ok.jsonl "$M999")"
run_case "chat: order then '#999 머지 보류. ok' (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-order-then-hold-ok.jsonl "$M999")"
run_case "chat: 'For #999, ok' to a non-merge question (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-ok-to-other-ask.jsonl "$M999")"
run_case "chat: conditional 'After I approve the release for #999, merge' (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-conditional.jsonl "$M999")"
run_case "chat: repo named only after the answer (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-repo-after-answer.jsonl 'gh pr merge 999 --repo x/y --squash')"
run_case "chat: numbered reply '1. approve / 2. hold' is not read (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-numbered-other-hold.jsonl "$M999")"
run_case "chat: 'ok' to a conditional merge question (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-conditional-ask.jsonl "$M999")"
run_case "chat: 'A' picked under another PR's merge question (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-letter-other-pr.jsonl "$M999")"
run_case "chat: 'A: merge' under this PR's plain merge question (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-letter-plain.jsonl "$M999")"
run_case "chat: AskUserQuestion result approving and holding the merge (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-ask-answer-held-same-result.jsonl "$M999")"
run_case "chat: bullet and label before a plain merge question (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-labelled-ask.jsonl "$M999")"
run_case "chat: plain merge question naming the PR by URL (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-url-ask.jsonl "$M999")"
run_case "chat: merge question with a second action (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-second-action-ask.jsonl "$M999")"
run_case "chat: condition written after the merge question (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-condition-after-ask.jsonl "$M999")"
run_case "chat: picked choice is a conditional approval (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-conditional-pick.jsonl "$M999")"
run_case "chat: AskUserQuestion pick is a conditional approval (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-conditional-ask-pick.jsonl "$M999")"
run_case "chat: short 'hold' to a later merge question (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-short-hold-later.jsonl "$M999")"
PRAXIS_MERGE_ASK_TRAILER_REGEX='Status: .*' run_case "chat: reply line and configured trailer after the question (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-reply-line-after-ask.jsonl "$M999")"
run_case "chat: trailer after the question with no trailer configured (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-reply-line-after-ask.jsonl "$M999")"
PRAXIS_MERGE_ASK_TRAILER_REGEX='Status: .*' run_case "chat: condition inside the reply line (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-reply-line-condition.jsonl "$M999")"
run_case "chat: question mark right after the pull URL (silent)" \
  "silent" "no-delegate" "$(bulk_payload momentum-merge-chat-url-question-mark.jsonl "$M999")"
run_case "chat: unnamed merge question after two PRs (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-unnamed-ask-two-prs.jsonl "$M999")"
run_case "chat: injected AskUserQuestion answer (ask)" \
  "ask" "no-delegate" "$(bulk_payload momentum-merge-chat-injected-ask-answer.jsonl "$M999")"


echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
