#!/bin/bash
# test_path_probe_gate.sh — coverage for hooks/path-probe-gate.sh
#
# Reproduces the 5-step scenario from issue #386 and covers the full
# advisory / silent / deny / skip / fail-open surface.
#
# Usage: bash tests/test_path_probe_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/path-probe-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# run_case name expectation tool_name file_path cwd [session_id] [ENV=VAL ...]
#   expectation:
#     "advisory:<marker>" — exit 0, stderr contains <marker>
#     "silent"            — exit 0, stderr empty
#     "deny:<marker>"     — exit 2, stdout contains <marker>
# ---------------------------------------------------------------------------
run_case() {
  local name="$1" expectation="$2" tool_name="$3" file_path="$4" cwd="$5"
  local sid="${6:-}"
  shift 6 || true
  # remaining args are ENV=VAL pairs to pass via env(1)
  local extra_env=("$@")

  local payload
  payload=$(python3 -c '
import json, sys
d = {
    "tool_name": sys.argv[1],
    "tool_input": {"file_path": sys.argv[2]},
    "cwd": sys.argv[3],
}
if sys.argv[4]:
    d["session_id"] = sys.argv[4]
print(json.dumps(d))
' "$tool_name" "$file_path" "$cwd" "$sid")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  # Use printf to avoid env-wrapping builtins; pipe into env-augmented hook.
  printf '%s' "$payload" | env "${extra_env[@]}" "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advisory:*)
      local marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    deny:*)
      local marker="${expectation#deny:}"
      [ "$rc" -eq 2 ] || ok=0
      case "$out" in
        *"$marker"*) ;;
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
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>} stdout=${out:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Setup: build a fake git worktree structure for testing
# ---------------------------------------------------------------------------

# Create a temp dir that mimics the worktree structure from issue #386:
#   <wt_root>/                              ← worktree root (git repo)
#   <wt_root>/repo-subdir/                  ← intermediate dir (depth=1)
#   <wt_root>/repo-subdir/content-root/     ← deeper dir (depth=2)

WT_ROOT=$(mktemp -d)
mkdir -p "$WT_ROOT/repo-subdir/content-root"

# Initialize a git repo at WT_ROOT so git worktree list returns it.
git -C "$WT_ROOT" init -q
git -C "$WT_ROOT" -c user.name=test -c user.email=test@test commit \
  --allow-empty -m "init" -q 2>/dev/null

# Verify the git repo was created — if git is too old for -c with commit, fall back.
if ! git -C "$WT_ROOT" log --oneline -1 >/dev/null 2>&1; then
  git -C "$WT_ROOT" config user.email "test@test"
  git -C "$WT_ROOT" config user.name "test"
  git -C "$WT_ROOT" commit --allow-empty -m "init" -q
fi

# Outside dir: a directory NOT in any git worktree.
OUTSIDE_DIR=$(mktemp -d)
mkdir -p "$OUTSIDE_DIR/subdir"

# ---------------------------------------------------------------------------
# === Issue #386 repro: 5-step scenario ===
# ---------------------------------------------------------------------------
# Step 1-3: agent enters a worktree (simulated — hook doesn't track navigation)
# Step 4: agent Writes to <wt_root>/content-root/file.md (one level too shallow)
#   The hook should fire advisory: parent $WT_ROOT/content-root not enumerated.
# Step 5: agent would enumerate first; here we just verify the advisory fires.

SID_REPRO="repro-$(date +%s)-$$"

run_case "repro: write to shallow path emits advisory" \
  "advisory:[path-probe-gate]" \
  "Write" "$WT_ROOT/content-root/file.md" "$WT_ROOT" "$SID_REPRO"

# Correct (deeper) path also emits advisory on first write.
SID_REPRO2="repro2-$(date +%s)-$$"

run_case "repro: write to correct deep path emits advisory on first attempt" \
  "advisory:[path-probe-gate]" \
  "Write" "$WT_ROOT/repo-subdir/content-root/file.md" "$WT_ROOT" "$SID_REPRO2"

# ---------------------------------------------------------------------------
# === DEPTH tests ===
# ---------------------------------------------------------------------------

# depth=0: file directly under worktree root → silent
SID_D0="d0-$(date +%s)-$$"
run_case "depth-0: file at worktree root is silent" \
  "silent" \
  "Write" "$WT_ROOT/file.md" "$WT_ROOT" "$SID_D0"

# depth=1: file one level below root → advisory on first write
SID_D1="d1-$(date +%s)-$$"
run_case "depth-1: first write emits advisory" \
  "advisory:[path-probe-gate]" \
  "Write" "$WT_ROOT/repo-subdir/file.md" "$WT_ROOT" "$SID_D1"

# depth=1: SAME session_id, SAME parent dir → deduped, silent
run_case "depth-1: second write to same parent is silent (deduped)" \
  "silent" \
  "Write" "$WT_ROOT/repo-subdir/other.md" "$WT_ROOT" "$SID_D1"

# depth=2: file two levels below root → advisory on first write
SID_D2="d2-$(date +%s)-$$"
run_case "depth-2: first write emits advisory" \
  "advisory:[path-probe-gate]" \
  "Write" "$WT_ROOT/repo-subdir/content-root/file.md" "$WT_ROOT" "$SID_D2"

# depth=2: different session → advisory again (no cross-session dedup)
SID_D2B="d2b-$(date +%s)-$$"
run_case "depth-2: different session emits advisory again" \
  "advisory:[path-probe-gate]" \
  "Write" "$WT_ROOT/repo-subdir/content-root/file.md" "$WT_ROOT" "$SID_D2B"

# ---------------------------------------------------------------------------
# === STRICT MODE tests ===
# ---------------------------------------------------------------------------

SID_STRICT="strict-$(date +%s)-$$"
run_case "strict: first write denies (exit 2)" \
  "deny:[path-probe-gate]" \
  "Write" "$WT_ROOT/repo-subdir/file.md" "$WT_ROOT" "$SID_STRICT" \
  "PRAXIS_PATH_PROBE_STRICT=1"

# In strict mode the hook does NOT record the parent — second call also denies.
SID_STRICT2="strict2-$(date +%s)-$$"
run_case "strict: second write same session still denies (no-record in strict)" \
  "deny:[path-probe-gate]" \
  "Write" "$WT_ROOT/repo-subdir/other.md" "$WT_ROOT" "$SID_STRICT2" \
  "PRAXIS_PATH_PROBE_STRICT=1"

# ---------------------------------------------------------------------------
# === SKIP env var ===
# ---------------------------------------------------------------------------

SID_SKIP="skip-$(date +%s)-$$"
run_case "skip: PRAXIS_PATH_PROBE_SKIP=1 disables gate" \
  "silent" \
  "Write" "$WT_ROOT/repo-subdir/file.md" "$WT_ROOT" "$SID_SKIP" \
  "PRAXIS_PATH_PROBE_SKIP=1"

# ---------------------------------------------------------------------------
# === TOOL NAME tests: Edit ===
# ---------------------------------------------------------------------------

SID_EDIT="edit-$(date +%s)-$$"
run_case "Edit tool at depth-1 triggers advisory" \
  "advisory:[path-probe-gate]" \
  "Edit" "$WT_ROOT/repo-subdir/file.md" "$WT_ROOT" "$SID_EDIT"

# ---------------------------------------------------------------------------
# === PATH OUTSIDE WORKTREE: fail-open ===
# ---------------------------------------------------------------------------

SID_OUT="outside-$(date +%s)-$$"
run_case "path outside all worktrees is silent (fail-open)" \
  "silent" \
  "Write" "$OUTSIDE_DIR/subdir/file.md" "$OUTSIDE_DIR" "$SID_OUT"

# ---------------------------------------------------------------------------
# === FAIL-OPEN: non-target tool ===
# ---------------------------------------------------------------------------

SID_BASH="bash-$(date +%s)-$$"
run_case "Bash tool is silent (not in target tools)" \
  "silent" \
  "Bash" "$WT_ROOT/repo-subdir/file.md" "$WT_ROOT" "$SID_BASH"

# ---------------------------------------------------------------------------
# === FAIL-OPEN: malformed JSON stdin ===
# ---------------------------------------------------------------------------

{
  printf 'not-json' | "$HOOK" >/dev/null 2>/dev/null
  local_rc=$?
  if [ "$local_rc" -eq 0 ]; then
    echo "PASS  [malformed JSON stdin is silent (exit 0)]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [malformed JSON stdin is silent (exit 0)] rc=$local_rc"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON stdin is silent (exit 0)")
  fi
}

# ---------------------------------------------------------------------------
# === FAIL-OPEN: empty file_path ===
# ---------------------------------------------------------------------------

SID_EMPTY="empty-$(date +%s)-$$"
run_case "empty file_path is silent" \
  "silent" \
  "Write" "" "$WT_ROOT" "$SID_EMPTY"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

rm -rf "$WT_ROOT" "$OUTSIDE_DIR"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
