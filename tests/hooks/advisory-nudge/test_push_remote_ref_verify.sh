#!/bin/bash
# Tests for advisory-nudge/push-remote-ref-verify.
# Uses a real local bare repo as the "remote" so git ls-remote works offline.
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/push-remote-ref-verify/impl.py"

unset PRAXIS_PUSH_VERIFY_BYPASS PRAXIS_PUSH_VERIFY_STRICT PRAXIS_HOOK_ERROR_STDERR
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t.test
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t.test

PASS=0
FAIL=0

# setup_repo -> sets globals WORK (work tree) and REMOTE (bare repo)
setup_repo() {
  local base
  base="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  REMOTE="$base/remote.git"
  WORK="$base/work"
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
}

# run_case <advisory|advisory-strict|silent> <name> <command> <tool_response_json> [ENV=val ...]
run_case() {
  local expected="$1" name="$2" cmd="$3" tr="$4"
  shift 4
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2],"tool_response":json.loads(sys.argv[3])}))' \
    "$cmd" "$WORK" "$tr")
  err=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>&1 1>/dev/null)
  rc=$?
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      printf '%s' "$err" | grep -q "\[push-remote-ref-verify\]" || ok=0
      ;;
    advisory-strict)
      [ "$rc" -eq 2 ] || ok=0
      printf '%s' "$err" | grep -q "\[push-remote-ref-verify\]" || ok=0
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

# --- verified push: remote tip == pushed SHA -> silent --------------------
setup_repo
git -C "$WORK" push -q -u origin main
run_case silent "verified-push" "git push origin main" \
  '{"exit":0,"stderr":"To remote\n   abc..def  main -> main"}'

# --- bare `git push` (resolves @{upstream}), verified -> silent -----------
setup_repo
git -C "$WORK" push -q -u origin main
run_case silent "bare-push-verified" "git push" \
  '{"exit":0,"stderr":"Everything up-to-date"}'

# --- diverged remote tip + [new branch] output -> advisory + note ---------
setup_repo
git -C "$WORK" push -q -u origin main
echo b >>"$WORK/a.txt"
git -C "$WORK" commit -qam b   # local main advances; remote stays stale
run_case advisory "diverged-newbranch" "git push origin main" \
  '{"exit":0,"stderr":"To remote\n * [new branch]      main -> main"}'

# --- bare push diverged -> advisory ---------------------------------------
setup_repo
git -C "$WORK" push -q -u origin main
echo b >>"$WORK/a.txt"
git -C "$WORK" commit -qam b
run_case advisory "bare-push-diverged" "git push" \
  '{"exit":0,"stderr":"some output"}'

# --- absent remote branch but push claims it wrote it -> advisory ---------
setup_repo
git -C "$WORK" push -q -u origin main
git -C "$WORK" checkout -q -b feature
echo c >"$WORK/c.txt"
git -C "$WORK" add c.txt
git -C "$WORK" commit -qm feat   # feature never reaches the remote
run_case advisory "absent-branch" "git push origin feature" \
  '{"exit":0,"stderr":" * [new branch]      feature -> feature"}'

# --- strict mode -> exit 2 -------------------------------------------------
setup_repo
git -C "$WORK" push -q -u origin main
echo b >>"$WORK/a.txt"
git -C "$WORK" commit -qam b
run_case advisory-strict "strict-mode" "git push origin main" \
  '{"exit":0,"stderr":" * [new branch]      main -> main"}' PRAXIS_PUSH_VERIFY_STRICT=1

# --- push that failed (exit 1) -> silent ----------------------------------
setup_repo
git -C "$WORK" push -q -u origin main
echo b >>"$WORK/a.txt"
git -C "$WORK" commit -qam b
run_case silent "failed-push" "git push origin main" \
  '{"exit":1,"stderr":"error: failed to push some refs"}'

# --- bypass env -> silent --------------------------------------------------
setup_repo
git -C "$WORK" push -q -u origin main
echo b >>"$WORK/a.txt"
git -C "$WORK" commit -qam b
run_case silent "bypass" "git push origin main" \
  '{"exit":0,"stderr":" * [new branch] main -> main"}' PRAXIS_PUSH_VERIFY_BYPASS=1

# --- non-push command -> silent -------------------------------------------
setup_repo
run_case silent "non-push" "git status" '{"exit":0}'

# --- --dry-run -> skip silent ---------------------------------------------
setup_repo
git -C "$WORK" push -q -u origin main
echo b >>"$WORK/a.txt"
git -C "$WORK" commit -qam b
run_case silent "dry-run" "git push --dry-run origin main" '{"exit":0,"stderr":"x"}'

# --- branch deletion (`:main`) -> skip silent -----------------------------
setup_repo
run_case silent "branch-delete" "git push origin :main" '{"exit":0}'

# --- unreachable remote -> fail-open silent -------------------------------
setup_repo
run_case silent "unreachable-remote" "git push nonexistent main" '{"exit":0,"stderr":"x"}'

# --- malformed JSON stdin -> fail-open silent -----------------------------
err=$(printf 'not json' | python3 "$HOOK" 2>&1 1>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
  echo "PASS  [malformed-json]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed-json] rc=$rc err=<$err>"
  FAIL=$((FAIL + 1))
fi

# --- `git push -u origin main` (verified) -> silent (proves -u parses correctly) ---
setup_repo
git -C "$WORK" push -q -u origin main
run_case silent "push-u-verified" "git push -u origin main" \
  '{"exit":0,"stderr":"Branch '"'"'main'"'"' set up to track remote branch '"'"'main'"'"' from '"'"'origin'"'"'."}'

# --- `git push origin HEAD:refs/heads/feature` absent on remote -> advisory ---
# Proves refs/heads/ prefix is stripped when resolving remote_branch.
setup_repo
git -C "$WORK" push -q -u origin main
git -C "$WORK" checkout -q -b feature
echo d >"$WORK/d.txt"
git -C "$WORK" add d.txt
git -C "$WORK" commit -qm feat2   # feature never reaches the remote
run_case advisory "refspec-refs-heads" "git push origin HEAD:refs/heads/feature" \
  '{"exit":0,"stderr":" * [new branch]      HEAD -> feature"}'

# --- `git push --repo=origin main` -> fail-open silent (proves fix #3: equals-form
#     value-flag is consumed inline, no extra positional shifted; the hook parses
#     positional `main` as the remote, ls-remote on non-existent remote `main`
#     is unreachable -> fail-open). NOTE: silent here comes from fail-open, not
#     from verification — see push-option-equals-verified below for the real path.
setup_repo
git -C "$WORK" push -q -u origin main
run_case silent "repo-equals-form" "git push --repo=origin main" \
  '{"exit":0,"stderr":"Branch set up to track remote branch."}'

# --- equals-form value-flag reaches REAL verification (advisory, NOT fail-open)
# `--push-option=ci.skip` is an equals-form value-flag consumed inline; the
# positionals `origin feature` then parse to remote=origin/refspec=feature.
# `feature` is absent on the remote and the output claims a new branch, so the
# hook reaches genuine verification and emits an advisory. The advisory outcome
# (not silent) is what distinguishes correct equals-form parsing from the
# fail-open path that repo-equals-form above exercises.
setup_repo
git -C "$WORK" push -q -u origin main
git -C "$WORK" checkout -q -b feature
echo d >"$WORK/d.txt"
git -C "$WORK" add d.txt
git -C "$WORK" commit -qm feat2   # feature never reaches the remote
run_case advisory "push-option-equals-verified" "git push --push-option=ci.skip origin feature" \
  '{"exit":0,"stderr":" * [new branch]      feature -> feature"}'

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
