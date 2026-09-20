#!/bin/bash
# Tests for advisory-nudge/pr-thread-resolve-advisory.
# A stub `gh` on PATH fixes the PR lookup and the reviewThreads GraphQL response,
# so no network and no real PR are involved.
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/pr-thread-resolve-advisory/impl.py"

unset PRAXIS_PR_THREAD_ADVISORY_BYPASS PRAXIS_PR_THREAD_ADVISORY_STRICT
unset PRAXIS_PR_THREAD_GH_TIMEOUT PRAXIS_HOOK_ERROR_STDERR
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t.test
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t.test

PASS=0
FAIL=0

# setup_repo -> sets globals WORK (work tree), REMOTE (bare), BIN (stub gh dir)
setup_repo() {
  local base
  base="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  REMOTE="$base/remote.git"
  WORK="$base/work"
  BIN="$base/bin"
  mkdir -p "$BIN"
  git init --bare -q "$REMOTE"
  git init -q "$WORK"
  # Throwaway repos must not hit the environment's commit-signing server.
  git -C "$WORK" config commit.gpgsign false
  git -C "$WORK" config tag.gpgsign false
  git -C "$WORK" checkout -q -b main
  echo a >"$WORK/a.txt"
  git -C "$WORK" add a.txt
  git -C "$WORK" commit -qm init
  git -C "$WORK" remote add origin "$REMOTE"
  git -C "$WORK" push -q -u origin main
}

# stub_gh <pr_list_json> <graphql_json>  — empty string => that call exits 1
stub_gh() {
  printf '%s' "$1" >"$BIN/pr_list.json"
  printf '%s' "$2" >"$BIN/graphql.json"
  cat >"$BIN/gh" <<'STUB'
#!/bin/bash
here="$(cd "$(dirname "$0")" && pwd)"
for a in "$@"; do
  case "$a" in
    graphql) f="$here/graphql.json"; break ;;
  esac
done
[ -n "$f" ] || f="$here/pr_list.json"
[ -s "$f" ] || exit 1
cat "$f"
STUB
  chmod +x "$BIN/gh"
}

PR_LIST='[{"number":42,"url":"https://github.com/o/r/pull/42"}]'
PR_LIST_AMBIGUOUS='[{"number":42,"url":"https://github.com/o/r/pull/42"},{"number":77,"url":"https://github.com/o/r/pull/77"}]'

# threads_json <thread-json>...  -> full GraphQL envelope
threads_json() {
  local nodes
  nodes=$(printf '%s,' "$@")
  printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"pageInfo":{"hasNextPage":%s},"nodes":[%s]}}}}}' \
    "${HAS_NEXT:-false}" "${nodes%,}"
}

# thread <isResolved> <path> <line> <body> [last_author] [last_body]
# The last comment defaults to the head comment — same author, same body — which
# is what an untouched thread looks like and must not read as dispositioned.
thread() {
  python3 -c 'import json,sys
last_author = sys.argv[5] if len(sys.argv) > 5 else "rev"
last_body = sys.argv[6] if len(sys.argv) > 6 else sys.argv[4]
print(json.dumps({"isResolved": sys.argv[1]=="true","path":sys.argv[2],
 "line": None if sys.argv[3]=="null" else int(sys.argv[3]),
 "head":{"nodes":[{"author":{"login":"rev"},"body":sys.argv[4]}]},
 "tail":{"nodes":[{"author":{"login":last_author},"body":last_body}]}}))' "$@"
}

# stdout_of <cmd> <tool_response> -> the hook's stdout alone (stub_gh first).
# run_case discards stdout (`1>/dev/null`), so the additionalContext document is
# unmeasurable through it.
stdout_of() {
  local payload
  payload=$(python3 -c 'import json,sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2],"tool_response":json.loads(sys.argv[3])}))' \
    "$1" "$WORK" "$2")
  printf '%s' "$payload" | PATH="$BIN:$PATH" python3 "$HOOK" 2>/dev/null
}

# run_case <advisory|advisory-strict|silent> <name> <cmd> <tool_response> [ENV=val ...]
run_case() {
  local expected="$1" name="$2" cmd="$3" tr="$4"
  shift 4
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2],"tool_response":json.loads(sys.argv[3])}))' \
    "$cmd" "$WORK" "$tr")
  err=$(printf '%s' "$payload" | PATH="$BIN:$PATH" env "$@" python3 "$HOOK" 2>&1 1>/dev/null)
  rc=$?
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      printf '%s' "$err" | grep -q "\[pr-thread-resolve-advisory\]" || ok=0
      ;;
    advisory-strict)
      [ "$rc" -eq 2 ] || ok=0
      printf '%s' "$err" | grep -q "\[pr-thread-resolve-advisory\]" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc err=<$err>"
    FAIL=$((FAIL + 1))
  fi
}

