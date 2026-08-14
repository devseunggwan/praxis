#!/bin/bash
# test_memory_hint.sh — coverage for hooks/memory-hint.sh
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   hit    → exit 0 + stderr contains the expected substring
#   silent → exit 0 + stderr empty
#
# Usage: bash tests/test_memory_hint.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/memory-hint/impl.py"
FIXTURES_MAIN="$SCRIPT_DIR/../../fixtures/memory-hint"
FIXTURES_CAP="$SCRIPT_DIR/../../fixtures/memory-hint-cap"
FIXTURES_EMPTY="$SCRIPT_DIR/../../fixtures/memory-hint-empty"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation memory_dir tool_name command
#   expectation:
#     "hit:<substring>"   — stderr contains <substring>, rc=0
#     "silent"            — stderr empty, rc=0
run_case() {
  local name="$1" expectation="$2" memory_dir="$3" tool_name="$4" command="$5"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
}))' "$tool_name" "$command")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | env PRAXIS_MEMORY_DIR="$memory_dir" "$HOOK" >/dev/null 2>"$err_file"
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
    hit:*)
      local needle="${expectation#hit:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$needle"*) ;;
        *) ok=0 ;;
      esac
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

# --- core hit / silent paths -------------------------------------------------
run_case "1 hit: kubectl token matches"               "hit:hook_kubectl.md"      "$FIXTURES_MAIN" Bash 'kubectl get pods'
run_case "2 hit: keyword across separator"            "hit:hook_kubectl.md"      "$FIXTURES_MAIN" Bash 'false || kubectl get pods'
run_case "3 silent: keyword inside quoted string"     silent                     "$FIXTURES_MAIN" Bash 'echo "use kubectl carefully"'
run_case "4 silent: keyword absent"                   silent                     "$FIXTURES_MAIN" Bash 'ls -la'

# run_input_case name expectation memory_dir tool_name tool_input_json
#   Like run_case but passes raw tool_input JSON for non-Bash events.
run_input_case() {
  local name="$1" expectation="$2" memory_dir="$3" tool_name="$4" tool_input_json="$5"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": sys.argv[1], "tool_input": json.loads(sys.argv[2])}))' "$tool_name" "$tool_input_json")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | env PRAXIS_MEMORY_DIR="$memory_dir" "$HOOK" >/dev/null 2>"$err_file"
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
    hit:*)
      local needle="${expectation#hit:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$needle"*) ;;
        *) ok=0 ;;
      esac
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

# --- frontmatter gate paths --------------------------------------------------
run_case "5 silent: hookable false skipped"           silent                     "$FIXTURES_MAIN" Bash 'echo __hookable_false_marker__'
run_case "6 silent: hookable missing skipped"         silent                     "$FIXTURES_MAIN" Bash 'echo no_hookable_token'
run_case "7 silent: hookable true but no keywords"    silent                     "$FIXTURES_MAIN" Bash 'echo hookable_no_keywords'
run_case "8 hit: malformed yaml does not break peers" "hit:hook_kubectl.md"      "$FIXTURES_MAIN" Bash 'kubectl get'

# --- noise cap: 4+ matches → 3 lines + summary ------------------------------
prepare_cap_mtimes() {
  python3 -c "
import os, time
base = '$FIXTURES_CAP'
order = ['foo_a.md', 'foo_b.md', 'foo_c.md', 'foo_d.md']
now = time.time()
for i, name in enumerate(order):
    t = now - (len(order) - i) * 100
    os.utime(os.path.join(base, name), (t, t))
"
}
prepare_cap_mtimes
run_case "9 hit: cap shows newest first"              "hit:foo_d.md"             "$FIXTURES_CAP"  Bash 'foo bar baz'
run_case "9b hit: cap summary line emitted"           "hit:and 1 more"           "$FIXTURES_CAP"  Bash 'foo bar baz'

