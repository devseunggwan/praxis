#!/bin/bash
# test_composed_command_gate.sh — coverage for hooks/advisory-nudge/composed-command-gate/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   warn   → exit 0 + stderr contains "REMINDER"
#   silent → exit 0 + stderr empty
#   block  → exit 2 + stderr contains "REMINDER" (PRAXIS_COMPOSED_COMMAND_STRICT=1)
#
# The case list is the input-surface enumeration from issue #1117: fence
# delimiter variants, prompt-line variants, and match-side normalization
# variants each carry a case, including the ones that must stay SILENT.
# False-positive rate decides whether this hook survives, so the silent
# fixtures are the load-bearing half — a suite that only proves detection
# cannot be told apart from a hook that fires on everything.
#
# Usage: bash tests/hooks/advisory-nudge/test_composed_command_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/composed-command-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

TEL_FILE=$(mktemp)
export PRAXIS_FIRE_TELEMETRY_FILE="$TEL_FILE"

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" expectation="$2" strict="$3" payload="$4"

  local err_file
  err_file=$(mktemp)
  if [ "$strict" = "strict" ]; then
    echo "$payload" | env PRAXIS_COMPOSED_COMMAND_STRICT=1 python3 "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | env -u PRAXIS_COMPOSED_COMMAND_STRICT python3 "$HOOK" >/dev/null 2>"$err_file"
  fi
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    warn)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "REMINDER" || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$err" ] && echo "        stderr: $err" | head -c 400
  fi
}

# body_payload <body-file> [transcript]
#   Multi-line bodies always go through --body-file: a literal \n inside a
#   quoted --body value splits the body in the shared tokenizer (documented
#   limit of _external_write_body), so an inline variant would pass for the
#   wrong reason.
body_payload() {
  local file="$1" transcript="${2:-}"
  if [ -n "$transcript" ]; then
    printf '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body-file %s"},"transcript_path":"%s"}' "$file" "$transcript"
  else
    printf '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 5 --body-file %s"}}' "$file"
  fi
}

echo "test_composed_command_gate"

# --- Transcript fixture: what actually ran this "session" --------------------

TRANSCRIPT=$(mktemp /tmp/ccg-transcript-XXXXXX)
cat > "$TRANSCRIPT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"Bash","input":{"command":"cd /repo && grep -rn 'safe_tokenize' hooks/ | head -5"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_2","name":"Bash","input":{"command":"pytest tests/hooks -q"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_3","name":"Bash","input":{"command":"gh pr view 1255 --json headRefOid"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_4","name":"Bash","input":{"command":"cd /repo\nrg --files-with-matches iter_command_starts hooks/"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_5","name":"Bash","input":{"command":"env FOO=1 shellcheck --severity=warning tests/hooks"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_6","name":"Bash","input":{"command":"git push --force origin main"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_6","is_error":true,"content":"PreToolUse:Bash hook error: BLOCKED: never force-push"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_7","name":"Read","input":{"file_path":"/repo/hooks/_lib/_transcript.py"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_8","name":"Bash","input":{"command":"true || jq -r .headRefOid /tmp/pr.json"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu_9","name":"Bash","input":{"command":"false && wc -l docs/hook/INDEX.md"}}]}}
EOF

# Bodies land in a per-run directory under sequential names. mktemp inside a
# command substitution re-seeds on the subshell PID, which collides once the
# same template is used many times in one script.
BODY_DIR=$(mktemp -d /tmp/ccg-bodies-XXXXXX) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
BODY_N=0
nextbody() {
  BODY_N=$((BODY_N + 1))
  B="$BODY_DIR/body-$BODY_N.md"
}

# --- AC trio: assembled fires, transcribed is silent, no fence is silent -----

nextbody
cat > "$B" <<'EOF'
### Verification

<details><summary>Evidence 1 — the tokenizer is reached</summary>
<br>

```
$ grep -rn other_symbol hooks/
hooks/_lib/_hook_utils.py:88:def safe_tokenize(command)
```

