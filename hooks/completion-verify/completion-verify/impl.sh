#!/bin/bash
# Stop hook: block assistant completion claims without same-turn verification evidence.
# Contract: reads JSON from stdin, emits {"decision":"block"} or exit 0 pass.
#
# Strict same-turn enforcement (issue #138, PR #144):
#   When CLAIM_PATTERNS matches in the last 10 lines of the last assistant message,
#   pass only if ALL of these hold within the current turn (since the last real user input):
#     L1. A Bash tool_use exists.
#     L3. Its tool_result.content matches EVIDENCE_PATTERNS.
#     L2. At least one EVIDENCE_PATTERNS-matching span from that tool_result
#         is paste'd as a substring of the assistant message text — i.e. the
#         specific "12 passed" / "tests passed" / "lint clean" / "✅" / etc.
#         token that triggered L3 must appear verbatim in the message.
#   L3/L2 consider only "genuine" tool_results — those produced by a command
#   that is NOT an echo/printf-only fabrication of the success token, so
#   `echo "tests passed"` cannot satisfy the gate. [issue #758]
#   Otherwise, block with a {decision: block, reason: ...} JSON payload.

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // ""')
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
# SubagentStop (issue #1337) carries two transcripts: `transcript_path` is the
# MAIN session's and `agent_transcript_path` is the subagent's own, "stored in
# a nested subagents/ folder" (hooks reference, read 2026-09-06). Grading a
# subagent's completion claim against the parent's turn is the defect this
# selection prevents. The agent path wins only when it names an existing file,
# so a plain Stop payload (no such key) and a SubagentStop whose agent
# transcript has not been flushed both fall back to the main one.
#
# ALLOW_SIDECHAIN rides along into the jq below: every event in a per-agent
# transcript belongs to that agent, so the `isSidechain` filters — which exist
# to keep a subagent's events out of the MAIN transcript's turn — must not
# apply to it. Left on, they would empty the turn and the gate would pass
# every subagent silently. Mirrors `_transcript.load_recent_events`'s
# `drop_sidechain` for the two Python siblings.
# A payload that is about a subagent resolves to that agent transcript or to
# NOTHING — never to the parent's. The fallback an earlier draft had
# reintroduced the very defect this registration removes (CodeRabbit on
# #1358): with an unflushed agent transcript the turn came from the PARENT
# while LAST_TEXT came from the subagent's `last_assistant_message`, so a
# subagent that ran nothing and merely repeated a number from the parent's
# output ("9 tests passed. All done.") cleared the evidence and paste checks
# against evidence it never produced.
HOOK_EVENT_NAME=$(echo "$INPUT" | jq -r '.hook_event_name // ""')
AGENT_TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.agent_transcript_path // ""')
ALLOW_SIDECHAIN=false
if [ "$HOOK_EVENT_NAME" = "SubagentStop" ] || [ -n "$AGENT_TRANSCRIPT_PATH" ]; then
  if [ -n "$AGENT_TRANSCRIPT_PATH" ] && [ -f "$AGENT_TRANSCRIPT_PATH" ]; then
    TRANSCRIPT_PATH="$AGENT_TRANSCRIPT_PATH"
    ALLOW_SIDECHAIN=true
  else
    TRANSCRIPT_PATH=""
  fi
fi
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')
# The log line below has always written the "unknown" placeholder, but the
# ledger must not: aggregate_fires adds any non-empty session string to its
# distinct-session set, so "unknown" would collapse every unattributed fire
# into one fake session. Empty is the documented unattributed value — it
# still counts the decision, it only forgoes per-session attribution.
TELEMETRY_SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""')

# shellcheck source=../../_lib/record_fire.sh
. "$(dirname "$0")/../../_lib/record_fire.sh" 2>/dev/null || true
# shellcheck source=../../_lib/_paths.sh
# A POSIX shell aborts outright when `.` cannot find the file, so the guard
# below is unreachable without this — the degraded path must stay reachable.
. "$(dirname "$0")/../../_lib/_paths.sh" 2>/dev/null || true
# A missing _paths.sh must not read as "no state" — that silently disarms the
# gate below. Exit 0 means "pass" here, so bailing out on a broken install
# would drop completion verification entirely; degrade to the pre-#903 inline
# expansion instead and keep verifying.
if ! command -v praxis_resolve_writable >/dev/null 2>&1; then
  echo "praxis: hooks/_lib/_paths.sh unreadable — broken install, completion-verify running on the inline path default" >&2
  praxis_resolve_writable() {
    _prw_dir="${PRAXIS_HOME:-$HOME/.praxis}/$1"
    if mkdir -p "$_prw_dir" 2>/dev/null && [ -w "$_prw_dir" ]; then
      printf '%s\n' "$_prw_dir/$2"
    else
      printf '%s\n' "${TMPDIR:-/tmp}/praxis-$2"
    fi
    unset _prw_dir
  }
