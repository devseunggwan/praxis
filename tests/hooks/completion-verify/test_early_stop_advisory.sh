#!/bin/bash
# Tests for completion-verify/early-stop-advisory (Stop hook, issue #1498).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/early-stop-advisory/impl.py"
MENU_HOOK="$ROOT_DIR/hooks/completion-verify/prose-option-menu-advisory/impl.py"

unset PRAXIS_EARLY_STOP_BYPASS PRAXIS_PROSE_OPTION_MENU_BYPASS

PASS=0
FAIL=0
TMP_FILES=()
trap 'rm -f "${TMP_FILES[@]}"' EXIT

USER_KO='users, orders, payments 3개 엔드포인트를 v2로 마이그레이션하고 테스트까지 통과시켜줘'
USER_EN='Migrate the users, orders and payments endpoints to v2 and get the tests passing.'

# build_transcript <final_text> [user_text] — the user message, one Bash
# tool_use (`pytest tests/api -q`) with its `28 passed` result, then the
# final assistant text.
build_transcript() {
  local final_text="$1" user_text="${2-$USER_KO}"
  TRANSCRIPT="$(mktemp)"
  TMP_FILES+=("$TRANSCRIPT")
  python3 - "$TRANSCRIPT" "$final_text" "$user_text" <<'PY'
import json, sys
path, final_text, user_text = sys.argv[1:4]
events = [
    {"message": {"role": "user", "content": user_text}},
    {"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "pytest tests/api -q"}}]}},
    {"message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "28 passed"}]}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": final_text}]}},
]
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_hook <hook> <stop_payload_extra_json> [ENV=v ...] — sets OUT, ERR, RC.
run_hook() {
  local hook="$1" extra="$2"
  shift 2
  local payload err_file
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  err_file=$(mktemp)
  OUT=$(printf '%s' "$payload" | env PRAXIS_FIRE_TELEMETRY_DISABLE=1 "$@" python3 "$hook" 2>"$err_file")
  RC=$?
  ERR=$(cat "$err_file"); rm -f "$err_file"
}

# run_case <advisory|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local ok=1
  run_hook "$HOOK" "$extra" "$@"
  [ "$RC" -eq 0 ] || ok=0
  [ -z "$ERR" ] || ok=0
  case "$expected" in
    advisory)
      printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["systemMessage"].startswith("[early-stop-advisory]"), d
assert "decision" not in d, d
' || ok=0
      ;;
    silent)
      [ -z "$OUT" ] || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$RC out=<$OUT> err=<$ERR>"; FAIL=$((FAIL + 1))
  fi
}

# assert_kind <substring> <name> — the last advisory names the detected type.
assert_kind() {
  local want="$1" name="$2"
  if printf '%s' "$OUT" | grep -qF -- "$want"; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] want=<$want> out=<$OUT>"; FAIL=$((FAIL + 1))
  fi
}

# =====================================================================
# Issue fixtures — must fire (Korean)
# =====================================================================

T1='3개 엔드포인트 중 `/users`, `/orders` 마이그레이션을 끝냈고 테스트도 통과했습니다.

다음 단계로 남은 `/payments` 엔드포인트를 마이그레이션하고 테스트를 갱신하겠습니다.'
build_transcript "$T1"
run_case advisory "T1 closing announces the next step" '{}'
assert_kind "a next step announced but not taken" "T1 is reported as type 1"

T2='`/users`, `/orders` 마이그레이션을 끝냈습니다. 원하시면 남은 `/payments`도 이어서 진행하겠습니다. 다른 방향을 원하시면 말씀해 주세요.'
build_transcript "$T2"
run_case advisory "T2 offers to continue on the user's preference" '{}'
assert_kind "an offer to continue" "T2 is reported as type 2"

T4B='## 중간 보고

스키마 마이그레이션 1단계까지 진행했습니다.

- users: 반영
- orders: 반영
- payments: 미착수

작업이 길어져 이 시점에서 한 번 공유드립니다.'
build_transcript "$T4B"
run_case advisory "T4b interim report with an unstarted item" '{}'
assert_kind "an interim report that lists unfinished items" "T4b is reported as type 4"

build_transcript '`/users`, `/orders`는 끝냈습니다.

이제 `/payments`를 옮길게요.'
run_case advisory "…ㄹ게요 form counts as a first-person future" '{}'

build_transcript '`/users`, `/orders` 마이그레이션을 끝냈습니다. 나머지 `/payments`도 계속 진행할까요?'
run_case advisory "할까요 question offering the rest" '{}'

# =====================================================================
# Issue fixtures — must fire (English)
# =====================================================================

build_transcript "I've migrated /users and /orders and the tests pass.

Next, I'll migrate the remaining /payments endpoint and update its tests." "$USER_EN"
run_case advisory "EN T1 next step announced" '{}'

build_transcript "I've finished migrating /users and /orders. If you'd like, I can continue with the remaining /payments endpoint — let me know if you'd prefer a different direction." "$USER_EN"
run_case advisory "EN T2 offer to continue" '{}'

build_transcript "## Progress update

Phase 1 of the schema migration is done.

- users: applied
- orders: applied
- payments: not started

This has been a long run, so I'm sharing progress at this point." "$USER_EN"
run_case advisory "EN T4b interim report" '{}'

build_transcript "/users and /orders are migrated. Want me to keep going with the rest of the endpoints?" "$USER_EN"
run_case advisory "EN 'want me to' offer" '{}'

