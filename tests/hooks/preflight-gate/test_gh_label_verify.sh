#!/bin/bash
# test_gh_label_verify.sh — coverage for hooks/preflight-gate/gh-label-verify/impl.py
#
# Synthesizes Claude Code PreToolUse(Bash) hook payloads and asserts:
#   block  → rc=2, stderr starts with "BLOCKED:"
#   silent → rc=0, stderr empty
#
# The hook shells out to `gh label list ...`. We stub `gh` via a temp
# directory on PATH so cases are deterministic and offline. The cache
# file path is also redirected to a temp file per-run.
#
# Usage: bash tests/test_gh_label_verify.sh

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/gh-label-verify/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMPDIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMPDIR"' EXIT

# ---------------------------------------------------------------------------
# Mock gh
# ---------------------------------------------------------------------------
#
# The stub recognises a handful of canned repos:
#   acme/repo      → labels: bug, enhancement, tool-friction:cli
#   acme/empty     → labels: (none)
#   acme/fail      → exit 1 (simulates network/auth failure)
#   acme/truncated → `label list` returns only `head-label`, but the label
#                    `tail-label` really exists and is reachable via
#                    `gh api`. Simulates a repo with more labels than the
#                    row limit, where the tail is invisible to the listing.
#   anything else  → exit 1
#
# Two invocations are recognised:
#   `gh label list --repo <r> ...`      → the canned listing above
#   `gh api repos/<r>/labels/<name>`    → exit 0 when the label really
#       exists, else `gh: Not Found (HTTP 404)` on stderr + exit 1
# Any other invocation (e.g. `gh issue create`) exits 0 with empty output,
# so test payloads that would otherwise call `gh` for real are inert.
MOCK_BIN="$TMPDIR/mockbin"
mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/gh" <<'EOF'
#!/bin/bash
if [ "$1" = "label" ] && [ "$2" = "list" ]; then
  shift 2
  repo=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) repo="$2"; shift 2 ;;
      --repo=*) repo="${1#--repo=}"; shift ;;
      *) shift ;;
    esac
  done
  # `gh` resolves the optional [HOST/] prefix itself; canned repos are keyed
  # by OWNER/REPO, so drop it.
  case "$repo" in */*/*) repo="${repo#*/}" ;; esac
  case "$repo" in
    acme/repo)
      printf 'bug\nenhancement\ntool-friction:cli\n' ;;
    acme/truncated|acme/neterr)
      # Fill the row limit (300) so the listing looks truncated. `tail-label`
      # is deliberately absent from it but real — see the `gh api` arm.
      printf 'head-label\n'
      for i in $(seq 1 299); do printf 'filler-%s\n' "$i"; done ;;
    acme/empty)
      : ;;
    acme/fail|"")
      exit 1 ;;
    *)
      exit 1 ;;
  esac
  exit 0
fi

# `gh api [--hostname <h>] repos/<owner>/<repo>/labels/<name>` — ground truth
# for existence. `gh api` does NOT parse a [HOST/] prefix out of the path, so
# a caller must pass the host via --hostname; keying truth off the path alone
# therefore catches a caller that leaves the host in the path.
if [ "$1" = "api" ]; then
  shift
  path=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --hostname) shift 2 ;;
      *) path="$1"; shift ;;
    esac
  done
  case "$path" in
    repos/*/labels/*)
      repo="${path#repos/}"; repo="${repo%%/labels/*}"
      name="${path##*/labels/}"
      truth=""
      case "$repo" in
        acme/repo)      truth='bug enhancement tool-friction:cli' ;;
        acme/truncated) truth='head-label tail-label' ;;
        acme/neterr)
          # gh echoes the request URL on a connection error, so the label name
          # lands in stderr — a label called *404* must not read its own name
          # back as proof of absence.
          echo "Get \"https://api.github.com/$path\": dial tcp: connect: connection refused" >&2
          exit 1 ;;
      esac
      for l in $truth; do
        if [ "$l" = "$name" ]; then exit 0; fi
      done
      echo "gh: Not Found (HTTP 404)" >&2
      exit 1
      ;;
  esac
fi
exit 0
EOF
chmod +x "$MOCK_BIN/gh"

# Per-run cache file. Reset between cases via `rm -f`.
CACHE_FILE="$TMPDIR/gh-label-cache.json"

# A cwd that is NOT a git repo (no .git directory). Resolves to None.
NON_GIT_CWD="$TMPDIR/non-git"
mkdir -p "$NON_GIT_CWD"

