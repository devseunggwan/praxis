#!/bin/bash
# test_block_unmatched_glob.sh — coverage for block-unmatched-glob.sh
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr empty
#
# Cases run against a temporary fixture tree so that "matches" and
# "matches nothing" are both deterministic, independent of the checkout.
#
# Usage: bash tests/hooks/preflight-gate/test_block_unmatched_glob.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export ROOT_DIR
HOOK="$ROOT_DIR/hooks/preflight-gate/block-unmatched-glob/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# The gate delegates every verdict to a real zsh and fails open without one, so
# each blocking assertion below would silently become a fail-open assertion.
# Skip loudly rather than report 14 unexplained failures (CI installs zsh).
if ! command -v zsh >/dev/null 2>&1; then
  # stdout marker so run-tests.sh folds this skip into strict mode (#1170).
  echo "PRAXIS_SUBSKIP: zsh $0"
  echo "SKIPPED: zsh not available — block-unmatched-glob has nothing to delegate to"
  exit 0
fi

# The gate reads $SHELL to decide whether the executing shell is zsh at all.
# CI runners report bash, so without this every blocking assertion would test
# the non-zsh pass-through path instead. The portability block near the end
# overrides SHELL per invocation and is unaffected.
SHELL=$(command -v zsh)
export SHELL

FIXTURE=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$FIXTURE"' EXIT
mkdir -p "$FIXTURE/logs" "$FIXTURE/nested/deep"
# Each exists so a cd-prefix pass case cannot pass as "missing target".
mkdir -p "$FIXTURE/+1" "$FIXTURE/^logs" "$FIXTURE/logs#" "$FIXTURE/a~b"
ln -s "$FIXTURE/nested/deep" "$FIXTURE/deeplink"
: >"$FIXTURE/logs/alpha.log"
: >"$FIXTURE/logs/beta.log"
: >"$FIXTURE/nested/deep/found.txt"

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected tool_name command
#   expected: "block" (exit 2, stderr non-empty) | "pass" (exit 0, stderr empty)
run_case() {
  local name="$1" expected="$2" tool_name="$3" command="$4"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
    "cwd": sys.argv[3],
}))' "$tool_name" "$command" "$FIXTURE")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  if [ "$expected" = "block" ]; then
    [ "$rc" = 2 ] && [ -n "$err" ] || ok=0
  else
    [ "$rc" = 0 ] && [ -z "$err" ] || ok=0
  fi

  if [ "$ok" = 1 ]; then
    PASS=$((PASS + 1))
    printf '  ok   %s\n' "$name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    printf '  FAIL %s (expected=%s rc=%s stderr_len=%s)\n' \
      "$name" "$expected" "$rc" "${#err}"
  fi
}

echo "== block-unmatched-glob =="

# --- blocking cases: the shell would abort these before they run -------------
run_case "absolute glob matching nothing" block Bash \
  "ls -d $FIXTURE/nosuchdir/*.json"
run_case "relative glob matching nothing" block Bash \
  "cat *.nonexistent-xyz"
run_case "unquoted find -name (aborts before find runs)" block Bash \
  "find $FIXTURE -maxdepth 1 -name *.nonexistent-xyz"
# A pipeline is cut at the `|` and each side judged as its own simple command
# (#1405). Verified against live zsh: `no matches found`, rc=1 — the abort the
# gate now reports rather than passing through.
run_case "pipeline segment with an unmatched glob blocks" block Bash \
  "echo start | cat $FIXTURE/nope/*.txt"
run_case "intermediate directory component unmatched" block Bash \
  "ls $FIXTURE/*/absent-leaf"
run_case "original incident shape (multi-arg ls with 2>/dev/null)" block Bash \
  "ls -d $FIXTURE/logs $FIXTURE/*/resume 2>/dev/null"

# --- passing cases ----------------------------------------------------------
run_case "glob that matches" pass Bash \
  "ls $FIXTURE/logs/*.log"
run_case "multi-level glob that matches" pass Bash \
  "ls $FIXTURE/*/deep/*.txt"
run_case "single-quoted pattern is never expanded" pass Bash \
  "find $FIXTURE -maxdepth 1 -name '*.nonexistent-xyz'"
run_case "double-quoted pattern is never expanded" pass Bash \
  "grep -r \"*.nonexistent-xyz\" $FIXTURE"
run_case "zsh nullglob qualifier makes zero matches legal" pass Bash \
  "ls $FIXTURE/*.nonexistent-xyz(N)"
