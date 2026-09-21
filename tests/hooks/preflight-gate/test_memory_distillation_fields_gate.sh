#!/usr/bin/env bash
# test_memory_distillation_fields_gate.sh — coverage for
# hooks/preflight-gate/memory-distillation-fields-gate/impl.py
#
# Uses a REAL temp memory directory pointed at by PRAXIS_MEMORY_DIR, so the
# directory-identity test (os.path.samefile) is exercised rather than mocked —
# a substring match on "memory" would pass every case here and still be wrong.
#
# Usage: bash tests/hooks/preflight-gate/test_memory_distillation_fields_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/memory-distillation-fields-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

TMPDIR_BASE=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMPDIR_BASE"' EXIT

MEM_DIR="$TMPDIR_BASE/projects/some-slug/memory"
OUTSIDE_DIR="$TMPDIR_BASE/elsewhere/memory"
mkdir -p "$MEM_DIR" "$OUTSIDE_DIR"

# The repeat counter is a separate concern with its own suite; disabling it
# keeps this suite's stderr assertions independent of block-call ordering.
COMMON_ENV=("PRAXIS_MEMORY_DIR=$MEM_DIR" "PRAXIS_BLOCK_REPEAT_DISABLE=1")

COMPLIANT='---
name: feedback-something
description: one line
metadata:
  type: feedback
  recurrence: 1
  enforcement: none
  escalated_to: none
---

body
'

MISSING_ONE='---
name: feedback-something
description: one line
metadata:
  type: feedback
  recurrence: 1
  enforcement: none
---

body
'

FLAT='---
name: feedback-something
description: one line
recurrence: 1
enforcement: none
escalated_to: none
metadata:
  type: feedback
---

body
'

NO_FRONTMATTER='# just a note

body
'

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

build_payload() {
  local tool="$1" path="$2" content="$3"
  python3 -c '
import json, sys
tool, path, content = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({"tool_name": tool, "tool_input": {"file_path": path, "content": content}}))
' "$tool" "$path" "$content"
}

check_block() {
  local rc="$1" err="$2"
  [ "$rc" -eq 2 ] || return 1
  echo "$err" | grep -qi "memory distillation fields" || return 1
  return 0
}

# run_case <name> <expected:block|pass> <tool_name> <file_path> <content> [KEY=VALUE ...]
run_case() {
  local name="$1" expected="$2" tool="$3" path="$4" content="$5"; shift 5
  local env_overrides=("${COMMON_ENV[@]}" "$@")

  local payload
  payload=$(build_payload "$tool" "$path" "$content")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  (
    for kv in "${env_overrides[@]}"; do
      # shellcheck disable=SC2163  # kv holds a literal KEY=VALUE pair
      export "$kv"
    done
    printf '%s' "$payload" | python3 "$HOOK"
  ) >"$out_file" 2>"$err_file"
  local rc=$?
  local err; err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    block) check_block "$rc" "$err" || ok=0 ;;
    pass)  [ "$rc" -eq 0 ] || ok=0; [ -z "$err" ] || ok=0 ;;
    *) echo "FATAL: unknown expectation '$expected'" >&2; exit 1 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$expected] $name"
    PASS=$((PASS+1))
  else
    echo "FAIL  [$expected] $name (rc=$rc)"
    echo "      stderr: $err"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

run_case "all three nested under metadata" pass \
  Write "$MEM_DIR/feedback_ok.md" "$COMPLIANT"

run_case "escalated_to absent" block \
  Write "$MEM_DIR/feedback_missing.md" "$MISSING_ONE"

run_case "three fields flat at top level" block \
  Write "$MEM_DIR/feedback_flat.md" "$FLAT"

run_case "no frontmatter at all" block \
  Write "$MEM_DIR/feedback_bare.md" "$NO_FRONTMATTER"

run_case "same basename outside the memory dir" pass \
  Write "$OUTSIDE_DIR/feedback_missing.md" "$MISSING_ONE"

run_case "MEMORY.md index is exempt" pass \
  Write "$MEM_DIR/MEMORY.md" "$NO_FRONTMATTER"

run_case "MEMORY-reference.md index is exempt" pass \
  Write "$MEM_DIR/MEMORY-reference.md" "$NO_FRONTMATTER"

run_case "non-markdown file in the memory dir" pass \
  Write "$MEM_DIR/cursor.json" "$NO_FRONTMATTER"

run_case "Edit is not this gate's tool" pass \
  Edit "$MEM_DIR/feedback_missing.md" "$MISSING_ONE"

run_case "bypass env clears the block" pass \
  Write "$MEM_DIR/feedback_missing.md" "$MISSING_ONE" \
  "PRAXIS_HOOK_BYPASS_MEMORY_FIELDS=1"

