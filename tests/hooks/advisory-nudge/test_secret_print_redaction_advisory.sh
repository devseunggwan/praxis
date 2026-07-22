#!/bin/bash
# test_secret_print_redaction_advisory.sh — coverage for the secret-print
# redaction advisory (issue #827).
#
# Synthesizes Claude Code PreToolUse payloads (Bash / Write / Edit) and asserts:
#   advisory → exit 0 + stderr contains the advisory marker
#   silent   → exit 0 + stderr empty
#
# All fixture token values are obvious fakes (FAKE_TOKEN_xxxx) — no live keys.
#
# Usage: bash tests/hooks/advisory-nudge/test_secret_print_redaction_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/secret-print-redaction-advisory/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case <name> <expected> <tool_name> <field-json>
# field-json is the tool_input object as a JSON string.
run_case() {
  local name="$1" expected="$2" tool_name="$3" tool_input="$4"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": json.loads(sys.argv[2]),
}))' "$tool_name" "$tool_input")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "\[secret-print-redaction-advisory\]" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# Helper: wrap a bash command string as a Bash tool_input JSON object.
bash_input() {
  python3 -c 'import json, sys; print(json.dumps({"command": sys.argv[1]}))' "$1"
}

# Helper: wrap path+content as a Write tool_input JSON object.
write_input() {
  python3 -c 'import json, sys; print(json.dumps({"file_path": sys.argv[1], "content": sys.argv[2]}))' "$1" "$2"
}

# === ADVISORY ==============================================================

# P1 — incident replica: Bash heredoc authoring a script that fetches a
# token and prints it per-item ([SKIP] <id>: <token>).
P1_CMD=$(printf 'cat > /tmp/verify.sh <<'"'"'EOF'"'"'\nfor id in 1 2 3; do\n  TOKEN=$(hubctl token fetch fake-provider --phase dev)\n  echo "[SKIP] $id: $TOKEN"\ndone\nEOF')
run_case "P1: Bash heredoc authoring capture+echo (incident replica)" \
  advisory Bash "$(bash_input "$P1_CMD")"

# P2 — Write .sh with the same capture+echo flow.
P2_CONTENT=$(printf '#!/bin/bash\nTOKEN=$(hubctl token fetch fake-provider --phase dev)\necho "[SKIP] item-1: $TOKEN"\n')
run_case "P2: Write .sh capture+echo" \
  advisory Write "$(write_input "/tmp/verify.sh" "$P2_CONTENT")"

# P3 — Write .py: subprocess list-form fetch assigned, then print(f-string).
P3_CONTENT=$(printf 'import subprocess\ntoken = subprocess.check_output(["hubctl", "token", "fetch", "fake-provider"], text=True)\nfor i in range(3):\n    print(f"[SKIP] {i}: {token}")\n')
run_case "P3: Write .py subprocess fetch + print(f-string)" \
  advisory Write "$(write_input "/tmp/verify.py" "$P3_CONTENT")"

# P4 — direct live Bash: capture then unmasked echo.
run_case "P4: live Bash aws secretsmanager capture + echo" \
  advisory Bash "$(bash_input 'TOKEN=$(aws secretsmanager get-secret-value --secret-id fake --query SecretString --output text); echo "$TOKEN"')"

# P5 — inline no-capture substitution inside an output command.
run_case "P5: live inline echo \$(hubctl token fetch)" \
  advisory Bash "$(bash_input 'echo "tok: $(hubctl token fetch fake-provider)"')"

# P6 — echo-append authoring: capture appended by one echo, sink by the next.
P6_CMD=$(printf "echo 'TOKEN=\$(hubctl token fetch fake-provider --phase dev)' >> verify.sh\necho 'echo \"[SKIP] \$id: \$TOKEN\"' >> verify.sh")
run_case "P6: echo-append authored capture+echo" \
  advisory Bash "$(bash_input "$P6_CMD")"

# P7 — other fetch CLI variants (infisical / vault / op).
run_case "P7a: infisical secrets get capture + echo" \
  advisory Bash "$(bash_input 'SECRET=$(infisical secrets get FAKE_KEY --plain); echo "$SECRET"')"

run_case "P7b: vault kv get capture + echo" \
  advisory Bash "$(bash_input 'SECRET=$(vault kv get -field=token secret/fake); echo "value: $SECRET"')"

run_case "P7c: op read capture + printf" \
  advisory Bash "$(bash_input 'SECRET=$(op read op://vault/fake/token); printf "%s\n" "$SECRET"')"

# P8 — python logger sink.
P8_CONTENT=$(printf 'token = client.get_secret_value(SecretId="fake-id")\nlogger.info("token=%%s", token)\n')
run_case "P8: Write .py get_secret_value + logger.info" \
  advisory Write "$(write_input "/tmp/verify.py" "$P8_CONTENT")"

