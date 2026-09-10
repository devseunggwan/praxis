#!/bin/bash
# test_zsh_word_split_advisory.sh — coverage for the advisory hook (#1405).
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr carries the hook tag and the ${=var} remedy
#   silent   → exit 0 + stderr empty
#
# $SHELL is forced per case: the whole premise is zsh-specific, so the
# non-zsh cases are part of the contract rather than environment noise.
#
# Usage: bash tests/hooks/advisory-nudge/test_zsh_word_split_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/zsh-word-split-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command [shell]
run_case() {
  # `${4-...}` not `${4:-...}`: the SHELL-unset case passes an EMPTY fourth
  # argument, and the `:-` form would substitute the default for it — the
  # test would then silently assert the zsh case twice.
  local name="$1" expected="$2" command="$3" shell="${4-/bin/zsh}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | SHELL="$shell" "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "\[zsh-word-split-advisory\]" || ok=0
      # The remedy is what makes the advisory actionable — assert it, not
      # merely that something was printed.
      echo "$err" | grep -q '\${=' || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# === ADVISORY — the two measured shapes =====================================

run_case "set -- with an unquoted var" advisory \
  'set -- $spec; gh pr view "$@"'
run_case "set -- with a braced unquoted var" advisory \
  'set -- ${spec}; print $#'
run_case "for ... in an unquoted var" advisory \
  'for f in $files; do print $f; done'
run_case "single-dash set form" advisory \
  'set - $spec'
run_case "several vars are all named" advisory \
  'set -- $a $b'

# === SILENT — intent is legible from the syntax =============================

run_case "quoted expansion is one argument on purpose" silent \
  'set -- "$spec"'
run_case "split flag already used" silent \
  'set -- ${=spec}'
run_case "explicit split flag on a bare name" silent \
  'set -- $=spec'
run_case "explicit separator split" silent \
  'set -- ${(s: :)spec}'
run_case "positional and special params carry their own rules" silent \
  'set -- $@'
run_case "numbered positional is not an identifier" silent \
  'set -- $1'
run_case "for over a literal list" silent \
  'for f in a b c; do print $f; done'
run_case "an ordinary command argument is out of scope" silent \
  'gh pr view $args'

# === SILENT — premise absent ================================================

run_case "SHELL=/bin/bash does not warn" silent \
  'set -- $spec' /bin/bash
run_case "SHELL=/usr/bin/fish does not warn" silent \
  'set -- $spec' /usr/bin/fish
run_case "SHELL unset does not warn" silent \
  'set -- $spec' ''
run_case "same payload under zsh does warn" advisory \
  'set -- $spec' /bin/zsh

# === SILENT — masked or opted out ===========================================

run_case "opt-out marker silences it" silent \
  'set -- $spec # word-split:ok'
run_case "heredoc body is data, not words" silent \
  'cat <<EOF
set -- $spec
EOF'
run_case "quoted text containing the shape is not the shape" silent \
  'print "set -- $spec"'

# === fail-open ==============================================================

echo '{"tool_name":"Read","tool_input":{}}' | SHELL=/bin/zsh "$HOOK" >/dev/null 2>&1
if [ $? -eq 0 ]; then echo "PASS  [non-Bash tool fails open]"; PASS=$((PASS + 1));
else echo "FAIL  [non-Bash tool fails open]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool fails open"); fi

echo 'not json' | SHELL=/bin/zsh "$HOOK" >/dev/null 2>&1
if [ $? -eq 0 ]; then echo "PASS  [malformed stdin fails open]"; PASS=$((PASS + 1));
else echo "FAIL  [malformed stdin fails open]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed stdin fails open"); fi

echo
echo "pass=$PASS fail=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "failed: ${FAILED_NAMES[*]}"; exit 1; }