# assert_grep <name> <pattern> — against the last advisory produced by run_advisory
LAST_ERR=""
run_advisory() {
  local cmd="$1" tr="$2"
  shift 2
  local payload
  payload=$(python3 -c 'import json,sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2],"tool_response":json.loads(sys.argv[3])}))' \
    "$cmd" "$WORK" "$tr")
  LAST_ERR=$(printf '%s' "$payload" | PATH="$BIN:$PATH" env "$@" python3 "$HOOK" 2>&1 1>/dev/null)
}

assert_grep() {
  local name="$1" pat="$2"
  if printf '%s' "$LAST_ERR" | grep -q "$pat"; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] pattern=<$pat> err=<$LAST_ERR>"
    FAIL=$((FAIL + 1))
  fi
}

OK='{"exit":0,"stderr":"To remote\n   abc..def  main -> main"}'

# --- one blocking thread -> advisory ---------------------------------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false api/x.py 12 '**issue (blocking):** null deref')")"
run_case advisory "blocking-thread" "git push origin main" "$OK"

# --- the same thread under STRICT -> exit 2 --------------------------------
run_case advisory-strict "blocking-thread-strict" "git push origin main" "$OK" \
  PRAXIS_PR_THREAD_ADVISORY_STRICT=1

# --- bypass wins over everything ------------------------------------------
run_case silent "bypass" "git push origin main" "$OK" \
  PRAXIS_PR_THREAD_ADVISORY_BYPASS=1

# --- grouping: blocking vs nitpick/note/non-blocking, and unlabeled --------
setup_repo
stub_gh "$PR_LIST" "$(threads_json \
  "$(thread false api/x.py 12 '**issue (blocking):** null deref')" \
  "$(thread false api/y.py 4 'nitpick: naming')" \
  "$(thread false api/z.py null 'note: unrelated to this PR')" \
  "$(thread false api/w.py 7 '**suggestion (non-blocking):** extract helper')" \
  "$(thread false api/v.py 9 'BugBot: possible race here')")"
run_advisory "git push origin main" "$OK"
assert_grep "group-counts" "답변 필요 2 / 참고 3"
assert_grep "unlabeled-needs-reply" "api/v.py:9"
assert_grep "line-null-renders-path-only" "api/z.py (@rev)"

# --- bot badge markup must not eat the excerpt (live shape, PR #5085) ------
# Codex opens every finding with a shields.io badge wrapped in <sub>. Left in,
# all ten threads rendered as the same 90 chars of badge markup.
setup_repo
CODEX_BODY='**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub> Prevent nil deref on the remote diagnose path**'
stub_gh "$PR_LIST" "$(threads_json "$(thread false orgctl/x.go 478 "$CODEX_BODY")")"
run_advisory "git push origin main" "$OK"
assert_grep "badge-stripped" "Prevent nil deref"
if printf '%s' "$LAST_ERR" | grep -q "img.shields.io"; then
  echo "FAIL  [badge-markup-absent] excerpt still carries badge markup"; FAIL=$((FAIL + 1))
else
  echo "PASS  [badge-markup-absent]"; PASS=$((PASS + 1))
fi

# --- STRICT with only fyi threads -> advisory but exit 0 -------------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false api/y.py 4 'nitpick: naming')")"
run_case advisory "strict-fyi-only-not-escalated" "git push origin main" "$OK" \
  PRAXIS_PR_THREAD_ADVISORY_STRICT=1

# --- truncation is stated, never silent -----------------------------------
setup_repo
HAS_NEXT=true stub_gh "$PR_LIST" "$(HAS_NEXT=true threads_json "$(thread false a.py 1 '**issue (blocking):** x')")"
run_advisory "git push origin main" "$OK"
assert_grep "truncation-stated" "첫 페이지만"

# --- F1: control bytes never reach the terminal ---------------------------
setup_repo
EVIL=$(python3 -c 'print("issue (blocking): \x1b]8;;http://evil\x07click\x1b]8;;\x07 \x1b[2K")')
stub_gh "$PR_LIST" "$(threads_json "$(thread false "api/x.py" 12 "$EVIL")")"
run_advisory "git push origin main" "$OK"
if printf '%s' "$LAST_ERR" | LC_ALL=C grep -q '[\x01-\x08\x0e-\x1f]'; then
  echo "FAIL  [F1-control-bytes-stripped] ESC/OSC survived into stderr"; FAIL=$((FAIL + 1))
else
  echo "PASS  [F1-control-bytes-stripped]"; PASS=$((PASS + 1))
fi
assert_grep "F1-text-preserved" "click"

# --- F2: `true || git push` — push never ran -> silent ---------------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 '**issue (blocking):** x')")"
run_case silent "F2-push-not-executed" "true || git push origin main" '{"exit":0,"stdout":""}'

