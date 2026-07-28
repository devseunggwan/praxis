#!/usr/bin/env bash
# test_gh_merge_worktree_precondition.sh — coverage for
# hooks/preflight-gate/gh-merge-worktree-precondition/impl.py
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   block → exit 2 + stderr non-empty (mentions the conflicting worktree path)
#   pass  → exit 0
#
# Real `gh pr view` calls are short-circuited via a per-case fake-bin dir
# prepended to PATH. `git worktree list` runs against a REAL temp git repo
# (git worktree state cannot be faked without a real .git) — each case that
# needs a worktree conflict creates one with `git worktree add`.
#
# Usage: bash tests/hooks/preflight-gate/test_gh_merge_worktree_precondition.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/gh-merge-worktree-precondition/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Fake-bin helpers
# ---------------------------------------------------------------------------

# make_fake_gh <mode> [head-branch] [required-repo]
#   mode:
#     branch        — `gh pr view ... --json headRefName -q .headRefName`
#                     prints head-branch and exits 0
#     error         — every call exits 1 (auth-style failure)
#     empty         — exits 0 but prints nothing (unresolvable PR)
#     repo-required — prints head-branch ONLY when argv carries
#                     `-R <required-repo>` (or `--repo <required-repo>`);
#                     otherwise exits 1. Asserts the hook forwards the
#                     merge command's repo selection to `gh pr view`.
make_fake_gh() {
  local mode="$1" branch="${2:-}" repo="${3:-}"
  local d
  d=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
  case "$mode" in
    branch)
      cat >"$d/gh" <<EOF
#!/usr/bin/env bash
echo "$branch"
exit 0
EOF
      ;;
    error)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
echo "gh: authentication required" >&2
exit 1
EOF
      ;;
    empty)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
      ;;
    repo-required)
      # Prints head-branch only when `-R/--repo <repo>` is present in argv;
      # also rejects the repo value showing up as a positional argument
      # (i.e. misparsed as the PR identifier).
      cat >"$d/gh" <<EOF
#!/usr/bin/env bash
prev=""
found=0
for arg in "\$@"; do
  if [ "\$arg" = "$repo" ]; then
    if [ "\$prev" = "-R" ] || [ "\$prev" = "--repo" ]; then
      found=1
    else
      echo "gh: repo value misparsed as positional identifier" >&2
      exit 1
    fi
  fi
  prev="\$arg"
done
if [ "\$found" = "1" ]; then
  echo "$branch"
  exit 0
fi
echo "gh: repo selection not forwarded" >&2
exit 1
EOF
      ;;
  esac
  chmod +x "$d/gh"
  echo "$d"
}

# ---------------------------------------------------------------------------
# Real git repo (worktree state needs a real .git)
# ---------------------------------------------------------------------------

REPO=$(cd "$(mktemp -d)" && pwd -P) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
git -C "$REPO" init -q -b main
git -C "$REPO" -c user.name=test -c user.email=test@example.com \
  commit -q --allow-empty -m init
git -C "$REPO" branch feature-checked-out
git -C "$REPO" branch feature-not-checked-out

WT=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
rm -rf "$WT"
git -C "$REPO" worktree add -q "$WT" feature-checked-out >/dev/null 2>&1

cleanup() {
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1
  rm -rf "$REPO" "$WT"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Payload builder + runner
# ---------------------------------------------------------------------------

# build_payload <command> [tool-name]
build_payload() {
  python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[2] if len(sys.argv) > 2 else "Bash",
    "tool_input": {"command": sys.argv[1]},
    "cwd": sys.argv[3] if len(sys.argv) > 3 else "'"$REPO"'",
}))
' "$1" "${2:-Bash}" "${3:-$REPO}"
}