run_case "no glob metacharacters at all" pass Bash \
  "git status"
run_case "inert pattern inside a quoted echo" pass Bash \
  "echo \"no matches for *.nonexistent-xyz\""
run_case "non-Bash tool is ignored" pass Read \
  "$FIXTURE/*.nonexistent-xyz"

# --- regression: cases where a pure-Python model disagreed with live zsh ----
# Every case below was verified against `zsh -f` before being asserted here.
run_case "test builtin bracket is not a glob" pass Bash \
  "[ -d . ]"
run_case "double-bracket conditional is not a glob" pass Bash \
  "[[ -d . ]]"
run_case "arithmetic expansion is passed through" pass Bash \
  "echo \$((2*3))"
run_case "pattern inside a trailing comment" pass Bash \
  "echo ok # *.missing"
run_case "variable prefix is unresolvable → pass" pass Bash \
  "ls \$HOME/*"
run_case "recursive ** is zsh syntax, resolved by zsh" pass Bash \
  "echo $FIXTURE/**/found.txt"
run_case "noglob disables the failure" pass Bash \
  "noglob print *.nonexistent-xyz"
run_case "setopt in-command changes glob behavior → pass" pass Bash \
  "setopt nullglob; print *.nonexistent-xyz"
run_case "brace expansion with no match still aborts" block Bash \
  "echo {logs,nested}/*.nonexistent-xyz"
# A leading `cd <existing dir> &&` is judged with that dir as the cwd (#1554).
run_case "cd <existing dir> && judges the rest there" block Bash \
  "cd $FIXTURE && echo *.nonexistent-xyz"
# The reason `cd` used to pass through: the glob matches in the new dir only.
run_case "cd prefix: glob matching only in the cd target passes" pass Bash \
  "cd $FIXTURE/logs && echo *.log"
run_case "cd prefix: ./ relative target" block Bash \
  "cd ./logs && echo *.nonexistent-xyz"
run_case "cd prefix: relative target, glob matches there" pass Bash \
  "cd ./logs && echo *.log"
run_case "cd prefix: .. is resolved logically" block Bash \
  "cd ./logs/.. && echo logs/*.nonexistent-xyz"
run_case "cd prefix: no spaces around &&" block Bash \
  "cd ./logs&&echo *.nonexistent-xyz"
run_case "cd prefix: leading whitespace" block Bash \
  "  cd ./logs && echo *.nonexistent-xyz"
run_case "cd prefix: rest is cut at ; and | as usual" block Bash \
  "cd ./logs && echo ok; echo *.nonexistent-xyz | cat"
run_case "cd prefix: ../ target is judged too" block Bash \
  "cd ./nested/deep/../.. && echo *.nonexistent-xyz"
# A bare relative target is where CDPATH (or posixcd) can redirect `cd`.
run_case "cd prefix: bare relative target passes (CDPATH)" pass Bash \
  "cd logs && echo *.nonexistent-xyz"
# A later cwd change makes every later segment's cwd unknown.
run_case "cd prefix: later cd in the rest passes" pass Bash \
  "cd ./logs && cd ..; echo logs/*.log"
run_case "cd segment: later segment runs in the new dir, passes" pass Bash \
  "cd logs; echo *.log"
run_case "pushd segment: later segment runs in the new dir, passes" pass Bash \
  "pushd logs; echo *.log"
run_case "cd prefix: missing target passes (rest never runs)" pass Bash \
  "cd $FIXTURE/no-such-dir && echo *.nonexistent-xyz"
run_case "cd prefix: cd - passes" pass Bash \
  "cd - && echo *.nonexistent-xyz"
run_case "cd prefix: option passes" pass Bash \
  "cd -P ./logs && echo *.nonexistent-xyz"
run_case "cd prefix: quoted target passes" pass Bash \
  "cd \"./logs\" && echo *.nonexistent-xyz"
run_case "cd prefix: glob in the target passes" pass Bash \
  "cd ./lo* && echo *.nonexistent-xyz"
run_case "cd prefix: +N (directory stack) passes" pass Bash \
  "cd +1 && echo *.nonexistent-xyz"
run_case "cd prefix: ^ (extendedglob negation) passes" pass Bash \
  "cd ./^logs && echo *.nonexistent-xyz"
run_case "cd prefix: # (extendedglob repetition) passes" pass Bash \
  "cd ./logs# && echo *.nonexistent-xyz"
run_case "cd prefix: non-leading ~ (extendedglob exclusion) passes" pass Bash \
  "cd ./a~b && echo *.nonexistent-xyz"
