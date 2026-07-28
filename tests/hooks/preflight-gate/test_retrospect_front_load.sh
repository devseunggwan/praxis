#!/usr/bin/env bash
# test_retrospect_front_load.sh — coverage for the Phase-2 front-load pre-pass in
# hooks/preflight-gate/retrospect-active-marker/impl.py (issue #772).
#
# When the retrospect skill is invoked and the PreToolUse payload carries a
# readable transcript_path, the hook scans the session-so-far for silent-pass
# conduct and drops a candidate hint file next to the marker. This is best-effort
# and MUST be fail-open: the marker is always set; any front-load failure is
# swallowed and never blocks (rc=0, empty stdout).

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/retrospect-active-marker/impl.py"
CATALOG="$ROOT_DIR/hooks/_lib/silent-pass-catalog.json"

PASS=0
FAIL=0
WORK_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$WORK_DIR"' EXIT

ok()   { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; }

# A conduct transcript: a credential-display (hard) + sanctioned-path-bypass (hard).
mk_conduct() {
  local f="$1"
  {
    jq -nc --arg t "showing the secret: | aws_secret_access_key | deadbeefcafe1234567890abcdefghijklmn0099 |" \
      '{message:{role:"assistant",content:[{type:"text",text:$t}]}}'
    jq -nc --arg t "ran aws secretsmanager get-secret-value --secret-id foo/bar" \
      '{message:{role:"assistant",content:[{type:"text",text:$t}]}}'
  } > "$f"
}

# invoke <marker-file> <payload-json>  -> sets OUT/RC globals
invoke() {
  local sf="$1" payload="$2"
  OUT=$(printf '%s' "$payload" | env PRAXIS_RETROSPECT_ACTIVE_FILE="$sf" python3 "$HOOK" 2>/dev/null)
  RC=$?
}

# --- case 1: transcript present -> candidate hints written -------------------
SF1="$WORK_DIR/marker1.json"
CAND1="$SF1.candidates.json"
TR1="$WORK_DIR/conduct1.jsonl"
mk_conduct "$TR1"
P1=$(jq -nc --arg tp "$TR1" \
  '{tool_name:"Skill", tool_input:{skill:"praxis:retrospect"}, session_id:"s1", transcript_path:$tp}')
invoke "$SF1" "$P1"
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then ok "front_load rc=0 + empty stdout"; else bad "front_load rc=$RC out=[$OUT]"; fi
[ -f "$SF1" ] && ok "front_load marker still set" || bad "front_load marker missing"
if [ -f "$CAND1" ]; then
  ok "candidate hint file written"
  got=$(jq -r '.mandatory_candidates[].class_id' "$CAND1" 2>/dev/null | sort | paste -sd, -)
  [ "$got" = "credential-display,sanctioned-path-bypass" ] \
    && ok "hints list both hard classes" || bad "hints class_ids=[$got]"
  cv=$(jq -r '.catalog_version' "$CAND1" 2>/dev/null)
  ev=$(jq -r '.version' "$CATALOG" 2>/dev/null)
  [ "$cv" = "$ev" ] && ok "catalog_version recorded ($cv)" || bad "catalog_version=[$cv] expected [$ev]"
else
  bad "candidate hint file NOT written"
fi

# --- case 2: no transcript_path -> no candidate file (skip silently) ---------
SF2="$WORK_DIR/marker2.json"
CAND2="$SF2.candidates.json"
P2='{"tool_name":"Skill","tool_input":{"skill":"praxis:retrospect"},"session_id":"s2"}'
invoke "$SF2" "$P2"
[ "$RC" -eq 0 ] && [ -f "$SF2" ] && ok "no-transcript marker set rc=0" || bad "no-transcript rc=$RC"
[ ! -f "$CAND2" ] && ok "no-transcript writes no candidate file" || bad "no-transcript wrote a candidate file"

# --- case 3: transcript_path points to a missing file -> fail-open ----------
SF3="$WORK_DIR/marker3.json"
CAND3="$SF3.candidates.json"
P3=$(jq -nc '{tool_name:"Skill", tool_input:{skill:"praxis:retrospect"}, session_id:"s3", transcript_path:"/no/such/transcript.jsonl"}')
invoke "$SF3" "$P3"
[ "$RC" -eq 0 ] && [ -z "$OUT" ] && [ -f "$SF3" ] && ok "missing-transcript fail-open (marker set, rc=0)" \
  || bad "missing-transcript rc=$RC out=[$OUT]"
[ ! -f "$CAND3" ] && ok "missing-transcript writes no candidate file" || bad "missing-transcript wrote a candidate file"

# --- case 4: non-retrospect skill -> no front-load --------------------------
SF4="$WORK_DIR/marker4.json"
CAND4="$SF4.candidates.json"
TR4="$WORK_DIR/conduct4.jsonl"; mk_conduct "$TR4"
P4=$(jq -nc --arg tp "$TR4" \
  '{tool_name:"Skill", tool_input:{skill:"some-other-skill"}, session_id:"s4", transcript_path:$tp}')
invoke "$SF4" "$P4"
[ "$RC" -eq 0 ] && [ ! -f "$SF4" ] && [ ! -f "$CAND4" ] \
  && ok "non-retrospect skill: no marker, no candidate" || bad "non-retrospect leaked state"

printf -- '--- retrospect-front-load: %d pass / %d fail ---\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
