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
# wording would look for a rejected question that this gate never found. A
# fresh PRAXIS_HOME makes this the session's first call: no cursor yet.
_blind_home=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
_blind=$(mk_payload 'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$OVERSIZE" | PRAXIS_HOME="$_blind_home" "$HOOK" 2>/dev/null)
if printf '%s' "$_blind" | grep -q "byte bound"; then
  echo "PASS  [blind-scan ask names the byte bound]"; PASS=$((PASS+1))
else
  echo "FAIL  [blind-scan ask does not name the byte bound]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("blind-scan ask wording")
fi

# Resumable (#1280 follow-up): the blind call above saved a cursor at the byte
# budget for "test-session" under that home, so the next call for the same
# session continues from there, catches up with the ~1 MiB left, and asks for
# the real reason — the rejected question naming the target — not the bound.
_caught=$(mk_payload 'aws s3 rm s3://acme-archive/raw/2024/ --recursive' "$OVERSIZE" | PRAXIS_HOME="$_blind_home" "$HOOK" 2>/dev/null)
if printf '%s' "$_caught" | grep -q '"ask"' && ! printf '%s' "$_caught" | grep -q "byte bound"; then
  echo "PASS  [next call for the session catches up and asks on the rejection itself]"; PASS=$((PASS+1))
else
  echo "FAIL  [next call for the session did not catch up: $(printf '%s' "$_caught" | head -c 200)]"; FAIL=$((FAIL+1)); FAILED_NAMES+=("resumable catch-up")
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
# Dispatch surface (issue #1035)
#
# The surface the ORIGINATING incident actually fired on: the deletion scope
# lived in a worker prompt, which the Bash matcher never sees. Two shapes are
# covered — the `Task` / `Agent` tool's own payload, and a worker prompt nested
# inside a Bash launcher command (`cmux … --command "claude -p '…'"`).
#
# Both directions are pinned against the SAME transcript, because that is the
# only way "it caught the incident" and "it catches everything" separate. A
# firing case proves nothing on its own: a gate wired to ask on every dispatch
# would pass every ask case below and fail every silent one.
# ---------------------------------------------------------------------------

# mk_dispatch_payload <tool_name> <prompt> <transcript> [description]
mk_dispatch_payload() {
  python3 -c '
import json, sys
tool_input = {"subagent_type": "general-purpose", "prompt": sys.argv[2]}
if len(sys.argv) > 4 and sys.argv[4]:
    tool_input["description"] = sys.argv[4]
print(json.dumps({"session_id": "test-session", "tool_name": sys.argv[1],
                  "transcript_path": sys.argv[3], "tool_input": tool_input}))' \
    "$1" "$2" "$3" "${4:-}"
}

# run_dispatch_case <name> <ask|pass> <tool_name> <prompt> <transcript> [description]
run_dispatch_case() {
  local name="$1" expected="$2" tool="$3" prompt="$4" transcript="$5" desc="${6:-}"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  out=$(mk_dispatch_payload "$tool" "$prompt" "$transcript" "$desc" | "$HOOK" 2>"$err_file")
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

# --- ASK: the worker prompt carries the rejected identifier -----------------

run_dispatch_case "Task prompt names the rejected s3 prefix" ask \
  Task 'Run the cleanup: remove every object under s3://acme-archive/raw/2024/ and report the count.' \
  "$REJ_S3"

run_dispatch_case "Agent is the same call under the other host's tool name" ask \
  Agent 'Run the cleanup: remove every object under s3://acme-archive/raw/2024/ and report the count.' \
  "$REJ_S3"

# `description` is read as well as `prompt`: a scope named only in the short
# label would otherwise be invisible.
run_dispatch_case "rejected prefix named only in the description" ask \
  Task 'Follow the plan in the issue body and report when done.' \
  "$REJ_S3" 'purge s3://acme-archive/raw/2024/'

run_dispatch_case "Task prompt re-issues the rejected table as a TRUNCATE" ask \
  Task 'In the warehouse session, run TRUNCATE TABLE prod.events_raw before the reload.' \
  "$REJ_SQL"

run_case "cmux launcher: rejected prefix inside the nested worker prompt" ask \
  'cmux create wk --command "claude -p '"'"'delete everything under s3://acme-archive/raw/2024/ then report'"'"'"' \
  "$REJ_S3"

# --- PASS: the negative controls -------------------------------------------
#
# Same transcript, same rejection, same surface. Only the target differs.

run_dispatch_case "unrelated worker dispatch on a DIFFERENT prefix is silent" pass \
  Task 'Run the cleanup: remove every object under s3://acme-archive/raw/2025/ and report the count.' \
  "$REJ_S3"

run_dispatch_case "ordinary worker dispatch naming no identifier at all is silent" pass \
  Task 'Implement issue #1035 in a new worktree and open the PR when the suite passes.' \
  "$REJ_S3"

run_dispatch_case "worker dispatch naming a different table is silent" pass \
  Task 'In the warehouse session, run TRUNCATE TABLE staging.events_raw before the reload.' \
  "$REJ_SQL"

run_dispatch_case "same prompt, no prior rejection in the transcript" pass \
  Task 'Run the cleanup: remove every object under s3://acme-archive/raw/2024/ and report the count.' \
  "$REJ_S3_NO_REJECTION"

run_dispatch_case "same prompt, rejection joined to a non-AskUserQuestion tool" pass \
  Task 'Run the cleanup: remove every object under s3://acme-archive/raw/2024/ and report the count.' \
  "$REJ_S3_NON_ASK"

run_case "cmux launcher: nested prompt names a different prefix" pass \
  'cmux create wk --command "claude -p '"'"'delete everything under s3://acme-archive/raw/2025/ then report'"'"'"' \
  "$REJ_S3"

# A URI in an ordinary command argument must not be read as a worker prompt —
# the launcher list is matched on argv[0] only.
run_case "non-launcher command mentioning the prefix stays silent" pass \
  'aws s3api head-object --bucket acme-archive --key raw/2024/ s3://acme-archive/raw/2024/' \
  "$REJ_S3"

# --- Launcher normalization (Codex round 1, three P1 bypasses) -------------
#
# `argv[0] == "cmux"` is not how a real dispatch is written. Each shape below
# reached the launcher comparison as a DIFFERENT argv[0] and returned no
# identifiers at all, so the gate skipped the transcript scan entirely and the
# refused target went out unasked. Probed on the pre-fix tree:
#   WS=$(cmux …)            -> argv[0] = 'WS=$(cmux'
#   env CMUX_QUIET=1 cmux … -> argv[0] = 'env'
#   /usr/local/bin/cmux …   -> argv[0] = '/usr/local/bin/cmux'
#   sudo claude …           -> argv[0] = 'sudo'
#   gemini -p …             -> not in the launcher set
# Fixed by iter_command_texts (substitution recursion) + strip_prefix
# (assignments and wrappers) + basename matching + adding `gemini`.

_NESTED="claude -p 'delete everything under s3://acme-archive/raw/2024/'"

run_case "command substitution: WS=\$(cmux …) is still a dispatch" ask \
  "WS=\$(cmux new-workspace --command \"$_NESTED\")" "$REJ_S3"

run_case "backtick substitution is still a dispatch" ask \
  "WS=\`cmux new-workspace --command \"$_NESTED\"\`" "$REJ_S3"

run_case "env wrapper: env VAR=1 cmux …" ask \
  "env CMUX_QUIET=1 cmux create wk --command \"$_NESTED\"" "$REJ_S3"

run_case "bare env assignment: VAR=1 cmux …" ask \
  "CMUX_QUIET=1 cmux create wk --command \"$_NESTED\"" "$REJ_S3"

run_case "absolute path: /usr/local/bin/cmux …" ask \
  "/usr/local/bin/cmux create wk --command \"$_NESTED\"" "$REJ_S3"

run_case "sudo wrapper in front of a direct claude dispatch" ask \
  'sudo claude -p "delete everything under s3://acme-archive/raw/2024/"' "$REJ_S3"

# `gemini` is a routable cmux-delegate provider (ARCHITECTURE.md → Provider
# Routing); omitting it from the launcher set was a hole, not a narrower gate.
run_case "gemini -p is a worker dispatch like claude -p" ask \
  'gemini -p "delete everything under s3://acme-archive/raw/2024/" --approval-mode yolo' \
  "$REJ_S3"

# The recursion must follow only ACTIVE substitutions. Inside single quotes
# `$(` starts no process, so this prints a string and dispatches nothing —
# recursing into it would ask on a command that never runs.
run_case "single-quoted \$( … ) is literal text, not a dispatch" pass \
  "printf '%s' '\$(cmux new-workspace --command \"claude -p delete s3://acme-archive/raw/2024/\")'" \
  "$REJ_S3"

# --- The ask has to say WHICH surface --------------------------------------
#
# "this command targets…" in front of a `Task` call sends the operator hunting
# through argv for a target that lives in a prompt.
_dispatch_reason=$(mk_dispatch_payload Task \
  'Run the cleanup: remove every object under s3://acme-archive/raw/2024/ and report the count.' \
  "$REJ_S3" | "$HOOK" 2>/dev/null)
if echo "$_dispatch_reason" | python3 -c '
import json,sys
r = json.load(sys.stdin)["hookSpecificOutput"]["permissionDecisionReason"]
sys.exit(0 if "worker dispatch" in r and "this command targets" not in r
         and "Delete the remaining ~295M objects" in r else 1)' 2>/dev/null; then
  echo "PASS  [ask-detail] dispatch ask names the dispatch surface and quotes the question"; PASS=$((PASS+1))
else
  echo "FAIL  [ask-detail] dispatch ask does not name the dispatch surface"; FAIL=$((FAIL+1)); FAILED_NAMES+=("dispatch ask subject")
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