run_case "cd prefix: .. after a symlink passes (chaselinks lands elsewhere)" pass Bash \
  "cd ./deeplink/.. && echo *.nonexistent-xyz"
run_case "cd prefix: ~user form passes" pass Bash \
  "cd ~nosuchuser-xyz && echo *.nonexistent-xyz"
run_case "cd prefix: a second && still passes through" pass Bash \
  "cd ./logs && true && echo *.nonexistent-xyz"
run_case "cd prefix: || after cd passes through" pass Bash \
  "cd ./logs || echo *.nonexistent-xyz"
run_case "cd prefix: cd not in leading position passes" pass Bash \
  "echo ok && cd ./logs && echo *.nonexistent-xyz"
run_case "cd prefix: rest with background & passes" pass Bash \
  "cd ./logs && echo *.nonexistent-xyz &"
run_case "unexecuted branch passes through" pass Bash \
  "true || echo *.nonexistent-xyz"
run_case "if/then body passes through" pass Bash \
  "if false; then echo *.nonexistent-xyz; fi"
run_case "assignment value is not glob-expanded" pass Bash \
  "FOO=*.nonexistent-xyz print ok"
run_case "flag-attached glob is expanded by the shell" block Bash \
  "grep -r --include=*.nonexistent-xyz needle $FIXTURE"
run_case "mixed quoting inside one word" block Bash \
  "echo $FIXTURE/logs/alpha*\".nonexistent-xyz\""
# A semicolon is cut the same way, so the qualifier on the FIRST segment no
# longer shields the bare pattern in the second. Verified against live zsh: rc=1.
run_case "qualifier does not carry across a semicolon" block Bash \
  "print *.nonexistent-xyz(N); print *.nonexistent-xyz"
run_case "qualifier applies to its own occurrence only" pass Bash \
  "print *.nonexistent-xyz(N)"

# --- segmentation boundary (#1405) -----------------------------------------
# `&&` / `||` / `&` / heredocs still pass through whole: cutting them would
# judge a segment that may never run, or a heredoc body that is data.
run_case "&& still passes through" pass Bash \
  "true && echo *.nonexistent-xyz"
run_case "background & still passes through" pass Bash \
  "echo *.nonexistent-xyz &"
run_case "background &| still passes through" pass Bash \
  "echo *.nonexistent-xyz &|"
run_case "background &! still passes through" pass Bash \
  "echo *.nonexistent-xyz &!"
run_case "|& pipe still passes through" pass Bash \
  "echo *.nonexistent-xyz |& cat"
run_case "|& followed by a redirect still passes through" pass Bash \
  "echo *.nonexistent-xyz |&> /dev/null cat"
run_case "background job beside a redirect still passes through" pass Bash \
  "echo *.nonexistent-xyz 2>&1 &"

# An `&` touching a redirect arrow moves a file descriptor and detaches
# nothing (live zsh: no `$!` for any of these), so the glob is still judged.
run_case "2>&1 is a redirect, not a background job" block Bash \
  "ls $FIXTURE/*.nonexistent-xyz 2>&1 | head -3"
run_case ">&2 is a redirect" block Bash \
  "echo $FIXTURE/*.nonexistent-xyz >&2"
run_case "<&0 is a redirect" block Bash \
  "cat $FIXTURE/*.nonexistent-xyz <&0"
run_case "2>&- is a redirect" block Bash \
  "ls $FIXTURE/*.nonexistent-xyz 2>&-"
run_case "&>/dev/null is a redirect" block Bash \
  "ls $FIXTURE/*.nonexistent-xyz &>/dev/null"
run_case "&>>file is a redirect" block Bash \
  "ls $FIXTURE/*.nonexistent-xyz &>>/dev/null"
run_case "&>| clobber is a redirect" block Bash \
  "ls $FIXTURE/*.nonexistent-xyz &>|/dev/null"
run_case "2>&1 with a matching glob still passes" pass Bash \
  "ls $FIXTURE/logs/*.log 2>&1 | head -3"
# `noglob` is a prefix, so only its own segment is dropped — the neighbour in
# the same line is still judged.
run_case "noglob shields its own segment only" block Bash \
  "noglob print *.nonexistent-xyz; print *.nonexistent-xyz"
# ...while `setopt` outlives its command, so the whole line passes through.
run_case "unsetopt anywhere passes the whole line through" pass Bash \
  "print ok; unsetopt nomatch; print *.nonexistent-xyz"
