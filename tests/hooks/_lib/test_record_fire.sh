#!/bin/bash
# Tests for _lib/record_fire.sh — fire-ledger instrumentation for shell hooks
# (issue #848). Covers the helper's own contract plus one end-to-end record
# per instrumented impl.sh hook, because the helper working in isolation says
# nothing about whether a hook actually armed it on the branch that fires.
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); echo "PASS  [$1]"; }
ko() { FAIL=$((FAIL + 1)); echo "FAIL  [$1] $2"; }

# assert_record <ledger> <name> <expected_hook> <expected_decision> <expected_session>
assert_record() {
  local ledger="$1" name="$2" hook="$3" decision="$4" session="$5"
  if [ ! -s "$ledger" ]; then
    ko "$name" "no ledger record written"
    return
  fi
  python3 - "$ledger" "$hook" "$decision" "$session" <<'PY'
import json, sys
path, hook, decision, session = sys.argv[1:5]
rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
match = [r for r in rows
         if r["hook"] == hook and r["decision"] == decision
         and r["session_id"] == session and r["granularity"] == "rich"]
# Exactly one: a duplicate arm (say, sourcing record_fire.sh twice) would
# double-count the engagement, and "at least one" cannot see that.
assert len(match) == 1, (
    f"expected exactly 1 rich {hook}/{decision}/{session} record, "
    f"got {len(match)} in {rows}"
)
PY
  if [ $? -eq 0 ]; then ok "$name"; else ko "$name" "record mismatch"; fi
}

# --- helper contract -------------------------------------------------------
# A shell hook lives two directories below _lib, so the probe harness has to
# reproduce that depth for the relative source path to resolve.
PROBE_ROOT="$(mktemp -d)"
mkdir -p "$PROBE_ROOT/_lib" "$PROBE_ROOT/role/name"
cp "$ROOT_DIR/hooks/_lib/record_fire.sh" "$PROBE_ROOT/_lib/"
cp "$ROOT_DIR/hooks/_lib/_fire_ledger.py" "$PROBE_ROOT/_lib/"
cat > "$PROBE_ROOT/role/name/impl.sh" <<'EOF'
#!/bin/sh
. "$(dirname "$0")/../../_lib/record_fire.sh"
praxis_fire_arm probe-hook probe-role "sess-probe" ""
[ "${PROBE_DECIDE:-}" = "block" ] && PRAXIS_FIRE_DECISION=block
exit 0
EOF

LEDGER="$PROBE_ROOT/led.jsonl"
PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" sh "$PROBE_ROOT/role/name/impl.sh"
assert_record "$LEDGER" "arm defaults to pass" probe-hook pass sess-probe

rm -f "$LEDGER"
PROBE_DECIDE=block PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" sh "$PROBE_ROOT/role/name/impl.sh"
assert_record "$LEDGER" "decision override recorded" probe-hook block sess-probe

# fail-open: telemetry disabled
rm -f "$LEDGER"
PRAXIS_FIRE_TELEMETRY_DISABLE=1 PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
  sh "$PROBE_ROOT/role/name/impl.sh"
rc=$?
if [ "$rc" -eq 0 ] && [ ! -f "$LEDGER" ]; then
  ok "disable env writes nothing, exit 0"
else
  ko "disable env writes nothing, exit 0" "rc=$rc ledger=$(ls "$LEDGER" 2>&1)"
fi

# fail-open: _fire_ledger.py missing from the resolved _lib
NOLIB_ROOT="$(mktemp -d)"
mkdir -p "$NOLIB_ROOT/_lib" "$NOLIB_ROOT/role/name"
cp "$ROOT_DIR/hooks/_lib/record_fire.sh" "$NOLIB_ROOT/_lib/"
cp "$PROBE_ROOT/role/name/impl.sh" "$NOLIB_ROOT/role/name/impl.sh"
PRAXIS_FIRE_TELEMETRY_FILE="$NOLIB_ROOT/led.jsonl" sh "$NOLIB_ROOT/role/name/impl.sh"
rc=$?
if [ "$rc" -eq 0 ] && [ ! -f "$NOLIB_ROOT/led.jsonl" ]; then
  ok "missing _fire_ledger fails open"
else
  ko "missing _fire_ledger fails open" "rc=$rc"
fi

# fail-open: unwritable ledger path
PRAXIS_FIRE_TELEMETRY_FILE=/dev/null/nope sh "$PROBE_ROOT/role/name/impl.sh"
rc=$?
[ "$rc" -eq 0 ] && ok "unwritable ledger fails open" \
  || ko "unwritable ledger fails open" "rc=$rc"

# --- end-to-end: each instrumented impl.sh hook ----------------------------
TMP="$(mktemp -d)"

