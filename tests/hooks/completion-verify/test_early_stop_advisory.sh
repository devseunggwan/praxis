#!/bin/bash
# Tests for completion-verify/early-stop-advisory (Stop hook, issue #1498).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/early-stop-advisory/impl.py"
MENU_HOOK="$ROOT_DIR/hooks/completion-verify/prose-option-menu-advisory/impl.py"

unset PRAXIS_EARLY_STOP_BYPASS PRAXIS_PROSE_OPTION_MENU_BYPASS PRAXIS_UNATTENDED

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
# Review fixtures (PR #1504) — quoted spans, cue binding, new offers
# =====================================================================

build_transcript '세 개 모두 끝냈습니다. 계획대로 "이제 payments를 옮기겠습니다" 단계까지 포함해 완료했습니다.'
run_case silent "a quoted plan step is not an announcement" '{}'

build_transcript '말씀하신 "이제 나머지는 제가 할게요"에 맞춰 users와 orders만 옮겼고 payments는 손대지 않았습니다.'
run_case silent "a quoted user phrase is not an announcement" '{}'

build_transcript '세 개 모두 끝냈습니다. 「이제 payments를 옮기겠습니다」라고 적었던 단계도 완료했습니다.'
run_case silent "a 「」-quoted step is not an announcement" '{}'

build_transcript "All three are migrated, following the plan 'Next, I'll migrate /payments' to the letter." "$USER_EN"
run_case silent "a single-quoted step with an inner apostrophe" '{}'

build_transcript '> 이제 나머지는 제가 할게요

알겠습니다. users와 orders만 옮겨 두었습니다.'
run_case silent "a > quote line is not an announcement" '{}'

build_transcript "All three endpoints are migrated. I will now summarize: 42 passed, 0 failed." "$USER_EN"
run_case silent "I will now summarize is a report" '{}'

build_transcript "I migrated /users, then /orders, then /payments. I'll note that all 42 tests pass now." "$USER_EN"
run_case silent "I'll note, with a then-narrative in another sentence" '{}'

build_transcript "Pushed the fix. CI is now running; I'll report back when it finishes." "$USER_EN"
run_case silent "I'll report back is a report" '{}'

build_transcript "Everything is migrated and green. If you'd like, I can also finish the changelog entry." "$USER_EN"
run_case silent "an offer to finish a new item" '{}'

build_transcript '모두 끝냈습니다. 원하시면 앞으로도 계속 이런 식으로 정리해 드릴게요.'
run_case silent "an offer about how to work from now on" '{}'

build_transcript "All three endpoints are migrated and tests pass. So far the deploy pipeline has not yet picked it up, which is expected." "$USER_EN"
run_case silent "interim framing and open item in the same sentence" '{}'

build_transcript '3개 엔드포인트 마이그레이션을 모두 끝냈고 42 passed입니다.

다음에는 곧바로 스키마 검사부터 돌리겠습니다.'
run_case silent "다음에는 defers even with a cue in the sentence" '{}'

build_transcript '알겠습니다, 이제 세 엔드포인트 모두 옮겼고 42 passed입니다.'
run_case silent "a cue after the future form is not bound to it" '{}'

build_transcript '세 엔드포인트 모두 옮겼고 42 passed입니다. 이제 작업을 마치겠습니다.'
run_case silent "a closing (마치겠습니다) is not a next step" '{}'

build_transcript "All three endpoints are migrated. Next time, I'll continue with the schema check first." "$USER_EN"
run_case silent "next time defers even with a continuation verb" '{}'

build_transcript '`/users`, `/orders`는 끝냈습니다. `/payments` 스키마 변경은 운영 DB라 승인이 필요합니다. 승인 후 이어서 진행하겠습니다.'
run_case silent "승인이 필요 alone silences" '{}'

build_transcript '`/users`, `/orders`는 끝냈습니다. 다음 단계로 `/payments`를 옮기겠습니다.'
run_case advisory "다음 단계로 is a next-step cue" '{}'

