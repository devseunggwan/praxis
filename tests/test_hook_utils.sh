#!/bin/bash
# tests/test_hook_utils.sh — unit coverage for the shared helpers in
# hooks/_lib/_hook_utils.py. Currently focused on the compound-cascade advisory
# primitives added for issue #229:
#
#   is_compound_command(command) -> bool
#   has_state_changing_redirect(command) -> bool
#   compound_cascade_hint(command) -> str  (empty unless both above are True)
#
# Each case spawns a python3 subprocess that imports the helper, prints the
# result, and the harness asserts expected==actual.
#
# Usage: bash tests/test_hook_utils.sh
# Exit:  0 = all pass, 1 = at least one fail

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_LIB_DIR="$REPO_ROOT/hooks/_lib"
export PYTHONPATH="$HOOK_LIB_DIR${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -f "$HOOK_LIB_DIR/_hook_utils.py" ]; then
  echo "FAIL: _hook_utils.py not found at $HOOK_LIB_DIR" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# Generic bool-helper runner.
#   $1 name   $2 helper   $3 expected (true|false)   $4 command
run_bool() {
  local name="$1" helper="$2" expected="$3" command="$4"
  local actual
  actual=$(python3 -c '
import sys
import _hook_utils as h
print(str(getattr(h, sys.argv[1])(sys.argv[2])).lower())
' "$helper" "$command")
  if [ "$actual" = "$expected" ]; then
    echo "PASS [$helper:$expected] $name"; PASS=$((PASS + 1))
  else
    echo "FAIL [$helper:expected=$expected got=$actual] $name"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# Hint runner — expect = true (non-empty hint) | false (empty hint).
run_hint() {
  local name="$1" expect="$2" command="$3"
  local actual
  actual=$(python3 -c '
import sys
import _hook_utils as h
print("true" if h.compound_cascade_hint(sys.argv[1]) else "false")
' "$command")
  if [ "$actual" = "$expect" ]; then
    echo "PASS [hint:$expect] $name"; PASS=$((PASS + 1))
  else
    echo "FAIL [hint:expected=$expect got=$actual] $name"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# is_compound_command
# ---------------------------------------------------------------------------

run_bool "single command"               is_compound_command false 'git status'
run_bool "&& separator"                 is_compound_command true  'mkdir /x && cp a b'
run_bool "|| separator"                 is_compound_command true  'test -d /x || mkdir /x'
run_bool "; separator"                  is_compound_command true  'echo a; echo b'
run_bool "| pipe separator"             is_compound_command true  'echo a | grep b'
run_bool "newline separator"            is_compound_command true  $'echo a\necho b'
run_bool "&& inside quotes"             is_compound_command false 'echo "a && b"'
run_bool "; inside quotes"              is_compound_command false 'echo "foo;bar"'
run_bool "empty command"                is_compound_command false ''
run_bool "whitespace only"              is_compound_command false '   '

# ---------------------------------------------------------------------------
# has_state_changing_redirect
# ---------------------------------------------------------------------------

run_bool "single > redirect"            has_state_changing_redirect true  'echo hi > /tmp/x'
run_bool "single >> redirect"           has_state_changing_redirect true  'echo hi >> /tmp/x'
run_bool "attached redirect >/tmp/x"    has_state_changing_redirect true  'echo hi >/tmp/x'
run_bool "embedded foo>/tmp/x"          has_state_changing_redirect true  'cat foo>/tmp/x'
run_bool "heredoc <<EOF"                has_state_changing_redirect true  $'cat <<EOF\nx\nEOF'
run_bool "mkdir as state change"        has_state_changing_redirect true  'mkdir -p /tmp/x'
run_bool "tee writes file"              has_state_changing_redirect true  'echo y | tee /tmp/x'
run_bool "cp mutates fs"                has_state_changing_redirect true  'cp a b'
run_bool "mv mutates fs"                has_state_changing_redirect true  'mv a b'
run_bool "rm mutates fs"                has_state_changing_redirect true  'rm /tmp/x'
run_bool "touch creates file"           has_state_changing_redirect true  'touch /tmp/x'
run_bool "curl -o downloads"            has_state_changing_redirect true  'curl -o /tmp/x https://e.com'
run_bool "curl --output downloads"      has_state_changing_redirect true  'curl --output /tmp/x https://e.com'
run_bool "wget -O downloads"            has_state_changing_redirect true  'wget -O /tmp/x https://e.com'
run_bool "cat file is read-only"        has_state_changing_redirect false 'cat /tmp/x'
run_bool "git status read-only"         has_state_changing_redirect false 'git status'
run_bool "echo > inside quotes"         has_state_changing_redirect false 'echo "a > b"'
run_bool "grep arrow in pattern"        has_state_changing_redirect false 'grep "a => b" /tmp/x'
run_bool "curl without -o"              has_state_changing_redirect false 'curl https://e.com'
# Reviewer-flagged false-positive regressions (review #229)
run_bool "echo quoted heredoc literal"  has_state_changing_redirect false 'echo "<<EOF something"'
run_bool "wget -o is log file"          has_state_changing_redirect false 'wget -o log.txt https://e.com'

# ---------------------------------------------------------------------------
# compound_cascade_hint — both detectors must be true
# ---------------------------------------------------------------------------

# Positives: compound + state-change
run_hint "heredoc redirect then pr create" true \
  $'cat <<EOF > /tmp/body.md\nbody\nEOF\ngh pr create --body-file /tmp/body.md'
run_hint "mkdir && cp"                     true 'mkdir -p /x && cp a /x/'
run_hint "curl -o && bash"                 true 'curl -o /tmp/run.sh https://e.com && bash /tmp/run.sh'
run_hint "rm && git push"                  true 'rm /tmp/x && git push'
run_hint "echo > file && cmd"              true 'echo new > /tmp/x && cat /tmp/x'

# Negatives: missing one of the two conditions
run_hint "single mkdir no hint"            false 'mkdir -p /tmp/x'
run_hint "single redirect no hint"         false 'echo hi > /tmp/x'
run_hint "compound no state change"        false 'git status && git log -3'
run_hint "compound cat | grep no hint"     false 'cat /tmp/x | grep foo'
run_hint "single gh pr create no hint"     false 'gh pr create --body "no marker"'
run_hint "empty command no hint"           false ''
# Reviewer-flagged: quoted "<<EOF" in compound command must NOT trigger hint
run_hint "quoted heredoc-like in compound" false 'echo "<<EOF foo" && git push'

# ---------------------------------------------------------------------------
# strip_prefix / _is_gh_binary — gh-gate normalization (issue #511)
# ---------------------------------------------------------------------------
#
# strip_prefix must peel the `command`/`builtin` shell wrappers so that
# `command gh pr merge` normalizes to argv[0]=="gh". A path-prefixed binary
# (`/usr/bin/gh`) is left intact by strip_prefix and instead recognized via
# the basename-aware _is_gh_binary helper (symmetric with _is_git_binary).

# run_strip name "expected_json_list" "tok1|tok2|..."
#   argv tokens are passed via the `|`-separated string; expected is a JSON
#   list compared against repr(strip_prefix(argv)).
run_strip() {
  local name="$1" expected="$2" tokens="$3"
  local actual
  actual=$(python3 - "$tokens" <<'PYEOF'
import json, sys
from _hook_utils import strip_prefix
argv = sys.argv[1].split("|")
print(json.dumps(strip_prefix(argv)))
PYEOF
)
  if [ "$actual" = "$expected" ]; then
    echo "PASS [strip_prefix] $name"; PASS=$((PASS + 1))
  else
    echo "FAIL [strip_prefix expected=$expected got=$actual] $name"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# run_gh_bin name expected(true|false) token
run_gh_bin() {
  local name="$1" expected="$2" token="$3"
  local actual
  actual=$(python3 - "$token" <<'PYEOF'
import sys
from _hook_utils import _is_gh_binary
print(str(_is_gh_binary(sys.argv[1])).lower())
PYEOF
)
  if [ "$actual" = "$expected" ]; then
    echo "PASS [_is_gh_binary:$expected] $name"; PASS=$((PASS + 1))
  else
    echo "FAIL [_is_gh_binary expected=$expected got=$actual] $name"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# command/builtin wrappers peel to the real argv[0]
run_strip "command wrapper peels"        '["gh", "pr", "merge"]'      'command|gh|pr|merge'
run_strip "builtin wrapper peels"         '["gh", "pr", "merge"]'      'builtin|gh|pr|merge'
run_strip "builtin command nested peels"  '["gh", "pr", "merge"]'      'builtin|command|gh|pr|merge'
run_strip "command after env peels"       '["gh", "pr", "merge"]'      'env|command|gh|pr|merge'
# path-prefixed gh is left intact by strip_prefix (basename handled downstream)
run_strip "path-prefix gh untouched"      '["/usr/bin/gh", "pr", "merge"]' '/usr/bin/gh|pr|merge'
run_strip "command + path-prefix gh"      '["/usr/bin/gh", "pr", "merge"]' 'command|/usr/bin/gh|pr|merge'

# _is_gh_binary basename recognition
run_gh_bin "bare gh"            true  'gh'
run_gh_bin "abs path /usr/bin/gh" true '/usr/bin/gh'
run_gh_bin "rel path ./gh"      true  './gh'
run_gh_bin "subshell \$(gh"     true  '$(gh'
run_gh_bin "group (gh"          true  '(gh'
run_gh_bin "not gh: github"     false 'github'
run_gh_bin "not gh: ghi"        false 'ghi'
run_gh_bin "not gh: git"        false 'git'
run_gh_bin "not gh: foogh"      false 'foogh'

# ---------------------------------------------------------------------------
# tokenize_with_roles — role-aware token API (issue #263)
# ---------------------------------------------------------------------------
#
# Each case spawns a python3 subprocess that imports tokenize_with_roles
# and prints the (role, text, flag_name) for each token as a single line
# per token in the form `seg<i>:<role>:<text>:<flag_name>`. The harness
# asserts substring presence (expected lines must appear in actual output).

# run_roles name "expected_substring_1|expected_substring_2|..." command
#   Each expected substring must appear (substring match) somewhere in the
#   actual output. Use `|` as the in-shell separator between substrings.
run_roles() {
  local name="$1" expected_substrings="$2" command="$3"
  local actual
  actual=$(python3 - "$command" <<'PYEOF'
import sys
from _hook_utils import tokenize_with_roles

cmd = sys.argv[1]
spec = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace"},
    "git merge-tree": {"--merge-base", "-X", "--strategy-option"},
    "kubectl": set(),
    "gh": {"--repo", "-R"},
}
segs = tokenize_with_roles(cmd, spec)
for i, seg in enumerate(segs):
    for t in seg:
        fn = t.flag_name if t.flag_name is not None else ""
        print(f"seg{i}:{t.role.value}:{t.text}:{fn}")
PYEOF
)
  local ok=1
  local IFS='|'
  for sub in $expected_substrings; do
    if ! printf '%s\n' "$actual" | grep -Fq "$sub"; then
      ok=0
      break
    fi
  done
  if [ "$ok" -eq 1 ]; then
    echo "PASS [roles] $name"; PASS=$((PASS + 1))
  else
    echo "FAIL [roles] $name"
    echo "  expected (each substring must appear): $expected_substrings"
    echo "  actual:"
    printf '%s\n' "$actual" | sed 's/^/    /'
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# run_roles_negative — asserts each substring is NOT present in output.
run_roles_negative() {
  local name="$1" forbidden_substrings="$2" command="$3"
  local actual
  actual=$(python3 - "$command" <<'PYEOF'
import sys
from _hook_utils import tokenize_with_roles

cmd = sys.argv[1]
spec = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace"},
    "git merge-tree": {"--merge-base", "-X", "--strategy-option"},
    "kubectl": set(),
    "gh": {"--repo", "-R"},
}
segs = tokenize_with_roles(cmd, spec)
for i, seg in enumerate(segs):
    for t in seg:
        fn = t.flag_name if t.flag_name is not None else ""
        print(f"seg{i}:{t.role.value}:{t.text}:{fn}")
PYEOF
)
  local ok=1
  local IFS='|'
  for sub in $forbidden_substrings; do
    if printf '%s\n' "$actual" | grep -Fq "$sub"; then
      ok=0
      break
    fi
  done
  if [ "$ok" -eq 1 ]; then
    echo "PASS [roles!] $name"; PASS=$((PASS + 1))
  else
    echo "FAIL [roles!] $name (forbidden substring present)"
    echo "  forbidden: $forbidden_substrings"
    echo "  actual:"
    printf '%s\n' "$actual" | sed 's/^/    /'
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# --- 1 test per role ---

run_roles "COMMAND role on argv[0]" \
  "seg0:command:git:" \
  "git status"

run_roles "FLAG role on --long" \
  "seg0:flag:--name-only:" \
  "git merge-tree --name-only HEAD origin/main"

run_roles "FLAG_VALUE role with flag_name attribution (space form)" \
  "seg0:flag:--merge-base:|seg0:flag_value:A:--merge-base" \
  "git merge-tree --merge-base A --name-only HEAD origin/main"

run_roles "POSITIONAL role on bare positional arg" \
  "seg0:positional:HEAD:|seg0:positional:origin/main:" \
  "git merge-tree --name-only HEAD origin/main"

run_roles "SEPARATOR_DD role on literal --" \
  "seg0:separator_dd:--:" \
  "kubectl exec pod -- mytool"

run_roles "POST_DD role on tokens after --" \
  "seg0:post_dd:mytool:|seg0:post_dd:--use-protocol-buffers:" \
  "kubectl exec pod -- mytool --use-protocol-buffers"

run_roles "SUBST_RUN role on coalesced \$() (unquoted free)" \
  "seg0:subst_run:\$(echo hi):" \
  'echo $(echo hi)'

# --- $() coalesce — multi-token unquoted ---

run_roles "\$() coalesce as FLAG_VALUE (space form)" \
  "seg0:flag_value:\$(git merge-base HEAD origin/main):--merge-base" \
  'git merge-tree --merge-base $(git merge-base HEAD origin/main) --name-only HEAD origin/main'

run_roles_negative "\$() coalesce — no spurious POSITIONAL fragments" \
  "seg0:positional:merge-base:|seg0:positional:origin/main):" \
  'git merge-tree --merge-base $(git merge-base HEAD origin/main) --name-only HEAD origin/main'

# --- -- boundary ---

run_roles "-- boundary — POSITIONAL before, POST_DD after" \
  "seg0:positional:exec:|seg0:separator_dd:--:|seg0:post_dd:mytool:|seg0:post_dd:--use-protocol-buffers:" \
  "kubectl exec pod -- mytool --use-protocol-buffers"

run_roles_negative "-- boundary — flag after -- must NOT be FLAG" \
  "seg0:flag:--use-protocol-buffers:" \
  "kubectl exec pod -- mytool --use-protocol-buffers"

# --- Both space-form and equals-form flag-value ---

run_roles "space form --repo VALUE" \
  "seg0:flag:--repo:|seg0:flag_value:owner/repo:--repo" \
  "gh pr list --repo owner/repo"

run_roles "equals form --repo=VALUE (single FLAG token, no FLAG_VALUE)" \
  "seg0:flag:--repo=owner/repo:" \
  "gh pr list --repo=owner/repo"

run_roles_negative "equals form must NOT consume next token as FLAG_VALUE" \
  ":flag_value:--state:--repo" \
  "gh pr list --repo=owner/repo --state open"

# --- Compound — `cd /B && git wt remove /B-wt` ---

run_roles "compound: two segments, COMMAND on each" \
  "seg0:command:cd:|seg1:command:git:" \
  "cd /B && git wt remove /B-wt"

run_roles "compound: && separator splits segments cleanly" \
  "seg0:positional:/B:|seg1:positional:/B-wt:" \
  "cd /B && git wt remove /B-wt"

# --- Subcommand-aware flag-value resolution ---

run_roles "subcommand-aware: git merge-tree --merge-base picked up via subcommand spec" \
  "seg0:flag_value:A:--merge-base" \
  "git merge-tree --merge-base A --name-only HEAD origin/main"

run_roles "subcommand-aware: git -C /tmp merge-tree resolves merge-tree as subcommand" \
  "seg0:flag_value:/tmp:-C|seg0:flag_value:A:--merge-base" \
  "git -C /tmp merge-tree --merge-base A --name-only HEAD origin/main"

# --- Regression coverage of PR #251/#252 R1-R3 patterns via the API ---

run_roles "R1: -c value pair recognized (gh-style flag-value set)" \
  "seg0:flag:--repo:|seg0:flag_value:owner/x:--repo" \
  "gh pr list --repo owner/x"

run_roles "R3: -- boundary stops flag scan in kubectl exec" \
  "seg0:separator_dd:--:|seg0:post_dd:--use-protocol-buffers:" \
  "kubectl exec pod -- bash -c '--use-protocol-buffers'"

# --- Codex review #284 regressions ---

run_roles "wrapper -- (env): kubectl still resolves as COMMAND after env --" \
  "seg0:command:kubectl:|seg0:flag:--use-protocol-buffers:" \
  "env -- kubectl --use-protocol-buffers"

run_roles_negative "wrapper -- (env): kubectl must NOT be POST_DD" \
  "seg0:post_dd:kubectl:" \
  "env -- kubectl --use-protocol-buffers"

run_roles "wrapper -- (sudo): git merge-tree resolves through sudo -- prefix" \
  "seg0:command:git:|seg0:flag_value:A:--merge-base" \
  "sudo -- git merge-tree --merge-base A --name-only HEAD origin/main"

run_roles "flag=value with \$(): --git-dir=\$() does not block subcommand resolution" \
  "seg0:command:git:|seg0:flag_value:A:--merge-base" \
  'git --git-dir=$(pwd)/.git merge-tree --merge-base A --name-only HEAD origin/main'

run_roles "post-DD \$(): \$() after -- becomes POST_DD (not SUBST_RUN)" \
  "seg0:separator_dd:--:|seg0:post_dd:\$(echo --flag):" \
  'kubectl exec pod -- $(echo --flag)'

run_roles_negative "post-DD \$(): \$() after -- must NOT be SUBST_RUN" \
  "seg0:subst_run:\$(echo --flag):" \
  'kubectl exec pod -- $(echo --flag)'

# ---------------------------------------------------------------------------
# safe_tokenize — bash line-continuation handling (issue #510)
# ---------------------------------------------------------------------------
#
# A `\` immediately before a newline is a bash line continuation, not a
# command separator. It must be rejoined before the per-line shlex pass so
# the leading line's argv[0] is preserved. Each case asserts the full token
# list (Python repr) matches exactly.

# run_tokens name "<expected python list repr>" <command-as-argv...>
run_tokens() {
  local name="$1" expected="$2"
  shift 2
  local actual
  actual=$(python3 -c '
import sys
from _hook_utils import safe_tokenize
print(safe_tokenize(sys.argv[1]))
' "$1")
  if [ "$actual" = "$expected" ]; then
    echo "PASS [tokenize] $name"; PASS=$((PASS + 1))
  else
    echo "FAIL [tokenize] $name"
    echo "  expected: $expected"
    echo "  actual:   $actual"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# Issue #510 regression: `git \<newline>commit` must keep argv[0] ('git').
run_tokens "line-continuation rejoins argv[0]" \
  "['git', 'commit']" \
  $'git \\\ncommit'

run_tokens "line-continuation with trailing flags" \
  "['git', 'commit', '-m', 'x']" \
  $'git \\\ncommit -m x'

run_tokens "multiple line continuations collapse" \
  "['a', 'b', 'c']" \
  $'a \\\nb \\\nc'

# A genuine newline (no preceding backslash) is still a `;` separator.
run_tokens "bare newline stays a separator" \
  "['git', 'status', ';', 'git', 'log']" \
  $'git status\ngit log'

# ---------------------------------------------------------------------------
# safe_tokenize — newlines inside a quote (issues #972, #987)
# ---------------------------------------------------------------------------
#
# A newline *inside* a quote is data, not a separator. Splitting there gave
# the opening and the closing line their own shlex pass, both raised
# `ValueError: No closing quotation`, and the fail-open arm dropped them —
# losing argv[0] (#972) and, when a real command rode on the closing line, the
# whole command (#987, which blinded all three merge gates). The same cases
# live in tests/test_safe_tokenize_multiline_quote.py; they are duplicated
# here because this shell suite runs without pytest installed.

# Issue #972: a multi-line `--body` must keep its `gh` argv[0], and the
# newline must stay *inside* the body token rather than splitting it off.
run_tokens "multi-line quoted --body keeps argv[0] (#972)" \
  "['gh', 'pr', 'comment', '949', '--body', '### Verification\\n| 1 | x | PASS(live) |\\n']" \
  $'gh pr comment 949 --body \'### Verification\n| 1 | x | PASS(live) |\n\''

# Issue #987: the command riding on the quote-closing line must survive.
run_tokens "command after a multi-line double quote survives (#987)" \
  "['git', 'commit', '-m', 'line one\\nline two', '&&', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'git commit -m "line one\nline two" && gh pr merge 9 --squash'

run_tokens "command after a multi-line single quote survives (#987)" \
  "['git', 'commit', '-m', 'line one\\nline two', '&&', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'git commit -m \'line one\nline two\' && gh pr merge 9 --squash'

# The heredoc-in-command-substitution shape #987 reported. The body is blanked
# by strip_heredoc_bodies first (#985), so the `-m` token is the opaque
# substitution — but the `&&` and the merge after it are now visible.
run_tokens "heredoc commit followed by a merge survives (#987)" \
  "['git', 'commit', '-m', \"\$(cat <<'EOF'\\n\\nEOF\\n)\", '&&', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'git commit -m "$(cat <<\'EOF\'\nbody line\nEOF\n)" && gh pr merge 9 --squash'

# Anti-bypass: an unquoted `#` comment opens no quote, so the apostrophe in
# `don't` must not swallow the real merge on the next line. Without the
# comment rule in `_quote_open_at_eol` this returns [] and every merge gate
# goes blind — a fresh hole traded for the one being closed.
run_tokens "apostrophe in an unquoted comment does not swallow the next line" \
  "[';', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'git status # don\'t do this\ngh pr merge 9 --squash'

# The mirror case: a `#` *inside* quotes is literal, not a comment, so the
# quote closes normally and the following line is a separate command.
run_tokens "hash inside quotes stays inside the token" \
  "['echo', '# not a comment', ';', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'echo \'# not a comment\'\ngh pr merge 9 --squash'

# The word-boundary set is bash's metacharacters, not a subset of them. `)`,
# `<` and `>` end a word exactly like `;` and `|` do, so a comment opening
# right after one is a comment — verified with `bash -n`, which rejects
# `: >#x` (the redirect lost its operand to the comment) while accepting
# `: >x#x` (there `#` is mid-word and not a comment at all). Reading those
# three as ordinary text let the apostrophe in `don't` open a quote and
# swallow the merge on the next line.
run_tokens "comment after a closing paren does not swallow the next line" \
  "[';', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'(echo ok)#don\'t care\ngh pr merge 9 --squash'

run_tokens "comment after an output redirect does not swallow the next line" \
  "[';', 'gh', 'pr', 'merge', '9', '--squash']" \
  $': >#don\'t care\ngh pr merge 9 --squash'

run_tokens "comment after an input redirect does not swallow the next line" \
  "[';', 'gh', 'pr', 'merge', '9', '--squash']" \
  $': <#don\'t care\ngh pr merge 9 --squash'

# The negative control for the three above: mid-word `#` is NOT a comment in
# bash (`bash -n` accepts `: >x#x`), so the apostrophe genuinely does open a
# quote here and folding the lines is correct. Without this case, widening the
# boundary set to every character would pass the three tests above.
run_tokens "hash mid-word is not a comment and still folds the lines" \
  "[]" \
  $'echo ok#don\'t care\ngh pr merge 9 --squash'

# A command substitution re-opens shell parsing inside double quotes, so the
# quotes within it are its own. Reading the inner `'"'` as closing the outer
# double quote left one apparently open at end of line, folded the next line in,
# and shlex dropped both — the merge went invisible on a command bash runs fine.
# The `echo` line itself still yields no tokens — shlex cannot parse a bare
# `$(printf '"')` and its ValueError arm drops that logical line. The point is
# that the loss is now confined to it: the merge on the next line survives,
# where before the two were folded into one group and both disappeared.
run_tokens "quotes inside a command substitution do not leak to the outer quote" \
  "[';', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'echo "$(printf \'"\')"\ngh pr merge 9 --squash'

# The arithmetic form must NOT be taken for a substitution: `$((` is not `$(`,
# and treating it as one would pop the wrong state at the first `)`.
run_tokens "arithmetic expansion is not read as a command substitution" \
  "['echo', '\$((1 << 3))', ';', 'gh', 'pr', 'merge', '9', '--squash']" \
  $'echo "$((1 << 3))"\ngh pr merge 9 --squash'

# `commenters = ""` is untouched: the side-effect-scan opt-out marker must
# still tokenize rather than being eaten as a comment.
run_tokens "side-effect ack marker still tokenizes" \
  "['git', 'commit', '-m', 'x', '#', 'side-effect:ack']" \
  'git commit -m x # side-effect:ack'

# Fail-open is preserved: a quote left open at the end of the command is
# handed to shlex unchanged, so it raises and the `except ValueError` arm
# returns no tokens rather than crashing the hook.
run_tokens "unterminated quote still fails open" \
  "[]" \
  $'echo "never closed'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
echo "=========================================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=========================================="
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