# =====================================================================
# Type 3 belongs to prose-option-menu-advisory — no double fire
# =====================================================================

T3='`/users`, `/orders` 마이그레이션을 끝냈습니다.

진행 전에 정해 주실 것:
- (a) payments 에러 코드를 v2 형식으로 바꿀지
- (b) 로그 레벨을 info로 둘지

어느 쪽으로 할지 선택해 주세요.'
build_transcript "$T3"
run_case silent "T3 decision menu is not this hook's" '{}'
run_hook "$MENU_HOOK" '{}'
if printf '%s' "$OUT" | grep -qF "[prose-option-menu-advisory]"; then
  echo "PASS  [T3 is caught by prose-option-menu-advisory]"; PASS=$((PASS + 1))
else
  echo "FAIL  [T3 is caught by prose-option-menu-advisory] out=<$OUT>"; FAIL=$((FAIL + 1))
fi

# A menu that also closes on a next-step announcement: the sibling fires, so
# this hook must stay silent even though its own type-1 predicate matches.
build_transcript "$T3

정해 주시면 다음으로 남은 payments를 마이그레이션하겠습니다."
run_case silent "menu plus next-step closing defers to the menu hook" '{}'

# =====================================================================
# Stops the user wants — silent
# =====================================================================

build_transcript '3개 엔드포인트 마이그레이션을 모두 끝냈습니다. `pytest tests/api` 결과 42 passed.'
run_case silent "control: done" '{}'

build_transcript '`/payments`는 스테이징 DB 자격 증명이 없어 진행할 수 없습니다. 자격 증명을 주시면 이어가겠습니다.'
run_case silent "real blocker: missing credentials" '{}'

build_transcript '`/users`, `/orders`는 끝냈습니다. `/payments` 스키마 변경은 운영 DB라 승인이 필요합니다. 승인해 주시면 이어서 진행하겠습니다.'
run_case silent "real blocker: approval needed" '{}'

build_transcript "All three endpoints are migrated. \`pytest tests/api\` reports 42 passed." "$USER_EN"
run_case silent "EN control: done" '{}'

build_transcript "/payments can't be migrated: there are no staging DB credentials in this environment. I'll continue once you provide them." "$USER_EN"
run_case silent "EN real blocker" '{}'

build_transcript "$T4B" '지금까지 진행 상황만 알려줘'
run_case silent "the user asked for a progress report" '{}'

build_transcript "$T1" '마이그레이션 계획만 세워줘'
run_case silent "the user asked for a plan" '{}'

# =====================================================================
# False-positive guards
# =====================================================================

build_transcript '3개 엔드포인트 마이그레이션을 모두 끝냈고 `pytest tests/api` 결과 42 passed입니다.

다음에는 스키마 검사도 먼저 돌리겠습니다.'
run_case silent "finished report mentioning 다음에는 in passing" '{}'

build_transcript "All three endpoints are migrated and 42 tests pass. Next time I'll run the schema check first." "$USER_EN"
run_case silent "finished report mentioning next time in passing" '{}'

build_transcript '`/payments` 테스트는 v2 스키마에서 `amount`가 정수로 바뀌어 실패했습니다. 원하시면 이어서 `serializers.py`의 타입도 고치겠습니다.' '왜 /payments 테스트가 실패했어?'
run_case silent "final answer to a question" '{}'

build_transcript "The /payments test fails because v2 made \`amount\` an integer. If you'd like, I can continue and fix the serializer next." "Why does the /payments test fail?"
run_case silent "EN final answer to a question" '{}'

build_transcript '3개 엔드포인트 마이그레이션을 모두 끝냈고 42 passed입니다. 원하시면 PR 설명도 작성해 드릴게요.'
run_case silent "offer of something new after the work is done" '{}'

build_transcript '## 진행 상황

- users, orders, payments: 반영 완료

남은 작업은 없습니다.'
run_case silent "progress heading with its open-item word negated" '{}'

build_transcript '마이그레이션을 모두 끝냈습니다.

```
# 다음 단계로 남은 스크립트를 실행하겠습니다
```'
run_case silent "announcement inside a code fence" '{}'

build_transcript '먼저 `/users`를 옮기고, 이어서 `/orders`와 `/payments`를 옮기겠습니다 — 라는 계획대로 세 개 모두 끝냈습니다.

`pytest tests/api` 결과 42 passed.

PR 본문도 갱신했습니다.

리뷰 요청까지 마쳤습니다.'
run_case silent "a future verb mid-report, outside the closing lines" '{}'

# =====================================================================
# Guards and fail-open
# =====================================================================

build_transcript "$T1"
run_case silent "stop_hook_active guard" '{"stop_hook_active": true}'

build_transcript "$T1"
run_case silent "bypass env" '{}' PRAXIS_EARLY_STOP_BYPASS=1

build_transcript '3개 엔드포인트 마이그레이션을 모두 끝냈습니다.'
run_case advisory "payload last_assistant_message is the text graded" \
  "$(python3 -c 'import json,sys; print(json.dumps({"last_assistant_message": sys.argv[1]}, ensure_ascii=False))' "$T1")"

TRANSCRIPT="/nonexistent/transcript-$$.jsonl"
run_case silent "missing transcript" '{}'

TRANSCRIPT="$(mktemp)"; TMP_FILES+=("$TRANSCRIPT")
printf 'not json\n' >"$TRANSCRIPT"
run_case silent "malformed transcript" '{}'

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
