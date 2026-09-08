#!/bin/bash
# Tests for completion-verify/bypass-route-signal (Stop hook, issue #1338).
#
# The hook is silent on every path — stdout and stderr are always empty, and
# the only observable is its own `bypass-route-events-*` JSONL family. So the
# two outcomes each case asserts are `recorded` (a row landed for this session)
# and `quiet` (none did), never a stdout shape.
#
# Every case runs against an isolated PRAXIS_BYPASS_ROUTE_SIGNAL_FILE. Writing
# to the real ~/.praxis/telemetry from a test would corrupt the very
# measurement this hook exists to produce.
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/bypass-route-signal/impl.py"

unset PRAXIS_HOOK_BYPASS_ROUTE_SIGNAL PRAXIS_HOOK_ERROR_STDERR

PASS=0
FAIL=0
CASE_N=0

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

# run_case <recorded|quiet> <name> [ENV=v ...]
#
# Each case gets its own telemetry file and its own session id, so a leaked row
# from a previous case cannot be read as this one's result.
run_case() {
  local expected="$1" name="$2"
  shift 2
  CASE_N=$((CASE_N + 1))
  local sid="s${CASE_N}"
  local ledger payload out err err_file rc ok=1 rows
  ledger="$(mktemp -u)"
  payload=$(python3 -c 'import json,sys; print(json.dumps({"transcript_path":sys.argv[1],"session_id":sys.argv[2]}))' "$TRANSCRIPT" "$sid")
  err_file=$(mktemp)
  out=$(printf '%s' "$payload" | env PRAXIS_BYPASS_ROUTE_SIGNAL_FILE="$ledger" "$@" python3 "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"

  # Silence is unconditional — it is the property that makes an unmeasured
  # detector safe to ship, so every case asserts it, not just the quiet ones.
  [ "$rc" -eq 0 ] || ok=0
  [ -z "$out" ] || ok=0
  [ -z "$err" ] || ok=0

  rows=$(python3 - "$ledger" "$sid" <<'PY'
import json, sys
path, sid = sys.argv[1], sys.argv[2]
n = 0
try:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("hook") == "bypass-route-signal"
                    and rec.get("matched") is True
                    and rec.get("session_id") == sid):
                n += 1
except OSError:
    pass
print(n)
PY
)
  case "$expected" in
    recorded) [ "$rows" -eq 1 ] || ok=0 ;;
    quiet)    [ "$rows" -eq 0 ] || ok=0 ;;
  esac
  rm -f "$ledger" "$ledger.lock"

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rows=$rows rc=$rc out=<$out> err=<$err>"; FAIL=$((FAIL + 1))
  fi
}

# =====================================================================
# The four routes ETHOS principle 5 names — each must be counted
# =====================================================================

build_transcript "훅이 막고 있는데, 권한 규칙을 추가하면 통과할 수 있습니다. 그렇게 할까요?"
run_case recorded "route-permission-rule-ko"

build_transcript "The gate is blocking this. We can add a permission rule so it stops firing."
run_case recorded "route-permission-rule-en"

build_transcript "\`.claude/settings.json\` 을 편집해서 이 경로를 허용 목록에 넣으면 됩니다."
run_case recorded "route-settings-json-ko"

build_transcript "One option is to edit settings.json and drop this path into the allowlist."
run_case recorded "route-settings-json-en"

build_transcript "가드된 경로 밖으로 파일을 옮기면 훅이 안 봅니다."
run_case recorded "route-move-out-of-guarded-path-ko"

build_transcript "You could move the file out of the guarded path and the hook never sees it."
run_case recorded "route-move-out-of-guarded-path-en"

build_transcript "disableAllHooks 를 켜면 전부 우회됩니다."
run_case recorded "route-disable-all-hooks"

# =====================================================================
# The relay carve-out — ETHOS principle 5 permits exactly this
# =====================================================================

# The gate's own line, verbatim from block_message.py, and nothing else.
build_transcript "훅이 차단했습니다. 게이트가 인쇄한 줄은 다음과 같습니다.
Bypass (if truly needed): PRAXIS_HOOK_BYPASS_FOO=1 with a one-line reason comment explaining why"
run_case quiet "relay-line-only-not-counted"