run_case "unresolvable memory dir fails open" pass \
  Write "$MEM_DIR/feedback_missing.md" "$MISSING_ONE" \
  "PRAXIS_MEMORY_DIR=$TMPDIR_BASE/does-not-exist"

# ---------------------------------------------------------------------------
# fail-open wrapping

# ---------------------------------------------------------------------------
# hookKeywords the hint index cannot read (issue #1426)
#
# The distillation fields are correct in every fixture below, so what is
# measured is only the second check. Each case is a WHOLE memory file, because
# the gate reads the Write payload rather than a frontmatter fragment.
# ---------------------------------------------------------------------------

dark_memory() {
  # dark_memory <hookable-value> <hookKeywords lines, or empty for none>
  printf -- '---\nname: feedback-something\ndescription: one line\nmetadata:\n  type: feedback\n  hookable: %s\n%s  recurrence: 1\n  enforcement: none\n  escalated_to: none\n---\n\nbody\n' "$1" "$2"
}

check_dark_block() {
  local rc="$1" err="$2"
  [ "$rc" -eq 2 ] || return 1
  echo "$err" | grep -qi "hint index cannot read" || return 1
  return 0
}

# run_dark_case <name> <expected:block|pass> <file> <content>
run_dark_case() {
  local name="$1" expected="$2" path="$3" content="$4"
  local payload; payload=$(build_payload Write "$path" "$content")
  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  (
    for kv in "${COMMON_ENV[@]}"; do
      # shellcheck disable=SC2163  # kv holds a literal KEY=VALUE pair
      export "$kv"
    done
    printf '%s' "$payload" | python3 "$HOOK"
  ) >"$out_file" 2>"$err_file"
  local rc=$?
  local err; err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    block) check_dark_block "$rc" "$err" || ok=0 ;;
    pass)  [ "$rc" -eq 0 ] || ok=0; [ -z "$err" ] || ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$expected] $name"
    PASS=$((PASS+1))
  else
    echo "FAIL  [$expected] $name (rc=$rc)"
    echo "      stderr: $err"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

run_dark_case "hookable true, flat list" pass \
  "$MEM_DIR/feedback_kw_ok.md" "$(dark_memory true '  hookKeywords: [git, push]
')"

run_dark_case "hookable true, flat list with trailing comment" pass \
  "$MEM_DIR/feedback_kw_comment.md" "$(dark_memory true '  hookKeywords: [git] # why
')"

run_dark_case "hookable true, block-list form" block \
  "$MEM_DIR/feedback_kw_block.md" "$(dark_memory true '  hookKeywords:
    - git
    - push
')"

run_dark_case "hookable true, scalar form" block \
  "$MEM_DIR/feedback_kw_scalar.md" "$(dark_memory true '  hookKeywords: git
')"

run_dark_case "hookable true, unclosed bracket" block \
  "$MEM_DIR/feedback_kw_unclosed.md" "$(dark_memory true '  hookKeywords: [git, push
')"

run_dark_case "hookable true, empty list" block \
  "$MEM_DIR/feedback_kw_empty.md" "$(dark_memory true '  hookKeywords: []
')"

# The shape the issue's own predicate could not see: no key at all. Six entries
# in the measured corpus are dark this way.
run_dark_case "hookable true, no hookKeywords key" block \
  "$MEM_DIR/feedback_kw_absent.md" "$(dark_memory true '')"

# hookable:false is not this gate's business at any shape — nothing indexes the
# entry, so no shape can hide it from anything.
run_dark_case "hookable false, block-list form" pass \
  "$MEM_DIR/feedback_kw_off_block.md" "$(dark_memory false '  hookKeywords:
    - git
')"

run_dark_case "hookable false, no hookKeywords key" pass \
  "$MEM_DIR/feedback_kw_off_absent.md" "$(dark_memory false '')"

# Priority: a file wrong on BOTH axes reports the distillation fields, the
# check this gate already had. No pre-existing violation changes its message.
BOTH_WRONG='---
name: feedback-something
description: one line
metadata:
  type: feedback
  hookable: true
  hookKeywords:
    - git
  recurrence: 1
---

body
'
run_case "both axes wrong reports the distillation fields" block \
  Write "$MEM_DIR/feedback_both.md" "$BOTH_WRONG"

# ---------------------------------------------------------------------------

_failopen_out=$(python3 - "$HOOK" <<'PYEOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("hook_under_test", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert getattr(mod.main, "__wrapped__", None) is not None, "main is not @fail_open-wrapped"
print("OK")
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ "$_failopen_out" = "OK" ]; then
  echo "PASS  [fail-open] main() is wrapped by the shared @fail_open guard"
  PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] main() not @fail_open-wrapped (rc=$_failopen_rc out=$_failopen_out)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open guard wrapping")
fi

# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
