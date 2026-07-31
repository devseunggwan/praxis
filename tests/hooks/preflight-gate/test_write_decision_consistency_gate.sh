#!/usr/bin/env bash
# test_write_decision_consistency_gate.sh — coverage for
# hooks/preflight-gate/write-decision-consistency-gate/impl.py
#
# Usage: bash tests/hooks/preflight-gate/test_write_decision_consistency_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/write-decision-consistency-gate/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

build_payload() {
  # build_payload <tool> <content>
  local tool="$1" content="$2"
  python3 -c '
import json, sys
tool, content = sys.argv[1], sys.argv[2]
key = "content" if tool == "Write" else "new_string"
print(json.dumps({
    "tool_name": tool,
    "tool_input": {"file_path": "/tmp/handoff.md", key: content},
}))
' "$tool" "$content"
}

pipe_hook() {
  local payload="$1"; shift
  (
    for kv in "$@"; do
      # shellcheck disable=SC2163  # kv holds a literal KEY=VALUE pair
      export "$kv"
    done
    printf '%s' "$payload" | python3 "$HOOK"
  )
}

expect_rc() {
  # expect_rc <name> <want_rc> <tool> <content> [ENV=VAL ...]
  local name="$1" want="$2" tool="$3" content="$4"; shift 4
  local payload out rc
  payload="$(build_payload "$tool" "$content")"
  out="$(pipe_hook "$payload" "$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq "$want" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS+1))
  else
    echo "FAIL  [$name] want rc=$want got rc=$rc"
    echo "      out: ${out:0:200}"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

expect_raw_rc() {
  # expect_raw_rc <name> <want_rc> <raw_stdin>
  local name="$1" want="$2" raw="$3"
  local out rc
  out="$(printf '%s' "$raw" | python3 "$HOOK" 2>&1)"
  rc=$?
  if [ "$rc" -eq "$want" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS+1))
  else
    echo "FAIL  [$name] want rc=$want got rc=$rc (out: ${out:0:120})"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Fixture: the issue #905 regression case (decision + invalidating constraint
# in the same block, no Consistency: line)
# ---------------------------------------------------------------------------

REGRESSION_BODY='# Task: hub #4608

## Handoff

### Decisions

- 필터 두 개 모두 추가합니다: `udl.use_yn = '"'"'Y'"'"'` + `dm.daily_yn = '"'"'Y'"'"'`.
- `data_source_status` (SYNC_DISABLED) 는 이번 범위가 아닙니다. 디스패처
  docstring 이 "AUTHORIZED/UNAUTHORIZED 체크는 하지 않는다" 를 의도적 설계로
  명시하고 있어, 건드리려면 별도 논의가 필요합니다.

### Next task

1. WHERE 절에 필터를 추가합니다.
'

echo "── block cases ───────────────────────────────────────────────"

expect_rc "regression #905 — Write, decision+constraint, no Consistency" 2 "Write" "$REGRESSION_BODY"

expect_rc "Edit surface fires on new_string" 2 "Edit" "$REGRESSION_BODY"

expect_rc "English markers — out of scope / by design" 2 "Write" '## Decisions

- Add both filters to the WHERE clause.
- Connection status is out of scope; the dispatcher tolerates failures by design.
'

expect_rc "F1 — a LATER decisions block carries the constraint" 2 "Write" '### Decisions

- 정상 결정입니다.

## Other

### Decisions

- 두 번째 결정.
- 이건 이번 범위가 아닙니다.
'

expect_rc "F2 — Consistency: in a DIFFERENT block does not clear" 2 "Write" '### Decisions

- 결정 A.
- 이건 이번 범위가 아닙니다.
Consistency: A vs 제약 — 모순 없음.

### Decisions

- 결정 B.
- 이것도 이번 범위가 아닙니다.
'

echo ""
echo "── pass cases ────────────────────────────────────────────────"

expect_rc "Consistency: inside the block clears the gate" 0 "Write" '### Decisions

- 필터 두 개를 추가합니다.
- `data_source_status` 는 이번 범위가 아닙니다.
Consistency: 필터 추가 vs 범위 제약 — 서로 다른 축이라 모순되지 않습니다.

### Next task

- ship it
'

expect_rc "F2 — Consistency: outside any block does NOT clear" 2 "Write" "$REGRESSION_BODY
Consistency: 문서 끝에 둔 무관한 기록 — 이건 면제하지 않습니다.
"

expect_rc "F3 — fenced example is not read as a real block" 0 "Write" '# Spec

Example of what fires:

```markdown
### Decisions
- add both filters
- status is out of scope by design
```
'

expect_rc "F3 — the gate own spec.md does not self-trip" 0 "Write" "$(cat "$ROOT_DIR/hooks/preflight-gate/write-decision-consistency-gate/spec.md")"

expect_rc "F4 — negated marker is not a constraint" 0 "Write" '### Decisions

- Authorization is not out of scope; implement it now.
- Ship the change.
'

expect_rc "F4 — marker inside inline code is a literal mention" 0 "Write" '### Decisions

- Detect the literal phrase `out of scope` in the body.
- Ship it.
'

expect_rc "decisions block with no constraint marker" 0 "Write" '### Decisions

- Add both filters to the WHERE clause.
- Mirror the sibling generator.
'

expect_rc "constraint present but no decisions header" 0 "Write" '## Findings

- `data_source_status` 는 이번 범위가 아닙니다. 의도적 설계입니다.
'

expect_rc "constraint lives OUTSIDE the decisions block" 0 "Write" '## Background

이 항목은 이번 범위가 아닙니다 — 의도적 설계입니다.

### Decisions

- Add both filters to the WHERE clause.

### Next task

- ship it
'

expect_rc "decisions header with no list item" 0 "Write" '### Decisions

산문만 있고 범위가 아닙니다 같은 서술이 있으나 목록 항목이 없습니다.
'

expect_rc "bypass env disables the gate" 0 "Write" "$REGRESSION_BODY" \
  "PRAXIS_HOOK_BYPASS_DECISION_CONSISTENCY_GATE=1"

echo ""
echo "── fail-open cases ───────────────────────────────────────────"

expect_raw_rc "malformed stdin JSON" 0 'not json at all'
expect_raw_rc "unknown tool_name" 0 '{"tool_name":"Bash","tool_input":{"command":"ls"}}'
expect_raw_rc "missing tool_input" 0 '{"tool_name":"Write"}'
expect_raw_rc "tool_input not a dict" 0 '{"tool_name":"Write","tool_input":"oops"}'
expect_raw_rc "empty content" 0 '{"tool_name":"Write","tool_input":{"content":""}}'

_failopen_out="$(python3 - "$HOOK" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("impl", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print("OK" if getattr(mod.main, "__wrapped__", None) is not None else "NO")
PY
)"
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