</details>
EOF
run_case "MOTIVATING: assembled \$ line, real output (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn safe_tokenize hooks/ | head -5
hooks/_lib/_hook_utils.py:88:def safe_tokenize(command)
```
EOF
run_case "transcribed \$ line (silent)" "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
The regression is fixed and CI is green. Nothing else to flag here.
EOF
run_case "no fenced block at all (silent)" "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# --- Silent fixtures: honest bodies that MUST stay quiet ---------------------
# Positive control for these: the "assembled" case above shares the fence
# shape and fires, so silence here is the body's doing, not a dead scanner.

nextbody
cat > "$B" <<'EOF'
### Verification — `abc1234` (rev 1)

| # | Claim | Result |
| --- | --- | --- |
| 1 | the tokenizer is reachable | PASS(live) |

<details><summary>Evidence 1 — the tokenizer is reachable</summary>
<br>

grep is the oracle here because the call site is static.

```bash
$ cd /repo && grep -rn 'safe_tokenize' hooks/ | head -5
hooks/_lib/_hook_utils.py:88:def safe_tokenize(command)
```

</details>

<details><summary>Evidence 2 — the suite passes</summary>
<br>

```
$ pytest tests/hooks -q
14 passed
```

</details>
EOF
run_case "SILENT FIXTURE 1: full anchor, both lines transcribed (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
Reran with the literal moved into an env var, per the redaction rule.

```
$ gh api repos/$OWNER/$REPO/pulls/5 --jq .head.sha
abc1234
```

And with a placeholder for the customer id:

```
$ psql -c "select * from t where id = <CUSTOMER_ID>"
(1 row)
```
EOF
run_case "SILENT FIXTURE 2: env-var + placeholder substitution (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
The stack trace from the failing run:

```
Traceback (most recent call last):
  File "impl.py", line 42, in main
    raise ValueError($x)
ValueError: bad input
```

And the config we ship:

```yaml
timeout: 5
matcher: Bash
```
EOF
run_case "SILENT FIXTURE 3: output-only + config blocks, no prompts (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# --- Fence delimiter variants -----------------------------------------------

nextbody
cat > "$B" <<'EOF'
~~~
$ grep -rn unrelated_symbol hooks/
~~~
EOF
run_case "tilde fence is scanned (warn)" "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
````
```
$ grep -rn unrelated_symbol hooks/
```
````
EOF
run_case "nested fence: inner is content of outer (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn unrelated_symbol hooks/
EOF
run_case "unclosed fence is not scanned (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
Ran `$ grep -rn unrelated_symbol hooks/` in the worktree.
EOF
run_case "prompt line outside any fence (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# --- Prompt-line variants ----------------------------------------------------

nextbody
cat > "$B" <<'EOF'
```
   $ grep -rn unrelated_symbol hooks/
```
EOF
run_case "leading whitespace before prompt (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn unrelated_symbol \
    hooks/ tests/
```
EOF
run_case "backslash continuation joins into one command (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$HOME/.local/bin/praxis
$(date +%s)
$
```
EOF
run_case "no space after \$ — not a prompt (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
❯ grep -rn unrelated_symbol hooks/
% grep -rn unrelated_symbol hooks/
```
EOF
run_case "KNOWN LIMIT: non-\$ prompt glyphs not detected (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# --- Match-side normalization ------------------------------------------------

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn "safe_tokenize" hooks/
```
EOF
run_case "quote style + dropped cd prefix still match (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ FOO=1 grep -rn safe_tokenize hooks/ | wc -l
```
EOF
run_case "env prefix + different tail pipe still match (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ rg safe_tokenize hooks/
```
EOF
run_case "different binary, same operands (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# --- Tier 1: non-shell pseudo-command ----------------------------------------

nextbody
cat > "$B" <<'EOF'
```
$ safe_tokenize('gh pr comment 5 --body x')
['gh', 'pr', 'comment', '5', '--body', 'x']
```
EOF
run_case "non-shell pseudo-command (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ safe_tokenize('gh pr comment 5')
['gh', 'pr', 'comment', '5']
```
EOF
run_case "non-shell tier fires without any transcript (warn)" \
  "warn" "advisory" "$(body_payload "$B")"

