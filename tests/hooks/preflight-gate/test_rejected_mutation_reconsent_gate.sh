#!/bin/bash
# tests/hooks/preflight-gate/test_rejected_mutation_reconsent_gate.sh
#
# Coverage for hooks/preflight-gate/rejected-mutation-reconsent-gate/impl.py
# (issue #1007).
#
# Two outcomes:
#   ask  — stdout contains permissionDecision "ask", exit 0
#   pass — exit 0, stdout empty, stderr empty
#
# Both directions are covered deliberately. A gate that only proves it fires
# would pass just as well if it fired on everything; the silent controls
# (different prefix, no prior rejection, non-AskUserQuestion source, read-only
# command) are what pin the narrowness the design chose.
#
# Usage: bash tests/hooks/preflight-gate/test_rejected_mutation_reconsent_gate.sh
# Exit:  0 = all pass, 1 = at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/preflight-gate/rejected-mutation-reconsent-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

TMP=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
# Transcript fixtures
#
# Shape copied from a live transcript record (toolDenialKind / toolUseResult /
# sourceToolAssistantUUID / tool_result is_error + the runtime's fixed refusal
# sentence). Hand-editing any one of those three markers is what the
# "structural only" decision is about, so the fixtures carry all of them.
# ---------------------------------------------------------------------------

REJECT_SENTENCE="The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file). STOP what you are doing and wait for the user to tell you how to proceed."

# mk_transcript <outfile> <tool_name> <question_text> [omit_rejection]
mk_transcript() {
  local out="$1" tool="$2" question="$3" omit="${4:-}"
  python3 - "$out" "$tool" "$question" "$omit" "$REJECT_SENTENCE" <<'PY'
import json, sys
out, tool, question, omit, sentence = sys.argv[1:6]
if tool == "AskUserQuestion":
    tool_input = {"questions": [{
        "question": question,
        "header": "irreversible mutation",
        "multiSelect": False,
        "options": [{"label": "Yes, proceed", "description": "run it"},
                    {"label": "No", "description": "stop"}],
    }]}
else:
    tool_input = {"command": question}
assistant = {
    "type": "assistant", "uuid": "asst-uuid-1", "isSidechain": False,
    "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_REJECTED1", "name": tool, "input": tool_input}]},
}
rejection = {
    "type": "user", "uuid": "rej-uuid-1", "parentUuid": "asst-uuid-1",
    "timestamp": "2026-08-15T00:00:00Z",
    "toolUseResult": "User rejected tool use",
    "toolDenialKind": "user-rejected",
    "sourceToolAssistantUUID": "asst-uuid-1",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_REJECTED1",
         "is_error": True, "content": sentence}]},
}
events = [assistant] if omit == "omit_rejection" else [assistant, rejection]
with open(out, "w", encoding="utf-8") as f:
    for ev in events:
        f.write(json.dumps(ev) + "\n")
PY
}

S3_QUESTION='Delete the remaining ~295M objects under s3://acme-archive/raw/2024/ ?'
SQL_QUESTION='Run DROP TABLE prod.events_raw on the production warehouse?'

REJ_S3="$TMP/rejected-s3.jsonl"
REJ_S3_NO_REJECTION="$TMP/no-rejection.jsonl"
REJ_S3_NON_ASK="$TMP/rejected-bash.jsonl"
REJ_SQL="$TMP/rejected-sql.jsonl"

mk_transcript "$REJ_S3" AskUserQuestion "$S3_QUESTION"
mk_transcript "$REJ_S3_NO_REJECTION" AskUserQuestion "$S3_QUESTION" omit_rejection
mk_transcript "$REJ_S3_NON_ASK" Bash "aws s3 rm s3://acme-archive/raw/2024/ --recursive"
mk_transcript "$REJ_SQL" AskUserQuestion "$SQL_QUESTION"

