#!/bin/bash
# tests/hooks/preflight-gate/test_cross_tool_reroute_gate.sh
#
# Coverage for hooks/preflight-gate/cross-tool-reroute-gate/impl.py
# (issue #1485).
#
# Two outcomes:
#   ask  — stdout contains permissionDecision "ask", exit 0
#   pass — exit 0, stdout empty, stderr empty
#
# The silent controls carry the design: a same-tool retry, a different target,
# a user refusal instead of a hook block, and a pair already approved once.
#
# Usage: bash tests/hooks/preflight-gate/test_cross_tool_reroute_gate.sh
# Exit:  0 = all pass, 1 = at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/preflight-gate/cross-tool-reroute-gate/impl.py"

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
# mk_transcript <outfile> <steps-json>
#   steps: [{"name": tool, "input": {...}, "result": "block"|"ok"|"reject"}]
# Record shapes follow a live transcript: a hook block is a role:user record
# with `toolDenialKind: "permission-rule"` and an `is_error` tool_result whose
# content is the blocking hook's prose; a user refusal carries
# `toolDenialKind: "user-rejected"` and the runtime's fixed sentence.
# ---------------------------------------------------------------------------

mk_transcript() {
  python3 - "$1" "$2" <<'PY'
import json, sys
out, steps = sys.argv[1], json.loads(sys.argv[2])
refusal = ("The user doesn't want to proceed with this tool use. The tool use "
           "was rejected (eg. if it was a file edit, the new_string was NOT "
           "written to the file). STOP what you are doing and wait for the "
           "user to tell you how to proceed.")
events = []
for i, step in enumerate(steps):
    use_id, asst = f"toolu_{i:03d}", f"asst-{i}"
    events.append({"type": "assistant", "uuid": asst, "message": {
        "role": "assistant", "content": [
            {"type": "tool_use", "id": use_id, "name": step["name"],
             "input": step["input"]}]}})
    result = {"type": "tool_result", "tool_use_id": use_id}
    record = {"type": "user", "uuid": f"res-{i}", "sourceToolAssistantUUID": asst,
              "message": {"role": "user", "content": [result]}}
    if step["result"] == "block":
        result.update(is_error=True, content=(
            "PreToolUse:" + step["name"] + " hook error: "
            "[bash -c 'x=\"$(y)\"; [ -z \"$x\" ] && exit 0']: "
            "GATE blocked: describe the table first"))
        record["toolDenialKind"] = "permission-rule"
    elif step["result"] == "reject":
        result.update(is_error=True, content=refusal)
        record["toolDenialKind"] = "user-rejected"
        record["toolUseResult"] = "User rejected tool use"
    else:
        result["content"] = "ok"
    events.append(record)
with open(out, "w", encoding="utf-8") as fh:
    for ev in events:
        fh.write(json.dumps(ev) + "\n")
PY
}

Q_A="mcp__db-a__query"
Q_B="mcp__db-b__query"
SQL_X="SELECT count(*) FROM cat.sch.tbl_x"
FILE="/repo/templates/tbl_x.sql"

step() {  # step <tool> <input-json> <result>
  printf '{"name":"%s","input":%s,"result":"%s"}' "$1" "$2" "$3"
}
sql_input() { python3 -c 'import json,sys; print(json.dumps({"sql": sys.argv[1]}))' "$1"; }
bash_input() { python3 -c 'import json,sys; print(json.dumps({"command": sys.argv[1]}))' "$1"; }

BLOCK_A=$(step "$Q_A" "$(sql_input "$SQL_X")" block)
DESCRIBE_A_OK=$(step "$Q_A" "$(sql_input "DESCRIBE cat.sch.tbl_x")" ok)
B_RAN=$(step "$Q_B" "$(sql_input "$SQL_X")" ok)
B_REJECTED=$(step "$Q_B" "$(sql_input "$SQL_X")" reject)
USER_REJECT_A=$(step "$Q_A" "$(sql_input "$SQL_X")" reject)
BLOCK_EDIT=$(step Edit "{\"file_path\":\"$FILE\",\"old_string\":\"a\",\"new_string\":\"b\"}" block)
BLOCK_BASH_SQL=$(step Bash "$(bash_input "psql -c \"$SQL_X\"")" block)
BLOCK_BASH_PATH=$(step Bash "$(bash_input "ls $FILE*.bak")" block)
BLOCK_WRITE=$(step Write "{\"file_path\":\"$FILE\",\"content\":\"x\"}" block)