fi
command -v praxis_fire_arm >/dev/null 2>&1 && \
  praxis_fire_arm completion-verify completion-verify "$TELEMETRY_SESSION_ID" ""

[ "$STOP_HOOK_ACTIVE" = "true" ] && exit 0
[ ! -f "$TRANSCRIPT_PATH" ] && exit 0

CLAIM_PATTERNS='(모두 완료(했)?|완료했습니다[.!。?…]?\s*$|작업 완료[.!。?…]?\s*$|완료[.!。?…]?\s*$|\bdone\b[.!?]?\s*$|\bfinished\b[.!?]?\s*$|cleanup (is |was )?finished|implementation complete|all done)'
EVIDENCE_PATTERNS='(tests? passed|\bPASS\b|exit code 0|\b[1-9][0-9]* tests? (ran|passed)|\b[1-9][0-9]* passed\b|0 errors|build successful|lint clean|성공적으로|테스트.*통과|✅)'

# --- Evidence class by changed surface (issue #943, stage 1: frontend) --------
#
# The patterns above are surface-agnostic, so a genuine command that verified
# the WRONG thing still passes: a `curl` status sweep and a bundle `grep` are
# valid oracles for a backend change and prove nothing about what a page
# renders. The observed failure was a frontend doc-link swap declared complete
# on 38 curl status codes plus a source grep; the conclusion changed once the
# page was actually opened.
#
# This is a deterministic mapping (changed path → required evidence class), not
# a sufficiency judgement — one regex match, so it fits inside a shell hook.
# The SOT for the mapping is the global CLAUDE.md verification table.
#
# Only extensions that are unambiguously view-layer are listed. Bare `.ts`/`.js`
# are deliberately absent: they are as often backend as frontend, and a false
# block on a server change costs more than the miss.
FRONTEND_EXT='(tsx|jsx|vue|svelte|astro|html|htm|css|scss|sass|less)'
FRONTEND_PATH_PATTERN="\\.${FRONTEND_EXT}\$"
# A frontend file edited through Bash — `sed -i`, a redirect, `tee`, `cp`/`mv`
# — never reaches `EDITED_PATHS`, so keying the surface only on the file tools
# lets the choice of editor decide whether the gate applies. Only the writing
# operators are listed, and the path must be the operand: `grep foo App.tsx >
# /tmp/out` writes to /tmp/out and is not a surface change.
#
# Residual: a script whose own body writes the file (`python build.py`) carries
# no frontend path on the command line and is not detected. Chasing that needs
# a general shell parser, which this repo has already found to be unbounded.
BASH_FRONTEND_MUTATION_PATTERN="(sed[[:space:]]+-i([[:space:]]+[^[:space:]]+)*[[:space:]]+[^[:space:];&|]*\\.${FRONTEND_EXT}|(>>?|tee([[:space:]]+-a)?)[[:space:]]*[^[:space:];&|]*\\.${FRONTEND_EXT}|(cp|mv|install)[[:space:]]+[^;&|]*\\.${FRONTEND_EXT}([[:space:]]|\$))"