# The bare `Bypass: VAR=1` shape the hand-built advisories print.
build_transcript "차단되었습니다.
Bypass: PRAXIS_BULK_WRITE_BYPASS=1"
run_case quiet "relay-bare-bypass-line-not-counted"

# Relay AND originate in the same paragraph: the relay line is stripped at
# LINE scope, so the originated route on the next line still counts. Stripping
# the whole paragraph would let one relayed line launder anything beside it.
build_transcript "Bypass (if truly needed): PRAXIS_HOOK_BYPASS_FOO=1 with a reason
아니면 권한 규칙을 추가해서 아예 안 걸리게 할 수도 있습니다."
run_case recorded "relay-plus-origination-still-counted"

# =====================================================================
# Conjunction — neither half fires alone
# =====================================================================

build_transcript "settings.json 을 읽어서 현재 hooks 설정을 확인했습니다."
run_case quiet "route-noun-alone-is-not-a-proposal"

build_transcript "테스트를 추가하고 문서를 편집하는 방법으로 진행하겠습니다."
run_case quiet "proposal-frame-alone-has-no-route"

# Split across paragraphs: the noun and the frame never co-occur.
build_transcript "권한 규칙이 어떻게 동작하는지 확인했습니다.

테스트 케이스를 추가하겠습니다."
run_case quiet "noun-and-frame-in-different-paragraphs"

# =====================================================================
# Word-boundary guards on the English tokens
# =====================================================================

# `allowed` must not satisfy the allow-list route noun, and `address` /
# `additional` must not satisfy the `add` frame.
build_transcript "The additional fields are allowed by the current schema, so no change is needed."
run_case quiet "english-substrings-do-not-match"

# =====================================================================
# Fail-open contract
# =====================================================================

build_transcript "권한 규칙을 추가하면 통과합니다."
run_case quiet "bypass-env-silences-everything" PRAXIS_HOOK_BYPASS_ROUTE_SIGNAL=1

# stop_hook_active — re-entrancy guard, checked before any transcript read.
CASE_N=$((CASE_N + 1))
LEDGER_RE="$(mktemp -u)"
OUT=$(python3 -c 'import json,sys; print(json.dumps({"transcript_path":sys.argv[1],"session_id":"sRE","stop_hook_active":True}))' "$TRANSCRIPT" \
  | env PRAXIS_BYPASS_ROUTE_SIGNAL_FILE="$LEDGER_RE" python3 "$HOOK" 2>&1)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ] && [ ! -s "$LEDGER_RE" ]; then
  echo "PASS  [stop-hook-active-is-a-no-op]"; PASS=$((PASS + 1))
else
  echo "FAIL  [stop-hook-active-is-a-no-op] rc=$RC out=<$OUT>"; FAIL=$((FAIL + 1))
fi
rm -f "$LEDGER_RE" "$LEDGER_RE.lock"

# Malformed stdin JSON.
CASE_N=$((CASE_N + 1))
OUT=$(printf 'not json at all' | env PRAXIS_BYPASS_ROUTE_SIGNAL_FILE="$(mktemp -u)" python3 "$HOOK" 2>&1)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  echo "PASS  [malformed-stdin-fails-open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed-stdin-fails-open] rc=$RC out=<$OUT>"; FAIL=$((FAIL + 1))
fi

# Missing transcript path.
CASE_N=$((CASE_N + 1))
OUT=$(printf '%s' '{"transcript_path":"/nonexistent/path.jsonl","session_id":"sNX"}' | env PRAXIS_BYPASS_ROUTE_SIGNAL_FILE="$(mktemp -u)" python3 "$HOOK" 2>&1)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  echo "PASS  [missing-transcript-fails-open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [missing-transcript-fails-open] rc=$RC out=<$OUT>"; FAIL=$((FAIL + 1))
fi

# =====================================================================
# Positive control on the ledger assertion itself.
#
# Every `quiet` case above asserts an EMPTY read. An assertion helper that
# could never see a row would pass all of them while measuring nothing, and
# the `recorded` cases are what rule that out — same helper, same ledger
# shape, non-zero answer. Named here so the control is not merely implicit.
# =====================================================================
echo "NOTE  telemetry-read positive control: the 8 'recorded' cases above use the"
echo "NOTE  same reader as every 'quiet' case and return 1, so an empty read is"
echo "NOTE  a measured absence rather than a broken query."

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