mk_payload() {
  python3 -c '
import json, sys
print(json.dumps({"session_id": "test-session", "tool_name": "Bash",
                  "transcript_path": sys.argv[2],
                  "tool_input": {"command": sys.argv[1]}}))' "$1" "$2"
}

run_case() {
  local name="$1" expected="$2" command="$3" transcript="$4"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  out=$(mk_payload "$command" "$transcript" | "$HOOK" 2>"$err_file")
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"

  case "$expected" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | grep -q '"permissionDecision": "ask"' || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [$expected→rc=$rc,stdout=$([ -n "$out" ] && echo non-empty || echo empty),stderr=$([ -n "$err" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# ASK — the rejected identifier is re-targeted by a destructive command
# ---------------------------------------------------------------------------

run_case "rejected s3 prefix, delete on the SAME prefix" ask \
  'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$REJ_S3"

run_case "same prefix without the trailing slash normalizes equal" ask \
  'aws s3 rm s3://acme-archive/raw/2024 --recursive' "$REJ_S3"

run_case "bucket-name case folds, key case preserved" ask \
  'aws s3 rm S3://ACME-ARCHIVE/raw/2024/ --recursive' "$REJ_S3"

run_case "gsutil rm on the rejected prefix (gs:// scheme)" ask \
  'gsutil -m rm -r gs://acme-archive/raw/2024/' \
  "$(mk_transcript "$TMP/rejected-gs.jsonl" AskUserQuestion \
      'Purge gs://acme-archive/raw/2024/ (568 GB)?' >/dev/null; echo "$TMP/rejected-gs.jsonl")"

run_case "rejected DROP TABLE, same table re-issued" ask \
  'psql -c "DROP TABLE prod.events_raw"' "$REJ_SQL"

run_case "rejected DROP TABLE, TRUNCATE of the same table" ask \
  'psql -c "TRUNCATE TABLE prod.events_raw"' "$REJ_SQL"

run_case "compound command: destructive half carries the rejected prefix" ask \
  'aws s3 ls s3://other/ && aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$REJ_S3"

# Message content: the block must quote the rejected question verbatim, since
# a re-consent prompt that does not say WHAT was refused cannot be answered.
_ask_reason=$(mk_payload 'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$REJ_S3" | "$HOOK" 2>/dev/null)
if echo "$_ask_reason" | python3 -c '
import json,sys
r = json.load(sys.stdin)["hookSpecificOutput"]["permissionDecisionReason"]
sys.exit(0 if "Delete the remaining ~295M objects" in r and "s3://acme-archive/raw/2024" in r else 1)' 2>/dev/null; then
  echo "PASS  [ask-detail] reason quotes the rejected question and the shared target"; PASS=$((PASS+1))
else
  echo "FAIL  [ask-detail] reason missing verbatim question or shared target"; FAIL=$((FAIL+1)); FAILED_NAMES+=("ask reason content")
fi

# ---------------------------------------------------------------------------
# PASS — controls. Each one is a way the gate could have been made to fire on
# everything; they must all stay silent.
# ---------------------------------------------------------------------------

run_case "SAME delete on a DIFFERENT prefix" pass \
  'aws s3 rm s3://acme-archive/raw/2025/ --recursive' "$REJ_S3"

run_case "sibling prefix is not a containment match" pass \
  'aws s3 rm s3://acme-archive/raw/ --recursive' "$REJ_S3"

run_case "different bucket, same key path" pass \
  'aws s3 rm s3://other-archive/raw/2024/ --recursive' "$REJ_S3"

run_case "no prior rejection in the transcript" pass \
  'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$REJ_S3_NO_REJECTION"

run_case "rejection joined to a non-AskUserQuestion tool" pass \
  'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$REJ_S3_NON_ASK"

run_case "read-only command on the rejected prefix" pass \
  'aws s3 ls s3://acme-archive/raw/2024/' "$REJ_S3"

run_case "read-only sync of the rejected prefix" pass \
  'aws s3 sync s3://acme-archive/raw/2024/ ./local' "$REJ_S3"

run_case "different table than the rejected one" pass \
  'psql -c "DROP TABLE staging.events_raw"' "$REJ_SQL"

run_case "unqualified table name does not match the qualified rejection" pass \
  'psql -c "DROP TABLE events_raw"' "$REJ_SQL"

run_case "no identifier in the command at all" pass \
  'rm -rf /tmp/scratch' "$REJ_S3"

# A rejection record missing one of the three structural markers must not count.
# This is the belt-and-braces decision made falsifiable: drop `is_error` and the
# same transcript stops being a rejection.
python3 - "$TMP/weak-rejection.jsonl" "$REJECT_SENTENCE" <<'PY'
import json, sys
out, sentence = sys.argv[1], sys.argv[2]
assistant = {
    "type": "assistant", "uuid": "asst-uuid-1", "isSidechain": False,
    "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_REJECTED1", "name": "AskUserQuestion",
         "input": {"questions": [{"question": "Delete s3://acme-archive/raw/2024/ ?"}]}}]},
}
rejection = {
    "type": "user", "uuid": "rej-uuid-1", "toolUseResult": "User rejected tool use",
    "toolDenialKind": "user-rejected", "sourceToolAssistantUUID": "asst-uuid-1",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_REJECTED1", "content": sentence}]},
}
with open(out, "w", encoding="utf-8") as f:
    for ev in (assistant, rejection):
        f.write(json.dumps(ev) + "\n")
PY
run_case "rejection record without is_error is not structural" pass \
  'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$TMP/weak-rejection.jsonl"

# ---------------------------------------------------------------------------
# Over the byte bound — indeterminate scan asks (issue #1231)
#
# The fixture is padded past the real REJECTION_SCAN_MAX_BYTES (20 MiB) rather
# than shrinking the bound, because the bound is not injectable and a test that
# shrank it would prove a different constant. Both directions are pinned on the
# SAME rejection record: over the bound the gate asks, and the identical record
# under the bound still asks for the matching target and stays silent for a
# different one — so the ask above is the bound talking, not a scan that started
# firing on everything.
# ---------------------------------------------------------------------------

OVERSIZE="$TMP/oversize.jsonl"
python3 - "$OVERSIZE" "$REJECT_SENTENCE" <<'PY'
import json, sys
out, sentence = sys.argv[1], sys.argv[2]
assistant = {
    "type": "assistant", "uuid": "asst-uuid-1", "isSidechain": False,
    "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_REJECTED1", "name": "AskUserQuestion",
         "input": {"questions": [{"question": "Delete s3://acme-archive/raw/2024/ ?"}]}}]},
}
rejection = {
    "type": "user", "uuid": "rej-uuid-1", "toolUseResult": "User rejected tool use",
    "toolDenialKind": "user-rejected", "sourceToolAssistantUUID": "asst-uuid-1",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_REJECTED1",
         "is_error": True, "content": sentence}]},
}
pad = json.dumps({"type": "system", "pad": "x" * 4000})
with open(out, "w", encoding="utf-8") as f:
    f.write(json.dumps(assistant) + "\n")
    f.write(json.dumps(rejection) + "\n")
    for _ in range(5300):          # ~21 MiB, past the 20 MiB bound
        f.write(pad + "\n")
