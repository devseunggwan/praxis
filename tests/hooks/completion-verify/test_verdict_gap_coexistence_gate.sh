#!/bin/bash
# Tests for completion-verify/verdict-gap-coexistence-gate (Stop hook).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/verdict-gap-coexistence-gate/impl.py"

unset PRAXIS_VERDICT_GAP_BYPASS PRAXIS_VERDICT_GAP_STRICT PRAXIS_HOOK_ERROR_STDERR

PASS=0
FAIL=0

# build_transcript <final_text> -> writes path to $TRANSCRIPT
build_transcript() {
  local final_text="$1"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" <<'PY'
import json, sys
path, final_text = sys.argv[1], sys.argv[2]
events = [
    {"message": {"role": "user", "content": "review this PR"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": final_text}]}},
]
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <advisory|advisory-strict|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  local out
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>/tmp/vgcg-stderr.$$)
  rc=$?
  err=$(cat /tmp/vgcg-stderr.$$ 2>/dev/null; rm -f /tmp/vgcg-stderr.$$)
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[verdict-gap-coexistence-gate]" in d["systemMessage"]
assert "decision" not in d
' || ok=0
      ;;
    advisory-strict)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["decision"] == "block"
assert "[verdict-gap-coexistence-gate]" in d["reason"]
' || ok=0
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

# --- motivating incident verbatim (issue #845) ------------------------------
build_transcript "⚠️ 미해소 갭 — 데이터 동일성 검증 증거 부재. 판단: 리뷰어에게 보내도 됩니다."
run_case advisory "incident-verbatim" '{}'

# --- EN GO-verdict + EN gap marker ------------------------------------------
build_transcript "This is ready to merge. TODO: still need to verify the edge case."
run_case advisory "en-ready-to-merge-plus-todo" '{}'

build_transcript "All clear. Note: the perf regression is unverified."
run_case advisory "en-all-clear-plus-unverified" '{}'

# --- KR variants -------------------------------------------------------------
build_transcript "머지 가능합니다. 다만 검증 증거 부재 상태입니다."
run_case advisory "kr-mergeable-plus-evidence-gap" '{}'

build_transcript "문제 없습니다. pending: 로드 테스트는 아직입니다."
run_case advisory "kr-no-problem-plus-pending" '{}'

# --- GO verdict alone, no gap marker -> silent ------------------------------
build_transcript "머지 가능합니다. 모든 테스트가 통과했습니다."
run_case silent "go-verdict-alone-no-gap" '{}'

# --- gap marker alone, no GO verdict -> silent ------------------------------
build_transcript "⚠️ 미해소 갭이 있습니다. 추가 검증이 필요합니다."
run_case silent "gap-alone-no-verdict" '{}'

# --- negated GO verdict does not count --------------------------------------
build_transcript "머지 가능하지 않습니다. ⚠️ 미해소 갭이 있습니다."
run_case silent "negated-verdict-kr" '{}'

build_transcript "This is not ready to merge. TODO: fix the flaky test."
run_case silent "negated-verdict-en" '{}'

# --- gap marker immediately followed by resolution language -> not "unresolved"
build_transcript "머지 가능합니다. 갭은 이미 해소되었습니다."
run_case silent "gap-already-resolved-kr" '{}'

build_transcript "Ready to merge. The TODO items were addressed before this commit."
run_case silent "gap-already-resolved-en" '{}'

# --- conditional linkage already present -> silenced (remedy already applied)
build_transcript "⚠️ 미해소 갭이 있습니다. 갭 해소 시 보내도 됩니다."
run_case silent "conditional-linkage-present-kr" '{}'

build_transcript "TODO: verify the migration. Once the gap is resolved, this is ready to merge."
run_case silent "conditional-linkage-present-en" '{}'

# --- quoted line (reporting, not asserting) ---------------------------------
build_transcript "> 지난 세션에서 '⚠️ 미해소 갭... 보내도 됩니다' 라고 잘못 말했습니다 (인용)."
run_case silent "quoted-line" '{}'

# --- neutral message, neither category -> silent ----------------------------
build_transcript "Reviewed the diff. Looks good overall."
run_case silent "no-signal" '{}'

# --- strict mode -------------------------------------------------------------
build_transcript "⚠️ 미해소 갭 — 데이터 동일성 검증 증거 부재. 판단: 리뷰어에게 보내도 됩니다."
run_case advisory-strict "strict-mode" '{}' PRAXIS_VERDICT_GAP_STRICT=1

# --- bypass env ---------------------------------------------------------------
build_transcript "⚠️ 미해소 갭 — 데이터 동일성 검증 증거 부재. 판단: 리뷰어에게 보내도 됩니다."
run_case silent "bypass" '{}' PRAXIS_VERDICT_GAP_BYPASS=1

# --- stop_hook_active -> silent (loop guard) --------------------------------
build_transcript "⚠️ 미해소 갭 — 데이터 동일성 검증 증거 부재. 판단: 리뷰어에게 보내도 됩니다."
run_case silent "stop-hook-active" '{"stop_hook_active": true}'

# --- missing transcript -> silent (fail-open) -------------------------------
TRANSCRIPT="/nonexistent/transcript.jsonl"
run_case silent "missing-transcript" '{}'

echo ""
echo "verdict-gap-coexistence-gate: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
