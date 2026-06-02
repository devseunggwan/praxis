#!/bin/bash
# test_block_personal_asset_leak.sh — coverage for
# hooks/advisory-nudge/block-personal-asset-leak/impl.py
#
# Synthesizes Claude Code PreToolUse(Bash) hook payloads and asserts:
#   warn   → exit 0 + stderr contains "REMINDER"
#   silent → exit 0 + stderr empty
#   block  → exit 2 + stderr contains "REMINDER" (when PRAXIS_PERSONAL_LEAK_STRICT=1)
#
# Usage: bash tests/hooks/advisory-nudge/test_block_personal_asset_leak.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/block-personal-asset-leak/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation strict payload_json
#   expectation: warn | silent | block
#   strict:      "strict" sets PRAXIS_PERSONAL_LEAK_STRICT=1, else advisory
run_case() {
  local name="$1" expectation="$2" strict="$3" payload="$4"

  local err_file
  err_file=$(mktemp)
  if [ "$strict" = "strict" ]; then
    echo "$payload" | PRAXIS_PERSONAL_LEAK_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | env -u PRAXIS_PERSONAL_LEAK_STRICT python3 "$HOOK" >/dev/null 2>"$err_file"
  fi
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    warn)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
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
    [ -n "$err" ] && echo "        stderr: $err" | head -c 400
  fi
}

echo "test_block_personal_asset_leak"

# --- leak detected → advisory ---
run_case "gh issue create + /Users dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo --body \"see /Users/alice/.claude/settings.json for config\""}}'

run_case "gh pr comment + /home .config path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 50 --body \"path is /home/bob/.config/foo/bar.json\""}}'

run_case "gh issue create -b form + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t -b \"hook at /Users/carol/.claude/hooks/x.py\""}}'

run_case "gh issue create --body=value form + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body=\"cfg /Users/dave/.codex/config.toml here\""}}'

run_case "gh pr review --body + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr review 7 --request-changes --body \"remove /Users/erin/.aws/credentials ref\""}}'

run_case "gh issue edit + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue edit 9 --body \"updated: /Users/frank/.ssh/config\""}}'

# --- false-positive boundary → silent ---
run_case "tilde ~/.claude form NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"edit ~/.claude/settings.json instead\""}}'

run_case "worktree /projects/ path NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title t --body \"built in /Users/alice/projects/praxis/hooks\""}}'

run_case "praxis hook name NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 1 --body \"the block-gh-state-all and pre-merge-approval-gate hooks fire here\""}}'

run_case "non-dotfiles absolute path NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"see /usr/local/bin/foo and /Users/alice/Documents/notes\""}}'

run_case "placeholder <name> username NOT flagged (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"docs write paths as /Users/<name>/.claude/settings.json\""}}'

# --- path-prefixed gh now detected (via _is_gh_binary) ---
run_case "path-prefixed /usr/bin/gh + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh issue create --title t --body \"cfg /Users/alice/.claude/x\""}}'

run_case "env-prefixed gh + dotfiles path (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"GH_TOKEN=xyz gh issue create --title t --body \"cfg /Users/alice/.claude/x\""}}'

# --- non-write / non-Bash → silent ---
run_case "gh issue list (not a write) silent" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list --search /Users/alice/.claude"}}'

run_case "Read tool (non-Bash) silent" \
  "silent" "advisory" \
  '{"tool_name":"Read","tool_input":{"file_path":"/Users/alice/.claude/x"}}'

run_case "gh write without body flag silent" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title foo"}}'

# --- duplicate body flags: leak in the LATER body must still fire (codex P2) ---
run_case "duplicate --body, leak in second (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"all clean here\" --body \"see /Users/alice/.claude/x\""}}'

run_case "duplicate --body, leak in first (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"/Users/alice/.claude/x\" --body \"clean\""}}'

# --- heredoc-variable body resolution (codex P2 round 3) ---
run_case "heredoc-var body with leak resolved (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"BODY=$(cat <<EOF\nsee /Users/alice/.claude/settings.json\nEOF\n)\ngh issue create --title t --body \"$BODY\""}}'

run_case "heredoc-var body clean (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"BODY=$(cat <<EOF\nall clean, use ~/.claude\nEOF\n)\ngh issue create --title t --body \"$BODY\""}}'

run_case "heredoc-var ${BODY} brace form resolved (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"BODY=$(cat <<MSG\nleak /home/bob/.config/x\nMSG\n)\ngh pr create --title t --body \"${BODY}\""}}'

# --- strict mode → block ---
run_case "strict mode + dotfiles path (block)" \
  "block" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"cfg at /Users/alice/.claude/settings.json\""}}'

run_case "strict mode + no marker (silent)" \
  "silent" "strict" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue comment 100 --body \"all clean, use ~/.claude\""}}'

# --- malformed payload → fail-open ---
run_case "malformed JSON payload (silent fail-open)" \
  "silent" "advisory" \
  'not-json-at-all'

# --- multi-marker dedup (two distinct paths) → warn ---
run_case "two distinct dotfiles paths (warn)" \
  "warn" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body \"/Users/alice/.claude/a and /home/bob/.config/b\""}}'

# --- --body-file disk read ---
BODY_FILE=$(mktemp)
printf 'config lives at /Users/grace/.claude/hooks/impl.py\n' > "$BODY_FILE"
run_case "gh issue create --body-file with dotfiles path (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t --body-file $BODY_FILE\"}}"
rm -f "$BODY_FILE"

CLEAN_BODY_FILE=$(mktemp)
printf 'all paths use the portable ~/.claude form\n' > "$CLEAN_BODY_FILE"
run_case "gh issue create --body-file clean (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t --body-file $CLEAN_BODY_FILE\"}}"
rm -f "$CLEAN_BODY_FILE"

run_case "gh issue create --body-file unreadable path (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t --body-file /nonexistent/does-not-exist-12345.md"}}'

# --- relative --body-file resolved against payload cwd (codex P2 round 2) ---
REL_DIR=$(mktemp -d)
printf 'cfg at /Users/grace/.claude/hooks/impl.py\n' > "$REL_DIR/body.md"
run_case "relative --body-file resolved against payload cwd (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --title t -F body.md\"},\"cwd\":\"$REL_DIR\"}"
run_case "relative --body-file with wrong cwd (silent fail-open)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --title t -F body.md"},"cwd":"/nonexistent/dir-12345"}'
rm -rf "$REL_DIR"

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