# run_case name expectation payload [env_kv ...]
run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  local env_args=("PRAXIS_GH_LABEL_CACHE_PATH=$CACHE_FILE" "PATH=$MOCK_BIN:$PATH")
  for kv in "$@"; do env_args+=("$kv"); done

  rm -f "$CACHE_FILE"

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)

  echo "$payload" | env "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "^BLOCKED:" || ok=0
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
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

echo "test_gh_label_verify"

# ---------------------------------------------------------------------------
# Subcommand × label × repo matrix — PR / issue, create / edit
# ---------------------------------------------------------------------------

run_case "gh pr create --label bug (exists) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label bug --repo acme/repo --title t --body b"}}'

run_case "gh pr create --label nope (missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label nope --repo acme/repo --title t --body b"}}'

# Regression (#803): the listing is row-limited, so a repo with more labels
# than the limit hides its tail. Absence from the listing must not block on
# its own — `tail-label` is real and the API says so.
run_case "gh pr create --label tail-label (listing truncated, label real) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label tail-label --repo acme/truncated --title t --body b"}}'

run_case "gh pr create --label ghost (listing truncated, label absent) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label ghost --repo acme/truncated --title t --body b"}}'

# gh echoes the request URL on a connection error, so a label named *404*
# appears in its own failure message. Only the `(HTTP 404)` status marker
# means absent — a network error must fail open.
run_case "gh pr create --label hub-issue-404 (re-check hits network error) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label hub-issue-404 --repo acme/neterr --title t --body b"}}'

# gh accepts [HOST/]OWNER/REPO. `gh label list` resolves the host itself, but
# `gh api` needs it in --hostname — left in the path it would 404 on a real label.
run_case "gh pr create --label tail-label --repo <host>/acme/truncated — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label tail-label --repo ghe.example.com/acme/truncated --title t --body b"}}'

run_case "gh issue create --label bug (exists) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --label bug --repo acme/repo --title t --body b"}}'

run_case "gh issue create --label nope (missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue create --label nope --repo acme/repo --title t --body b"}}'

run_case "gh pr edit 1 --add-label bug (exists) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr edit 1 --add-label bug --repo acme/repo"}}'

run_case "gh pr edit 1 --add-label nope (missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr edit 1 --add-label nope --repo acme/repo"}}'

run_case "gh issue edit 1 --add-label nope (missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue edit 1 --add-label nope --repo acme/repo"}}'

# ---------------------------------------------------------------------------
# Multi-value forms
# ---------------------------------------------------------------------------

run_case "gh pr create --label bug,enhancement (both exist) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label bug,enhancement --repo acme/repo --title t --body b"}}'

run_case "gh pr create --label bug,nope (one missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label bug,nope --repo acme/repo --title t --body b"}}'

run_case "gh pr create --label bug --label nope (repeated; one missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label bug --label nope --repo acme/repo --title t --body b"}}'

run_case "gh pr create --label=nope (inline = form; missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label=nope --repo acme/repo --title t --body b"}}'

run_case "gh pr create -l bug (short flag; exists) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create -l bug --repo acme/repo --title t --body b"}}'

run_case "gh pr create -l nope (short flag; missing) — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create -l nope --repo acme/repo --title t --body b"}}'

run_case "gh pr create --label 'tool-friction:cli' (existing namespaced label) — silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label tool-friction:cli --repo acme/repo --title t --body b"}}'

# ---------------------------------------------------------------------------
# Repo resolution
# ---------------------------------------------------------------------------

run_case "no --repo, cwd not a git repo — fail-open silent" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr create --label nope --title t --body b\"},\"cwd\":\"$NON_GIT_CWD\"}"

run_case "gh label list returns failure (acme/fail) — fail-open silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label nope --repo acme/fail --title t --body b"}}'

run_case "gh label list returns empty set, label missing — block" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label bug --repo acme/empty --title t --body b"}}'

# ---------------------------------------------------------------------------
# Help / no-op paths
# ---------------------------------------------------------------------------

run_case "gh pr create --label nope --help — silent (help bypass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label nope --help --repo acme/repo"}}'

run_case "gh pr create with no label flag — silent (nothing to validate)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --repo acme/repo --title t --body b"}}'

run_case "gh pr list --label nope (filter, not write) — silent (subcmd out of scope)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr list --label nope --repo acme/repo"}}'

run_case "non-Bash tool — silent" \
  "silent" \
  '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/x"}}'

run_case "malformed JSON payload — fail-open silent" \
  "silent" \
  "not-json"

run_case "shlex-breaking unmatched quote — fail-open silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label \"open-quote --repo acme/repo"}}'

# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------
#
# The inline cases below construct PATH-restricted environments to prove
# that cache state — not a live `gh` call — drives enforcement. They
# include the directory holding the real Python interpreter in PATH so
# the testbed can still launch the interpreter; only `gh` is selectively
# dropped from the env to simulate a missing/failing CLI.
#
# We resolve python3 via `sys.executable` rather than `command -v python3`
# so wrapper-shim installations (pyenv, asdf) do not leak a `bash` runtime
# dependency into the restricted PATH — those shims are bash scripts and
# would otherwise fail with `env: bash: No such file or directory`.

PY_BIN=$(python3 -c 'import sys; print(sys.executable)' 2>/dev/null) || PY_BIN=$(command -v python3)
PY_DIR=$(dirname "$PY_BIN")
GH_ABSENT_PATH="$PY_DIR"

run_inline_case() {
  local name="$1" expectation="$2" payload="$3" path_override="$4"
  shift 4
  local extra_env=("$@")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)

  echo "$payload" \
    | env "PRAXIS_GH_LABEL_CACHE_PATH=$CACHE_FILE" "PATH=$path_override" \
          "${extra_env[@]}" \
          python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "^BLOCKED:" || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

# Prime: fetch the label set under MOCK_BIN once so the cache file holds
# {bug, enhancement, tool-friction:cli} for acme/repo. Subsequent cases
# drop `gh` from PATH and rely on the cache.
rm -f "$CACHE_FILE"
echo '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label bug --repo acme/repo --title t --body b"}}' \
  | PRAXIS_GH_LABEL_CACHE_PATH="$CACHE_FILE" PATH="$MOCK_BIN:$PATH" python3 "$HOOK" >/dev/null 2>&1

run_inline_case "cache-hit blocks missing label even when gh is gone" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label nope --repo acme/repo --title t --body b"}}' \
  "$GH_ABSENT_PATH"

run_inline_case "cache-hit passes existing label when gh is gone" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label bug --repo acme/repo --title t --body b"}}' \
  "$GH_ABSENT_PATH"

# Corrupt the cache file. With gh available, a fresh fetch repopulates
# the cache; the missing label still blocks.
echo "not-json-garbage" > "$CACHE_FILE"
run_inline_case "corrupt cache falls back to fresh fetch" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label nope --repo acme/repo --title t --body b"}}' \
  "$MOCK_BIN:$PATH"

# TTL=0 forces re-fetch every call. With `gh` removed AND TTL=0 (so the
# warm cache is rejected as stale), enforcement falls open silently
# instead of blocking a missing label.
run_inline_case "TTL=0 expires cache; gh missing → fail-open silent" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --label nope --repo acme/repo --title t --body b"}}' \
  "$GH_ABSENT_PATH" \
  "PRAXIS_GH_LABEL_CACHE_TTL_SEC=0"

# ---------------------------------------------------------------------------
# Uncaught exception fail-open (outer Exception guard)
# ---------------------------------------------------------------------------

# main() now opts into the shared @fail_open guard; verify the decorator is
# applied (fail-open behaviour itself is covered in tests/test_hook_runtime.sh).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# ---------------------------------------------------------------------------
# Re-check honours the shared hook deadline
# ---------------------------------------------------------------------------

# The listing fetch and the per-label re-checks share the gate's 15s manifest
# budget. Once the deadline passes, a re-check must fail open immediately
# rather than spawn gh and overrun. Asserted directly: reproducing it through
# a payload would mean burning the whole budget in wall-clock.
_deadline_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, time
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# gh would exit 0 here (label exists) — if it were consulted, absent=False for
# the wrong reason. Make any spawn a hard failure instead.
def _boom(*a, **k):
    raise AssertionError("gh spawned after the deadline had passed")
mod.subprocess.run = _boom

if mod._label_absent("acme/repo", "ghost", time.monotonic() - 1) is not False:
    sys.stderr.write("expired deadline did not fail open\n"); sys.exit(1)

# Budget must be derived from the manifest timeout, not exceed it.
if mod._HOOK_TIMEOUT_SEC - mod._HOOK_TIMEOUT_MARGIN_SEC >= mod._HOOK_TIMEOUT_SEC:
    sys.stderr.write("deadline leaves no margin under the manifest timeout\n"); sys.exit(1)
PYEOF
)
_deadline_rc=$?
if [ "$_deadline_rc" -eq 0 ] && [ -z "$_deadline_out" ]; then
  echo "  PASS  expired deadline fails open without spawning gh"
  PASS=$((PASS + 1))
else
  echo "  FAIL  deadline not honoured (rc=$_deadline_rc, out=$(echo "$_deadline_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("expired deadline fails open without spawning gh")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
TOTAL=$((PASS + FAIL))
echo "Result: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