# completion-verify — claim without evidence blocks
python3 - "$TMP/cv.jsonl" <<'PY'
import json, sys
events = [
    {"message": {"role": "user", "content": "go"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": "모두 완료했습니다."}]}},
]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
LEDGER="$TMP/cv-led.jsonl"
printf '{"transcript_path":"%s","session_id":"sess-cv"}' "$TMP/cv.jsonl" \
  | PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
    bash "$ROOT_DIR/hooks/completion-verify/completion-verify/impl.sh" >/dev/null
assert_record "$LEDGER" "completion-verify records block" completion-verify block sess-cv

# retrospect-mix-check — ordinary message passes
python3 - "$TMP/rm.jsonl" <<'PY'
import json, sys
events = [
    {"message": {"role": "user", "content": "go"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": "평범한 응답입니다."}]}},
]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
LEDGER="$TMP/rm-led.jsonl"
printf '{"transcript_path":"%s","session_id":"sess-rm"}' "$TMP/rm.jsonl" \
  | PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
    bash "$ROOT_DIR/hooks/completion-verify/retrospect-mix-check/impl.sh" >/dev/null
assert_record "$LEDGER" "retrospect-mix-check records pass" retrospect-mix-check pass sess-rm

# codex-review-route — /codex:review advises.
#
# The advisory fires on >= 2 non-bare worktrees, so it must run against a
# repository this test builds rather than whichever checkout happens to host
# the run: a developer machine mid-session has many worktrees and a CI runner
# has exactly one, which would make the same assertion pass locally and fail
# in CI.
CR_REPO="$TMP/cr-repo"
git init -q "$CR_REPO"
: > "$CR_REPO/seed"
git -C "$CR_REPO" add seed
git -C "$CR_REPO" -c user.name=t -c user.email=t@e commit -qm seed
git -C "$CR_REPO" worktree add -q -b second "$TMP/cr-wt2"
LEDGER="$TMP/cr-led.jsonl"
printf '{"prompt":"/codex:review","session_id":"sess-cr"}' \
  | (cd "$CR_REPO" && PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
      bash "$ROOT_DIR/hooks/advisory-nudge/codex-review-route/impl.sh") >/dev/null
assert_record "$LEDGER" "codex-review-route records advise" codex-review-route advise sess-cr

# An absent session_id must reach the ledger as "", never as the "unknown"
# placeholder those hooks use for their log lines and marker filenames.
# aggregate_fires adds any non-empty session string to its distinct-session
# set, so "unknown" would collapse every unattributed fire into one fake
# session — inflating the session count these records exist to measure.
for probe in \
  "completion-verify:completion-verify/completion-verify/impl.sh" \
  "retrospect-mix-check:completion-verify/retrospect-mix-check/impl.sh"
do
  probe_hook="${probe%%:*}"
  LEDGER="$TMP/${probe_hook}-nosess.jsonl"
  printf '{"transcript_path":"%s"}' "$TMP/rm.jsonl" \
    | PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
      bash "$ROOT_DIR/hooks/${probe#*:}" >/dev/null
  if python3 -c '
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
sys.exit(0 if recs and all(r.get("session_id") == "" for r in recs) else 1)
' "$LEDGER" 2>/dev/null; then
    ok "$probe_hook records an absent session as empty, not \"unknown\""
  else
    ko "$probe_hook records an absent session as empty, not \"unknown\"" \
      "$(cat "$LEDGER" 2>/dev/null)"
  fi
done

# strike-counter — three strikes then a blocking stop
STATE="$TMP/state"
printf '{"session_id":"sess-sc"}' \
  | PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_DISABLE=1 \
    bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" session-start >/dev/null
for i in 1 2 3; do
  CLAUDE_SESSION_ID=sess-sc PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_DISABLE=1 \
    bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" strike "v$i" >/dev/null
done
LEDGER="$TMP/sc-led.jsonl"
printf '{"session_id":"sess-sc"}' \
  | PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
    bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" stop >/dev/null
assert_record "$LEDGER" "strike-counter records block" strike-counter block sess-sc

# slash modes are user commands, not hook engagements — they must stay out of
# the ledger, otherwise the fire rate this ledger measures is inflated.
LEDGER="$TMP/sc-slash.jsonl"
CLAUDE_SESSION_ID=sess-sc PRAXIS_STATE_DIR="$STATE" PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" \
  bash "$ROOT_DIR/hooks/completion-verify/strike-counter/impl.sh" status >/dev/null
if [ ! -f "$LEDGER" ]; then
  ok "strike-counter slash mode records nothing"
else
  ko "strike-counter slash mode records nothing" "$(cat "$LEDGER")"
fi

rm -rf "$PROBE_ROOT" "$NOLIB_ROOT" "$TMP"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