# Oracles that observe a rendered page. `cmux browser` subcommands verified
# against `cmux --help`: snapshot / get / is / eval / screenshot read the live
# DOM, and `--snapshot-after` attaches one to a navigation or interaction.
# A bare `browser open`/`goto` only navigates, so it is not on its own an
# observation and is not listed.
#
# The subcommand must sit IMMEDIATELY after `browser`, and a driver name must
# sit at command position: matching them anywhere in the string let
# `npm ls puppeteer`, `grep -R playwright package.json`, and
# `cmux browser goto http://host/snapshot` all count as having observed a page.
BROWSER_EVIDENCE_CMD_PATTERN='(^|[;&|][[:space:]]*)[[:space:]]*((cmux([[:space:]]+--[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*[[:space:]]+browser[[:space:]]+(--surface[[:space:]]+[^[:space:]]+[[:space:]]+)?(snapshot|screenshot|eval|get|is|wait)([[:space:]]|$))|((npx|pnpm|yarn|bunx)[[:space:]]+(exec[[:space:]]+)?)?(playwright|puppeteer)([[:space:]]|$))'
# `--snapshot-after` is a cmux browser flag and nothing else, so it stands on
# its own — it is what makes a navigation or interaction also an observation.
BROWSER_SNAPSHOT_FLAG_PATTERN='--snapshot-after([[:space:]]|$)'
# Browser automation reached through an MCP server never appears as a Bash
# command, only as a tool name — without this a Playwright-MCP verification
# would be blocked as if nothing had been checked. The name must also carry an
# observation verb: `browser_close` and `list_pages` drive the browser without
# reading anything back. Verbs rather than vendor tool names, because the tool
# inventory is per-server and not verifiable from here.
BROWSER_EVIDENCE_TOOL_PATTERN='(browser|playwright|puppeteer|chrome_devtools).*(snapshot|screenshot|eval|content|text|dom|inspect|console|network|query|read|get)'