# A separator inside quotes is not a separator, so the word stays one span.
# The quote character goes in via a variable: written inline, the nested
# escaping reads to shellcheck as a command name ending in an apostrophe.
SQ="'"
run_case "quoted separator does not split the command" pass Bash \
  "print ${SQ}a;b${SQ}"

# --- regression: word position decides meaning (round 6) --------------------
# Every case below was verified against live zsh before being asserted here.
run_case "prefix assignment does not shield the rest of the command" block Bash \
  "LC_ALL=C ls $FIXTURE/nosuchdir/*.json"
run_case "disabler word as an argument is not a disabler" block Bash \
  "echo noglob $FIXTURE/nosuchdir/*.json"
run_case "control word as an argument is not control flow" block Bash \
  "echo cd $FIXTURE/nosuchdir/*.json"
run_case "quoted pipe is not a pipeline" block Bash \
  "grep 'a|b' $FIXTURE/nosuchdir/*.json"
run_case "single-quoted dollar is not an expansion" block Bash \
  "grep '\$value' $FIXTURE/nosuchdir/*.json"
# A `$` makes only its own word undecidable (#1555); the literal glob beside it
# is still judged. Live zsh: `no matches found`, rc=1.
run_case "double-quoted dollar in another word does not shield the glob" block Bash \
  "grep \"\$value\" $FIXTURE/nosuchdir/*.json"
run_case "disabler in command position still passes through" pass Bash \
  "noglob print $FIXTURE/nosuchdir/*.json"
run_case "assignment-only word is still not expanded" pass Bash \
  "FOO=$FIXTURE/nosuchdir/*.json print ok"

# --- dynamic expansion is scoped to its own word (#1555) --------------------
# Every verdict below was checked against live zsh (`zsh -f`, `setopt nomatch`)
# in a fixture with the same layout. Single-quoted bash strings keep each
# command byte-for-byte as zsh sees it; cwd is FIXTURE.
run_case "dynamic: \$? in a later segment does not shield the glob" block Bash \
  'ls *.nonexistent-xyz; echo "exit=$?"'
run_case "dynamic: backtick in another word does not shield the glob" block Bash \
  'echo `date` *.nonexistent-xyz'
run_case "dynamic: backtick inside double quotes, glob beside it" block Bash \
  'echo "`date`" *.nonexistent-xyz'
run_case "dynamic: prefix assignment with a substitution" block Bash \
  'X=$(date) ls *.nonexistent-xyz'
run_case "dynamic: braced parameter in another pipeline segment" block Bash \
  'echo ${HOME} | ls logs/*.nonexistent-xyz'
run_case "dynamic: escaped dollar is literal text" block Bash \
  'echo \$HOME *.nonexistent-xyz'
# The body of an expansion belongs to its word: its separators, `cd`, `&&`,
# quotes, and spaces neither cut the line nor pass it through.
run_case "dynamic: ; and cd inside \$( ) stay inside it" block Bash \
  'echo $(cd /; true) *.nonexistent-xyz'
run_case "dynamic: && inside \$( ) stays inside it" block Bash \
  'echo $(true && true) *.nonexistent-xyz'
run_case "dynamic: quoted ) inside \$( ) does not end it" block Bash \
  'echo "$(echo ")")" *.nonexistent-xyz'
run_case "dynamic: spaces inside \${ } stay inside it" block Bash \
  'echo ${x:-a b} *.nonexistent-xyz'
run_case "dynamic: \$'...' with a space and an escaped quote" block Bash \
  "echo \$'it\\'s a' *.nonexistent-xyz"
run_case "dynamic: matching glob beside a substitution passes" pass Bash \
  'echo $(echo; true) logs/*.log'
# The word holding the expansion is never probed.
run_case "dynamic: glob word with a quoted variable prefix" pass Bash \
  'ls "$X"/*.nonexistent-xyz'
run_case "dynamic: glob word with a variable in the middle" pass Bash \
  'ls logs/*$X.nonexistent-xyz'
run_case "dynamic: glob inside \${ } is part of the dynamic word" pass Bash \
  'echo ${x:-*.nonexistent-xyz}'
run_case "dynamic: \$'...' body is quoted text" pass Bash \
  "echo \$'*.nonexistent-xyz'"
run_case "dynamic: arithmetic body with spaces is one word" pass Bash \
  'echo $(( 2 *.nonexistent-xyz ))'
run_case "arithmetic command (( )) is not a pathname context" pass Bash \
  '(( n *.nonexistent-xyz ))'
