#!/bin/bash
# test_zsh_dialect_advisory.sh — coverage for the dialect hook (#1405, #1425).
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   ask      → exit 0 + stdout permissionDecision=ask carrying the given marker
#   advisory → exit 0 + stdout additionalContext + stderr with the tag + remedy
#   silent   → exit 0 + both streams empty
#
# $SHELL is forced per case: three of the four shapes are zsh-specific, so the
# non-zsh cases are part of the contract rather than environment noise. The
# nested-heredoc shape is shell-general and is asserted under bash too.
#
# Usage: bash tests/hooks/advisory-nudge/test_zsh_dialect_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/zsh-dialect-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command [shell]
#   expected — `silent`, `advisory`, or `ask:<marker>`; the marker is matched
#   against the DECODED reason, because the emitted JSON escapes non-ASCII.
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
    ask:*)
      local marker="${expected#ask:}"
      [ "$rc" -eq 0 ] || ok=0
      local decoded
      decoded=$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)["hookSpecificOutput"]
except Exception:
    sys.exit(0)
print(d.get("permissionDecision", ""))
print(d.get("permissionDecisionReason", ""))
') || ok=0
      case "$decoded" in ask*) ;; *) ok=0 ;; esac
      case "$decoded" in *"$marker"*) ;; *) ok=0 ;; esac
      ;;
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      # The advisory reaches the actor only through additionalContext; stderr
      # is what the fire ledger grades the fire on. Both, or neither counts.
      echo "$out" | grep -q '"additionalContext"' || ok=0
      echo "$err" | grep -q "\[zsh-dialect-advisory\]" || ok=0
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

# === ASK — `=word` is a command path lookup =================================

run_case "a separator line of equals signs" "ask:======" \
  'echo ======'
run_case "an argument starting with =" "ask:=foo" \
  'echo =foo'
run_case "== outside [[ ]] is such a word" "ask:==" \
  '[ "$x" == a ] && print yes'
run_case "an assignment whose value starts with =" "ask:=foo" \
  'V==foo print hi'

run_case "== inside [[ ]] is an operator" silent \
  '[[ $x == a ]] && print yes'
run_case "an ordinary assignment is not the shape" silent \
  'print a=b'
run_case "a bare = is not expanded" silent \
  'test 1 = 1'
run_case "a quoted == is safe" silent \
  "print '=='"
run_case "= inside a long flag is not word-leading" silent \
  'git diff --stat=2'
# Arithmetic and comments are not words zsh expands, so `==` there runs fine.
run_case "== inside (( )) is an arithmetic operator" silent \
  '(( x == y )) && print yes'
run_case "== inside \$(( )) is an arithmetic operator" silent \
  'print $(( 1 == 1 ))'
run_case "== inside a comment is never expanded" silent \
  'print hi # a==b'
# Control for the comment mask: `${#x}` holds a `#` that begins no word, so
# the real =word after it must still ask.
run_case "a # inside \${#x} is not a comment" "ask:==z" \
  'print ${#x} ==z'

# === ASK — an unmatched [ inside a pattern operator =========================

run_case "unmatched [[ in a # pattern" 'ask:${w#[[}' \
  'print ${w#[[}'
run_case "double quotes do not protect the pattern" 'ask:${w#[[}' \
  'print "${w#[[}"'
run_case "unmatched [ in a / pattern" 'ask:${w/[[/Z}' \
  'print ${w/[[/Z}'

run_case "a closed character class is a valid pattern" silent \
  'print ${w#[a-z]}'
run_case "a default value is not parsed as a pattern" silent \
  'print ${w:-[[}'
run_case "single quotes do protect it" silent \
  "print '\${w#[[}'"
# Inside a class `[` is a member and a leading `]` is a member, so these close.
run_case "[[] is a closed class matching a literal [" silent \
  'print ${w#[[]}'
run_case "a leading ] is a class member" silent \
  'print ${w#[]]}'
run_case "a POSIX class inside a class closes" silent \
  'print ${w#[[:alpha:]]}'
run_case "an open POSIX class is still unmatched" 'ask:${w#[[:alpha:]}' \
  'print ${w#[[:alpha:]}'
# zsh 5.9 treats an open class led by `]` as a no-match, not a bad pattern.
run_case "an open class led by ] is not a bad pattern" silent \
  'print ${w#[]}'

# === ASK — a heredoc opener shadowed by its own delimiter ===================

run_case "nested opener reusing the outer delimiter" "ask:EOF" \
  "cat <<'EOF'
x
cat <<'EOF'
hi
EOF
EOF"
run_case "the same shape under bash, which shares it" "ask:PY" \
  "python3 - <<'PY'
x = 1
python3 - <<'PY'
y = 2
PY
PY" /bin/bash

run_case "a nested heredoc with its own delimiter is fine" silent \
  "cat <<'OUTER'
py <<'EOF'
x
EOF
OUTER"
run_case "a body that merely mentions the delimiter" silent \
  "python3 - <<'PY'
print(\"write it as: cat <<'PY'\")
PY"
run_case "a single ordinary heredoc" silent \
  "cat <<'EOF'
plain
EOF"

# === ADVISORY — word split, where intent is not decidable ===================

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
run_case "the = shape is zsh-only too" silent \
  'echo ======' /bin/bash

# === SILENT — masked or opted out ===========================================

run_case "the original opt-out marker still silences it" silent \
  'set -- $spec # word-split:ok'
run_case "the shared marker silences every detector" silent \
  'echo ====== # zsh-dialect:ok'
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