T_BLOCK="$TMP/block.jsonl";              mk_transcript "$T_BLOCK" "[$BLOCK_A]"
T_BLOCK_DESCRIBE_FAIL="$TMP/describe.jsonl"; mk_transcript "$T_BLOCK_DESCRIBE_FAIL" "[$BLOCK_A,$DESCRIBE_A_OK]"
T_LIFTED="$TMP/lifted.jsonl";            mk_transcript "$T_LIFTED" "[$BLOCK_A,$B_RAN]"
SQL_XY="SELECT 1 FROM cat.sch.tbl_x JOIN cat.sch.tbl_y ON true"
BLOCK_A_XY=$(step "$Q_A" "$(sql_input "$SQL_XY")" block)
T_LIFTED_X_OF_XY="$TMP/lifted-x-of-xy.jsonl"; mk_transcript "$T_LIFTED_X_OF_XY" "[$BLOCK_A_XY,$B_RAN]"
T_ASK_REJECTED="$TMP/ask-rejected.jsonl"; mk_transcript "$T_ASK_REJECTED" "[$BLOCK_A,$B_REJECTED]"
T_USER_REJECT="$TMP/user-reject.jsonl";  mk_transcript "$T_USER_REJECT" "[$USER_REJECT_A]"
T_EMPTY="$TMP/none.jsonl";               mk_transcript "$T_EMPTY" "[]"
T_EDIT="$TMP/edit.jsonl";                mk_transcript "$T_EDIT" "[$BLOCK_EDIT]"
T_BASH_SQL="$TMP/bash-sql.jsonl";        mk_transcript "$T_BASH_SQL" "[$BLOCK_BASH_SQL]"
T_BASH_PATH="$TMP/bash-path.jsonl";      mk_transcript "$T_BASH_PATH" "[$BLOCK_BASH_PATH]"
T_WRITE="$TMP/write.jsonl";              mk_transcript "$T_WRITE" "[$BLOCK_WRITE]"

# A resumed transcript repeating one tool_use id: the block names its source
# record (tbl_x), and a later record reuses the id for another table.
T_DUP_ID="$TMP/dup-id.jsonl"
python3 - "$T_DUP_ID" "$Q_A" <<'PY'
import json, sys
out, tool = sys.argv[1], sys.argv[2]
def use(uuid, sql):
    return {"type": "assistant", "uuid": uuid, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_dup", "name": tool, "input": {"sql": sql}}]}}
events = [
    use("asst-x", "SELECT 1 FROM cat.sch.tbl_x"),
    use("asst-z", "SELECT 1 FROM cat.sch.tbl_z"),
    {"type": "user", "uuid": "res-x", "sourceToolAssistantUUID": "asst-x",
     "toolDenialKind": "permission-rule", "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "toolu_dup", "is_error": True,
          "content": "GATE blocked: describe the table first"}]}},
]
with open(out, "w", encoding="utf-8") as fh:
    fh.write("\n".join(json.dumps(e) for e in events) + "\n")
PY

mk_payload() {  # mk_payload <tool> <input-json> <transcript>
  python3 -c '
import json, sys
print(json.dumps({"session_id": "test-session", "tool_name": sys.argv[1],
                  "transcript_path": sys.argv[3],
                  "tool_input": json.loads(sys.argv[2])}))' "$1" "$2" "$3"
}