# A dynamic command word may be `noglob`, `setopt`, or `cd`, in any segment.
run_case "dynamic: command word in the same segment passes" pass Bash \
  '$CMD *.nonexistent-xyz'
run_case "dynamic: command word in a later segment passes the line" pass Bash \
  'echo *.nonexistent-xyz; $CMD x'
run_case "dynamic: command word after a prefix assignment passes" pass Bash \
  'X=1 ${CMD} *.nonexistent-xyz'
# An expansion whose end the scanner cannot find passes the whole line.
run_case "dynamic: unterminated \$( passes" pass Bash \
  'echo $(ls *.nonexistent-xyz'
run_case "dynamic: case inside \$( ) passes (pat) breaks paren counting)" pass Bash \
  'echo $(case a in a) echo x;; esac) *.nonexistent-xyz'
run_case "dynamic: quoted case text inside \$( ) is not a case" block Bash \
  "echo \$(: 'case') *.nonexistent-xyz"
run_case "dynamic: double-quoted case text inside \$( ) is not a case" block Bash \
  'echo $(: "case") *.nonexistent-xyz'
# zsh runs every substitution before any filename generation, so `touch` makes
# the glob match and zsh runs the line (rc=0). The gate judges the glob anyway,
# the same trade it already makes for `touch made.side; ls *.side`. The hook
# must still never run the substitution itself.
run_case "dynamic: substitution side effect is not modelled" block Bash \
  'ls $(touch made.side) *.side'
if [ -e "$FIXTURE/made.side" ]; then
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("substitution executed by the hook")
  printf '  FAIL %s\n' "substitution executed by the hook ($FIXTURE/made.side created)"
  rm -f "$FIXTURE/made.side"
else
  PASS=$((PASS + 1)); printf '  ok   %s\n' "hook never runs a command substitution"
fi

# `|` and `;` in a case arm or a glob group are pattern grammar, not
# separators. The old `$` pass-through used to hide this, since `case $f` and
# `[[ $f = (…) ]]` nearly always carry a `$`. Live zsh runs each without a
# `no matches found`.
run_case "pattern grammar: case arms with | and ;;" pass Bash \
  'case $x in a) echo;; *.c|*.h) echo hi;; esac'
run_case "pattern grammar: case inside a for loop" pass Bash \
  'for f in *.txt; do case $f in *.md|*.rst) echo doc;; *.c|*.h) echo src;; esac; done'
run_case "pattern grammar: case on a substitution" pass Bash \
  'case $(uname) in Darwin|Linux) echo unix;; *BSD|*bsd) echo bsd;; esac'
run_case "pattern grammar: glob group inside [[ ]]" pass Bash \
  'if [[ $f = (*.c|*.h|*.go) ]]; then echo y; fi'
run_case "pattern grammar: glob group as a word" pass Bash \
  'echo $x (logs|*.nonexistent-xyz|nested)'
run_case "pattern grammar: a separator outside parens still cuts" block Bash \
  '[[ -d . ]]; ls logs/*.nonexistent-xyz'

# --- newline is a separator; constructs stop the judgement (#1559) ----------
# Verified against live zsh (`zsh -f`, `setopt nomatch`) in the same layout.
run_case "newline: second plain line is judged" block Bash \
  $'echo ok\nls *.nonexistent-xyz'
run_case "newline: comment line, then a glob line" block Bash \
  $'# look for logs\nls *.nonexistent-xyz'
run_case "newline: matching glob on a later line passes" pass Bash \
  $'echo ok\nls logs/*.log'
run_case "newline: inside quotes it does not split, glob beside still judged" block Bash \
  $'echo "a\nb" *.nonexistent-xyz'
run_case "newline: inside quotes, matching glob passes" pass Bash \
  $'echo "a\nb" logs/*.log'
run_case "cd line: rest judged in the cd target" block Bash \
  $'cd ./logs\nls *.nonexistent-xyz'
run_case "cd line: glob matching only in the cd target passes" pass Bash \
  $'cd ./logs\necho *.log'
run_case "cd ;: rest judged in the cd target" block Bash \
  'cd ./logs; echo *.nonexistent-xyz'
run_case "cd ;: glob matching only in the cd target passes" pass Bash \
  'cd ./logs; echo *.log'
run_case "cd line: missing target passes" pass Bash \
  $'cd ./no-such-dir\nls *.nonexistent-xyz'
run_case "newline: setopt on an earlier line passes" pass Bash \
  $'echo ok\nsetopt nullglob\nls *.nonexistent-xyz'