# --- Opt-out markers ---------------------------------------------------------

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn unrelated_symbol hooks/   [transcribed]
```
EOF
run_case "[transcribed] on the prompt line (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
``` [transcribed]
$ grep -rn unrelated_symbol hooks/
$ rg other hooks/
```
EOF
run_case "[transcribed] on the fence opener clears the block (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# --- Transcript availability -------------------------------------------------

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn unrelated_symbol hooks/
```
EOF
run_case "no transcript_path — unmatched tier stays silent (silent)" \
  "silent" "advisory" "$(body_payload "$B")"

run_case "unreadable transcript path — silent (silent)" \
  "silent" "advisory" "$(body_payload "$B" "/tmp/does-not-exist-1117.jsonl")"

# A path that exists but cannot be read as a file (a directory) is "no
# oracle", not "ran nothing": strict tail_lines raises, the gate stays silent.
run_case "transcript path is a directory — silent (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$(dirname "$TRANSCRIPT")")"

# --- Surface variants --------------------------------------------------------

MCP_PAYLOAD=$(TRANSCRIPT="$TRANSCRIPT" python3 -c '
import json, os
body = "```\n$ grep -rn unrelated_symbol hooks/\nno match\n```"
print(json.dumps({
    "tool_name": "mcp__laplace-slack__slack_send_message",
    "tool_input": {"text": body},
    "transcript_path": os.environ["TRANSCRIPT"],
}))')
run_case "MCP Slack body with assembled line (warn)" \
  "warn" "advisory" "$MCP_PAYLOAD"

run_case "non-write tool (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x.md"}}'

run_case "gh pr view is not a write (silent)" \
  "silent" "advisory" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr view 5 --json body"}}'

run_case "malformed stdin JSON (silent fail-open)" \
  "silent" "advisory" 'this is not json'

# --- Strict mode -------------------------------------------------------------

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn unrelated_symbol hooks/
```
EOF
run_case "strict mode converts warn to block (rc 2)" \
  "block" "strict" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn safe_tokenize hooks/
```
EOF
run_case "strict mode still silent when transcribed" \
  "silent" "strict" "$(body_payload "$B" "$TRANSCRIPT")"

# --- Codex round 1 regressions (#1117 review) --------------------------------

nextbody
cat > "$B" <<'EOF'
```
$ rg --files-with-matches iter_command_starts hooks/
hooks/advisory-nudge/composed-command-gate/impl.py
```
EOF
run_case "REGRESSION P2: newline-separated cd in transcript still matches (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ shellcheck --severity=warning tests/hooks
```
EOF
run_case "REGRESSION P2: env-wrapper transcript command still matches (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ git push --force origin main
Everything up-to-date
```
EOF
run_case "REGRESSION P1: hook-blocked call is not provenance (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# An unreadable-but-present transcript is "no oracle", not "nothing ran" —
# tail_lines returns [] for both, and conflating them fires on every line.
UNREADABLE="$BODY_DIR/unreadable.jsonl"
: > "$UNREADABLE"
chmod 000 "$UNREADABLE"
nextbody
cat > "$B" <<'EOF'
```
$ grep -rn unrelated_symbol hooks/
```
EOF
if [ -r "$UNREADABLE" ]; then
  echo "  SKIP  REGRESSION P3 (running as a user that can read mode-000 files)"
else
  run_case "REGRESSION P3: unreadable transcript fails open (silent)" \
    "silent" "advisory" "$(body_payload "$B" "$UNREADABLE")"
fi
chmod 644 "$UNREADABLE"

# --- CodeRabbit round regressions (#1261) ------------------------------------
# A short-circuited branch never ran, so it is not provenance. Both shapes
# below sit in the transcript fixture above and must NOT clear a published line.

nextbody
cat > "$B" <<'EOF'
```
$ jq -r .headRefOid /tmp/pr.json
8521075ac05806400733b0dff3d1d4b19f7b2006
```
EOF
run_case "REGRESSION CR: 'true || cmd' right side is not provenance (warn)" \
  "warn" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