# --- F2: `git push && false` — push landed, command failed -> advisory -----
run_case advisory "F2-push-landed-command-failed" "git push origin main && false" \
  '{"exit":1,"stderr":"To github.com\n   abc..def  main -> main"}'

# --- F2: rejected push -> silent ------------------------------------------
run_case silent "F2-push-rejected" "git push origin main" \
  '{"exit":1,"stderr":"To github.com\n ! [rejected]  main -> main (fetch first)"}'

# --- F2: no captured output -> falls back to exit status ------------------
run_case advisory "F2-no-output-exit0-fallback" "git push origin main" '{"exit":0}'
run_case silent   "F2-no-output-exit1-fallback" "git push origin main" '{"exit":1}'

# --- R1: neither output nor exit -> silent, not an assumed success ---------
run_case silent "R1-no-evidence-at-all" "git push origin main" '{}'
run_case silent "R1-no-evidence-strict" "git push origin main" '{}' \
  PRAXIS_PR_THREAD_ADVISORY_STRICT=1
run_case silent "R1-unparseable-exit" "git push origin main" '{"exit":"nope"}'

# --- R2: --repo sets the destination, so the parser must skip the push -----
run_case silent "R2-repo-equals-form" "git push --repo=other main" "$OK"
run_case silent "R2-repo-spaced-form" "git push --repo other main" "$OK"

# --- F3: first page all resolved but more pages exist -> still speaks ------
setup_repo
stub_gh "$PR_LIST" "$(HAS_NEXT=true threads_json "$(thread true a.py 1 'issue (blocking): already fixed')")"
run_advisory "git push origin main" "$OK"
assert_grep "F3-empty-page-truncation-reported" "미해결 스레드 없음이라고 말할 수 없습니다"

# --- F4: two PRs on the same head branch name -> silent, not a guess ------
setup_repo
stub_gh "$PR_LIST_AMBIGUOUS" "$(threads_json "$(thread false a.py 1 '**issue (blocking):** x')")"
run_case silent "F4-ambiguous-head-branch" "git push origin main" "$OK"

# --- all threads resolved -> silent ---------------------------------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread true api/x.py 12 '**issue (blocking):** fixed already')")"
run_case silent "all-resolved" "git push origin main" "$OK"

# --- no open PR on the branch -> silent -----------------------------------
setup_repo
stub_gh '[]' "$(threads_json "$(thread false a.py 1 'issue (blocking): x')")"
run_case silent "no-open-pr" "git push origin main" "$OK"

# --- gh unavailable / unauthenticated -> fail-open ------------------------
setup_repo
stub_gh '' ''
run_case silent "gh-fails" "git push origin main" "$OK"

# --- GraphQL call fails while the PR lookup succeeds -> fail-open ---------
setup_repo
stub_gh "$PR_LIST" ''
run_case silent "graphql-fails" "git push origin main" "$OK"

# --- unparseable GraphQL body -> fail-open --------------------------------
setup_repo
stub_gh "$PR_LIST" 'not json at all'
run_case silent "graphql-malformed" "git push origin main" "$OK"

# --- push itself failed -> silent (nothing landed) ------------------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 'issue (blocking): x')")"
run_case silent "failed-push" "git push origin main" '{"exit":1,"stderr":"rejected"}'

# --- non-push command -> silent -------------------------------------------
run_case silent "non-push" "git status" "$OK"

# --- push shapes the shared parser skips -> silent ------------------------
run_case silent "dry-run" "git push --dry-run origin main" "$OK"
run_case silent "branch-delete" "git push origin :main" "$OK"

# --- bare `git push` resolves through @{upstream} -> advisory -------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 '**issue (blocking):** x')")"
run_case advisory "bare-push-upstream" "git push" "$OK"

# --- malformed stdin / non-Bash tool -> fail-open -------------------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 'issue (blocking): x')")"
out=$(printf 'not json' | PATH="$BIN:$PATH" python3 "$HOOK" 2>&1 1>/dev/null); rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  echo "PASS  [malformed-stdin]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed-stdin] rc=$rc out=<$out>"; FAIL=$((FAIL + 1))
fi
out=$(printf '{"tool_name":"Read","tool_input":{}}' | PATH="$BIN:$PATH" python3 "$HOOK" 2>&1 1>/dev/null); rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  echo "PASS  [non-bash-tool]"; PASS=$((PASS + 1))
else
  echo "FAIL  [non-bash-tool] rc=$rc out=<$out>"; FAIL=$((FAIL + 1))
fi

