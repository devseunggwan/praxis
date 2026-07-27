#!/bin/bash
# Tests for completion-verify/proposal-premise-gate (Stop hook, issue #846).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/proposal-premise-gate/impl.py"

unset PRAXIS_PROPOSAL_PREMISE_BYPASS

PASS=0
FAIL=0

# build_transcript <final_text> [tool_name] [tool_input_json] -> writes $TRANSCRIPT
# When tool_name is given, an assistant tool_use block (+ a tool_result-only
# user bridge event) is inserted BEFORE the final assistant text message, so
# `get_current_turn` includes it as this-turn probe evidence.
build_transcript() {
  local final_text="$1" tool_name="${2:-}" tool_input="${3:-{\}}"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" "$tool_name" "$tool_input" <<'PY'
import json, sys
path, final_text, tool_name, tool_input_raw = sys.argv[1:5]
events = [{"message": {"role": "user", "content": "please wrap up"}}]
if tool_name:
    events.append({
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": tool_name,
                         "input": json.loads(tool_input_raw)}],
        },
    })
    events.append({
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    })
events.append({
    "message": {"role": "assistant",
                "content": [{"type": "text", "text": final_text}]},
})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <advisory|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload out err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  local err_file
  err_file=$(mktemp)
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  case "$expected" in
    advisory:*)
      local marker="${expected#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[proposal-premise-gate]" in d["systemMessage"], d
assert "decision" not in d, d
' || ok=0
      case "$out" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc out=<$out> err=<$err>"; FAIL=$((FAIL + 1))
  fi
}

# =====================================================================
# CORE: shape + premise + no probe -> advisory
# =====================================================================

build_transcript "## 우선순위 개선안

1. tiered-model-routing-flag 를 도입한다 — no such routing_tier_flag path exists.
2. 다른 항목은 무관하다."
run_case "advisory:contains 1 code-checkable" "numbered item, negative-existence, no probe -> advisory" '{}'

build_transcript "## Priority Recommendation

| Item | Detail |
| --- | --- |
| routing | tier-routing-config does not exist in this repo |
"
run_case "advisory:contains 1 code-checkable" "table row, negative-existence, no probe -> advisory" '{}'

build_transcript "## 개선 제안

1. artifact-pr-suppressor 가 review queue 를 burns 합니다."
run_case "advisory:contains 1 code-checkable" "cost claim (burns), no probe -> advisory" '{}'

build_transcript "## 우선순위

1. grid materialize 개선 시 30% 향상됩니다."
run_case "advisory:contains 1 code-checkable" "percent cost claim, no probe -> advisory" '{}'

build_transcript "**우선순위**

1. legacy-sync-job 가 없습니다 — 재도입 제안."
run_case "advisory:contains 1 code-checkable" "bold-label heading, no probe -> advisory" '{}'

# =====================================================================
# SILENT: probe evidence overlaps the claim's key token
# =====================================================================

build_transcript \
  "## 우선순위 개선안

1. tiered-model-routing-flag 를 도입한다 — no such routing_tier_flag path exists." \
  "Grep" '{"pattern": "routing_tier_flag"}'
run_case silent "grep pattern overlaps claim token -> silent" '{}'

build_transcript \
  "## Priority

1. legacy-connector-module does not exist in this codebase." \
  "Bash" '{"command": "grep -rn legacy-connector-module ."}'
run_case silent "bash command overlaps claim token -> silent" '{}'

build_transcript \
  "## 제안

1. artifact-pr-suppressor 가 review 를 burns 합니다." \
  "Read" '{"file_path": "docs/artifact-pr-suppressor.md"}'
run_case silent "read file_path overlaps claim token -> silent" '{}'

# =====================================================================
# SILENT: no proposal shape at all
# =====================================================================

run_case_no_shape() {
  build_transcript "$1"
  run_case silent "$2" '{}'
}

run_case_no_shape \
  "이 기능은 does not exist 인 것으로 보입니다. 하지만 이건 그냥 일반 서술입니다." \
  "plain prose, no heading/table shape -> silent"

run_case_no_shape \
  "1. 첫 항목
2. 둘째 항목
3. 셋째 항목 does not exist 관련 서술" \
  "numbered list with no heading/keyword shape -> silent"

# =====================================================================
# SILENT: shape present but no code-checkable premise marker
# =====================================================================

build_transcript "## 우선순위

1. 문서를 업데이트한다.
2. 테스트를 추가한다."
run_case silent "shape + items but no premise marker -> silent" '{}'

# =====================================================================
# SILENT: standalone heading keyword with ordinary prose below (no item lines)
# =====================================================================

build_transcript "## Recommendation

We think this capability does not exist, as a passing remark in normal prose."
run_case silent "heading present but premise marker not inside an item line -> silent" '{}'

# =====================================================================
# Env / fail-open
# =====================================================================

build_transcript "## 우선순위

1. tiered-routing-flag does not exist."
run_case silent "bypass env suppresses advisory" '{}' PRAXIS_PROPOSAL_PREMISE_BYPASS=1

out=$(echo 'not json' | python3 "$HOOK" 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  echo "PASS  [malformed JSON fails open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON fails open] rc=$rc out=<$out>"; FAIL=$((FAIL + 1))
fi

out=$(echo '{"transcript_path": "/nonexistent/path.jsonl"}' | python3 "$HOOK" 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  echo "PASS  [missing transcript fails open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [missing transcript fails open] rc=$rc out=<$out>"; FAIL=$((FAIL + 1))
fi

build_transcript "## 우선순위

1. tiered-routing-flag does not exist."
run_case silent "stop_hook_active guard" '{"stop_hook_active": true}'

# empty assistant text
TRANSCRIPT="$(mktemp)"
python3 -c '
import json
events = [{"message": {"role": "user", "content": "please wrap up"}}]
with open("'"$TRANSCRIPT"'", "w") as f:
    for e in events:
        f.write(json.dumps(e) + "\n")
'
run_case silent "no last assistant text -> silent" '{}'

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