run_case() {  # run_case <name> <ask|pass> <tool> <input-json> <transcript>
  local name="$1" expected="$2" tool="$3" input="$4" transcript="$5"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  # The resumable scan caches its cursor and folded state under PRAXIS_HOME;
  # a fresh home per case keeps one case's scan out of the next.
  out=$(mk_payload "$tool" "$input" "$transcript" | PRAXIS_HOME="$TMP/home-$PASS-$FAIL" "$HOOK" 2>"$err_file")
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
# ASK — the blocked target is reached through a different tool
# ---------------------------------------------------------------------------

run_case "blocked on query tool A, same table through query tool B" ask \
  "$Q_B" "$(sql_input "$SQL_X")" "$T_BLOCK"

run_case "the describe the gate asked for ran and failed on A; B still asks" ask \
  "$Q_B" "$(sql_input "$SQL_X")" "$T_BLOCK_DESCRIBE_FAIL"

run_case "table identifier case and quoting normalize equal" ask \
  "$Q_B" "$(sql_input 'select 1 from "CAT"."SCH"."TBL_X"')" "$T_BLOCK"

run_case "a modifier between the keyword and the table (FROM ONLY)" ask \
  "$Q_B" "$(sql_input "SELECT 1 FROM ONLY cat.sch.tbl_x")" "$T_BLOCK"

run_case "a modifier between the keyword and the table (DESCRIBE TABLE)" ask \
  "$Q_B" "$(sql_input "DESCRIBE TABLE cat.sch.tbl_x")" "$T_BLOCK"

run_case "blocked query tool, same table through a Bash CLI" ask \
  Bash "$(bash_input "trino --execute \"$SQL_X\"")" "$T_BLOCK"

run_case "blocked Bash CLI query, same table through an MCP tool" ask \
  "$Q_A" "$(sql_input "$SQL_X")" "$T_BASH_SQL"

run_case "blocked Edit, same file rewritten through Bash" ask \
  Bash "$(bash_input "sed -i '' 's/a/b/' $FILE")" "$T_EDIT"

run_case "the operator rejected the earlier ask; the pair stays armed" ask \
  "$Q_B" "$(sql_input "$SQL_X")" "$T_ASK_REJECTED"

run_case "block named two tables; running one through B leaves the other armed" ask \
  "$Q_B" "$(sql_input "SELECT 1 FROM cat.sch.tbl_y")" "$T_LIFTED_X_OF_XY"

run_case "a repeated tool_use id resolves to the record the denial names" ask \
  "$Q_B" "$(sql_input "SELECT 1 FROM cat.sch.tbl_x")" "$T_DUP_ID"

run_case "blocked query tool, SQL client behind a wrapper and env assignment" ask \
  Bash "$(bash_input "HOST=h timeout 60 trino --server \"\$HOST\" --execute \"$SQL_X\"")" "$T_BLOCK"

run_case "blocked Edit, an inline script opens the file for writing" ask \
  Bash "$(bash_input "python3 -c 'open(\"$FILE\", \"w\").write(\"x\")'")" "$T_EDIT"

run_case "blocked Edit, an inline script opens the file read-write (r+)" ask \
  Bash "$(bash_input "python3 -c 'open(\"$FILE\", \"r+\").write(\"x\")'")" "$T_EDIT"

run_case "blocked Edit, an inline script writes the file through Path.write_text" ask \
  Bash "$(bash_input "python3 -c 'from pathlib import Path; Path(\"$FILE\").write_text(\"x\")'")" "$T_EDIT"

run_case "blocked Write, the same file created through touch" ask \
  Bash "$(bash_input "touch $FILE && ls -la $FILE")" "$T_WRITE"

run_case "blocked Edit, the file overwritten as the destination of cp" ask \
  Bash "$(bash_input "cp /tmp/new.sql $FILE && echo ok")" "$T_EDIT"

# ---------------------------------------------------------------------------
# PASS — silent controls
# ---------------------------------------------------------------------------

run_case "same tool retry on the blocked target" pass \
  "$Q_A" "$(sql_input "$SQL_X")" "$T_BLOCK"

run_case "different tool, different table" pass \
  "$Q_B" "$(sql_input "SELECT 1 FROM cat.sch.tbl_y")" "$T_BLOCK"

run_case "unqualified name does not match the qualified block" pass \
  "$Q_B" "$(sql_input "SELECT 1 FROM tbl_x")" "$T_BLOCK"

run_case "no block in the transcript" pass \
  "$Q_B" "$(sql_input "$SQL_X")" "$T_EMPTY"