# --- disposition recorded in the thread ends the advisory -----------------
# check_json <name> <expect-empty|expect-context> <stdout>
check_json() {
  local name="$1" mode="$2" out="$3" ok=1
  case "$mode" in
    expect-empty) [ -z "$out" ] || ok=0 ;;
    expect-context)
      printf '%s' "$out" | python3 -c 'import json,sys
d = json.load(sys.stdin)
inner = d["hookSpecificOutput"]
assert inner["hookEventName"] == "PostToolUse", inner
assert "additionalContext" in inner, inner
assert "Not fixed" in inner["additionalContext"]' 2>/dev/null || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] mode=$mode out=<$out>"; FAIL=$((FAIL + 1))
  fi
}

# an undispositioned thread reaches the model channel
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 'issue (blocking): x')")"
check_json "context-emitted" expect-context "$(stdout_of "git push origin main" "$OK")"

# the same thread, once answered by someone else, stops being listed
setup_repo
stub_gh "$PR_LIST" "$(threads_json \
  "$(thread false a.py 1 'issue (blocking): x' me 'Not fixed — 후속 이슈 #7')")"
run_case advisory "dispositioned-demoted-to-fyi" "git push origin main" "$OK"
check_json "dispositioned-no-context" expect-empty "$(stdout_of "git push origin main" "$OK")"

# every disposition verb closes the model channel, and only at the head of
# the newest comment
setup_repo
stub_gh "$PR_LIST" "$(threads_json \
  "$(thread false a.py 1 'issue (blocking): x' me 'Fixed — abc1234 guard added')")"
check_json "disposition-fixed-no-context" expect-empty "$(stdout_of "git push origin main" "$OK")"
setup_repo
stub_gh "$PR_LIST" "$(threads_json \
  "$(thread false a.py 1 'issue (blocking): x' me 'False positive — probe output')")"
check_json "disposition-false-positive-no-context" expect-empty "$(stdout_of "git push origin main" "$OK")"
setup_repo
stub_gh "$PR_LIST" "$(threads_json \
  "$(thread false a.py 1 'issue (blocking): x' me 'I think this is Fixed — later')")"
run_case advisory "disposition-not-at-head-still-needs" "git push origin main" "$OK"

# a reply from the thread's own author is not a disposition
setup_repo
stub_gh "$PR_LIST" "$(threads_json \
  "$(thread false a.py 1 'issue (blocking): x' rev 'Fixed — bot quoting itself')")"
run_case advisory "self-authored-not-disposition" "git push origin main" "$OK"

# a later objection re-arms the advisory
setup_repo
stub_gh "$PR_LIST" "$(threads_json \
  "$(thread false a.py 1 'issue (blocking): x' rev 'still broken after that change')")"
run_case advisory "new-comment-rearms" "git push origin main" "$OK"

# --- a quoted disposition is an objection, not a verdict ------------------
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 'issue (blocking): x' me \
  '> Fixed — abc1234 guard added

Actually still broken')")"
run_case advisory "quoted-disposition-still-needs" "git push origin main" "$OK"
check_json "quoted-disposition-emits-context" expect-context \
  "$(stdout_of "git push origin main" "$OK")"

# quoting the finding before answering it is the ordinary reply shape
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 'issue (blocking): x' me \
  '> issue (blocking): x

Not fixed — 후속 이슈 #7')")"
check_json "quote-then-disposition-no-context" expect-empty \
  "$(stdout_of "git push origin main" "$OK")"

# --- a truncated first page says so on the model channel too --------------
# context_has <name> <present|absent> <needle> <stdout>
context_has() {
  local name="$1" mode="$2" needle="$3" out="$4" ok=1 body
  body=$(printf '%s' "$out" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])
except Exception:
    pass' 2>/dev/null)
  case "$mode" in
    present) printf '%s' "$body" | grep -qF -- "$needle" || ok=0 ;;
    absent)  printf '%s' "$body" | grep -qF -- "$needle" && ok=0 ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] mode=$mode needle=<$needle> body=<$body>"; FAIL=$((FAIL + 1))
  fi
}

setup_repo
HAS_NEXT=true stub_gh "$PR_LIST" "$(HAS_NEXT=true threads_json \
  "$(thread false a.py 1 'issue (blocking): x')")"
context_has "context-carries-truncation" present "not complete" \
  "$(stdout_of "git push origin main" "$OK")"

# --- the model channel carries the objection that reopened the thread -----
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 'issue (blocking): original finding' \
  rev2 'still broken after that change')")"
context_has "context-carries-latest-objection" present "still broken after that change" \
  "$(stdout_of "git push origin main" "$OK")"

# an untouched thread does not get a redundant "latest" line
setup_repo
stub_gh "$PR_LIST" "$(threads_json "$(thread false a.py 1 'issue (blocking): x')")"
context_has "context-omits-redundant-latest" absent "latest (@" \
  "$(stdout_of "git push origin main" "$OK")"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