PY
_oversize_bytes=$(wc -c < "$OVERSIZE" | tr -d ' ')
if [ "$_oversize_bytes" -gt 20971520 ]; then
  echo "PASS  [fixture is past the bound: $_oversize_bytes > 20971520]"; PASS=$((PASS+1))
else
  echo "FAIL  [fixture only $_oversize_bytes bytes, bound is 20971520]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("oversize fixture size")
fi

run_case "over the byte bound: destructive target asks even though the scan is blind" ask \
  'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$OVERSIZE"

run_case "over the byte bound: a command with no destructive identifier still passes" pass \
  'aws s3 ls s3://acme-archive/raw/2024/' "$OVERSIZE"

# The ask must say the scan could not run — an operator who reads the #1007
# wording would look for a rejected question that this gate never found.
_blind=$(mk_payload 'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$OVERSIZE" | "$HOOK" 2>/dev/null)
if printf '%s' "$_blind" | grep -q "byte bound"; then
  echo "PASS  [blind-scan ask names the byte bound]"; PASS=$((PASS+1))
else
  echo "FAIL  [blind-scan ask does not name the byte bound]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("blind-scan ask wording")
fi

# Positive control: the same rejection record UNDER the bound still resolves
# normally — asks on the matching target, silent on a different one.
UNDERSIZE="$TMP/undersize.jsonl"
head -n 2 "$OVERSIZE" > "$UNDERSIZE"
run_case "under the bound: same record still asks on the matching target" ask \
  'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$UNDERSIZE"
run_case "under the bound: same record stays silent on a different target" pass \
  'aws s3 rm s3://acme-archive/raw/2025/ --recursive' "$UNDERSIZE"

# ---------------------------------------------------------------------------
# Cascade-hint suffix (issue #229) — present on a compound state-changing
# command, absent on a single command.
# ---------------------------------------------------------------------------

_cascade=$(mk_payload 'echo seed > /tmp/x && aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$REJ_S3" | "$HOOK" 2>/dev/null)
if printf '%s' "$_cascade" | grep -q "aborts ALL parts atomically"; then
  echo "PASS  [cascade hint on compound ask]"; PASS=$((PASS+1))
else
  echo "FAIL  [cascade hint missing on compound ask]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("cascade hint on ask")
fi

_single=$(mk_payload 'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$REJ_S3" | "$HOOK" 2>/dev/null)
if printf '%s' "$_single" | grep -q "aborts ALL parts atomically"; then
  echo "FAIL  [cascade hint leaked on single-command ask]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("cascade hint leaked")
else
  echo "PASS  [no cascade hint on single-command ask]"; PASS=$((PASS+1))
fi

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

non_bash_out=$(echo '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}' | "$HOOK" 2>/dev/null)
if [ -z "$non_bash_out" ]; then
  echo "PASS  [non-Bash passthrough]"; PASS=$((PASS+1))
else
  echo "FAIL  [non-Bash passthrough] got: $non_bash_out"; FAIL=$((FAIL+1)); FAILED_NAMES+=("non-Bash passthrough")
fi

bad_out=$(echo 'not-json' | "$HOOK" 2>/dev/null)
bad_rc=$?
if [ "$bad_rc" -eq 0 ] && [ -z "$bad_out" ]; then
  echo "PASS  [malformed JSON fail-open]"; PASS=$((PASS+1))
else
  echo "FAIL  [malformed JSON fail-open] rc=$bad_rc out=$bad_out"; FAIL=$((FAIL+1)); FAILED_NAMES+=("malformed JSON")
fi

missing_out=$(mk_payload 'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$TMP/does-not-exist.jsonl" | "$HOOK" 2>/dev/null)
if [ -z "$missing_out" ]; then
  echo "PASS  [unreadable transcript fail-open]"; PASS=$((PASS+1))
else
  echo "FAIL  [unreadable transcript fail-open] got: $missing_out"; FAIL=$((FAIL+1)); FAILED_NAMES+=("unreadable transcript")
fi

# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert getattr(mod.main, "__wrapped__", None) is not None, "main is not @fail_open-wrapped"
print("OK")
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ "$_failopen_out" = "OK" ]; then
  echo "PASS  [fail-open] main() is wrapped by the shared @fail_open guard"; PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] main() not @fail_open-wrapped (rc=$_failopen_rc out=$_failopen_out)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open guard wrapping")
fi

# ---------------------------------------------------------------------------
echo
echo "=================================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=================================="
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