run_case "newline: && still passes through" pass Bash \
  $'ls *.nonexistent-xyz &&\necho ok'
run_case "newline: backslash continuation passes" pass Bash \
  $'ls \\\n  *.nonexistent-xyz'
run_case "newline: heredoc body passes" pass Bash \
  $'cat <<EOF\n*.nonexistent-xyz\nEOF'
run_case "newline: subshell across lines passes" pass Bash \
  $'(\nls *.nonexistent-xyz\n)'
# A construct's body may run zero times; everything from it on is skipped.
# The `;` forms were blocked before #1559 although zsh runs them (rc=0).
run_case "construct: while false; do …; done passes" pass Bash \
  'while false; do :; echo *.nonexistent-xyz; done'
run_case "construct: for over an empty list passes" pass Bash \
  'for f in; do :; echo *.nonexistent-xyz; done'
run_case "construct: if false; then …; fi passes" pass Bash \
  'if false; then :; echo *.nonexistent-xyz; fi'
run_case "construct: multi-line for loop passes" pass Bash \
  $'for f in a\ndo\n  echo *.nonexistent-xyz\ndone'
run_case "construct: multi-line if passes" pass Bash \
  $'if false\nthen\n  echo *.nonexistent-xyz\nfi'
run_case "construct: function body (name()) passes" pass Bash \
  $'f() {\n  ls *.nonexistent-xyz\n}'
run_case "construct: function body (name ()) passes" pass Bash \
  'f () { ls *.nonexistent-xyz; }'
run_case "construct: segments before it are still judged" block Bash \
  'ls *.nonexistent-xyz; if true; then echo; fi'
# Review round 1 (#1559): each was blocked here although zsh runs it cleanly.
run_case "construct: zsh foreach … end passes" pass Bash \
  $'foreach f ( )\necho *.nonexistent-xyz\nend'
run_case "construct: precommand time before a short for passes" pass Bash \
  $'time for f in\necho *.nonexistent-xyz'
run_case "construct: ! before a short repeat passes" pass Bash \
  $'! repeat 0\necho *.nonexistent-xyz'
run_case "construct: coproc before a loop passes" pass Bash \
  $'coproc for f in\necho *.nonexistent-xyz'
run_case "state: set -o nullglob on an earlier line passes" pass Bash \
  $'set -o nullglob\nls *.nonexistent-xyz'
run_case "state: set +o nomatch passes" pass Bash \
  'set +o nomatch; ls *.nonexistent-xyz'
run_case "state: emulate passes" pass Bash \
  $'emulate sh\nls *.nonexistent-xyz'
run_case "state: builtin setopt passes" pass Bash \
  $'builtin setopt nullglob\nls *.nonexistent-xyz'
run_case "state: eval of setopt passes" pass Bash \
  "eval 'setopt nullglob'; ls *.nonexistent-xyz"
run_case "state: set -- (positionals only) is still judged" block Bash \
  'set -- a b; ls *.nonexistent-xyz'
run_case "state: set -o pipefail does not touch globbing, still judged" block Bash \
  $'set -o pipefail\nls *.nonexistent-xyz'
run_case "state: set -euo pipefail is still judged" block Bash \
  'set -euo pipefail; ls *.nonexistent-xyz'
run_case "state: set -G (nullglob letter) passes" pass Bash \
  'set -G; ls *.nonexistent-xyz'
run_case "state: set -o null_glob passes" pass Bash \
  'set -o null_glob; ls *.nonexistent-xyz'
run_case "condition: newline inside [[ ]] is whitespace" pass Bash \
  $'[[ a ==\n*.nonexistent-xyz ]] ; echo ok'
run_case "comment right after ; hides its quote" pass Bash \
  $'echo a;# it\'s\ntrue; cd logs\nls *.log # \''
run_case "stray ;; passes (zsh rejects it)" pass Bash \
  $'echo ok;;\necho *.nonexistent-xyz'
# Review round 2 (#1559): blocked here although zsh runs each cleanly.
run_case "construct: f(){ on its own line passes" pass Bash \
  $'f(){\nls *.nonexistent-xyz\n}'
run_case "construct: f (){ on its own line passes" pass Bash \
  $'f (){\nls *.nonexistent-xyz\n}'
run_case "quoted command word: backslash-escaped setopt passes" pass Bash \
  $'\\setopt nullglob\nls *.nonexistent-xyz'
run_case "quoted command word: quoted cd passes" pass Bash \
  $'\'cd\' logs\nls *.log'