# run_case <name> <expect: block|pass> <fake-gh-dir-or-empty> <command> [cwd] [tool-name]
run_case() {
  local name="$1" expect="$2" gh_dir="$3" command="$4" cwd="${5:-$REPO}" tool="${6:-Bash}"
  local payload out rc path_prefix

  payload=$(build_payload "$command" "$tool" "$cwd")
  path_prefix="$PATH"
  if [ -n "$gh_dir" ]; then
    path_prefix="$gh_dir:$PATH"
  fi

  out=$(echo "$payload" | PATH="$path_prefix" "$HOOK" 2>&1 1>/dev/null)
  rc=$?

  local ok=1
  case "$expect" in
    block)
      # exit 2 alone is not enough — the block message must identify the
      # conflicting worktree, or a crash could masquerade as a gate decision
      [ "$rc" -eq 2 ] && [[ "$out" == *"$WT"* ]] || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] (rc=$rc, stderr=$out)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

BRANCH_GH=$(make_fake_gh branch feature-checked-out)
NOCONFLICT_GH=$(make_fake_gh branch feature-not-checked-out)
ERROR_GH=$(make_fake_gh error)
EMPTY_GH=$(make_fake_gh empty)

run_case "1 block: --delete-branch + head branch checked out in another worktree" \
  block "$BRANCH_GH" "gh pr merge 42 --squash --delete-branch"

run_case "2 block: -d short flag triggers the same check" \
  block "$BRANCH_GH" "gh pr merge 42 --squash -d"

run_case "3 pass: no delete-branch flag at all" \
  pass "$BRANCH_GH" "gh pr merge 42 --squash"

run_case "4 pass: delete-branch requested but head branch not checked out anywhere" \
  pass "$NOCONFLICT_GH" "gh pr merge 43 --squash --delete-branch"

run_case "5 pass: gh pr view errors (auth failure) -> fail-open" \
  pass "$ERROR_GH" "gh pr merge 42 --squash --delete-branch"

run_case "6 pass: gh pr view returns empty headRefName -> fail-open" \
  pass "$EMPTY_GH" "gh pr merge 42 --squash --delete-branch"

run_case "7 pass: non-Bash tool" \
  pass "$BRANCH_GH" "gh pr merge 42 --squash --delete-branch" "$REPO" AskUserQuestion

run_case "8 pass: unrelated gh command (gh pr view)" \
  pass "$BRANCH_GH" "gh pr view 42 --json state"

run_case "9 pass: unrelated Bash command" \
  pass "$BRANCH_GH" "echo hello"

REPO_GH=$(make_fake_gh repo-required feature-checked-out owner/repo)

run_case "10 block: global flag before subcommand still detected + -R forwarded to gh pr view" \
  block "$REPO_GH" "gh -R owner/repo pr merge 42 --squash --delete-branch"

run_case "10b block: --repo=owner/repo inline form forwarded" \
  block "$REPO_GH" "gh --repo=owner/repo pr merge 42 --squash --delete-branch"

run_case "10c block: -R between pr and merge (inherited flag placement)" \
  block "$REPO_GH" "gh pr -R owner/repo merge 42 --squash --delete-branch"

run_case "10d block: -R after merge, no identifier -> repo value not misparsed as identifier" \
  block "$REPO_GH" "gh pr merge -R owner/repo --squash --delete-branch"

run_case "10e block: -R trailing after all merge flags" \
  block "$REPO_GH" "gh pr merge 42 --squash --delete-branch -R owner/repo"

run_case "11 block: no PR identifier -> gh infers from current branch" \
  block "$BRANCH_GH" "gh pr merge --squash --delete-branch"

run_case "11b pass: -d as a value of --body is not a delete-branch request" \
  pass "$BRANCH_GH" "gh pr merge 42 --body -d"

PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE=1 \
  run_case "12 pass: bypass env var set" \
  pass "$BRANCH_GH" "gh pr merge 42 --squash --delete-branch"

# Case 13 needs raw malformed stdin, not a JSON-wrapped command string —
# bypass run_case/build_payload for this one.
_malformed_out=$(echo "not json at all <<<" | "$HOOK" 2>&1 1>/dev/null)
_malformed_rc=$?
if [ "$_malformed_rc" -eq 0 ]; then
  echo "PASS  [13 pass: malformed JSON stdin -> fail-open]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [13 pass: malformed JSON stdin -> fail-open] (rc=$_malformed_rc, stderr=$_malformed_out)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("13 malformed JSON stdin")
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