# --- discovery / fail-safe paths --------------------------------------------
run_case "10 silent: PRAXIS_MEMORY_DIR nonexistent"    silent                    "/tmp/praxis-memhint-no-such-dir-$$" Bash 'kubectl get'
run_case "11 silent: PRAXIS_MEMORY_DIR empty dir"      silent                    "$FIXTURES_EMPTY" Bash 'kubectl get'
malformed_json_test() {
  local name="12 silent: malformed JSON stdin"
  local err_file
  err_file=$(mktemp)
  echo "not-valid-json" | env PRAXIS_MEMORY_DIR="$FIXTURES_MAIN" "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
malformed_json_test
run_case "13 silent: tool_name Read"                   silent                    "$FIXTURES_MAIN" Read 'kubectl get'
run_case "14 silent: empty command"                    silent                    "$FIXTURES_MAIN" Bash ''
run_case "15 hit: backslash line continuation"         "hit:hook_kubectl.md"     "$FIXTURES_MAIN" Bash "$(printf 'kubectl \\\n  get pods')"
run_case "16 hit: comment-prefixed token still matches" "hit:hook_kubectl.md"    "$FIXTURES_MAIN" Bash '# kubectl get'
run_case "17 hit: multiple distinct memories fire"     "hit:hook_gh_search.md"   "$FIXTURES_MAIN" Bash 'gh search issues "kubectl"'

# --- AC-21 / AC-22 / AC-23 ---------------------------------------------------
run_case "18 hit: no description trailer"              "hit:hook_no_description.md"   "$FIXTURES_MAIN" Bash 'foo bar'
run_case "19 silent: scalar hookKeywords skipped"      silent                          "$FIXTURES_MAIN" Bash 'kubectl_only_in_scalar_fixture'
run_case "20 silent: case-sensitive keyword miss"      silent                          "$FIXTURES_MAIN" Bash 'Kubectl Get'
run_case "21 hit: hookKeywords with trailing inline comment" "hit:hook_inline_comment.md" "$FIXTURES_MAIN" Bash 'bazinga now'
run_case "22 hit: hookable + description with trailing comments parsed correctly" "hit:hook_full_inline_comments.md" "$FIXTURES_MAIN" Bash 'zorblax run'
run_case "23 hit: trailing comment stripped from description visible portion" "hit:this description ends here" "$FIXTURES_MAIN" Bash 'zorblax run'

description_no_leak_test() {
  local name="24 silent: trailing yaml comment NOT leaked into stderr"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": "zorblax run"}}))')
  local err_file
  err_file=$(mktemp)
  echo "$payload" | env PRAXIS_MEMORY_DIR="$FIXTURES_MAIN" "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"
  # rc must be 0; stderr must contain the description fragment but NOT the comment
  if [ "$rc" -eq 0 ] && [[ "$err" == *"this description ends here"* ]] && [[ "$err" != *"# comment to be stripped"* ]]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
description_no_leak_test

# --- AC-25 / AC-26: fail-open on undecodable memory file --------------------
run_case "25 hit: undecodable peer does not break sibling matches" "hit:hook_kubectl.md" "$FIXTURES_MAIN" Bash 'kubectl get pods'

undecodable_silent_test() {
  local name="26 silent: undecodable memory alone exits 0 silently"
  local tmpdir
  tmpdir=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  cp "$FIXTURES_MAIN/non_utf8.md" "$tmpdir/non_utf8.md"
  local payload
  payload=$(python3 -c '
import json
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": "kubectl get pods"}}))')
  local err_file
  err_file=$(mktemp)
  echo "$payload" | env PRAXIS_MEMORY_DIR="$tmpdir" "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"
  rm -rf "$tmpdir"
  if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
undecodable_silent_test

# --- AC-27..33: non-Bash events (Edit/Write/NotebookEdit/AskUserQuestion) ----
run_input_case "27 hit: Edit event fires hookEvents=[Edit]" \
  "hit:hook_edit_event.md" "$FIXTURES_MAIN" Edit \
  '{"file_path": "/tmp/x.py", "old_string": "EditEventToken here", "new_string": "y"}'

run_input_case "28 hit: Write event fires hookEvents=[Write]" \
  "hit:hook_write_event.md" "$FIXTURES_MAIN" Write \
  '{"file_path": "/tmp/y.txt", "content": "body with WriteEventToken inside"}'

run_input_case "29 hit: NotebookEdit event fires hookEvents=[NotebookEdit]" \
  "hit:hook_notebook_event.md" "$FIXTURES_MAIN" NotebookEdit \
  '{"notebook_path": "/tmp/n.ipynb", "new_source": "cell source NotebookEditToken"}'

run_input_case "30 hit: AskUserQuestion event fires hookEvents=[AskUserQuestion]" \
  "hit:hook_ask_event.md" "$FIXTURES_MAIN" AskUserQuestion \
  '{"questions": [{"question": "go?", "header": "x", "options": [{"label": "AskEventToken", "description": "y"}]}]}'

run_input_case "31 silent: Edit event but memory has default hookEvents=[Bash]" \
  silent "$FIXTURES_MAIN" Edit \
  '{"file_path": "/tmp/x.py", "old_string": "kubectl get pods", "new_string": "y"}'

run_input_case "32 hit: multi-event memory fires on Bash" \
  "hit:hook_multi_event.md" "$FIXTURES_MAIN" Bash \
  '{"command": "echo MultiEventToken"}'

run_input_case "33 hit: multi-event memory fires on Edit" \
  "hit:hook_multi_event.md" "$FIXTURES_MAIN" Edit \
  '{"file_path": "/tmp/x.py", "old_string": "MultiEventToken in code", "new_string": "y"}'

# --- AC-34: ASCII keyword adjacent to Hangul must split (mixed-text guard) ---
run_input_case "34 hit: ASCII keyword adjacent to Hangul splits as separate token" \
  "hit:hook_edit_event.md" "$FIXTURES_MAIN" Edit \
  '{"file_path": "/tmp/x.py", "old_string": "EditEventToken할까요?", "new_string": "y"}'

# --- AC-35/36: resolve_memory_dir() fallback-path slugification -------------
# All cases above always set PRAXIS_MEMORY_DIR explicitly, so the fallback
# branch in resolve_memory_dir() (cwd -> ~/.claude/projects/<slug>/memory)
# was never exercised by this suite. That gap let a real bug ship silently:
# the fallback slugified cwd via cwd.replace("/", "-") only, which does not
# match Claude Code's actual per-character non-alphanumeric replacement
# (e.g. "/Users/nathan.song/.claude" slugs to "-Users-nathan-song--claude",
# not "-Users-nathan.song-.claude") — any home path containing a special
# character other than "/" left the fallback path permanently unresolvable
# for that user, silently disabling every hookable memory unconditionally.
FALLBACK_HOME=$(cd "$(mktemp -d)" && pwd -P) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
# cwd containing a literal "." mirrors the real-world failure (a "." inside
# a path segment, as in a username like "nathan.song"). Resolve via `pwd -P`
# (physical path) so this test is immune to macOS's /var -> /private/var
# symlink, which would otherwise desync the expected slug from what
# os.getcwd() actually reports.
FALLBACK_CWD="$FALLBACK_HOME/work/my.repo"
mkdir -p "$FALLBACK_CWD"
FALLBACK_SLUG=$(python3 -c "
import re
print(re.sub(r'[^a-zA-Z0-9]', '-', '$FALLBACK_CWD'))
")
FALLBACK_MEMDIR="$FALLBACK_HOME/.claude/projects/$FALLBACK_SLUG/memory"
mkdir -p "$FALLBACK_MEMDIR"
cat > "$FALLBACK_MEMDIR/fallback-slug-probe.md" <<'EOF'
---
name: fallback-slug-probe
description: probe memory for the resolve_memory_dir fallback-path test
hookable: true
hookKeywords: [FallbackSlugToken]
---
probe body
EOF

FALLBACK_ERR=$(mktemp)
(
  cd "$FALLBACK_CWD" || exit 1
  # CLAUDE_CONFIG_DIR must be unset, not pointed elsewhere: resolve_memory_dir()
  # treats it as authoritative over the HOME-derived default (#853), and the
  # default is exactly the branch this case exercises. An ambient value sends
  # the hook to the developer's real store instead of the fixture. Overriding
  # it with a throwaway path fails identically — "set" is what wins, not the
  # value. Mirrors tests/hooks/_lib/test_memory_dir.py, whose HOME fixtures all
  # delenv it and which covers the relocated root as its own separate case.
  echo '{"tool_name": "Bash", "tool_input": {"command": "echo FallbackSlugToken"}}' \
    | env -u PRAXIS_MEMORY_DIR -u CLAUDE_CONFIG_DIR HOME="$FALLBACK_HOME" "$HOOK" \
      >/dev/null 2>"$FALLBACK_ERR"
)
FALLBACK_RC=$?
FALLBACK_OUT=$(cat "$FALLBACK_ERR")
rm -f "$FALLBACK_ERR"
rm -rf "$FALLBACK_HOME"

if [ "$FALLBACK_RC" -eq 0 ] && printf '%s' "$FALLBACK_OUT" | grep -q "fallback-slug-probe.md"; then
  echo "PASS  [35 hit: resolve_memory_dir() fallback path matches Claude Code's per-char slug for a cwd containing \".\"]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [35 hit: resolve_memory_dir() fallback path matches Claude Code's per-char slug for a cwd containing \".\"] (rc=$FALLBACK_RC, stderr=$FALLBACK_OUT)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("35 resolve_memory_dir fallback slug match")
fi

# --- summary -----------------------------------------------------------------
echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_NAMES[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
