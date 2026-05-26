#!/usr/bin/env bash
# test-pre-gh-pr-create-dedup-gate.sh — coverage for the dedup-search gate
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr matches optional pattern (or empty)
#
# Real `gh` / `git` calls are short-circuited via a per-case fake-bin dir
# prepended to PATH. This makes the test deterministic and offline-safe.
#
# Usage: bash hooks/test-pre-gh-pr-create-dedup-gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/pre-gh-pr-create-dedup-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Fake-bin helpers
# ---------------------------------------------------------------------------

# Args:
#   $1: scenario name — one of:
#         pass         (gh returns []          / git returns owner/repo)
#         pass-matches (gh returns 2 rows      / git returns owner/repo)
#         gh-err       (gh exits 1 with stderr / git returns owner/repo)
#         gh-bad-json  (gh returns unparseable / git returns owner/repo)
#         no-git       (gh ok                  / git remote get-url fails)
#         no-gh        (gh missing             / git returns owner/repo)
make_fake_bin() {
  local scenario="$1"
  local d
  d=$(mktemp -d)

  # git shim — returns origin URL unless `no-git` scenario.
  if [ "$scenario" = "no-git" ]; then
    cat >"$d/git" <<'EOF'
#!/usr/bin/env bash
if [ "$1 $2 $3" = "remote get-url origin" ]; then
  echo "fatal: no upstream configured" >&2
  exit 128
fi
exec /usr/bin/env -i PATH=/usr/bin:/bin git "$@"
EOF
  else
    cat >"$d/git" <<'EOF'
#!/usr/bin/env bash
if [ "$1 $2 $3" = "remote get-url origin" ]; then
  echo "git@github.com:test-org/test-repo.git"
  exit 0
fi
exec /usr/bin/env -i PATH=/usr/bin:/bin git "$@"
EOF
  fi
  chmod +x "$d/git"

  # gh shim — varies by scenario; for `no-gh` we omit entirely.
  case "$scenario" in
    no-gh)
      ;;
    pass|no-git)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
# Only respond to `gh pr list ... --json ...` shape; anything else error.
for arg in "$@"; do
  if [ "$arg" = "--json" ]; then
    echo "[]"
    exit 0
  fi
done
echo "fake-gh: unexpected args: $*" >&2
exit 99
EOF
      ;;
    pass-matches)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
for arg in "$@"; do
  if [ "$arg" = "--json" ]; then
    cat <<JSON
[
  {"number":214,"title":"feat(hooks): dedup gate prototype","state":"MERGED","author":{"login":"alice"},"url":"https://github.com/test-org/test-repo/pull/214","mergedAt":"2026-05-13T10:00:00Z"},
  {"number":220,"title":"feat(hooks): add pre-gh-pr-create dedup gate","state":"OPEN","author":{"login":"bob"},"url":"https://github.com/test-org/test-repo/pull/220","mergedAt":null}
]
JSON
    exit 0
  fi
done
echo "fake-gh: unexpected args: $*" >&2
exit 99
EOF
      ;;
    gh-err)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
echo "GraphQL: Could not resolve to a Repository with the name 'x/y'." >&2
exit 1
EOF
      ;;
    gh-bad-json)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
for arg in "$@"; do
  if [ "$arg" = "--json" ]; then
    echo "not json at all <<<"
    exit 0
  fi
done
exit 99
EOF
      ;;
    gh-json-object)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
for arg in "$@"; do
  if [ "$arg" = "--json" ]; then
    echo '{"message":"Bad credentials","documentation_url":"https://docs.github.com"}'
    exit 0
  fi
done
exit 99
EOF
      ;;
  esac
  [ -f "$d/gh" ] && chmod +x "$d/gh"

  echo "$d"
}

# Args:
#   $1: case name
#   $2: expected outcome — "block" or "pass"
#   $3: tool_name (Bash / Edit / ...)
#   $4: command string
#   $5: scenario (selects fake-bin behavior; default: pass)
#   $6: optional grep regex that MUST appear in stderr (only checked on pass+grep cases or block cases)
run_case() {
  local name="$1" expected="$2" tool_name="$3" command="$4"
  local scenario="${5:-pass}" need_grep="${6:-}"

  local payload err_file rc fake_bin
  fake_bin=$(make_fake_bin "$scenario")
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
}))' "$tool_name" "$command")
  err_file=$(mktemp)
  echo "$payload" | env PATH="$fake_bin:/usr/bin:/bin" "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"
  rm -rf "$fake_bin"

  local ok=1
  if [ "$expected" = "block" ]; then
    { [ "$rc" -eq 2 ] && [ -n "$err_content" ]; } || ok=0
  else
    { [ "$rc" -eq 0 ]; } || ok=0
  fi
  if [ "$ok" -eq 1 ] && [ -n "$need_grep" ]; then
    printf '%s' "$err_content" | grep -Eq "$need_grep" || ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected→rc=$rc] $name"
    printf '  stderr: %s\n' "$err_content" | head -5
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Repo resolution
# ---------------------------------------------------------------------------