build_transcript '`/users`, `/orders`는 끝냈습니다.

이제 `/payments`를 옮기겠습니다.

테스트는 28 passed입니다.'
run_case advisory "an announcement two prose lines from the end" '{}'

build_transcript "Next, I'll migrate /payments.

Tests: 28 passed.
Lint: clean.
Typecheck: clean." "$USER_EN"
run_case advisory "status lines after the announcement do not push it out" '{}'

build_transcript '## 중간 보고
- users: 완료
- payments: 미착수'
run_case advisory "중간 보고 heading with an unstarted item" '{}'

build_transcript "## Progress update
- users: done
- payments: not started" "$USER_EN"
run_case advisory "Progress update heading with an unstarted item" '{}'

build_transcript "Milestone 1 is done (users, orders). Payments is still to do; stopping here to report." "$USER_EN"
run_case advisory "milestone and open item in separate sentences" '{}'

build_transcript '`/users`, `/orders` 마이그레이션 끝났습니다. 남은 `/payments`도 진행하면 될까요?'
run_case advisory "…면 될까요 offer to continue" '{}'

build_transcript "Migrated /users and /orders; tests pass.

Next up: migrating /payments." "$USER_EN"
run_case advisory "Next up: opens the closing sentence" '{}'

build_transcript "/users and /orders done. Moving on to /payments next." "$USER_EN"
run_case advisory "Moving on to … next" '{}'

build_transcript "/users and /orders are migrated. I can continue with /payments if that works for you." "$USER_EN"
run_case advisory "I can continue … if that works for you" '{}'

build_transcript "Done with /users and /orders. I'll tackle /payments after this." "$USER_EN"
run_case advisory "I'll … after this" '{}'

build_transcript '`/users`, `/orders` 끝냈습니다. `/payments` 마이그레이션 진행할게요.'
run_case advisory "진행할게요 is a next step without a cue word" '{}'

build_transcript '세 개 모두 끝냈습니다. 배포는 내일 진행하겠습니다.'
run_case silent "진행하겠습니다 deferred to tomorrow" '{}'

# --- blockers: a need or a lack, not a bare word --------------------

build_transcript "Migrated /users and /orders (the credentials table is untouched). Next, I'll migrate /payments." "$USER_EN"
run_case advisory "credentials as a table name is not a blocker" '{}'

build_transcript "Migrated /users and /orders; no longer waiting for the lock. Next, I'll migrate /payments." "$USER_EN"
run_case advisory "waiting for the lock is not a blocker" '{}'

build_transcript "I migrated /users and /orders. /payments needs a staging DB password I don't have. Next I'll migrate /payments once you share it." "$USER_EN"
run_case silent "needs a password I don't have" '{}'

build_transcript "/users and /orders are done. /payments needs a staging DB password I don't have; next I'll migrate it." "$USER_EN"
run_case silent "a need bound to what is missing, with no handover phrase" '{}'

build_transcript 'users, orders는 끝냈습니다. payments는 스테이징 DB 비밀번호를 알려주시면 이어서 진행하겠습니다.'
run_case silent "비밀번호를 알려주시면" '{}'

build_transcript '`/users`, `/orders`는 끝냈습니다. `/payments`에는 스테이징 DB 자격 증명이 필요합니다. 준비되면 이어서 진행하겠습니다.'
run_case silent "자격 증명이 필요 alone silences" '{}'

build_transcript "Migrated /users and /orders. /payments is waiting on your credentials for staging. Next I'll migrate it." "$USER_EN"
run_case silent "waiting on your credentials (issue #1498 fixture)" '{}'

# --- the user asked for the stop, or did not ------------------------

EN_NEXT="Done /users and /orders. Next, I'll migrate /payments."
KO_NEXT='users, orders 끝냈습니다. 이제 payments를 옮기겠습니다.'