run_case "quoted command word: a quoted path is still judged" block Bash \
  '"/bin/ls" *.nonexistent-xyz'
run_case "precommand: time noglob shields its own segment" pass Bash \
  'time noglob ls *.nonexistent-xyz'
run_case "precommand: ! before (( )) is arithmetic" pass Bash \
  '! (( 2*3 ))'
run_case "precommand: time before a plain command is still judged" block Bash \
  'time ls *.nonexistent-xyz'

# --- assignment values and shell-state words (#1561) -----------------------
# Verified against live zsh: each pass case runs with rc=0 and no
# `no matches found`; each block case aborts.
run_case "typeset family: local value is an assignment" pass Bash \
  'local foo=*.nonexistent-xyz'
run_case "typeset family: export value is an assignment" pass Bash \
  'export FOO=*.nonexistent-xyz'
run_case "typeset family: readonly value is an assignment" pass Bash \
  'readonly R=*.nonexistent-xyz'
run_case "typeset family: declare bracket value is an assignment" pass Bash \
  'declare D=[a]'
run_case "typeset family: integer arithmetic value is an assignment" pass Bash \
  'integer i=2*3'
run_case "typeset family: a non-assignment argument is still judged" block Bash \
  'typeset x=1 *.nonexistent-xyz'
run_case "typeset family: export then a glob segment is still judged" block Bash \
  'export FOO=bar; ls *.nonexistent-xyz'
run_case "typeset family: builtin export globs its arguments" block Bash \
  'builtin export FOO=*.nonexistent-xyz'
run_case "typeset family: command export globs its arguments" block Bash \
  'command export FOO=*.nonexistent-xyz'
run_case "typeset family: time export keeps assignment semantics" pass Bash \
  'time export FOO=*.nonexistent-xyz'
run_case "typeset family: subscript argument is an assignment" pass Bash \
  'typeset h[*.nonexistent-xyz]=v'
run_case "assignment: array element" pass Bash \
  'a[1]=x'
run_case "assignment: glob in an element subscript" pass Bash \
  'typeset -A h; h[*.nonexistent-xyz]=v'
run_case "assignment: append" pass Bash \
  'a+=*.nonexistent-xyz'
run_case "state: options[...] assignment passes the line" pass Bash \
  'options[nullglob]=on; ls *.nonexistent-xyz'
run_case "state: disable -p passes the line" pass Bash \
  "disable -p '*'; ls *.nonexistent-xyz"
run_case "word blanks: CR keeps the (N) qualifier attached" pass Bash \
  $'echo *.nonexistent-xyz\r(N)'
run_case "word blanks: NBSP keeps the (N) qualifier attached" pass Bash \
  $'echo *.nonexistent-xyz\xc2\xa0(N)'