run_case "repo from --repo flag" pass Bash \
  'gh pr create --repo owner/name --title "feat(hooks): add dedup gate" --body "x"' \
  pass 'repo : owner/name'

run_case "repo from -R short flag" pass Bash \
  'gh pr create -R owner/name --title "feat(hooks): add dedup gate"' \
  pass 'repo : owner/name'

run_case "repo from --repo= equals form" pass Bash \
  'gh pr create --repo=owner/name --title "feat: dedup gate"' \
  pass 'repo : owner/name'

run_case "repo from gh global -R flag" pass Bash \
  'gh -R owner/name pr create --title "feat: dedup gate"' \
  pass 'repo : owner/name'

run_case "repo from git origin fallback" pass Bash \
  'gh pr create --title "feat(hooks): add dedup gate"' \
  pass 'repo : test-org/test-repo'

run_case "unresolved repo blocks" block Bash \
  'gh pr create --title "feat: x"' \
  no-git 'cannot resolve PR target repo'

# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

run_case "conventional commits prefix stripped" pass Bash \
  'gh pr create --repo o/r --title "feat(hooks): add dedup gate"' \
  pass 'query: dedup gate'

run_case "title with only stop-words skips search" pass Bash \
  'gh pr create --repo o/r --title "fix"' \
  pass 'no usable --title keywords'

run_case "missing title skips search with notice" pass Bash \
  'gh pr create --repo o/r --body "no title"' \
  pass 'no usable --title keywords'

run_case "WIP title is stop-word skip" pass Bash \
  'gh pr create --repo o/r --title "WIP"' \
  pass 'no usable --title keywords'

# ---------------------------------------------------------------------------
# Artifact emission
# ---------------------------------------------------------------------------

run_case "no-matches artifact has header + 'no matches'" pass Bash \
  'gh pr create --repo o/r --title "feat: dedup gate"' \
  pass 'result: no matches'

run_case "matches artifact lists PRs" pass Bash \
  'gh pr create --repo test-org/test-repo --title "feat: dedup gate"' \
  pass-matches 'matches: 2'

run_case "matches artifact shows merged tag" pass Bash \
  'gh pr create --repo test-org/test-repo --title "feat: dedup gate"' \
  pass-matches '\[MERGED'

run_case "matches artifact shows pr URL" pass Bash \
  'gh pr create --repo test-org/test-repo --title "feat: dedup gate"' \
  pass-matches 'https://github.com/test-org/test-repo/pull/214'

# ---------------------------------------------------------------------------
# gh failure modes — blocks
# ---------------------------------------------------------------------------

run_case "gh returns non-zero blocks" block Bash \
  'gh pr create --repo bogus/repo --title "feat: dedup gate"' \
  gh-err 'dedup search failed'

run_case "gh non-zero block includes gh stderr" block Bash \
  'gh pr create --repo bogus/repo --title "feat: dedup gate"' \
  gh-err 'Could not resolve'

run_case "gh unparseable JSON blocks" block Bash \
  'gh pr create --repo o/r --title "feat: dedup gate"' \
  gh-bad-json 'unparseable gh JSON output'

run_case "gh JSON object (not list) blocks" block Bash \
  'gh pr create --repo o/r --title "feat: dedup gate"' \
  gh-json-object 'expected list'

# ---------------------------------------------------------------------------
# Passthroughs
# ---------------------------------------------------------------------------

run_case "gh pr create --help passes" pass Bash \
  'gh pr create --help'

run_case "gh pr list is not pr create" pass Bash \
  'gh pr list --state all'

run_case "gh issue create is different subcommand" pass Bash \
  'gh issue create --title "feat: x" --body "y"'

run_case "non-Bash tool passes" pass Edit \
  'gh pr create --repo o/r --title "feat: x"'

run_case "env wrapper transparent" pass Bash \
  'env GH_TOKEN=xyz gh pr create --repo owner/name --title "feat: dedup gate"' \
  pass 'repo : owner/name'

run_case "sudo wrapper transparent" pass Bash \
  'sudo gh pr create --repo owner/name --title "feat: dedup gate"' \
  pass 'repo : owner/name'

run_case "gh missing fails open" pass Bash \
  'gh pr create --repo o/r --title "feat: dedup gate"' \
  no-gh

run_case "chained command, gh part dedup-checked" pass Bash \
  'echo go && gh pr create --repo owner/name --title "feat: dedup gate"' \
  pass 'repo : owner/name'

# ---------------------------------------------------------------------------
# Fail-open infrastructure
# ---------------------------------------------------------------------------

bad_json_err=$(mktemp)
printf 'not-json\n' | env PATH="/usr/bin:/bin" "$HOOK" >/dev/null 2>"$bad_json_err"
bad_rc=$?
if [ "$bad_rc" -eq 0 ]; then
  echo "PASS [pass] malformed stdin fails open"; ((PASS++))
else
  echo "FAIL [pass→rc=$bad_rc] malformed stdin"; ((FAIL++))
  FAILED_NAMES+=("malformed stdin")
fi
rm -f "$bad_json_err"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