build_transcript "$EN_NEXT" "Are the endpoints done?"
run_case silent "auxiliary-inversion question" '{}'

build_transcript "$EN_NEXT" "Status of the migration, please."
run_case silent "status of — a status request" '{}'

build_transcript "$KO_NEXT" '마이그레이션 현황 알려줘'
run_case silent "현황 알려줘 — a status request" '{}'

build_transcript "$EN_NEXT" "Migrate the endpoints; fix the status field in payments too."
run_case advisory "status as a field name is not a status request" '{}'

build_transcript "$EN_NEXT" "Migrate the progress-bar endpoints too."
run_case advisory "progress as a noun is not a progress request" '{}'

build_transcript "$KO_NEXT" '결제 현황 API까지 포함해서 3개 엔드포인트 마이그레이션 해줘'
run_case advisory "현황 as part of an API name" '{}'

build_transcript "$KO_NEXT" '어떻게든 3개 엔드포인트 다 옮겨줄 수 있지?'
run_case advisory "어떻게든 …있지? is a request, not a question" '{}'

build_transcript "$EN_NEXT" "Can you migrate all three endpoints?"
run_case advisory "Can you …? is a request" '{}'

# =====================================================================
# Review fixtures (PR #1504, round 2) — hand-backs, confirmations, fences
# =====================================================================

build_transcript "All three files are updated and tests pass. Now let me know if you want any changes." "$USER_EN"
run_case silent "let me know if … hands the turn back" '{}'

build_transcript "Migration complete. I'll leave the next steps to you." "$USER_EN"
run_case silent "I'll leave the next steps to you is a hand-off" '{}'

build_transcript "Migration complete. I'll hand the rest over to you." "$USER_EN"
run_case silent "I'll hand the rest over to you is a hand-off" '{}'

# --- a confirmation asked before a destructive action is a wanted stop ---

DESTRUCTIVE_EN="Should I delete the remaining 40 stale branches? That is destructive, so I stopped."
DESTRUCTIVE_KO='users, orders 마이그레이션은 끝났습니다. 남은 브랜치 40개를 삭제할까요? 되돌릴 수 없어서 멈췄습니다.'

build_transcript "$DESTRUCTIVE_EN" "$USER_EN"
run_case silent "destructive confirmation question (EN)" '{}'

build_transcript "$DESTRUCTIVE_KO"
run_case silent "destructive confirmation question (KO, 삭제할까요)" '{}'

build_transcript "Migrated /users and /orders. Should I force-push the rewritten history to the remaining branches?" "$USER_EN"
run_case silent "force-push confirmation question" '{}'

build_transcript "Should I continue with the remaining endpoints?" "$USER_EN"
run_case advisory "a plain continue question still fires" '{}'

# --- negated open items and TODO as a file name --------------------

build_transcript "Summary so far: A done, B done. Remaining: none." "$USER_EN"
run_case silent "Remaining: none negates the open item" '{}'

build_transcript "Progress update: users and orders migrated. No remaining items." "$USER_EN"
run_case silent "no remaining items negates the open item" '{}'

build_transcript "The remaining items are documented in TODO.md. So far everything requested is done." "$USER_EN"
run_case silent "items documented in TODO.md are not open here" '{}'

build_transcript "So far /users and /orders are done. See TODO.md for the details." "$USER_EN"
run_case silent "TODO.md is a file name, not an open item" '{}'

build_transcript "Progress update: /users and /orders are migrated.
- payments: TODO" "$USER_EN"
run_case advisory "TODO as a word is still an open item" '{}'
assert_kind "an interim report that lists unfinished items" "TODO as a word is reported as type 4"

# --- fences close on the opener's character and length (CommonMark) ---

build_transcript "Done with /users and /orders; tests pass.

~~~
# next I'll deploy
~~~

All tests pass." "$USER_EN"
run_case silent "announcement inside a tilde fence" '{}'

build_transcript "Done with /users and /orders; tests pass.