# The executing shell's glob options must reach the probe: under
# `setopt extendedglob`, `^<something>` is a negation pattern that DOES match
# here, so a probe running plain `zsh -f` would wrongly report no matches.
if zsh -f -c 'setopt extendedglob' 2>/dev/null; then
  if python3 - "$FIXTURE" <<'PYEOF'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location(
    "h", os.path.join(os.environ["ROOT_DIR"],
                      "hooks/preflight-gate/block-unmatched-glob/impl.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
span = "logs/^*.nonexistent-xyz"
without = m.zsh_finds_no_match(span, sys.argv[1])
with_opt = m.zsh_finds_no_match(span, sys.argv[1], options=("extendedglob",))
# Without the option the pattern is literal and matches nothing; with it, the
# negation matches both fixture logs. Divergence proves the option is honored.
sys.exit(0 if (without and not with_opt) else 1)
PYEOF
  then
    PASS=$((PASS + 1)); printf '  ok   %s\n' "probe honors a forwarded glob option (extendedglob)"
  else
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("probe honors forwarded glob option")
    printf '  FAIL %s\n' "probe honors a forwarded glob option (extendedglob)"
  fi
fi

# --- security: a preflight gate must never execute the command it inspects --
# zsh glob qualifiers can carry code (`*(e:'cmd':)`), which the probe would
# otherwise run before the permission boundary. The side effect would land in
# the payload cwd — FIXTURE — so that is the only directory worth asserting on.
run_case "qualified pattern is never probed" pass Bash \
  "ls *(e:'touch SIDE_EFFECT':)"
if [ -e "$FIXTURE/SIDE_EFFECT" ]; then
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("qualifier code executed during probe")
  printf '  FAIL %s\n' "qualifier code executed during probe ($FIXTURE/SIDE_EFFECT created)"
  rm -f "$FIXTURE/SIDE_EFFECT"
else
  PASS=$((PASS + 1)); printf '  ok   %s\n' "no side effect from qualifier probe"
fi

# Guard the guard: with the qualifier skip removed the probe DOES execute the
# body, so this asserts the check above can actually fail.
python3 - "$FIXTURE" <<'PYEOF'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location(
    "h", os.path.join(os.environ["ROOT_DIR"],
                      "hooks/preflight-gate/block-unmatched-glob/impl.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.zsh_finds_no_match("*(e:'touch CANARY':)", sys.argv[1])
PYEOF
if [ -e "$FIXTURE/CANARY" ]; then
  PASS=$((PASS + 1)); printf '  ok   %s\n' "canary confirms the probe would execute an unskipped qualifier"
  rm -f "$FIXTURE/CANARY"
else
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("canary did not fire")
  printf '  FAIL %s\n' "canary did not fire — the security test cannot detect a regression"
fi

# --- conditional expressions ------------------------------------------------
run_case "pattern inside [[ ]] is a match operand" pass Bash \
  "[[ abc = *.nonexistent-xyz ]]"

# --- portability: the premise is zsh-specific -------------------------------
# bash and fish hand the literal pattern to the command, which then runs and
# whose stderr `2>/dev/null` does suppress. Blocking there is a false positive.
for shell_path in /bin/bash /usr/bin/fish ""; do
  label="SHELL=${shell_path:-<unset>} does not block"
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "cwd": sys.argv[2],
}))' "ls -d $FIXTURE/nosuchdir/*.json" "$FIXTURE")
  err_file=$(mktemp)
  if [ -n "$shell_path" ]; then
    echo "$payload" | SHELL="$shell_path" "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | env -u SHELL "$HOOK" >/dev/null 2>"$err_file"
  fi
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  if [ "$rc" = 0 ] && [ -z "$err" ]; then
    PASS=$((PASS + 1)); printf '  ok   %s\n' "$label"
  else
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$label")
    printf '  FAIL %s (rc=%s stderr_len=%s)\n' "$label" "$rc" "${#err}"
  fi
done
# Guard the guard: the very same payload under a zsh SHELL must still block, or
# the three assertions above would pass for the wrong reason.
err_file=$(mktemp)
echo "$payload" | SHELL=/bin/zsh "$HOOK" >/dev/null 2>"$err_file"
rc=$?; err=$(cat "$err_file"); rm -f "$err_file"
if [ "$rc" = 2 ] && [ -n "$err" ]; then
  PASS=$((PASS + 1)); printf '  ok   %s\n' "same payload under SHELL=zsh still blocks"
else
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("zsh control case")
  printf '  FAIL %s (rc=%s)\n' "same payload under SHELL=zsh still blocks" "$rc"
fi

# --- cd prefix: `~` expands against HOME ------------------------------------
# HOME is pointed at the fixture so the verdict does not depend on the runner.
for spec in "block|cd ~/logs && echo *.nonexistent-xyz" "pass|cd ~/logs && echo *.log"; do
  expected=${spec%%|*}; command=${spec#*|}
  label="cd prefix with ~ (HOME=fixture): $expected"
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}, "cwd": "/"}))' "$command")
  err_file=$(mktemp)
  echo "$payload" | HOME="$FIXTURE" "$HOOK" >/dev/null 2>"$err_file"
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"
  if { [ "$expected" = block ] && [ "$rc" = 2 ] && [ -n "$err" ]; } \
     || { [ "$expected" = pass ] && [ "$rc" = 0 ] && [ -z "$err" ]; }; then
    PASS=$((PASS + 1)); printf '  ok   %s\n' "$label"
  else
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$label")
    printf '  FAIL %s (rc=%s stderr_len=%s)\n' "$label" "$rc" "${#err}"
  fi
done

# --- fail-open --------------------------------------------------------------
err_file=$(mktemp)
printf 'not json' | "$HOOK" >/dev/null 2>"$err_file"
rc=$?
err=$(cat "$err_file")
rm -f "$err_file"
if [ "$rc" = 0 ] && [ -z "$err" ]; then
  PASS=$((PASS + 1))
  printf '  ok   %s\n' "malformed stdin fails open"
else
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("malformed stdin fails open")
  printf '  FAIL %s (rc=%s)\n' "malformed stdin fails open" "$rc"
fi

echo
echo "pass=$PASS fail=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