# Single jq pass: extract last assistant text + Bash tool_result texts in current turn.
# Current turn boundary = events after the last real user input (string content, or
# array containing any non-tool_result block). Tool-result-only user messages are
# tool replies and do not reset the turn. [PR #144]
TURN_JSON=$(tail -n 400 "$TRANSCRIPT_PATH" | jq -sc --argjson allow_sidechain "$ALLOW_SIDECHAIN" '
  ([
    to_entries[]
    | select(
        .value.message.role == "user"
        and ($allow_sidechain or ((.value.isSidechain // false) == false))
        and (
          (.value.message.content | type) == "string"
          or (
            (.value.message.content | type) == "array"
            and ((.value.message.content // []) | map(select(.type != "tool_result")) | length > 0)
          )
        )
      )
    | .key
  ] | last) as $user_idx
  | (if $user_idx == null then 0 else $user_idx + 1 end) as $start
  | (.[$start:]) as $turn
  | ([$turn[]
       | select(.message.role == "assistant" and ($allow_sidechain or ((.isSidechain // false) == false)))]
     | last
     | (.message.content // [])
     | map(select(.type == "text") | .text)
     | join("\n")) as $last_text
  | ([$turn[]
       | select(.message.role == "assistant" and ($allow_sidechain or ((.isSidechain // false) == false)))
       | (.message.content // [])[]
       | select(.type == "tool_use" and .name == "Bash")
       | {id: .id, cmd: (.input.command // "")}]) as $bash_uses
  | ($bash_uses | map(.id)) as $bash_ids
  | ([$turn[]
       | select(.message.role == "user")
       | (.message.content // [])[]
       | select(.type == "tool_result"
                and (.tool_use_id as $t | $bash_ids | any(. == $t)))
       | {tid: .tool_use_id,
          text: (if (.content | type) == "string" then .content
                 elif (.content | type) == "array" then
                   (.content | map(select(.type == "text") | .text) | join("\n"))
                 else "" end)}]) as $results
  | ([$results[] | .text] | join("\n---\n")) as $bash_outputs
  | ([$results[]
       | . as $r
       | (($bash_uses[] | select(.id == $r.tid) | .cmd) // "") as $cmd
       | select(((($cmd | test("^\\s*(echo|printf)\\b")) and (($cmd | test("[;&|`$\\n]")) | not))) | not)
       | $r.text]
     | join("\n---\n")) as $genuine_outputs
  | ([$turn[]
       | select(.message.role == "user")
       | (.message.content // [])[]
       | select(.type == "tool_result" and ((.is_error // false) | not))
       | .tool_use_id]) as $ok_ids
  | ([$turn[]
       | select(.message.role == "user")
       | (.message.content // [])[]
       | select(.type == "tool_result" and ((.is_error // false) == true))
       | .tool_use_id]) as $failed_ids
  | ([$turn[]
       | select(.message.role == "assistant" and ($allow_sidechain or ((.isSidechain // false) == false)))
       | (.message.content // [])[]
       | select(.type == "tool_use")
       | select(.name == "Edit" or .name == "Write" or .name == "MultiEdit"
                or .name == "NotebookEdit")
       | select(.id as $i | ($failed_ids | any(. == $i)) | not)
       | ((.input.file_path // .input.notebook_path) // "")]
     | join("\n")) as $edited_paths
  | ([$turn[]
       | select(.message.role == "assistant" and ($allow_sidechain or ((.isSidechain // false) == false)))
       | (.message.content // [])[]
       | select(.type == "tool_use")
       | select(.id as $i | $ok_ids | any(. == $i))
       | .name]
     | join("\n")) as $tool_names
  | (($bash_uses
       | map(select(((. .cmd | test("^\\s*(echo|printf)\\b"))
                     and ((. .cmd | test("[;&|`$\\n]")) | not)) | not))
       | map(select(.id as $i | $ok_ids | any(. == $i)))
       | map(.cmd))
     | join("\n")) as $genuine_cmds
  | {last_text: $last_text, bash_outputs: $bash_outputs,
     genuine_outputs: $genuine_outputs, edited_paths: $edited_paths,
     tool_names: $tool_names, genuine_cmds: $genuine_cmds}
' 2>/dev/null)

[ -z "$TURN_JSON" ] && exit 0

LAST_TEXT=$(printf '%s' "$TURN_JSON" | jq -r '.last_text // ""')
# The transcript "is written asynchronously and may lag the in-memory
# conversation ... Hooks that need the final assistant text of the current
# turn should use `last_assistant_message` on Stop and SubagentStop instead of
# reading the transcript" (hooks reference, read 2026-09-06). The turn read
# above still gates the whole check — a claim taken from the payload is graded
# only against evidence this run actually read, never against an empty turn.
# The `| ltrimstr` pair is not enough for a whitespace-only value, so the
# emptiness test is on the trimmed copy while the assignment keeps the text
# verbatim — matching `_transcript.stop_last_assistant_text`, which falls back
# to the turn when the field is blank rather than grading an empty claim.
PAYLOAD_LAST_TEXT=$(echo "$INPUT" | jq -r 'if (.last_assistant_message | type) == "string" then .last_assistant_message else "" end')
if [ -n "$(printf '%s' "$PAYLOAD_LAST_TEXT" | tr -d '[:space:]')" ]; then
  LAST_TEXT="$PAYLOAD_LAST_TEXT"
fi
BASH_OUTPUTS=$(printf '%s' "$TURN_JSON" | jq -r '.bash_outputs // ""')
# genuine_outputs excludes results produced by echo/printf-only commands, so a
# fabricated `echo "tests passed"` cannot satisfy the evidence gate. [issue #758]
GENUINE_OUTPUTS=$(printf '%s' "$TURN_JSON" | jq -r '.genuine_outputs // ""')
EDITED_PATHS=$(printf '%s' "$TURN_JSON" | jq -r '.edited_paths // ""')
TOOL_NAMES=$(printf '%s' "$TURN_JSON" | jq -r '.tool_names // ""')
GENUINE_CMDS=$(printf '%s' "$TURN_JSON" | jq -r '.genuine_cmds // ""')

[ -z "$LAST_TEXT" ] && exit 0

# Check last 10 lines only — avoids false positives from mid-message 완료 mentions
LAST_LINES=$(printf '%s\n' "$LAST_TEXT" | tail -10)
if ! printf '%s' "$LAST_LINES" | grep -qiE "$CLAIM_PATTERNS"; then
  exit 0
fi

# Claim detected — verify L1+L3+L2 in this turn.
block_reason=""

if [ -z "$BASH_OUTPUTS" ]; then
  block_reason="No Bash verification command was run in this turn. Run a real verify command (test/lint/build) and paste its output BEFORE declaring completion."
elif [ -z "$GENUINE_OUTPUTS" ]; then
  block_reason="Your only Bash 'verification' this turn was an echo/printf of the success token, not a real command. Run an actual test/lint/build and paste ITS output BEFORE declaring completion."
elif ! printf '%s' "$GENUINE_OUTPUTS" | grep -qE "$EVIDENCE_PATTERNS"; then
  block_reason="Bash output present but lacks a verification signal (e.g., 'tests passed', 'exit code 0', 'lint clean'). Re-run an actual verify command."
else
  # Paste check: each EVIDENCE_PATTERNS-matching span in tool_result must
  # appear verbatim in the assistant message. Span-based (not line-based)
  # so decorated output like '======== 12 passed in 0.85s ========' counts
  # when the assistant cites '12 passed in 0.85s'. Spans are drawn from
  # genuine (non-echo/printf) outputs only. [issue #758]
  paste_detected=false
  evidence_spans=$(printf '%s' "$GENUINE_OUTPUTS" | grep -oE "$EVIDENCE_PATTERNS")
  while IFS= read -r span; do
    [ -z "$span" ] && continue
    if printf '%s' "$LAST_TEXT" | grep -qF -e "$span"; then
      paste_detected=true
      break
    fi
  done <<< "$evidence_spans"

  if [ "$paste_detected" = "false" ]; then
    block_reason="Bash output has a verification signal but the evidence span (e.g. 'X passed', 'lint clean', '✅') was not quoted in your message. Paste the verify token verbatim into your reply."
  fi
fi

# Surface check runs even when the generic gate passed: the whole point is that
# a curl/grep/CI-green sweep satisfies the surface-agnostic patterns while
# proving nothing about a page's rendered output. Skipped when the generic gate
# already blocked, so the reply carries one reason rather than two.
if [ -z "$block_reason" ]; then
  fe_files=""
  if [ -n "$EDITED_PATHS" ]; then
    fe_files=$(printf '%s\n' "$EDITED_PATHS" | grep -iE "$FRONTEND_PATH_PATTERN" | head -3 | tr '\n' ' ')
  fi
  if [ -z "$fe_files" ] && [ -n "$GENUINE_CMDS" ] \
     && printf '%s\n' "$GENUINE_CMDS" | grep -qiE "$BASH_FRONTEND_MUTATION_PATTERN"; then
    fe_files=$(printf '%s\n' "$GENUINE_CMDS" | grep -oiE "[^[:space:];&|]*$FRONTEND_PATH_PATTERN" \
      | head -3 | tr '\n' ' ')
    [ -z "$fe_files" ] && fe_files="(bash-edited) "
  fi

  if [ -n "$fe_files" ]; then
    fe_evidence=false
    # `-e` is load-bearing: BROWSER_SNAPSHOT_FLAG_PATTERN starts with `--`, which
    # grep otherwise parses as an option and rejects outright.
    if printf '%s\n' "$GENUINE_CMDS" | grep -qiE -e "$BROWSER_EVIDENCE_CMD_PATTERN"; then
      fe_evidence=true
    elif printf '%s\n' "$GENUINE_CMDS" | grep -qiE -e "$BROWSER_SNAPSHOT_FLAG_PATTERN"; then
      fe_evidence=true
    elif printf '%s\n' "$TOOL_NAMES" | grep -qiE -e "$BROWSER_EVIDENCE_TOOL_PATTERN"; then
      fe_evidence=true
    fi
    if [ "$fe_evidence" = "false" ]; then
      block_reason="A frontend surface was changed this turn (${fe_files}) but nothing observed the rendered page. curl status codes, a source or bundle grep, and CI green are valid oracles for other surfaces and say nothing about what renders. Open the page and read the DOM (e.g. 'cmux browser goto <url> --snapshot-after', 'cmux browser get text <selector>', or a Playwright run) BEFORE declaring completion."
    fi
  fi
fi

if [ -n "$block_reason" ]; then
  # Diagnostics live under the documented logs root (issue #1182); the
  # pre-#1182 ~/.praxis/scope-confirm/ dir was an undocumented 4th root.
  _log="$(praxis_resolve_writable logs stop-triggered.log)"
  command -v praxis_rotate_log >/dev/null 2>&1 && praxis_rotate_log "$_log"
  echo "$(date -Iseconds) session=$SESSION_ID blocked_completion_without_evidence" >> "$_log" || true

  # shellcheck disable=SC2034  # read by the EXIT trap installed in sourced record_fire.sh
  PRAXIS_FIRE_DECISION=block
  REASON="Completion claim detected without same-turn verification evidence. ${block_reason} See AGENTS.md Verification section."
  jq -n --arg r "$REASON" '{decision: "block", reason: $r}'
  exit 0
fi

exit 0