run_case "a user refusal is not a hook block" pass \
  "$Q_B" "$(sql_input "$SQL_X")" "$T_USER_REJECT"

run_case "pair already approved once (the B call ran)" pass \
  "$Q_B" "$(sql_input "$SQL_X")" "$T_LIFTED"

run_case "block named two tables; the one B already ran on stays lifted" pass \
  "$Q_B" "$(sql_input "$SQL_X")" "$T_LIFTED_X_OF_XY"

run_case "a Bash block contributes no path targets" pass \
  Write "{\"file_path\":\"$FILE\",\"content\":\"x\"}" "$T_BASH_PATH"

run_case "blocked Edit, a different file sharing a prefix" pass \
  Write "{\"file_path\":\"/repo/templates/tbl_x_test.sql\",\"content\":\"x\"}" "$T_EDIT"

run_case "Edit after a blocked Write on the same file (one tool family)" pass \
  Edit "{\"file_path\":\"$FILE\",\"old_string\":\"a\",\"new_string\":\"b\"}" "$T_WRITE"

run_case "blocked Edit, Bash only reads the file" pass \
  Bash "$(bash_input "wc -c $FILE && grep -n x $FILE")" "$T_EDIT"

run_case "blocked Edit, cp and ln only read the file as their source" pass \
  Bash "$(bash_input "cp $FILE /tmp/tbl_x.sql.bak && ln -s $FILE /tmp/link")" "$T_EDIT"

run_case "blocked query tool, a PR body names the client and the table" pass \
  Bash "$(bash_input "gh pr comment 1 --body \"- [ ] trino-plugin
\$ trino (phase=prod)
$SQL_X\"")" "$T_BLOCK"

run_case "blocked query tool, a markdown table cell names the client" pass \
  Bash "$(bash_input "gh pr comment 1 --body \"| 4 | trino matcher | $SQL_X |\"")" "$T_BLOCK"

run_case "blocked Edit, an inline script reads the file with open()" pass \
  Bash "$(bash_input "python3 -c 'print(open(\"$FILE\").read())'")" "$T_EDIT"

run_case "blocked Edit, an inline script reads the file with Path.read_text" pass \
  Bash "$(bash_input "python3 -c 'from pathlib import Path; print(Path(\"$FILE\").read_text())'")" "$T_EDIT"

run_case "blocked Edit, prose arrow before the path is not a redirect" pass \
  Bash "$(bash_input "gh pr comment 1 --body \"link -> $FILE\"")" "$T_EDIT"

run_case "blocked query tool, a Python import line is not a query" pass \
  Bash "$(bash_input "python3 -c 'from cat.sch.tbl_x import y'")" "$T_BLOCK"

run_case "blocked query tool, the table only inside a string literal" pass \
  "$Q_B" "$(sql_input "SELECT 'from cat.sch.tbl_x'")" "$T_BLOCK"

run_case "blocked query tool, the table only inside SQL comments" pass \
  "$Q_B" "$(sql_input "SELECT 1 -- FROM cat.sch.tbl_x
/* JOIN cat.sch.tbl_x */")" "$T_BLOCK"

run_case "pending call names no table and no path" pass \
  "$Q_B" "$(sql_input "SELECT 1")" "$T_BLOCK"

run_case "missing transcript fails open" pass \
  "$Q_B" "$(sql_input "$SQL_X")" "$TMP/does-not-exist.jsonl"

# ---------------------------------------------------------------------------
# The ask quotes the original block
# ---------------------------------------------------------------------------

out=$(mk_payload "$Q_B" "$(sql_input "$SQL_X")" "$T_BLOCK" | PRAXIS_HOME="$TMP/home-quote" "$HOOK" 2>/dev/null)
if echo "$out" | grep -q "Original block: GATE blocked: describe the table first" && echo "$out" | grep -q "cat.sch.tbl_x"; then
  echo "PASS  [ask] reason quotes the original block without the runtime prefix"; PASS=$((PASS+1))
else
  echo "FAIL  [ask] reason quotes the original block without the runtime prefix"; FAIL=$((FAIL+1))
  FAILED_NAMES+=("reason quotes the original block")
fi

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
