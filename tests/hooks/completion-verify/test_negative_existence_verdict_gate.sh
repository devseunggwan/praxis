#!/bin/bash
# Tests for completion-verify/negative-existence-verdict-gate (Stop hook, issue #804).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/negative-existence-verdict-gate/impl.py"

unset PRAXIS_HOOK_BYPASS_NEGATIVE_EXISTENCE_GATE PRAXIS_NEGATIVE_EXISTENCE_ADVISORY PRAXIS_HOOK_ERROR_STDERR

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
    {"message": {"role": "user", "content": "please wrap up"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": final_text}]}},
]
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <block|advisory|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  # issue #647 H3: advisory/block both arrive as stdout JSON (exit always 0);
  # stderr must stay empty in every case.
  local out
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>/tmp/nev-stderr.$$)
  rc=$?
  err=$(cat /tmp/nev-stderr.$$ 2>/dev/null; rm -f /tmp/nev-stderr.$$)
  case "$expected" in
    block)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["decision"] == "block", d
assert "[negative-existence-verdict-gate]" in d["reason"], d
assert "Enumerated:" in d["reason"], d
' || ok=0
      ;;
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[negative-existence-verdict-gate]" in d["systemMessage"], d
assert "decision" not in d, d
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

# =====================================================================
# Core regression — F5 motivating case (Hub #3981)
# =====================================================================

# F5: gate-result framing + negative-existence marker, NO Enumerated -> block
build_transcript "게이트 결과가 나왔습니다 — 매핑 규칙이 없습니다."
run_case block "f5-verdict-no-enumerated" '{}'

# F5 WITH an Enumerated line in the same paragraph -> silent pass
build_transcript "게이트 결과가 나왔습니다 — 매핑 규칙이 없습니다.
Enumerated: dags/tests/laplace/templates/sql (테스트 1개)"
run_case silent "f5-verdict-with-enumerated" '{}'

# =====================================================================
# False-positive regression from the issue's 미결 — a LEGIT narrow verdict
# still triggers (it carries framing+marker) and must PASS with an
# Enumerated line, BLOCK without one.
# =====================================================================

build_transcript "확인 결과: gmail OAuth 는 없습니다. 있는 건 facebook/google_ads/github 뿐."
run_case block "legit-verdict-no-enumerated" '{}'

build_transcript "확인 결과: gmail OAuth 는 없습니다. 있는 건 facebook/google_ads/github 뿐.
Enumerated: facebook, google_ads, github, gmail → gmail 만 부재"
run_case silent "legit-verdict-with-enumerated" '{}'

# =====================================================================
# Trigger requires BOTH marker AND framing in the SAME paragraph
# =====================================================================

# marker only, no framing -> silent (self-surfacing error, per issue rationale)
build_transcript "cost 컬럼이 테이블에 없습니다."
run_case silent "marker-only-no-framing" '{}'

# framing only, positive verdict (no marker) -> silent
build_transcript "게이트 결과: 매핑 규칙이 정상 존재합니다. 통과."
run_case silent "framing-only-positive" '{}'

# marker + framing in DIFFERENT paragraphs -> silent (not co-located)
build_transcript "게이트 결과를 확인했습니다.

별도로, 캐시 파일이 없습니다."
run_case silent "marker-framing-different-paragraphs" '{}'

# =====================================================================
# English tokens
# =====================================================================

# EN marker + EN framing, no Enumerated -> block
build_transcript "Acceptance check complete — the mapping rule does not exist."
run_case block "en-acceptance-does-not-exist" '{}'

# EN marker + EN framing WITH Enumerated -> silent
build_transcript "Acceptance check complete — the mapping rule does not exist.
Enumerated: dags/tests/templates/sql (1 test) -> only candidate read"
run_case silent "en-acceptance-with-enumerated" '{}'

# AC # framing + KO marker -> block
build_transcript "AC #2 판정: 스키마 매핑이 존재하지 않습니다."
run_case block "ac-hash-framing-block" '{}'

# =====================================================================
# Other KO markers / framings
# =====================================================================

build_transcript "완료 조건 검토: 해당 엔드포인트는 미구현 상태입니다."
run_case block "wanryo-jogeon-migujeon" '{}'

build_transcript "판정이 나왔습니다: 관련 훅을 찾지 못했습니다."
run_case block "panjeong-motchatda" '{}'

# =====================================================================
# Enumerated line edge cases (presence-only)
# =====================================================================

# bare 'Enumerated:' with nothing after the colon -> does NOT satisfy -> block
build_transcript "게이트 결과 — 매핑 규칙이 없습니다.
Enumerated:"
run_case block "enumerated-empty-still-block" '{}'

# 'Enumerated:' not at column 0 (indented / mid-line) -> does NOT satisfy -> block
build_transcript "게이트 결과 — 매핑 규칙이 없습니다. 참고로 Enumerated: x 는 인라인이라 무효."
run_case block "enumerated-inline-still-block" '{}'

# multi-paragraph: one clean verdict WITH its line, one bare verdict WITHOUT -> block
build_transcript "게이트 결과 A — 규칙이 없습니다.
Enumerated: a, b, c → 모두 부재

게이트 결과 B — 규칙이 존재하지 않습니다."
run_case block "multi-para-one-unenumerated" '{}'

# =====================================================================
# Tiers / env
# =====================================================================

# advisory demote -> systemMessage, no decision
build_transcript "게이트 결과가 나왔습니다 — 매핑 규칙이 없습니다."
run_case advisory "advisory-demote" '{}' PRAXIS_NEGATIVE_EXISTENCE_ADVISORY=1

# bypass -> silent
build_transcript "게이트 결과가 나왔습니다 — 매핑 규칙이 없습니다."
run_case silent "bypass-env" '{}' PRAXIS_HOOK_BYPASS_NEGATIVE_EXISTENCE_GATE=1

# stop_hook_active -> silent (loop guard)
build_transcript "게이트 결과가 나왔습니다 — 매핑 규칙이 없습니다."
run_case silent "stop-hook-active" '{"stop_hook_active": true}'

# neutral final message -> silent
build_transcript "README 를 업데이트하고 테스트를 돌렸습니다; 전부 통과."
run_case silent "neutral-message" '{}'

# =====================================================================
# Fail-open
# =====================================================================

# missing transcript path -> silent
TRANSCRIPT="/nonexistent/transcript.jsonl"
run_case silent "missing-transcript" '{}'

# malformed JSON stdin -> silent
err=$(printf 'not json' | python3 "$HOOK" 2>&1 1>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
  echo "PASS  [malformed-json]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed-json] rc=$rc err=<$err>"; FAIL=$((FAIL + 1))
fi

# empty payload -> silent
err=$(printf '{}' | python3 "$HOOK" 2>&1 1>/dev/null)
rc=$?
out=$(printf '{}' | python3 "$HOOK" 2>/dev/null)
if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -z "$out" ]; then
  echo "PASS  [empty-payload]"; PASS=$((PASS + 1))
else
  echo "FAIL  [empty-payload] rc=$rc out=<$out> err=<$err>"; FAIL=$((FAIL + 1))
fi

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