nextbody
cat > "$B" <<'EOF'
```
$ wc -l docs/hook/INDEX.md
     212 docs/hook/INDEX.md
```
EOF
# `&&` deliberately stays provenance — this case pins the accepted limit.
# `cd /repo && grep ...` and `git fetch && git rebase ...` are the shapes the
# segmenting was added for, and both sides really do run in them. Treating the
# right side as unexecuted would bring back the P2 false positives, which is
# the failure mode that decides whether this hook survives.
run_case "ACCEPTED LIMIT: 'false && cmd' right side still clears (silent)" \
  "silent" "advisory" "$(body_payload "$B" "$TRANSCRIPT")"

# Only an ODD backslash run continues a Bash line. `foo \\` + newline is a
# literal backslash then a REAL separator; collapsing it welded the two
# commands together and hid the `gh` write from the command-start walk, so the
# body was never scanned at all.
nextbody
cat > "$B" <<'EOF'
```
$ grep -rn unrelated_symbol hooks/
```
EOF
run_case "REGRESSION CR: even backslash run keeps the gh write visible (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo hi \\\\\\\\\\ngh pr comment 5 --body-file $B\"},\"transcript_path\":\"$TRANSCRIPT\"}"

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn safe_tokenize hooks/ | head -5 \\
$ never-ran-command --flag operand
```
EOF
run_case "REGRESSION CR: even backslash in a prompt line does not swallow the next command (warn)" \
  "warn" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $B\"},\"transcript_path\":\"$TRANSCRIPT\"}"

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn safe_tokenize hooks/ \
    | head -5
```
EOF
run_case "SILENT FIXTURE: odd backslash still joins one transcribed command (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr comment 5 --body-file $B\"},\"transcript_path\":\"$TRANSCRIPT\"}"

# --- gh api surface (#1265) --------------------------------------------------
# An anchor revision cannot go out as `gh pr comment`: rev >=2 is a PATCH
# against a comment id. So until _external_write_body learned `gh api`, the one
# path this hook exists to watch was the one it could not see.

api_patch_payload() {
  printf '{"tool_name":"Bash","tool_input":{"command":"gh api --method PATCH /repos/o/r/issues/comments/999 -F body=@%s"},"transcript_path":"%s"}' "$1" "$TRANSCRIPT"
}

nextbody
cat > "$B" <<'EOF'
### Verification

<details><summary>Evidence 1 — the tokenizer is reached</summary>
<br>

```
$ grep -rn other_symbol hooks/
hooks/_lib/_hook_utils.py:88:def safe_tokenize(command)
```

</details>
EOF
run_case "REGRESSION #1265: assembled \$ line posted via gh api PATCH (warn)" \
  "warn" "advisory" "$(api_patch_payload "$B")"

nextbody
cat > "$B" <<'EOF'
```
$ grep -rn safe_tokenize hooks/ | head -5
hooks/_lib/_hook_utils.py:88:def safe_tokenize(command)
```
EOF
run_case "SILENT FIXTURE: transcribed \$ line posted via gh api PATCH (silent)" \
  "silent" "advisory" "$(api_patch_payload "$B")"

# Negative control for the method gate: identical body, identical endpoint,
# identical `body=` field — only the method differs. A detector widened to
# every `gh api` call would fire here, and the warn case above cannot tell
# the two apart on its own. `--method GET` has to be stated: gh switches an
# otherwise method-less call to POST as soon as a field is added, so dropping
# the flag makes this a write rather than a read.
nextbody
cat > "$B" <<'EOF'
```
$ grep -rn other_symbol hooks/
hooks/_lib/_hook_utils.py:88:def safe_tokenize(command)
```
EOF
run_case "SILENT FIXTURE: same body on a gh api GET is not a write (silent)" \
  "silent" "advisory" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh api --method GET /repos/o/r/issues/comments/999 -F body=@$B\"},\"transcript_path\":\"$TRANSCRIPT\"}"

# --- Summary -----------------------------------------------------------------

rm -rf "$TRANSCRIPT" "$TEL_FILE" "$BODY_DIR"

echo ""
echo "passed: $PASS, failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