# P9 — authored bare no-capture fetch piped to jq passthrough.
P9_CONTENT=$(printf '#!/bin/bash\naws secretsmanager get-secret-value --secret-id fake | jq -r .SecretString\n')
run_case "P9: Write .sh bare fetch | jq -r .SecretString" \
  advisory Write "$(write_input "/tmp/verify.sh" "$P9_CONTENT")"

# Edit new_string is scanned like Write content.
run_case "Edit new_string capture+echo" \
  advisory Edit \
  "$(python3 -c 'import json; print(json.dumps({"file_path": "/tmp/verify.sh", "old_string": "placeholder", "new_string": "TOKEN=$(hubctl token fetch fake-provider)\necho \"$TOKEN\""}))')"

# gh auth token / kubectl get secret variants.
run_case "gh auth token capture + echo" \
  advisory Bash "$(bash_input 'T=$(gh auth token); echo "$T"')"

P_KUBE_CONTENT=$(printf '#!/bin/bash\nkubectl get secret fake-secret -o jsonpath="{.data.token}"\n')
run_case "authored bare kubectl get secret" \
  advisory Write "$(write_input "/tmp/verify.sh" "$P_KUBE_CONTENT")"

# === SILENT ================================================================

# N1 — fetched token used only as a curl auth header (legitimate usage).
run_case "N1: fetch then curl -H Authorization only" \
  silent Bash "$(bash_input 'TOKEN=$(hubctl token fetch fake-provider); curl -s -H "Authorization: Bearer $TOKEN" https://api.example.com/v1/ping')"

# N2 — masked echo (first6 + last4 substring expansion).
run_case "N2: masked echo \${TOKEN:0:6}****\${TOKEN: -4}" \
  silent Bash "$(bash_input 'TOKEN=$(hubctl token fetch fake-provider); echo "${TOKEN:0:6}****${TOKEN: -4}"')"

# N3 — literal placeholder assignment (no fetch anchor) + echo.
run_case "N3: placeholder assignment without fetch + echo" \
  silent Bash "$(bash_input 'TOKEN="FAKE_TOKEN_xxxx"; echo "$TOKEN"')"

# N4 — normal script with no fetch at all.
N4_CONTENT=$(printf '#!/bin/bash\necho "hello"\nls -la /tmp\n')
run_case "N4: normal script, no fetch" \
  silent Write "$(write_input "/tmp/run.sh" "$N4_CONTENT")"

# N5 — Write spec.md containing fetch+echo example text (file-type gate).
N5_CONTENT=$(printf '# Example\n\nTOKEN=$(hubctl token fetch fake-provider)\necho "$TOKEN"\n')
run_case "N5: Write spec.md with fetch+echo example text" \
  silent Write "$(write_input "/tmp/spec.md" "$N5_CONTENT")"

# N6 — digest sink: only a byte count reaches the transcript.
run_case "N6: echo \"\$TOKEN\" | wc -c digest sink" \
  silent Bash "$(bash_input 'TOKEN=$(hubctl token fetch fake-provider); echo "$TOKEN" | wc -c')"

run_case "N6b: sha256sum digest sink" \
  silent Bash "$(bash_input 'TOKEN=$(hubctl token fetch fake-provider); echo "$TOKEN" | sha256sum')"

# N7 — live bare interactive fetch (sanctioned read-only usage).
run_case "N7: live bare hubctl token fetch" \
  silent Bash "$(bash_input 'hubctl token fetch fake-provider --phase dev')"

# python masked slice sink.
NPY_CONTENT=$(printf 'token = client.get_secret_value(SecretId="fake-id")\nprint(f"{token[:6]}...{token[-4:]}")\n')
run_case "python slice-masked print" \
  silent Write "$(write_input "/tmp/verify.py" "$NPY_CONTENT")"

# heredoc redirected into a .md file (file-type gate on redirect target).
NMD_CMD=$(printf 'cat > /tmp/notes.md <<'"'"'EOF'"'"'\nTOKEN=$(hubctl token fetch fake-provider)\necho "$TOKEN"\nEOF')
run_case "heredoc body targeting .md is not scanned" \
  silent Bash "$(bash_input "$NMD_CMD")"

# aws ssm get-parameter WITHOUT --with-decryption is not a plaintext fetch.
run_case "aws ssm get-parameter without --with-decryption" \
  silent Bash "$(bash_input 'P=$(aws ssm get-parameter --name fake); echo "$P"')"

# === Fail-open infrastructure (N8 trio) ====================================

run_case_raw_payload() {
  local name="$1" expected="$2" payload="$3"

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

run_case_raw_payload "N8a: malformed JSON fails open silently" silent \
  'not valid json {{{'

run_case_raw_payload "N8b: non-matching tool passes silently" silent \
  '{"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}}'

run_case_raw_payload "N8c: empty Bash command silent" silent \
  '{"tool_name": "Bash", "tool_input": {"command": ""}}'

run_case_raw_payload "N8d: whitespace-only Bash command silent" silent \
  '{"tool_name": "Bash", "tool_input": {"command": "   "}}'

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------

_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