\`\`\`\`
\`\`\`
# next I'll deploy
\`\`\`
\`\`\`\`

All tests pass." "$USER_EN"
run_case silent "inner three-backtick line does not close a four-backtick fence" '{}'

build_transcript "Done with /users and /orders.

~~~
\`\`\`
Next, I'll migrate /payments
~~~

All tests pass." "$USER_EN"
run_case silent "a backtick line does not close a tilde fence" '{}'

build_transcript "Migrated /users and /orders.

\`\`\`
pytest tests/api -q
\`\`\`

Next, I'll migrate /payments." "$USER_EN"
run_case advisory "an announcement after a closed fence still fires" '{}'

# --- the notice is addressed to the user ----------------------------

build_transcript "$T1"
run_hook "$HOOK" '{}'
assert_kind 'reply \"continue\"' "the notice tells the user they can reply continue"
assert_kind "Claude does not see this notice" "the notice says the model does not receive it"

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

# =====================================================================
# Unattended runs: PRAXIS_UNATTENDED=1 blocks, capped per human turn
# =====================================================================

STATE_HOME="$(mktemp -d)" || { echo "FAIL  [mktemp -d]"; exit 1; }
trap 'rm -f "${TMP_FILES[@]}"; rm -rf "$STATE_HOME"' EXIT
SID="sess-early-stop-test"
STATE_FILE="$STATE_HOME/cache/early-stop-continuations-$SID.json"
UA=(PRAXIS_HOME="$STATE_HOME" PRAXIS_UNATTENDED=1)

# build_run_transcript <final_text> <record>... — records are `h:<uuid>`
# (the opening human message, USER_KO), `f` (Stop-hook feedback written back
# as a user message after a block), or `a` (an assistant tool call).
build_run_transcript() {
  local final_text="$1"
  shift
  TRANSCRIPT="$(mktemp)"
  TMP_FILES+=("$TRANSCRIPT")
  python3 - "$TRANSCRIPT" "$final_text" "$USER_KO" "$@" <<'PY'
import json, sys
path, final_text, user_text, *records = sys.argv[1:]
events = []
for rec in records:
    if rec.startswith("h:"):
        events.append({"type": "user", "uuid": rec[2:], "origin": {"kind": "human"},
                       "message": {"role": "user", "content": user_text}})
    elif rec == "f":
        events.append({"type": "user", "uuid": f"fb-{len(events)}", "message": {
            "role": "user",
            "content": "Stop hook feedback:\n[early-stop-advisory] Your turn ended "
                       "with requested work still open"}})
    else:
        events.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "working"}]}})
events.append({"type": "assistant", "message": {"role": "assistant",
               "content": [{"type": "text", "text": final_text}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# expect_block <name> <count> — OUT is a block whose reason reaches the model.
expect_block() {
  local name="$1" count="$2"
  if [ "$RC" -eq 0 ] && [ -z "$ERR" ] && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
r = d["reason"]
assert d["decision"] == "block" and "systemMessage" not in d, d
assert r.startswith("[early-stop-advisory] Your turn ended"), r
assert "a next step announced but not taken" in r, r
assert "다음 단계로 남은" in r, r
assert "Continue with the open items. If one is blocked, say in one line what is blocking it." in r, r
assert "does not override the need for confirmation on risky or destructive actions" in r, r
assert "automatic continuation " + sys.argv[1] + " of 2" in r, r
' "$count"; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$RC out=<$OUT> err=<$ERR>"; FAIL=$((FAIL + 1))
  fi
}

# expect_notice <name> <cap|nocap>
expect_notice() {
  local name="$1" cap="$2"
  if [ "$RC" -eq 0 ] && [ -z "$ERR" ] && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
m = d["systemMessage"]
assert "decision" not in d, d
assert m.startswith("[early-stop-advisory] The turn ended"), m
assert ("continuation cap reached" in m) == (sys.argv[1] == "cap"), m
' "$cap"; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$RC out=<$OUT> err=<$ERR>"; FAIL=$((FAIL + 1))
  fi
}

# Marker unset: the notice, never a decision — even with a session id.
build_run_transcript "$T1" h:u1
run_hook "$HOOK" "{\"session_id\": \"$SID\"}" PRAXIS_HOME="$STATE_HOME"
expect_notice "marker unset: T1 is the user notice, no decision" nocap
[ ! -e "$STATE_FILE" ] && { echo "PASS  [marker unset keeps no counter]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [marker unset keeps no counter]"; FAIL=$((FAIL + 1)); }

# Anything but exactly `1` is not the marker.
for v in "true" " 1" "yes"; do
  run_hook "$HOOK" "{\"session_id\": \"$SID\"}" PRAXIS_HOME="$STATE_HOME" PRAXIS_UNATTENDED="$v"
  expect_notice "PRAXIS_UNATTENDED='$v' is not the marker" nocap
done

# Marker set: stop 1 and 2 block, stop 3 in the same human turn is the capped
# notice. Stops 2 and 3 follow a block, so the host sets stop_hook_active and
# the transcript carries the block's feedback record.
build_run_transcript "$T1" h:u1 a
run_hook "$HOOK" "{\"session_id\": \"$SID\"}" "${UA[@]}"
expect_block "marker set: T1 blocks with a reason for the model" 1

build_run_transcript "$T1" h:u1 a f a
run_hook "$HOOK" "{\"session_id\": \"$SID\", \"stop_hook_active\": true}" "${UA[@]}"
expect_block "second stop in the same turn blocks despite stop_hook_active" 2

build_run_transcript "$T1" h:u1 a f a f a
run_hook "$HOOK" "{\"session_id\": \"$SID\", \"stop_hook_active\": true}" "${UA[@]}"
expect_notice "third stop in the same turn falls back to the capped notice" cap

run_hook "$HOOK" "{\"session_id\": \"$SID\", \"stop_hook_active\": true}" "${UA[@]}"
expect_notice "a further stop in that turn stays capped" cap

# A new human message (new uuid, same text) opens a new turn: count resets.
build_run_transcript "$T1" h:u1 a f a f a h:u2 a
run_hook "$HOOK" "{\"session_id\": \"$SID\"}" "${UA[@]}"
expect_block "a new human message resets the counter" 1

# Marker set but the stop is one this hook does not advise on: silent.
build_run_transcript '3개 엔드포인트 마이그레이션을 모두 끝냈습니다. `pytest tests/api` 결과 42 passed.' h:u3
run_hook "$HOOK" "{\"session_id\": \"$SID\"}" "${UA[@]}"
[ -z "$OUT" ] && [ "$RC" -eq 0 ] && { echo "PASS  [marker set: a finished turn stays silent]"; PASS=$((PASS + 1)); } \
  || { echo "FAIL  [marker set: a finished turn stays silent] out=<$OUT>"; FAIL=$((FAIL + 1)); }

# Bypass wins over the marker.
build_run_transcript "$T1" h:u4
run_case silent "bypass with the marker set" "{\"session_id\": \"$SID\"}" \
  "${UA[@]}" PRAXIS_EARLY_STOP_BYPASS=1

# Type-3 menu with the marker: the menu hook's lane, this hook stays silent.
build_run_transcript "$T3" h:u5
run_case silent "T3 menu with the marker set" "{\"session_id\": \"$SID\"}" "${UA[@]}"

# The user asked for the stop: silent even with the marker.
build_transcript "$T1" '마이그레이션 계획만 세워줘'
run_case silent "plan request with the marker set" "{\"session_id\": \"$SID\"}" "${UA[@]}"

# No session id: the count cannot be kept, so no block — the notice.
build_run_transcript "$T1" h:u6
run_hook "$HOOK" '{}' "${UA[@]}"
expect_notice "marker set without session_id: notice, not a block" nocap

# Malformed state file: never a block, never a crash; the next human turn
# counts from zero again.
build_run_transcript "$T1" h:u7
for junk in '{not json' '[1, 2]' '{"turn": "uuid:u7", "blocks": "x"}'; do
  printf '%s' "$junk" >"$STATE_FILE"
  run_hook "$HOOK" "{\"session_id\": \"$SID\"}" "${UA[@]}"
  expect_notice "malformed state <$junk>: notice, not a block" nocap
done
printf '{not json' >"$STATE_FILE"
run_hook "$HOOK" "{\"session_id\": \"$SID\", \"stop_hook_active\": true}" "${UA[@]}"
if [ "$RC" -eq 0 ] && [ -z "$ERR" ] && ! printf '%s' "$OUT" | grep -q '"decision"'; then
  echo "PASS  [malformed state after a block: no block]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed state after a block: no block] out=<$OUT> err=<$ERR>"; FAIL=$((FAIL + 1))
fi
build_run_transcript "$T1" h:u7 a h:u8
run_hook "$HOOK" "{\"session_id\": \"$SID\"}" "${UA[@]}"
expect_block "after a malformed state, the next human turn blocks again" 1

# Unwritable state: a count that cannot be stored never blocks.
rm -f "$STATE_FILE"; mkdir -p "$STATE_FILE"
build_run_transcript "$T1" h:u9
run_hook "$HOOK" "{\"session_id\": \"$SID\"}" "${UA[@]}"
expect_notice "state path unusable: notice, not a block" nocap
rmdir "$STATE_FILE"

# A readable state whose new count cannot be written: no continuation is
# granted (the block is only emitted after the bounding count is on disk).
if PRAXIS_HOME="$STATE_HOME" python3 - "$HOOK" "$SID" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("esa_impl", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod._save_state = lambda *a, **k: False
assert mod.claim_continuation(sys.argv[2], "uuid:write-fails") is None
PY
then
  echo "PASS  [failed count write grants no continuation]"; PASS=$((PASS + 1))
else
  echo "FAIL  [failed count write grants no continuation]"; FAIL=$((FAIL + 1))
fi

# =====================================================================
# Stops that wait on the user's approval or decision — silent (#1498 replay)
# =====================================================================

DONE_KO='`/users`, `/orders` 마이그레이션을 끝냈습니다.

'
DONE_EN='Two endpoints are migrated. '

for text in \
  "${DONE_KO}머지는 다른 세션에 영향을 주는 변경이라 승인받고 진행하겠습니다." \
  "${DONE_KO}공개 저장소 코멘트를 고치는 일이라 승인을 받고 진행하겠습니다." \
  "${DONE_KO}승인받아 진행하겠습니다." \
  "${DONE_KO}두 가지만 정해 주시면 바로 진행하겠습니다." \
  "${DONE_KO}preview 를 띄울지는 말씀 주시면 그대로 진행하겠습니다." \
  "${DONE_KO}그대로 둬도 무해합니다. 판단만 주시면 됩니다." \
  "${DONE_KO}어떻게 할지 지시 주십시오." \
  "${DONE_KO}머지하시려면 별도로 브리핑 올리고 진행하겠습니다." \
  "${DONE_KO}이 프레이밍이면 진행하겠습니다." \
  "${DONE_KO}(가)와 (나) 중 어느 쪽으로 갈까요?" \
  "${DONE_KO}계속 진행할까요, 아니면 이 분류 자체를 먼저 정할까요?" \
  "${DONE_KO}작업 목록을 정리해도 될까요?" \
  "${DONE_EN}I'll merge it with your approval." \
  "${DONE_EN}Once approved, I'll proceed with the merge." \
  "${DONE_EN}Should I go ahead and merge?" \
  "${DONE_EN}Should I continue, or would you rather review first?" \
  "${DONE_KO}1번과 3번 결과를 붙여 주시면 이어서 판단하겠습니다." \
  "${DONE_KO}로그인 마치시면 알려주세요. 바로 2번부터 진행하겠습니다." \
  "${DONE_KO}창을 재시작하시면 이어서 두 가지를 재드리겠습니다." \
  "${DONE_KO}머지는 확인받고 진행하겠습니다." \
  "${DONE_KO}머지를 원하시면 머지 전 확인 절차부터 진행하겠습니다." \
  "${DONE_KO}스레드에 현황 답글을 달지는 알려주세요." \
  "${DONE_KO}PR 을 생성할까요?" \
  "${DONE_KO}B-2 로 갈까요? 가면 3번부터 읽고 시작하겠습니다." \
  "${DONE_KO}A 안 또는 B 안 중에 무엇으로 갈까요?" \
  "${DONE_EN}Should I merge now or wait for your review?"; do
  build_transcript "$text"
  run_case silent "approval/decision stop: ${text##*$'\n'}" '{}'
done

# Controls: the same families without the user's decision still fire.
for text in \
  "${DONE_KO}승인받았으니 다음 단계로 남은 \`/payments\`를 진행하겠습니다." \
  "${DONE_KO}그러면 남은 \`/payments\`를 진행하겠습니다." \
  "${DONE_KO}남은 \`/payments\`도 이어서 진행해도 될까요?" \
  "${DONE_KO}어느 쪽이든 결과는 같으니 다음 단계로 남은 \`/payments\`를 진행하겠습니다." \
  "${DONE_EN}Should I continue with the remaining endpoint?" \
  "${DONE_EN}Should I continue with the remaining tests or docs?" \
  "${DONE_KO}남은 테스트 또는 문서 작업을 이어서 진행할까요?" \
  "${DONE_KO}필요하시면 남은 \`/payments\`도 이어서 진행하겠습니다." \
  "${DONE_KO}원하시면 실패한 실행의 로그도 이어서 보겠습니다." \
  "${DONE_KO}커밋이 두 번 막힌 원인을 확정했습니다. 다음 단계로 남은 \`/payments\`를 진행하겠습니다."; do
  build_transcript "$text"
  run_case advisory "control still fires: ${text##*$'\n'}" '{}'
done

# =====================================================================
# A launched background task is still running
# =====================================================================

# build_bg_transcript <final_text> <record ...> — a human message, then each
# record, then the final assistant text, one second apart. Records:
#   L:<id> Bash run_in_background launch   M:<id> Monitor launch, timeout 300000ms
#   V:<id> Monitor launch, expires in 2m   P:<id> Monitor launch, persistent
#   A:<id> background Agent launch         N:<id> task-notification, completed
#   E:<id> Monitor event (no <status>)     Q:<id> queue-operation, completed
#   C:<id> queued_command attachment       S:<id>/R:<id> task_status done/running
#   K:<id> TaskStop by the model           h   a later human message
#   X:<id> tool result quoting a finished notification for <id>
#   G:<id> tool result mentioning a launch line mid-output
#   W:<seconds> the clock moves on by that much before the next record
build_bg_transcript() {
  local final_text="$1"
  shift
  TRANSCRIPT="$(mktemp)"
  TMP_FILES+=("$TRANSCRIPT")
  python3 - "$TRANSCRIPT" "$final_text" "$USER_KO" "$@" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
path, final_text, user_text, *records = sys.argv[1:]

def human():
    return {"type": "user", "uuid": f"h{len(events)}", "origin": {"kind": "human"},
            "message": {"role": "user", "content": user_text}}

def result(text):
    return [{"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{len(events)}", "name": "Bash", "input": {}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{len(events)}", "content": text}]}}]

def note(tid, status=True):
    body = f"<task-notification>\n<task-id>{tid}</task-id>\n"
    body += "<status>completed</status>\n" if status else "<event>tick</event>\n"
    return body + "</task-notification>"

events = []
events.append(human())
for rec in records:
    kind, _, tid = rec.partition(":")
    if kind == "h":
        events.append(human())
    elif kind == "L":
        events += result(f"Command running in background with ID: {tid}. Output is being written to: /tmp/{tid}.output")
    elif kind == "M":
        events += result(f"Monitor started (task {tid}, timeout 300000ms). You will be notified on each event.")
    elif kind == "V":
        events += result(f"Monitor started (task {tid}, expires in 2m unless the source ends first; you get one notice at expiry).")
    elif kind == "P":
        events += result(f"Monitor started (task {tid}, persistent — runs until TaskStop or session end).")
    elif kind == "W":
        events.append({"advance": int(tid)})
    elif kind == "A":
        events += result(f"Async agent launched successfully.\nagentId: {tid} (internal ID)")
    elif kind in ("N", "E"):
        events.append({"type": "user", "origin": {"kind": "task-notification"},
                       "message": {"role": "user", "content": note(tid, kind == "N")}})
    elif kind == "Q":
        events.append({"type": "queue-operation", "operation": "enqueue", "content": note(tid)})
    elif kind == "C":
        events.append({"type": "attachment", "attachment": {"type": "queued_command", "prompt": note(tid)}})
    elif kind in ("S", "R"):
        events.append({"type": "attachment", "attachment": {
            "type": "task_status", "taskId": tid, "status": "completed" if kind == "S" else "running"}})
    elif kind == "K":
        events.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "stop", "name": "TaskStop", "input": {"task_id": tid}}]}})
    elif kind == "X":
        events += result("earlier output:\n" + note(tid))
    elif kind == "G":
        events += result(f"grep hits:\nCommand running in background with ID: {tid}")
events.append({"type": "assistant", "message": {"role": "assistant",
               "content": [{"type": "text", "text": final_text}]}})
clock = datetime(2026, 9, 28, tzinfo=timezone.utc)
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        if "advance" in e:
            clock += timedelta(seconds=e["advance"])
            continue
        clock += timedelta(seconds=1)
        e["timestamp"] = clock.isoformat().replace("+00:00", "Z")
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

bg_case() {
  local expected="$1" name="$2"
  shift 2
  build_bg_transcript "$T1" "$@"
  run_case "$expected" "background: $name" '{}'
}

bg_case silent   "Bash launch still running"                         L:b1
bg_case silent   "Monitor launch still watching"                     M:m1
bg_case silent   "background Agent still running"                    A:a1
bg_case silent   "Monitor event is not the end of the watch"         M:m1 E:m1
bg_case silent   "task_status running is not the end"                L:b1 R:b1
bg_case silent   "one of two launches still running, across a notification" L:b1 L:b2 N:b1
bg_case silent   "a quoted notification in a tool result ends nothing" L:b1 X:b1
bg_case advisory "Bash launch finished (notification record)"        L:b1 N:b1
bg_case advisory "finished via queue-operation"                      L:b1 Q:b1
bg_case advisory "finished via queued_command attachment"            L:b1 C:b1
bg_case advisory "finished via task_status"                          L:b1 S:b1
bg_case advisory "stopped by TaskStop"                               M:m1 K:m1
bg_case advisory "a launch line mid-output is not a launch"          G:b1
bg_case silent   "a launch before a question asked mid-wait still counts" L:b1 h
bg_case advisory "a launch before the human message, since finished" L:b1 h N:b1
bg_case silent   "Monitor inside its timeout"                        M:m1 W:200
bg_case advisory "Monitor past its timeout has ended"                M:m1 W:400
bg_case silent   "Monitor inside its expires-in"                     V:m1 W:60
bg_case advisory "Monitor past its expires-in has ended"             V:m1 W:200
bg_case silent   "persistent Monitor has no deadline"                P:m1 W:100000
bg_case silent   "a Bash launch has no deadline"                     L:b1 W:100000
bg_case advisory "no background task at all"

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
