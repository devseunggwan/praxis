#!/bin/bash
# Stop hook: block retrospect Stage 3 outputs that violate the memory-bias gate.
# Contract: reads JSON from stdin, emits {"decision":"block"} or exit 0 pass.
#
# T3 double gate (issue #146):
#   Gate-1 (Categorical): findings tagged tool/workflow/spec-gap may not have
#     Proposed Actions = memory (single, not compound).
#   Gate-2 (Procedural): every memory-only row's Rationale must match one of:
#     Schema A — exactly 5 lines matching '^not (issue|claude_md_draft|
#       skill_idea|hook_code|upstream_feedback): .+$' (one per non-memory type).
#     Schema B — 1-2 lines matching '^not-others: .+$' (dimension-tag form;
#       issue #285). No mixing of Schema A and B lines.
#
# Gate-7 (Transcript-Enumeration Receipt, issue #600 follow-up): session-level
#   structural check. When the transcript contains a compaction-summary marker
#   ("isCompactSummary":true), the Stage 3 report MUST carry a
#   'retrospect:transcript_receipt' fence (or the '..._skipped' variant). Its
#   absence blocks — it is the structural enforcement of SKILL.md Stage 2's
#   "Compaction + readable transcript (MUST)" prose, which recurred as a
#   salient-window default even after shipping (rule exists != retrieval).
#
# Trigger: last assistant message contains a line starting with '## Retrospect
#   Report' AND the distribution-card fence '<!-- retrospect:distribution
#   begin -->' / 'end' AND the most-recent '## Retrospect Report' block does
#   NOT contain '## Actions Executed' (i.e., we're at Stage 3 awaiting approval,
#   before Stage 4 execution).
#
# Parses the AUTHORITATIVE_SCHEMA distribution card (deterministic snake_case
# enum) plus the unified findings table (literal column headers). Drift in the
# Stage 3 output schema requires synchronized edits to this hook + tests +
# fixtures. No bypass marker — false positives are reported as new issues.

command -v jq >/dev/null 2>&1 || exit 0

# Wall-clock origin for Gate-4b's network budget. Taken before any work so the
# deadline covers this hook's whole run, not just the lookups (bash 3.2 on
# macOS has no $EPOCHREALTIME, so the resolution is 1s — coarse against the
# 0.5s subprocess floor, and deliberately so: it can only end up conservative).
HOOK_START_EPOCH=$(date +%s)

INPUT=$(cat)
# One jq spawn for all four scalars (was four; ~10ms/fire on a ~68ms hook).
# NUL-separated, because `@tsv` is NOT lossless here: it escapes a backslash,
# tab, CR or newline inside a value, and nothing downstream decodes them. A
# `transcript_path` holding a backslash then reaches `[ ! -f ]` in its encoded
# form, the file is not found, and the gate exits 0 — silently disarmed on a
# path the four separate `jq -r` calls handled correctly.
# NUL is the one byte a filesystem path cannot contain, so `jq -j` can emit
# the values raw with no escaping at all.
# Read with `read -r -d ''`, not `mapfile -d ''` — mapfile's -d needs bash 4.4
# and this repo still has to run under the bash 3.2 that ships on macOS. An
# `IFS=$'\t' read` split would be wrong for a further reason: tab is IFS
# whitespace, so it drops a leading empty field and collapses runs, shifting
# every value one slot.
# Pre-seeded empty so malformed JSON (jq prints nothing, every read fails)
# leaves all four blank — the same fail-open result as the four separate calls.
# "unknown" is fine for the marker filename and the log lines below, but not
# for the ledger — see the same guard in completion-verify/impl.sh.
TRANSCRIPT_PATH=""; STOP_HOOK_ACTIVE=""; SESSION_ID=""; TELEMETRY_SESSION_ID=""
{
  IFS= read -r -d "" TRANSCRIPT_PATH
  IFS= read -r -d "" STOP_HOOK_ACTIVE
  IFS= read -r -d "" SESSION_ID
  IFS= read -r -d "" TELEMETRY_SESSION_ID
} < <(printf '%s' "$INPUT" | jq -j '
  (.transcript_path // ""), "\u0000",
  (.stop_hook_active // false | tostring), "\u0000",
  (.session_id // "unknown"), "\u0000",
  (.session_id // ""), "\u0000"' 2>/dev/null)

# shellcheck source=../../_lib/record_fire.sh
. "$(dirname "$0")/../../_lib/record_fire.sh" 2>/dev/null || true
# shellcheck source=../../_lib/_paths.sh
# A POSIX shell aborts outright when `.` cannot find the file, so the guard
# below is unreachable without this — the degraded path must stay reachable.
. "$(dirname "$0")/../../_lib/_paths.sh" 2>/dev/null || true
# A missing _paths.sh must not read as "no state" — that silently disarms the
# gate below. Exit 0 means "pass" here, so bailing out on a broken install
# would drop the check entirely; degrade to the pre-#903 inline expansion
# instead and keep checking.
if ! command -v praxis_resolve_writable >/dev/null 2>&1; then
  echo "praxis: hooks/_lib/_paths.sh unreadable — broken install, retrospect-mix-check running on the inline path default" >&2
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
  praxis_fire_arm retrospect-mix-check completion-verify "$TELEMETRY_SESSION_ID" ""

[ "$STOP_HOOK_ACTIVE" = "true" ] && exit 0
[ ! -f "$TRANSCRIPT_PATH" ] && exit 0


# Extract last assistant message text from the transcript JSONL.
LAST_TEXT=$(tail -n 400 "$TRANSCRIPT_PATH" | jq -rs '
  [ .[]
    | select(.message.role == "assistant" and (.isSidechain // false) == false)
  ] | last
    | (.message.content // [])
    | map(select(.type == "text") | .text)
    | join("\n")
' 2>/dev/null)

[ -z "$LAST_TEXT" ] && exit 0

# Strip fenced code blocks (```...```) from LAST_TEXT before identifier checks
# so that documentation/example output quoted inside fences does NOT trip the
# hook. This addresses the meta-hazard where Claude pastes a retrospect example
# while authoring SKILL.md or this very review.
LAST_TEXT=$(printf '%s\n' "$LAST_TEXT" | awk '
  BEGIN { in_fence = 0 }
  /^[[:space:]]*```/ { in_fence = !in_fence; next }
  !in_fence { print }
')

# Canonical Stage-3 signals (computed once; reused below).
HAS_HEADER=false
printf '%s\n' "$LAST_TEXT" | grep -qE '^## Retrospect Report' && HAS_HEADER=true
HAS_FENCE=false
if printf '%s' "$LAST_TEXT" | grep -qF '<!-- retrospect:distribution begin -->' \
   && printf '%s' "$LAST_TEXT" | grep -qF '<!-- retrospect:distribution end -->'; then
  HAS_FENCE=true
fi
HAS_ACTIONS=false
printf '%s\n' "$LAST_TEXT" | grep -qF '## Actions Executed' && HAS_ACTIONS=true
# Report-shaped signal: a markdown table separator row (e.g. '|---|---|'). This
# is LANGUAGE-INDEPENDENT — a localized Stage 3 findings table still uses
# markdown pipe syntax — so it distinguishes "presenting a findings report" from
# a legitimate pre-Stage-3 prose clarification stop (SKILL.md self-conflict /
# ambiguous-backing_repo surfaces), without keying on any localizable header.
HAS_TABLE=false
printf '%s\n' "$LAST_TEXT" | grep -qE '^[[:space:]]*\|[[:space:]:|-]*-[[:space:]:|-]*\|[[:space:]]*$' && HAS_TABLE=true

# Issue #666 — retrospect-active Stage-3 fence-omission gate.
# The marker is written by hooks/preflight-gate/retrospect-active-marker/impl.py
# at retrospect skill-invocation time — a format-INDEPENDENT signal that a
# Stage 3 report is owed this turn. The identifier checks below key on the
# agent's own output format (header + distribution fence); a free-form /
# localized report that omits the fence evades them and silently no-ops every
# gate (incl. Gate-7) — "the gate exists but does not fire". Close that here,
# BEFORE the format-keyed pass-throughs run.
RETRO_ACTIVE=false
# [#903] The marker moved from ${TMPDIR} to <praxis_home>/cache. A session that
# was already retrospect-active when the upgrade landed still has its marker at
# the old path, so fall back to it for reads — otherwise the gate goes silent
# mid-retrospect, which is the failure #666 was opened for.
if [ -n "${PRAXIS_RETROSPECT_ACTIVE_FILE:-}" ]; then
  MARKER_FILE="$PRAXIS_RETROSPECT_ACTIVE_FILE"
else
  MARKER_FILE="$(praxis_resolve_writable cache "retrospect-active-${SESSION_ID}.json")"
  if [ ! -f "$MARKER_FILE" ]; then
    _RM_TMP="${TMPDIR:-/tmp}"; _RM_TMP="${_RM_TMP%/}"
    _RM_LEGACY="${_RM_TMP}/praxis-retrospect-active-${SESSION_ID}.json"
    [ -f "$_RM_LEGACY" ] && MARKER_FILE="$_RM_LEGACY"
  fi
fi
[ -f "$MARKER_FILE" ] && RETRO_ACTIVE=true

if [ "$RETRO_ACTIVE" = "true" ]; then
  if [ "$HAS_ACTIONS" = "true" ]; then
    # Stage 4 reached — retrospect cycle complete. Clear the marker; the
    # identifier checks below pass the post-Stage-4 output through.
    rm -f "$MARKER_FILE" 2>/dev/null || true
  elif [ "$HAS_FENCE" = "false" ] && [ "$HAS_TABLE" = "true" ]; then
    # Bypass: retrospect-active, presenting a findings table (report-shaped),
    # no Stage-4 marker, distribution fence absent. The mix-check gates cannot
    # evaluate the report. Block. (A retrospect-active stop WITHOUT a table is a
    # legitimate pre-Stage-3 prose clarification — not gated.)
    _log="$(praxis_resolve_writable logs retrospect-mix-blocked.log)"
    command -v praxis_rotate_log >/dev/null 2>&1 && praxis_rotate_log "$_log"
    echo "$(date -Iseconds) session=$SESSION_ID blocked_retrospect_fence_omission" \
      >> "$_log" || true
    fence_reason="Retrospect Stage 3 distribution fence missing (issue #666). This is a retrospect-active session (the retrospect skill was invoked this turn) presenting a findings table, but the assistant message carries no '<!-- retrospect:distribution begin -->' fence and no '## Actions Executed' marker. A free-form or localized Stage 3 report bypasses every mix-check gate (Gate-1..7) because the gates key on the canonical output schema. Re-emit the Stage 3 output per the Output Schema Contract in skills/retrospect/references/stage3-reporting.md: '## Retrospect Report' header -> audit fences -> '<!-- retrospect:distribution begin/end -->' card -> unified findings table."
    PRAXIS_FIRE_DECISION=block
    jq -n --arg r "$fence_reason" '{decision: "block", reason: $r}'
    exit 0
  fi
fi

# Identifier check 1+2: a gateable Stage 3 report is EITHER the canonical
# '## Retrospect Report' header form, OR — in a retrospect-active session — any
# message carrying the distribution fence (header-independent, so a localized
# header cannot skip the gates). Otherwise pass through.
if [ "$HAS_HEADER" = "false" ]; then
  if [ "$RETRO_ACTIVE" = "true" ] && [ "$HAS_FENCE" = "true" ]; then
    : # header-independent retrospect report — proceed to gate evaluation.
  else
    exit 0
  fi
fi
# The distribution-card fence must be present to parse the card.
if [ "$HAS_FENCE" = "false" ]; then
  exit 0
fi

# Identifier check 3: within the most recent '## Retrospect Report' block, no
# '## Actions Executed' marker (otherwise Stage 4 already ran — too late to gate).
# Extract the most recent block: from the last '^## Retrospect Report' line to
# either next '^## ' heading or end of message. For the header-independent
# retrospect-active path (no '## Retrospect Report' line), gate the whole
# message instead.
if [ "$HAS_HEADER" = "true" ]; then
  MOST_RECENT_BLOCK=$(printf '%s\n' "$LAST_TEXT" | awk '
    /^## Retrospect Report/ { capture=1; buf=""; }
    capture {
      if (NR > 1 && /^## / && !/^## Retrospect Report/) {
        capture=0
        next
      }
      buf = buf $0 "\n"
    }
    END { printf "%s", buf }
  ')
else
  MOST_RECENT_BLOCK="$LAST_TEXT"
fi

if printf '%s' "$MOST_RECENT_BLOCK" | grep -qF '## Actions Executed'; then
  exit 0
fi

# ── Silent-pass scan + critic-run detection (issue #772) ──────────────────────
# Placed AFTER every gate that can exit without reading them (issue #1077) —
# the identifier gates and the Stage 4 carve-out both sit above. These
# three passes read the WHOLE transcript — a grep sweep, a jq pass, and a `jq
# -rs` slurp — and at 224 MB the grep alone took 19.98s against this hook's 10s
# budget, so the hook was SIGKILLed and every gate it holds silently vanished.
# Nothing here is consumed before Gate-8c (first use ~700 lines below), and no
# path above can block without HAS_FENCE or the retrospect-active marker, so
# running them only once a gateable Stage 3 report is in hand is behaviour-
# preserving: the ordinary Stop, which is nearly every Stop, now does none of
# it, and neither does a Stage 4 completion report, which exits above.
# Everything here is FAIL-OPEN: a missing scanner / catalog, or any jq error,
# must never convert a pass into a crash — all captures use `2>/dev/null` with
# numeric/string defaults.
_SP_LIB="$(dirname "$0")/../../_lib"
SP_SCANNER="$_SP_LIB/scan-silent-pass.sh"
SP_CATALOG="$_SP_LIB/silent-pass-catalog.json"
SILENT_PASS_MATCHES=""
if [ -f "$SP_SCANNER" ] && [ -f "$SP_CATALOG" ]; then
  SILENT_PASS_MATCHES=$(bash "$SP_SCANNER" --transcript "$TRANSCRIPT_PATH" --catalog "$SP_CATALOG" 2>/dev/null || true)
fi
# Count of HARD (zero-FP) silent-pass candidates — drives Gate-9 coverage AND
# Gate-8c/Gate-10 eligibility. awk emits `c+0` so this is always an integer.
GATE9_HARD_COUNT=$(printf '%s\n' "$SILENT_PASS_MATCHES" \
  | awk -F'\t' 'NF>=2 && $2=="hard" { c++ } END { print c+0 }')

# Strong-correction count (transcript-derived eligibility half). This is the SAME
# regex + jq the Gate-8c floor uses; hoisted here so both Gate-8c and the
# block-decision Gate-10 read one value (no double jq, no drift). Kept low-FP per
# issue #722 (see the Gate-8c comment for the dropped everyday tokens).
SP_STRONG_CORRECTION_RE="하라고 했잖아|that's not what I asked|그렇게 하지 말라고"
LIVE_STRONG_UC_COUNT=$(jq -r --arg re "$SP_STRONG_CORRECTION_RE" '
  def human_text_payload:
    (.message.content // .content // empty) as $c
    | if ($c|type) == "array" then
        $c[]? | if type == "object" then
          if ((.type? // "text") == "text") then (.text? // empty) else empty end
        else tostring end
      elif ($c|type) == "string" then $c
      else empty end;
  select(type == "object" and .type != "tool_result" and (.message.role == "user" or .role == "user" or .type == "user") and ([human_text_payload] | join("\n") | test($re; "i"))) | 1
' "$TRANSCRIPT_PATH" 2>/dev/null | wc -l | tr -d '[:space:]')
LIVE_STRONG_UC_COUNT=${LIVE_STRONG_UC_COUNT:-0}

# Critic-run detection (D-C2 anti-tamper oracle). The authoritative "did the
# externalized critic actually run" signal is a `critic_roots:` block emitted in
# the critic subagent's RETURN. A subagent return is delivered to the main agent
# as a role:user tool_result record, so we extract text ONLY from tool_result
# blocks — via `.. | objects | select(.type=="tool_result")`, which finds a
# tool_result at top level OR nested inside a user message's content array. A
# `retrospect:critic_roots` fence pasted into the MAIN agent's assistant text
# (isSidechain:false) is display-only and MUST NOT count — that is the C2
# property (the agent cannot self-certify the tier by pasting a fence), and it is
# structurally excluded here because assistant text never lives in a tool_result
# block. Tool_result attribution is reliable with the pinned critic contract, so
# the plan's isSidechain:true fallback is NOT used. jq errors on a partially
# written final line fail open (empty text ⇒ CRITIC_RAN=false ⇒ Gate-8c may
# block an eligible session — the safe direction).
CRITIC_TR_TEXT=$(jq -rs '
  def tr_texts:
    .. | objects | select(.type == "tool_result")
    | (.content // .text // empty) as $c
    | if ($c | type) == "array" then
        ($c[]? | if type == "object" then (.text? // empty)
                 elif type == "string" then .
                 else empty end)
      elif ($c | type) == "string" then $c
      else empty end;
  [ .[] | tr_texts ] | join("\n")
' "$TRANSCRIPT_PATH" 2>/dev/null || true)
CRITIC_RAN=false
CRITIC_ROOTS=""
if printf '%s\n' "$CRITIC_TR_TEXT" | grep -q 'critic_roots:'; then
  CRITIC_RAN=true
  # Root-ids are the contiguous `- <root-id>: <one-line>` bullets that follow the
  # `critic_roots:` header (blank lines inside the block are tolerated). An empty
  # block (header with no bullets) yields zero roots → CRITIC_RAN=true with empty
  # CRITIC_ROOTS (critic ran, 0 roots).
  CRITIC_ROOTS=$(printf '%s\n' "$CRITIC_TR_TEXT" | awk '
    /critic_roots:/ { f=1; next }
    f && /^[[:space:]]*$/ { next }
    f && /^[[:space:]]*-[[:space:]]*[^:]+:/ {
      s=$0
      sub(/^[[:space:]]*-[[:space:]]*/, "", s)
      sub(/:.*/, "", s)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", s)
      if (s != "") print s
      next
    }
    f { exit }
  ')
fi

# Parse distribution-card key/value pairs.
DIST_CARD=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk '
  /<!-- retrospect:distribution begin -->/ { capture=1; next }
  /<!-- retrospect:distribution end -->/ { capture=0 }
  capture { print }
')

# Extract gate verdicts from the card. Default to "MISSING" so a missing key
# trips the violation check below.
# LAST occurrence wins (handles dual-card-in-block case where a stale earlier
# PASS would otherwise shadow a corrected FAIL).
# `xargs` trims trailing whitespace that awk -F': *' leaves on the value (e.g.
# "FAIL " from a hand-edited card), which would break the exact-string verdict
# comparisons below ([ "FAIL " = "FAIL" ] is false → silent skip).
GATE_1=$(printf '%s\n' "$DIST_CARD" | awk -F': *' '/^- gate_1_verdict:/ {v=$2} END{print v}' | xargs)
GATE_2=$(printf '%s\n' "$DIST_CARD" | awk -F': *' '/^- gate_2_verdict:/ {v=$2} END{print v}' | xargs)
GATE_3=$(printf '%s\n' "$DIST_CARD" | awk -F': *' '/^- gate_3_verdict:/ {v=$2} END{print v}' | xargs)
GATE_4=$(printf '%s\n' "$DIST_CARD" | awk -F': *' '/^- gate_4_verdict:/ {v=$2} END{print v}' | xargs)
[ -z "$GATE_1" ] && GATE_1="MISSING"
[ -z "$GATE_2" ] && GATE_2="MISSING"
# Gate-3 MISSING is not blocked (newly added enforcement; old cards legitimately omit this key).
# Gate-4 MISSING is not blocked (old cards legitimately omit this key).

# Independent table parse (defense-in-depth: don't trust the card alone).
# Find the unified findings table header row and walk subsequent rows until a
# blank line or a non-table line.
TABLE_LINES=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk '
  /^\| # \| Category \| Tool Layer \| Pattern \| Root Cause \| Rule \/ Gap \| Repeat\? \| Proposed Actions \(1~2\) \| Rationale \| Priority \|/ {
    capture=1
    print
    next
  }
  capture {
    if (/^\|/) { print } else { exit }
  }
')

# Walk data rows (skip header + separator).
declare -a GATE1_VIOLATIONS=()
declare -a GATE2_VIOLATIONS=()
declare -a GATE3_VIOLATIONS=()
declare -a SHORT_ROW_VIOLATIONS=()
# Gate-4b input: one TAB-separated record per routed row that declared a
# backing repo — finding / repo / declared visibility / EXTERNAL-marker flag.
declare -a GATE4_ROWS=()
# Finding IDs proposing a remedy-layer action — Gate-11's coverage list. Built
# here rather than re-parsed later: the table is already being split cell-wise,
# and the card's per-action counts cannot say WHICH findings owe a receipt.
declare -a REMEDY_FINDING_IDS=()
ROW_INDEX=0
PIPE_SENTINEL=$'\x01PIPE\x01'

while IFS= read -r row; do
  [ -z "$row" ] && continue
  ROW_INDEX=$((ROW_INDEX + 1))
  # Skip header (row 1) and separator (row 2: starts with '|---').
  if [ "$ROW_INDEX" -le 2 ]; then continue; fi

  # Protect markdown-escaped pipes (`\|`) from the IFS='|' split.
  # Substitute '\|' with a sentinel before splitting; restore inside cells.
  protected=$(printf '%s' "$row" | sed "s/\\\\|/${PIPE_SENTINEL}/g")

  # Split row by '|' into cells (trim leading/trailing pipes + whitespace).
  trimmed="${protected#\|}"; trimmed="${trimmed%\|}"
  IFS='|' read -ra cells <<< "$trimmed"
  # cells indices (0-based): 0=#, 1=Category, 2=Tool Layer, 3=Pattern,
  # 4=Root Cause, 5=Rule/Gap, 6=Repeat?, 7=Proposed Actions, 8=Rationale,
  # 9=Priority

  # Fail closed on short rows (schema violation — block, do not silently skip).
  if [ "${#cells[@]}" -lt 10 ]; then
    finding_num_short=$(echo "${cells[0]:-?}" | xargs)
    SHORT_ROW_VIOLATIONS+=("finding #${finding_num_short}: row has ${#cells[@]} cells, expected 10 — table schema violation")
    continue
  fi
  finding_num=$(echo "${cells[0]}" | xargs)
  category=$(echo "${cells[1]}" | xargs)
  actions_raw=$(echo "${cells[7]}" | xargs)
  rationale=$(echo "${cells[8]}" | sed "s/${PIPE_SENTINEL}/|/g")

  # Tokenize Proposed Actions and dedupe (handles degenerate compounds like
  # 'memory, memory' — these are semantically memory-only).
  # Bash 3.2 compatible: string-based seen tracker (no associative arrays).
  IFS=',' read -ra action_tokens <<< "$actions_raw"
  unique_actions=()
  seen_str=" "
  for tok in "${action_tokens[@]}"; do
    t=$(echo "$tok" | xargs)
    [ -z "$t" ] && continue
    case "$seen_str" in
      *" $t "*) ;;
      *)
        seen_str="$seen_str$t "
        unique_actions+=("$t")
        ;;
    esac
  done

  # Gate-11 coverage list: this finding owes a remedy-reach row.
  for act in "${unique_actions[@]}"; do
    case "$act" in
      memory|claude_md_draft|skill_idea|hook_code)
        REMEDY_FINDING_IDS+=("$finding_num")
        break
        ;;
    esac
  done

  # Gate-1: tool/workflow/spec-gap labeled finding with action == 'memory' only.
  cat_has_nonbehavioral=false
  case ",$category," in
    *,tool,*|*,workflow,*|*,spec-gap,*) cat_has_nonbehavioral=true ;;
  esac
  # Also catch single-token (no comma) form.
  case "$category" in
    tool|workflow|spec-gap) cat_has_nonbehavioral=true ;;
  esac

  is_memory_only=false
  if [ "${#unique_actions[@]}" -eq 1 ] && [ "${unique_actions[0]}" = "memory" ]; then
    is_memory_only=true
  fi

  if [ "$cat_has_nonbehavioral" = "true" ] && [ "$is_memory_only" = "true" ]; then
    GATE1_VIOLATIONS+=("finding #${finding_num} (category=${category}): tool/workflow/spec-gap labeled but Proposed Actions = memory only")
  fi

  # Gate-2: memory-only row Rationale must match Schema A or Schema B.
  #   Schema A: exactly 5 'not <action>: ...' lines (one per non-memory type).
  #   Schema B: 1-2 'not-others: ...' dimension-tag lines, no Schema A lines.
  if [ "$is_memory_only" = "true" ]; then
    # Normalize '<br>' line-break markup to actual newlines.
    normalized=$(printf '%s' "$rationale" | sed 's/<br *\/*>/\n/g')
    matches_a=$(printf '%s\n' "$normalized" | grep -cE '^[[:space:]]*not (issue|claude_md_draft|skill_idea|hook_code|upstream_feedback): .+')
    matches_b=$(printf '%s\n' "$normalized" | grep -cE '^[[:space:]]*not-others: .+')
    schema_a_pass=false
    schema_b_pass=false
    [ "$matches_a" -eq 5 ] && schema_a_pass=true
    if [ "$matches_b" -ge 1 ] && [ "$matches_b" -le 2 ] && [ "$matches_a" -eq 0 ]; then
      schema_b_pass=true
    fi
    if [ "$schema_a_pass" = "false" ] && [ "$schema_b_pass" = "false" ]; then
      GATE2_VIOLATIONS+=("finding #${finding_num}: memory-only row Rationale matches neither schema A (5 'not <action>: ...' lines, found ${matches_a}/5) nor schema B (1-2 'not-others: ...' lines, found ${matches_b})")
    fi
  fi

  # Gate-3 (backing_repo): rows whose Proposed Actions contain upstream_feedback
  # or issue MUST declare backing_repo: <owner/repo> in the Rationale cell.
  # Stage 2 step 7 documents this as MUST; Stage 4 Action 4 step 0 aborts on absence.
  has_routed_action=false
  for tok in "${unique_actions[@]}"; do
    case "$tok" in
      upstream_feedback|issue) has_routed_action=true; break ;;
    esac
  done
  if [ "$has_routed_action" = "true" ]; then
    normalized_r3=$(printf '%s' "$rationale" | sed 's/<br *\/*>/\n/g')
    if ! printf '%s\n' "$normalized_r3" | grep -qE '^[[:space:]]*backing_repo: [A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*'; then
      GATE3_VIOLATIONS+=("finding #${finding_num}: Proposed Actions contains upstream_feedback or issue but Rationale missing backing_repo: <owner/repo> — return to Stage 2 step 7")
    else
      # Gate-4b input row: the DECLARED facts, captured verbatim. Whether they
      # hold is settled after the loop against GitHub, never against the card.
      g4_repo=$(printf '%s\n' "$normalized_r3" \
        | sed -n 's|^[[:space:]]*backing_repo:[[:space:]]*\([A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*\).*|\1|p' | head -1)
      g4_vis=$(printf '%s\n' "$normalized_r3" \
        | sed -n -E 's/^[[:space:]]*repo_visibility:[[:space:]]*(public|private|internal)[[:space:]]*$/\1/p' | head -1)
      [ -z "$g4_vis" ] && g4_vis="absent"
      g4_marker=no
      case "$rationale" in
        *"⚠ EXTERNAL: per-action approval required at Stage 4"*) g4_marker=yes ;;
      esac
      GATE4_ROWS+=("${finding_num}"$'\t'"${g4_repo}"$'\t'"${g4_vis}"$'\t'"${g4_marker}")
    fi
  fi
done <<< "$TABLE_LINES"

# Gate-4b (Independent visibility resolution, issue #1244).
#
# Gate-4 above reads `gate_4_verdict` out of a card the agent wrote, so the
# enforcement layer's input is authored by the thing it constrains: writing
# `gate_4_verdict: PASS` passes the gate. Gate-4b recomputes the classification
# from the row's own declarations plus GitHub, and blocks when the recomputed
# answer contradicts what the card claims.
#
# Escalation criterion mirrors skills/retrospect/references/stage2.5-audit.md
# Gate-4 (the SoT): a row is unescalated only when BOTH halves hold — the owner
# is own-org AND the repo is private/internal by declaration AND by API. Since
# #993 the criterion is visibility, not ownership, so a declared-public or
# undeclared row escalates with no lookup at all — that path stays fully
# offline-deterministic.
#
# THE FAILURE PATH IS A DEMOTION, NOT A BLOCK. Failing open would reproduce
# today's behaviour exactly (the hole this closes); failing closed would block
# every offline retrospect, since no Stop hook in this repo has ever needed the
# network. So an unresolved lookup lets the Stop proceed and demotes that row's
# ground to "visibility unresolved", surfaced as a systemMessage. That demotion
# reaches Stage 4 without needing anything written into the Rationale cell:
# stage4-execution.md Step 0a fires on either of two independent triggers, and
# trigger 2 (public-visibility recheck) is itself fail-closed — "if the
# visibility query errors, times out, or returns anything other than PRIVATE /
# INTERNAL, treat the repo as public and fire the gate". A row the hook could
# not resolve is exactly a row Step 0a re-resolves and fails closed on, so it
# stays in the per-action approval set. The literal `⚠ EXTERNAL:` marker is
# trigger 1 only, and the hook cannot edit the report to add it.
GATE4_BUDGET_SEC=8      # manifest timeout 10 - 2s spawn/margin
GATE4_MAX_LOOKUPS=8     # past ~15 repos the budget breaks (issue #1244 condition)
declare -a GATE4B_VIOLATIONS=()
declare -a GATE4B_DEMOTIONS=()
GATE4B_ESCALATED=0

if [ "${#GATE4_ROWS[@]}" -gt 0 ]; then
  # Only a row that DECLARES private/internal can carry the exemption, so only
  # those cost a round trip. Deduping is the helper's job (one call per repo).
  g4_lookup_repos=""
  for g4_rec in "${GATE4_ROWS[@]}"; do
    IFS=$'\t' read -r _ g4_r g4_v _ <<< "$g4_rec"
    case "$g4_v" in
      private|internal) g4_lookup_repos="${g4_lookup_repos}${g4_r}"$'\n' ;;
    esac
  done

  G4_OWN_ORGS="UNRESOLVED"
  G4_VIS_MAP=""
  # Which input was actually missing. The demotion message names this, so it
  # cannot blame an env var that is in fact set.
  G4_OWN_ORGS_GAP="no PRAXIS_OWN_ORGS, gh unavailable"
  if [ -n "$g4_lookup_repos" ]; then
    if command -v python3 >/dev/null 2>&1; then
      g4_out=$(printf '%s' "$g4_lookup_repos" | python3 \
        "$(dirname "$0")/gate4_visibility.py" \
        --deadline-epoch "$((HOOK_START_EPOCH + GATE4_BUDGET_SEC))" \
        --max-lookups "$GATE4_MAX_LOOKUPS" 2>/dev/null) || g4_out=""
      G4_OWN_ORGS=$(printf '%s\n' "$g4_out" | awk -F'\t' '$1=="own_orgs"{print $2}' | head -1)
      [ -z "$G4_OWN_ORGS" ] && G4_OWN_ORGS="UNRESOLVED"
      G4_VIS_MAP=$(printf '%s\n' "$g4_out" | awk -F'\t' '$1=="vis"{print $2"\t"$3}')
    else
      G4_OWN_ORGS_GAP="no PRAXIS_OWN_ORGS, python3 unavailable"
    fi
    # The visibility half needs the helper, but the own-org half has a purely
    # local answer whenever PRAXIS_OWN_ORGS is set. Without this leg, python3
    # being absent — or the helper failing, or printing nothing — demotes an
    # owner the allowlist already refutes, and a knowably-external repo passes
    # the Stop. Mirrors gate4_visibility.resolve_own_orgs()'s env leg exactly:
    # split on comma, trim, drop empties, lowercase. Only the env leg — the
    # `gh api user` leg genuinely needs a subprocess, so an allowlist that
    # exists nowhere locally still demotes, which is the deliberate offline
    # tradeoff documented above.
    if [ "$G4_OWN_ORGS" = "UNRESOLVED" ]; then
      g4_env_orgs=$(printf '%s' "${PRAXIS_OWN_ORGS:-}" | awk '{
        n = split($0, a, ",")
        for (i = 1; i <= n; i++) {
          gsub(/^[ \t]+|[ \t]+$/, "", a[i])
          if (a[i] != "") out = (out == "" ? a[i] : out "," a[i])
        }
        print tolower(out)
      }')
      [ -n "$g4_env_orgs" ] && G4_OWN_ORGS="$g4_env_orgs"
    fi
  fi

  for g4_rec in "${GATE4_ROWS[@]}"; do
    IFS=$'\t' read -r g4_num g4_repo g4_decl g4_mark <<< "$g4_rec"
    g4_verdict="escalate"
    g4_why="declared ${g4_decl} — a public or undeclared backing repo is a cross-boundary write whoever owns it (issue #993)"
    case "$g4_decl" in
      private|internal)
        g4_key=$(printf '%s' "$g4_repo" | tr '[:upper:]' '[:lower:]')
        # Always `owner/repo`: the Gate-3 parse above only admits a value
        # matching `<owner>/<repo>`, so a bare handle never reaches GATE4_ROWS —
        # it is rejected one gate earlier (T36m asserts that boundary). The
        # resolver keeps its own `not slash` guard because it reads a stdin list
        # rather than this already-filtered array.
        g4_owner="${g4_key%%/*}"
        if [ "$G4_OWN_ORGS" = "UNRESOLVED" ]; then
          # Ownness itself unknown: conservative here would mean escalate, but
          # escalate blocks, and this is the offline case. Demote instead.
          g4_verdict="unresolved"
          g4_why="own-org allowlist unresolved (${G4_OWN_ORGS_GAP})"
        elif ! printf ',%s,' "$G4_OWN_ORGS" | grep -qF ",${g4_owner},"; then
          g4_why="owner '${g4_owner}' is outside the own-org allowlist, so the declaration cannot exempt it"
        else
          g4_actual=$(printf '%s\n' "$G4_VIS_MAP" | awk -F'\t' -v k="$g4_key" '$1==k{print $2}' | head -1)
          case "${g4_actual:-UNRESOLVED}" in
            private|internal) g4_verdict="exempt" ;;
            public)
              g4_why="declares repo_visibility: ${g4_decl} but GitHub reports public for '${g4_repo}'"
              ;;
            *)
              g4_verdict="unresolved"
              g4_why="visibility lookup for '${g4_repo}' returned no answer (gh missing, unauthenticated, 404, rate-limited, over the ${GATE4_MAX_LOOKUPS}-repo cap, below the subprocess budget floor, or offline with no PRAXIS_REPO_VISIBILITY entry)"
              ;;
          esac
        fi
        ;;
    esac

    case "$g4_verdict" in
      escalate)
        GATE4B_ESCALATED=1
        if [ "$g4_mark" != "yes" ]; then
          GATE4B_VIOLATIONS+=("finding #${g4_num}: backing_repo '${g4_repo}' resolves as a cross-boundary write (${g4_why}) but the Rationale carries no '⚠ EXTERNAL: per-action approval required at Stage 4' marker — Stage 4 Step 0a trigger 1 is disarmed for this row")
        fi
        ;;
      unresolved)
        GATE4B_DEMOTIONS+=("finding #${g4_num}: backing_repo '${g4_repo}' — ${g4_why}; its ground is 'visibility unresolved', not 'exempt', so it stays in the Stage 4 per-action approval set (stage4-execution.md Step 0a trigger 2 fails closed on exactly this)")
        ;;
    esac
  done

  # The card's own verdict, checked against what was just recomputed.
  #
  # NA does not mean "nothing escalated" — audit-distribution-gates.py emits it
  # only when `routed` is empty, i.e. no finding proposed upstream_feedback or
  # issue at all. Every entry in GATE4_ROWS is such a finding, so a non-empty
  # array refutes NA outright, whatever the per-row resolution came back as. It
  # is checked ahead of the escalation test, not inside it: an NA card whose
  # rows all resolve exempt or unresolved is still a false card, and that is the
  # shape the escalation test could never see.
  if [ "$GATE_4" = "NA" ]; then
    g4_na_list=""
    for g4_rec in "${GATE4_ROWS[@]}"; do
      IFS=$'\t' read -r g4_num g4_repo _ _ <<< "$g4_rec"
      g4_na_list="${g4_na_list}, #${g4_num} (${g4_repo})"
    done
    GATE4B_VIOLATIONS+=("distribution card declares gate_4_verdict: NA — the value reserved for a draft with no routed external write at all — but the findings table carries ${#GATE4_ROWS[@]} routed row(s) with a declared backing repo: ${g4_na_list#, }; re-run skills/retrospect/audit-distribution-gates.py and paste the card it renders")
  elif [ "$GATE4B_ESCALATED" = "1" ] && [ "$GATE_4" = "PASS" ]; then
    GATE4B_VIOLATIONS+=("distribution card declares gate_4_verdict: PASS but an independent resolution escalates at least one routed row — the card's verdict is written by the agent this gate constrains, so it is not the oracle (issue #1244); re-run skills/retrospect/audit-distribution-gates.py and paste the card it renders")
  fi
fi

# Gate-7 (Transcript-Enumeration Receipt). Session-level structural check (not a
# per-finding Stage 2.5 gate like Gate-1..6). When the session transcript
# contains a compaction-summary marker, the Stage 3 report MUST carry a
# 'retrospect:transcript_receipt' fence — the friction pre-scan's
# full-transcript enumeration evidence (is_error / user-turn / interrupt counts
# from real commands). Its absence means the pre-scan likely defaulted to the
# compaction summary's salient narrative instead of enumerating the transcript
# (skills/retrospect/SKILL.md Stage 2 "Compaction + readable transcript MUST").
# The skipped variant ('retrospect:transcript_receipt_skipped') matches the same
# substring and satisfies the structural check; SKILL.md governs when that
# variant is legitimate. The compaction grep scans the WHOLE file (not the tail)
# because the marker can sit far back in a long post-compaction session.
# The (^|[^\\]) prefix excludes JSON-escaped textual mentions
# (\"isCompactSummary\":true inside a message body) so a session that merely
# DISCUSSES the marker does not false-trigger — only a real top-level field,
# whose quote is preceded by { or , (never a backslash), matches. grep (not jq)
# is deliberate: the transcript is appended live and a Stop hook may run
# mid-write, where jq aborts on a partially-written final line but grep tolerates
# it. [PR #639] hardened from a bare pattern after a CodeRabbit false-trigger flag.
GATE7_VIOLATION=""
if grep -Eq '(^|[^\\])"isCompactSummary"[[:space:]]*:[[:space:]]*true' "$TRANSCRIPT_PATH" 2>/dev/null; then
  # All Gate-7 content parsing MUST be scoped to the actual transcript_receipt
  # fence — tokens scattered OUTSIDE it (a stray transcript_receipt_skipped
  # marker, an is_error_enum block planted elsewhere, a count line in prose)
  # must not satisfy the gate [issue #664 / CodeRabbit]. Whole-block greps were
  # the same parse-scope leak this PR closes one level down for is_error_enum.
  #
  # Marker matches are anchored to standalone HTML-comment delimiter LINES
  # (^\s*<!-- MARKER -->\s*$), NOT bare substrings. A post-compaction receipt
  # legitimately ENUMERATES the prior session's is_error contents, and those
  # excerpts can themselves quote 'retrospect:transcript_receipt begin' (e.g.
  # retrospecting THIS very change). A bare substring count would then see >1
  # 'fence' and false-positive block a valid single-fence report [Codex round 5].
  # The markers ARE comment delimiters, so only the delimiter-line form counts.
  re_rb='^[[:space:]]*<!--[[:space:]]*retrospect:transcript_receipt begin[[:space:]]*-->[[:space:]]*$'
  re_re='^[[:space:]]*<!--[[:space:]]*retrospect:transcript_receipt end[[:space:]]*-->[[:space:]]*$'
  re_rs='^[[:space:]]*<!--[[:space:]]*retrospect:transcript_receipt_skipped:.*-->[[:space:]]*$'
  re_ib='^[[:space:]]*<!--[[:space:]]*retrospect:is_error_enum begin[[:space:]]*-->[[:space:]]*$'
  re_ie='^[[:space:]]*<!--[[:space:]]*retrospect:is_error_enum end[[:space:]]*-->[[:space:]]*$'
  re_cb='^[[:space:]]*<!--[[:space:]]*retrospect:content_error_enum begin[[:space:]]*-->[[:space:]]*$'
  re_ce='^[[:space:]]*<!--[[:space:]]*retrospect:content_error_enum end[[:space:]]*-->[[:space:]]*$'
  rcpt_begin=$(printf '%s\n' "$MOST_RECENT_BLOCK" | grep -cE "$re_rb")
  rcpt_skipped=$(printf '%s\n' "$MOST_RECENT_BLOCK" | grep -cE "$re_rs")
  # Marker COUNTS cannot detect a malformed fence: a balanced begin==end count
  # still passes for 'end ... begin' ordering; a prior CLOSED fence (e.g.
  # is_error_count: 0) followed by a new unterminated 'begin' would let the stale
  # closed fence satisfy the gate [Codex round 2]; and a NESTED 'begin' inside an
  # open fence makes the extractor reset its buffer and validate only the inner
  # span [Codex round 3]. So walk fence state and flag malformed = ended INSIDE a
  # fence (unterminated) OR saw a 'begin' while already inside one (nested).
  rcpt_malformed=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v rb="$re_rb" -v re="$re_re" '
    $0 ~ rb { if (inr) nested=1; inr=1; next }
    $0 ~ re { inr=0; next }
    END { print (inr || nested) ? 1 : 0 }')
  if [ "$rcpt_begin" -lt 1 ] && [ "$rcpt_skipped" -gt 0 ]; then
    : # Skipped variant is legitimate (SKILL.md governs when); no content check.
  elif [ "$rcpt_begin" -lt 1 ]; then
    # No fence at all. A stray skipped token alongside a real begin no longer
    # short-circuits — that bypass is closed.
    GATE7_VIOLATION="post-compaction session but the Stage 3 report has no '<!-- retrospect:transcript_receipt begin/end -->' fence — run the full-transcript friction scan and paste the REAL command output (is_error / user-turn / interrupt counts) in the receipt fence before Stage 3"
  elif [ "$rcpt_begin" -gt 1 ]; then
    # A Stage 3 report carries exactly ONE receipt. Multiple closed fences let a
    # benign trailing fence (is_error_count: 0) mask an earlier adverse one
    # (is_error_count: 3 with no enum) — the exact masking bypass this PR closes
    # [Codex round 4]. Reject >1 fence outright rather than picking one to trust.
    GATE7_VIOLATION="post-compaction report has $rcpt_begin transcript_receipt fences — Stage 3 must carry exactly one; multiple fences let a benign receipt mask an adverse one. Emit a single well-formed begin/end fence before Stage 3"
  elif [ "$rcpt_malformed" -gt 0 ]; then
    # The fence is unterminated (begin without end) or nested (begin inside an
    # open fence). Either way the latest receipt is malformed; validating an
    # earlier/inner span would let a benign fragment mask the real receipt.
    GATE7_VIOLATION="post-compaction transcript_receipt fence is malformed (an unterminated or nested 'retrospect:transcript_receipt begin') — emit exactly one well-formed begin/end fence with the REAL scan output before Stage 3"
  else
    # Extract ONLY the content inside the receipt fence (last CLOSED fence, which
    # is guaranteed to be the latest one now that an unterminated trailing fence
    # is rejected above), then run every content check against that region.
    RECEIPT_BLOCK=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v rb="$re_rb" -v re="$re_re" '
      $0 ~ rb { inr=1; buf=""; next }
      $0 ~ re { if (inr) last=buf; inr=0; next }
      inr { buf = buf $0 "\n" }
      END { printf "%s", last }')
    # Content-enumeration check [issue #664]: a 'grep -c' count proves a scan RAN,
    # not that the errors were READ — the exact gap that let a salient-window pass
    # survive Gate-7 (the count satisfied the fence while the error contents went
    # unexamined, and the most adverse error was silently dropped). When the
    # receipt declares is_error_count > 0, require a per-event
    # 'retrospect:is_error_enum' block so each error is surfaced with a
    # promote/note/dismiss disposition — invisible silent omission becomes
    # visible (challengeable) mis-disposition.
    # Anchor the count line to line-start so an enum excerpt quoting
    # 'is_error_count: N' in an error body cannot be picked up as the count.
    ie_count=$(printf '%s\n' "$RECEIPT_BLOCK" \
      | grep -oE '^[[:space:]]*is_error_count:[[:space:]]*[0-9]+' | grep -oE '[0-9]+' | head -1)
    if [ -z "$ie_count" ]; then
      GATE7_VIOLATION="post-compaction transcript_receipt fence carries no parseable 'is_error_count: N' line inside the fence — paste the REAL grep -c output in the receipt before Stage 3"
    elif [ "$ie_count" -gt 0 ] 2>/dev/null; then
      # A bare substring match on 'retrospect:is_error_enum' would reintroduce the
      # count-not-content gap one layer down: a prose mention, a lone begin marker,
      # or an empty begin/end pair with no rows would all pass while enumerating
      # nothing. Require both begin AND end markers AND >= 1 disposition row
      # (promote|note|dismiss) BETWEEN them; the awk scan counts rows only while
      # inside the enum fence, so rows leaking outside it do not satisfy the gate.
      ie_begin=$(printf '%s\n' "$RECEIPT_BLOCK" | grep -cE "$re_ib")
      ie_end=$(printf '%s\n' "$RECEIPT_BLOCK" | grep -cE "$re_ie")
      ie_rows=$(printf '%s\n' "$RECEIPT_BLOCK" | awk -v ib="$re_ib" -v ie="$re_ie" '
        $0 ~ ib { inblock=1; next }
        $0 ~ ie { inblock=0 }
        inblock && /disposition:[[:space:]]*(promote|note|dismiss)/ { c++ }
        END { print c+0 }')
      if [ "$ie_begin" -lt 1 ] || [ "$ie_end" -lt 1 ] || [ "$ie_rows" -lt 1 ]; then
        GATE7_VIOLATION="post-compaction receipt declares is_error_count=$ie_count but has no complete '<!-- retrospect:is_error_enum begin/end -->' block with per-event disposition rows inside the receipt fence (found begin=$ie_begin end=$ie_end disposition_rows=$ie_rows) — a count proves a scan ran, not that the errors were read; enumerate each is_error inside the fence with a promote/note/dismiss disposition before Stage 3"
      fi
    fi
    # Content-error-signal check [issue #670]: errors embedded in exit-0
    # tool_result CONTENT are not flagged by is_error:true — they appear in the
    # text body of a successful tool_result (e.g. "Exit code 1", "FATAL:",
    # "js_error:"). A separate scan anchored to error SYNTAX (not the bare word
    # "error", which produces false positives on field names like "is_error":false)
    # catches these. Calibrated regex: 'Exit code [1-9]|command terminated|
    # js_error|FATAL|No such file|denied|BLOCKED|Usage:([[:space:]]|$)'
    # (the Usage: alternation matches a trailing space OR end-of-line so bare
    # usage banners without a trailing space are not undercounted).
    # When the receipt declares content_error_count > 0, require a per-signal
    # 'retrospect:content_error_enum' block — same structure as is_error_enum.
    # An absent content_error_count field blocks (mirrors is_error_count absent).
    if [ -z "$GATE7_VIOLATION" ]; then
      # content_error_count sits on the pipe-delimited count line (not its own line),
      # so do NOT anchor with '^'. Use a word-boundary-style guard: require the field
      # to be preceded by a non-digit non-letter char (pipe/space) or start of string
      # to resist injection via enum rows that could quote 'content_error_count: 99'.
      # Pick the first match (head -1) to be consistent with ie_count extraction.
      ce_count=$(printf '%s\n' "$RECEIPT_BLOCK" \
        | grep -oE '[^a-zA-Z0-9_]content_error_count:[[:space:]]*[0-9]+|^[[:space:]]*content_error_count:[[:space:]]*[0-9]+' \
        | grep -oE 'content_error_count:[[:space:]]*[0-9]+' | grep -oE '[0-9]+' | head -1)
      if [ -z "$ce_count" ]; then
        GATE7_VIOLATION="post-compaction transcript_receipt fence carries no parseable 'content_error_count: N' line inside the fence — run: grep '\"type\":\"tool_result\"' \"\$transcript\" | grep -cE 'Exit code [1-9]|command terminated|js_error|FATAL|No such file|denied|BLOCKED|Usage:([[:space:]]|\$)' and paste the REAL count in the receipt before Stage 3"
      elif [ "$ce_count" -gt 0 ] 2>/dev/null; then
        # Require begin+end markers AND >= 1 disposition row inside the fence,
        # mirroring the is_error_enum enforcement exactly.
        ce_begin=$(printf '%s\n' "$RECEIPT_BLOCK" | grep -cE "$re_cb")
        ce_end=$(printf '%s\n' "$RECEIPT_BLOCK" | grep -cE "$re_ce")
        ce_rows=$(printf '%s\n' "$RECEIPT_BLOCK" | awk -v cb="$re_cb" -v ce="$re_ce" '
          $0 ~ cb { inblock=1; next }
          $0 ~ ce { inblock=0 }
          inblock && /disposition:[[:space:]]*(promote|note|dismiss)/ { c++ }
          END { print c+0 }')
        if [ "$ce_begin" -lt 1 ] || [ "$ce_end" -lt 1 ] || [ "$ce_rows" -lt 1 ]; then
          GATE7_VIOLATION="post-compaction receipt declares content_error_count=$ce_count but has no complete '<!-- retrospect:content_error_enum begin/end -->' block with per-signal disposition rows inside the receipt fence (found begin=$ce_begin end=$ce_end disposition_rows=$ce_rows) — a count proves a scan ran, not that the signals were read; enumerate each content-error signal inside the fence with a promote/note/dismiss disposition before Stage 3"
        fi
      fi
    fi
    # Gate-7 VALUE check [issue #671]: presence + structural validity alone do not
    # prove freshness — a receipt whose counts were transcribed verbatim from the
    # compaction summary passes every structural check while the declared values
    # diverge from a live grep of the same transcript. Re-derive is_error_count
    # and user_turn_count from the transcript and compare against the declared
    # values. Only runs when no structural violation is already set (no point
    # overwriting a more specific error).
    #
    # Canonical derivation commands (from skills/retrospect/references/report-template.md):
    #   is_error_count      ← grep -c '"is_error":true' {transcript_path}
    #   user_turn_count     ← grep -c '"role":"user"'  {transcript_path}
    #   content_error_count ← tool_result lines | grep -cE '<calibrated error syntax>'
    #                         (floor-only: declared 0 while live>tol is laundering)
    # interrupt_count has no canonical grep command in the spec; skip re-derivation.
    #
    # Tolerance: 1 line off-by-one is accepted to account for live-append races
    # (the transcript is appended while the Stop hook runs; the final line may be
    # partially written when grep scans). A delta > GATE7_VALUE_TOLERANCE lines
    # indicates the count was not derived from a fresh scan this turn.
    # Exit-code trap: grep -c returns exit code 1 on 0 matches; capture output
    # into a variable and never put grep -c inside an 'if' condition or '&&' chain.
    GATE7_VALUE_TOLERANCE=1
    if [ -z "$GATE7_VIOLATION" ] && [ -f "$TRANSCRIPT_PATH" ]; then
      # Re-derive is_error_count.
      live_ie_count=$(grep -c '"is_error":true' "$TRANSCRIPT_PATH" 2>/dev/null || true)
      live_ie_count=${live_ie_count:-0}
      # Re-derive user_turn_count (role:user covers both literal JSON forms).
      live_ut_count=$(grep -c '"role":"user"' "$TRANSCRIPT_PATH" 2>/dev/null || true)
      live_ut_count=${live_ut_count:-0}
      # Re-derive content_error_count [issue #670], scoped to tool_result-bearing
      # lines so the calibrated regex matches errors embedded in exit-0 tool_result
      # CONTENT, not the agent's own analysis prose or the Stage-3 report (those
      # live in assistant/text lines, never carry "type":"tool_result").
      live_ce_count=$(grep '"type":"tool_result"' "$TRANSCRIPT_PATH" 2>/dev/null \
        | grep -cE 'Exit code [1-9]|command terminated|js_error|FATAL|No such file|denied|BLOCKED|Usage:([[:space:]]|$)' 2>/dev/null || true)
      live_ce_count=${live_ce_count:-0}

      # Parse declared user_turn_count from the receipt body.
      # The declared line is: is_error_count: N | user_turn_count: N | interrupt_count: N
      # Anchor to line-start; pick the first match to resist injection via enum rows.
      declared_ut=$(printf '%s\n' "$RECEIPT_BLOCK" \
        | grep -oE '^[[:space:]]*is_error_count:[[:space:]]*[0-9]+[[:space:]]*\|[[:space:]]*user_turn_count:[[:space:]]*[0-9]+' \
        | grep -oE 'user_turn_count:[[:space:]]*[0-9]+' | grep -oE '[0-9]+' | head -1)
      # ie_count was already parsed above; reuse it as declared_ie.
      declared_ie="$ie_count"

      # Numeric comparison helper: abs(a - b) > tol => mismatch.
      _gate7_mismatch() {
        local a="$1" b="$2" tol="$3"
        # Validate both are integers; non-integer means unparseable → treat as mismatch.
        case "$a$b" in *[!0-9]*) echo 1; return ;; esac
        local diff=$(( a - b ))
        [ "$diff" -lt 0 ] && diff=$(( -diff ))
        [ "$diff" -gt "$tol" ] && echo 1 || echo 0
      }

      ie_mismatch=$(_gate7_mismatch "$declared_ie" "$live_ie_count" "$GATE7_VALUE_TOLERANCE")
      # user_turn_count: an absent/unparseable field is always a mismatch.
      # _gate7_mismatch("", N) silently passes because "" concatenated with a
      # digit string contains no non-digit chars; guard explicitly instead.
      if [ -z "$declared_ut" ]; then
        ut_mismatch=1
      else
        ut_mismatch=$(_gate7_mismatch "$declared_ut" "$live_ut_count" "$GATE7_VALUE_TOLERANCE")
      fi

      if [ "$ie_mismatch" = "1" ] || [ "$ut_mismatch" = "1" ]; then
        GATE7_VIOLATION="post-compaction receipt declares counts that do not match a live grep of the transcript (tolerance=$GATE7_VALUE_TOLERANCE): declared is_error_count=$declared_ie vs live=$live_ie_count; declared user_turn_count=${declared_ut:-(unparseable)} vs live=$live_ut_count — re-run grep -c '\"is_error\":true' and grep -c '\"role\":\"user\"' against the transcript this turn and paste the REAL output in the receipt fence before Stage 3"
      fi

      # Content-error floor [issue #670]: the structural block above accepts a
      # declared content_error_count of 0 without requiring an enum. But a declared
      # 0 while the transcript's tool_result content carries real error signals is
      # the same laundering #671 closes for is_error_count — the leading signal (the
      # content scan) was never run. Re-derive and block when declared 0 but live
      # signals exceed tolerance. Only fires when no earlier violation is set.
      if [ -z "$GATE7_VIOLATION" ] && [ "${ce_count:-0}" -eq 0 ] 2>/dev/null \
         && [ "$live_ce_count" -gt "$GATE7_VALUE_TOLERANCE" ]; then
        GATE7_VIOLATION="post-compaction receipt declares content_error_count=0 but a live scan of the transcript's tool_result content finds $live_ce_count error signal(s) (tolerance=$GATE7_VALUE_TOLERANCE) — exit-0 tool_result content (e.g. 'Exit code 1', 'FATAL', 'js_error') is not flagged by is_error:true; re-run: grep '\"type\":\"tool_result\"' \"\$transcript\" | grep -cE 'Exit code [1-9]|command terminated|js_error|FATAL|No such file|denied|BLOCKED|Usage:([[:space:]]|\$)' — paste the REAL count and enumerate each signal in a content_error_enum block before Stage 3"
      fi
    fi
  fi
fi

# Gate-8 (Suppression-Ledger Receipt, issue #699). Session-level structural
# check (not a per-finding Stage 2.5 gate): every gateable Stage 3 report MUST
# carry a 'retrospect:suppression_ledger' fence with at least a
# 'worst_agent_failure:' line, a 'self_adversarial:' line, and a 'critic_diff:'
# line. The fence is the report-level record of the Stage 2 self-incrimination
# pass and the conditional externalized critic re-scan tier; its absence means
# the painful agent-caused friction the analyzing context is most motivated to
# bury was never surfaced for audit ("the retrospect hides the painful parts").
# [PR #704] `critic_diff:` makes the conditional critic tier auditable even
# when it is skipped, so Stage 3 cannot silently omit the second-pass result.
# Mirrors the Gate-7 receipt pattern: presence + minimal content, scoped to the
# most-recent report block, markers anchored to standalone HTML-comment
# delimiter LINES so quoted examples / in-row mentions do not false-trigger.
#
# Stage-4 carve-out: Gate-8 is the first gate requiring a POSITIVE fence
# presence (Gates 1-7 only fire on violations), so unlike them it would
# retroactively block a COMPLETED cycle whose report block predates the #699
# contract. Skip Gate-8 when an '## Actions Executed' heading appears AFTER the
# most-recent '## Retrospect Report' header (Stage 4 reached for the latest
# report). A NEW report block after a prior Actions Executed (the T19 rerun
# shape) resets the flag, so it is still gated. The existing HAS_ACTIONS (line
# 74) is position-blind — it cannot distinguish report->actions (carve out) from
# actions->report2 (still gate), so a dedicated position-aware awk pass is
# required here, not a reuse of HAS_ACTIONS.
GATE8_VIOLATION=""
STAGE4_AFTER_REPORT=$(printf '%s\n' "$LAST_TEXT" | awk '
  /^## Retrospect Report/ { seen_report=1; actions_after=0; next }
  /^## Actions Executed/  { if (seen_report) actions_after=1; next }
  END { print actions_after+0 }')
if [ "$STAGE4_AFTER_REPORT" != "1" ]; then
re_sb='^[[:space:]]*<!--[[:space:]]*retrospect:suppression_ledger begin[[:space:]]*-->[[:space:]]*$'
re_se='^[[:space:]]*<!--[[:space:]]*retrospect:suppression_ledger end[[:space:]]*-->[[:space:]]*$'
sl_begin=$(printf '%s\n' "$MOST_RECENT_BLOCK" | grep -cE "$re_sb")
# Walk fence state: malformed = ended INSIDE a fence (unterminated) OR saw a
# 'begin' while already inside one (nested). Same defense as the Gate-7 receipt.
sl_malformed=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v sb="$re_sb" -v se="$re_se" '
  $0 ~ sb { if (ins) nested=1; ins=1; next }
  $0 ~ se { ins=0; next }
  END { print (ins || nested) ? 1 : 0 }')
if [ "$sl_begin" -lt 1 ]; then
  GATE8_VIOLATION="Stage 3 report has no '<!-- retrospect:suppression_ledger begin/end -->' fence (issue #699) — run the Stage 2 self-incrimination pass and emit the ledger with a 'worst_agent_failure:' line and a 'self_adversarial:' line before Stage 3, even on the clean path"
elif [ "$sl_malformed" -gt 0 ]; then
  # Check malformed BEFORE the duplicate-count branch: a nested begin makes both
  # sl_begin>1 AND sl_malformed=1, and the nested case is more precisely a
  # malformed fence than "multiple fences" [CodeRabbit PR #700].
  GATE8_VIOLATION="suppression_ledger fence is malformed (an unterminated or nested 'retrospect:suppression_ledger begin') — emit exactly one well-formed begin/end fence before Stage 3"
elif [ "$sl_begin" -gt 1 ]; then
  GATE8_VIOLATION="Stage 3 report has $sl_begin suppression_ledger fences — emit exactly one; multiple fences let a benign ledger mask an adverse one"
else
  # Extract the content of the (single, well-formed) ledger fence and require
  # both mandatory lines inside it. Anchored to line-start (optional leading
  # '- ') so an enum row quoting the field name in prose cannot satisfy it.
  SLEDGER_BLOCK=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v sb="$re_sb" -v se="$re_se" '
    $0 ~ sb { ins=1; buf=""; next }
    $0 ~ se { if (ins) last=buf; ins=0; next }
    ins { buf = buf $0 "\n" }
    END { printf "%s", last }')
  sl_worst=$(printf '%s\n' "$SLEDGER_BLOCK" | grep -cE '^[[:space:]]*-?[[:space:]]*worst_agent_failure:[[:space:]]*.+')
  sl_adv=$(printf '%s\n' "$SLEDGER_BLOCK" | grep -cE '^[[:space:]]*-?[[:space:]]*self_adversarial:[[:space:]]*.+')
  sl_critic=$(printf '%s\n' "$SLEDGER_BLOCK" | grep -cE '^[[:space:]]*-?[[:space:]]*critic_diff:[[:space:]]*.+')
  if [ "$sl_worst" -lt 1 ] || [ "$sl_adv" -lt 1 ] || [ "$sl_critic" -lt 1 ]; then
    GATE8_VIOLATION="suppression_ledger fence is present but missing a required line (found worst_agent_failure=$sl_worst self_adversarial=$sl_adv critic_diff=$sl_critic, need >=1 each) — the ledger must name the single worst agent-caused friction, record that the self-incrimination pass ran, and record the conditional critic_diff outcome before Stage 3"
  else
    # [PR #703] Gate-8b (Ledger-laundering floor, issue #701): Gate-8 originally proved
    # only that a ledger exists. Re-derive cheap deterministic adverse signals
    # from the live transcript so a ledger cannot claim a clean/no-failure path
    # while the transcript itself carries multiple user corrections or errors.
    GATE8_SIGNAL_TOLERANCE=1
    sl_content_error_re='Exit code [1-9]|command terminated|js_error|FATAL|No such file|denied|BLOCKED|Usage:([[:space:]]|$)'
    sl_user_correction_re="아니|그거 아니야|하지 마|그거 말고|(^|[^A-Za-z])no([^A-Za-z]|$)|don't|(^|[^A-Za-z])stop([^A-Za-z]|$)|라니까|하라고 했잖아|그게 아니라|내 말은|다시|I said |not that|하라니까|왜 .*안 하고|왜 .*했어|that's not what I asked"
    live_sl_ie_count=$(jq -r '
      def message_content: .message.content // .content // empty;
      def nested_tool_results:
        message_content as $c
        | if ($c|type) == "array" then
            $c[]? | select(type == "object" and .type == "tool_result")
          else empty end;
      (
        select(type == "object" and .type == "tool_result" and .is_error == true),
        (select(type == "object") | nested_tool_results | select(.is_error == true))
      ) | 1
    ' "$TRANSCRIPT_PATH" 2>/dev/null | wc -l | tr -d '[:space:]')
    live_sl_ie_count=${live_sl_ie_count:-0}
    live_sl_ce_count=$(jq -r --arg re "$sl_content_error_re" '
      def message_content: .message.content // .content // empty;
      def block_text:
        (.message.content // .content // .text // empty) as $c
        | if ($c|type) == "array" then
            $c[]? | if type == "object" then (.text? // empty) else tostring end
          elif ($c|type) == "string" then $c
          else empty end;
      def nested_tool_results:
        message_content as $c
        | if ($c|type) == "array" then
            $c[]? | select(type == "object" and .type == "tool_result")
          else empty end;
      (
        select(type == "object" and .type == "tool_result" and ([block_text] | join("\n") | test($re))),
        (select(type == "object") | nested_tool_results | select(([block_text] | join("\n") | test($re))))
      ) | 1
    ' "$TRANSCRIPT_PATH" 2>/dev/null | wc -l | tr -d '[:space:]')
    live_sl_ce_count=${live_sl_ce_count:-0}
    live_sl_tool_signal_count=$(jq -r --arg re "$sl_content_error_re" '
      def message_content: .message.content // .content // empty;
      def block_text:
        (.message.content // .content // .text // empty) as $c
        | if ($c|type) == "array" then
            $c[]? | if type == "object" then (.text? // empty) else tostring end
          elif ($c|type) == "string" then $c
          else empty end;
      def nested_tool_results:
        message_content as $c
        | if ($c|type) == "array" then
            $c[]? | select(type == "object" and .type == "tool_result")
          else empty end;
      select(type == "object" and (
        (.type == "tool_result" and ((.is_error == true) or ([block_text] | join("\n") | test($re))))
        or ([nested_tool_results | select((.is_error == true) or ([block_text] | join("\n") | test($re)))] | length > 0)
      )) | 1
    ' "$TRANSCRIPT_PATH" 2>/dev/null | wc -l | tr -d '[:space:]')
    live_sl_tool_signal_count=${live_sl_tool_signal_count:-0}
    live_sl_uc_count=$(jq -r --arg re "$sl_user_correction_re" '
      def human_text_payload:
        (.message.content // .content // empty) as $c
        | if ($c|type) == "array" then
            $c[]? | if type == "object" then
              if ((.type? // "text") == "text") then (.text? // empty) else empty end
            else tostring end
          elif ($c|type) == "string" then $c
          else empty end;
      select(type == "object" and .type != "tool_result" and (.message.role == "user" or .role == "user" or .type == "user") and ([human_text_payload] | join("\n") | test($re; "i"))) | 1
    ' "$TRANSCRIPT_PATH" 2>/dev/null | wc -l | tr -d '[:space:]')
    live_sl_uc_count=${live_sl_uc_count:-0}
    live_sl_signal_count=$(( live_sl_tool_signal_count + live_sl_uc_count ))

    sl_clean_like=false
    sl_worst_line=$(printf '%s\n' "$SLEDGER_BLOCK" | grep -iE '^[[:space:]]*-?[[:space:]]*worst_agent_failure:[[:space:]]*.+' | tail -n 1)
    sl_adv_line=$(printf '%s\n' "$SLEDGER_BLOCK" | grep -iE '^[[:space:]]*-?[[:space:]]*self_adversarial:[[:space:]]*.+' | tail -n 1)
    sl_adv_clean=false
    sl_worst_clean_phrase=false
    if printf '%s\n' "$sl_adv_line" | grep -qiE '(concurred.*(nothing|no ))|nothing omitted|nothing to omit|nothing softened|no omission|no suppression'; then
      sl_adv_clean=true
    fi
    if printf '%s\n' "$sl_worst_line" | grep -qiE 'clean path|no failure|none found|no painful|nothing painful|nothing to omit|nothing omitted|(synthetic|fixture|ledger-presence).*(no real session|not a real session)|(no real session|not a real session).*(synthetic|fixture|ledger-presence)'; then
      sl_worst_clean_phrase=true
    fi
    if printf '%s\n' "$sl_worst_line" \
      | grep -qiE 'disposition:[[:space:]]*none-found|no painful agent failure|nothing painful|no real session to scan|nothing to omit|nothing omitted'; then
      sl_clean_like=true
    elif [ "$sl_adv_clean" = "true" ] && [ "$sl_worst_clean_phrase" = "true" ]; then
      sl_clean_like=true
    fi

    if [ "$sl_clean_like" = "true" ] && [ "$live_sl_signal_count" -gt "$GATE8_SIGNAL_TOLERANCE" ]; then
      GATE8_VIOLATION="suppression_ledger claims a clean/no-failure path while a live transcript scan finds $live_sl_signal_count adverse signal(s) (tool_event=$live_sl_tool_signal_count is_error=$live_sl_ie_count content_error=$live_sl_ce_count user_correction_marker=$live_sl_uc_count, tolerance=$GATE8_SIGNAL_TOLERANCE) — re-run the self-incrimination pass and surface or justify the signals instead of laundering them as none-found"
    fi

    # [issue #715] Gate-8c (Critic self-skip floor). The externalized critic tier
    # is the only anti-concealment mechanism the self-correction literature shows
    # works (external signal, not self-feedback), so an agent must not be free to
    # self-skip it whenever real agent-caused friction exists. The tier predicate
    # (stage1-2-analysis.md) fires when a friction_event required user correction;
    # block a 'critic_diff: not-run' that coexists with live user-correction
    # markers — this converts the run-the-critic guidance from unenforced
    # self-feedback into a deterministic format gate.
    #
    # PRECISION: Gate-8c uses a TIGHTER regex than Gate-8b's broad
    # sl_user_correction_re. Gate-8b is double-guarded by sl_clean_like, so its
    # broad regex (bare "no"/"stop"/"다시") is acceptable there. Gate-8c is a
    # single condition, so the broad regex would fire on benign phrasings
    # ("no problem", "다시 설명해줘", "stop the dev server" — a code-reviewer probe
    # matched 7/7 benign) and, because Gate-8c scans the WHOLE session, force the
    # critic on any busy session regardless of real correction. Restrict to
    # explicit multi-word redirect/correction phrases so the floor keys on genuine
    # correction density, not session length. Threshold reuses
    # GATE8_SIGNAL_TOLERANCE (>1): one stray strong marker is absorbed, so a single
    # genuine correction may still mark not-run (accepted recall gap). Guarded by
    # -z GATE8_VIOLATION so a more specific Gate-8b laundering message wins.
    if [ -z "$GATE8_VIOLATION" ]; then
      # This is a HARD BLOCK, so it must fire only on a LOW-false-positive signal
      # (issue #722). Because the consequence asymmetry favours a false-negative
      # (a missed self-skip is still nudged by the prose self-incrimination layer)
      # over a false-positive (blocking a clean retrospect erodes trust in the
      # gate), the set is deliberately narrowed to near-unambiguous redirects —
      # NOT an exhaustive correction detector. Everyday Korean conversational
      # tokens were measured as high-FP (7/10 benign phrases matched) and dropped:
      # '그거 아니야' (benign 그거 아니야? 맞는데), '그거 말고' (그거 말고도),
      # '그게 아니라' (그게 아니라 그냥), '내 말은' (내 말은 ~ 뜻), '하라니까'
      # (하라니까 바로 했어요), '왜 .*안 하고' (greedy span over narration),
      # '(^|[^A-Za-z])I said ' (benign English recap "I said I would …").
      # Kept tokens verified low-FP via jq probe: benign set 13/13 no-match
      # (incl. reportive '내가 말한 건 아니지만 …' forms — a greedy '내가 말한 건
      # .*아니' candidate was rejected for matching exactly those, code-reviewer
      # #722), genuine corrections 3/3 match. The dropped tokens remain the prose
      # self-incrimination layer's job, not this hard block's.
      # NEW-1 (#772): "did the critic run" is TRANSCRIPT-DERIVED (CRITIC_RAN, a
      # critic_roots block in a subagent tool_result), NOT the agent's critic_diff
      # label. Eligibility fires on EITHER a hard silent-pass candidate
      # (GATE9_HARD_COUNT>0) OR the strong-correction floor (LIVE_STRONG_UC_COUNT
      # > tolerance) — both hoisted above. When eligible AND the critic did not
      # actually run, block REGARDLESS of the label (not-run/none/checked): a
      # `none`/`checked` string is no longer a dodge. LIVE_STRONG_UC_COUNT is the
      # same value the old local jq computed (regex per the comment above).
      live_sl_strong_uc_count="$LIVE_STRONG_UC_COUNT"
      eligible=false
      if [ "${GATE9_HARD_COUNT:-0}" -gt 0 ] || [ "$live_sl_strong_uc_count" -gt "$GATE8_SIGNAL_TOLERANCE" ]; then
        eligible=true
      fi
      if [ "$eligible" = "true" ] && [ "$CRITIC_RAN" != "true" ]; then
        GATE8_VIOLATION="critic tier owed (eligible: hard silent-pass candidate present and/or user corrections; gate9_hard_candidate_count=${GATE9_HARD_COUNT:-0} strong_user_correction_count=$live_sl_strong_uc_count, tolerance=$GATE8_SIGNAL_TOLERANCE) but no critic_roots block found in any transcript subagent return — the externalized critic must actually run; a 'none'/'checked'/'not-run' label without a real critic subagent return does not satisfy the tier. Spawn the READ-ONLY critic, brief it with the verbatim worst_agent_failure, and record its critic_roots block (the subagent's own return is the oracle, not a pasted fence)"
      fi
    fi
  fi
fi
fi

# Gate-11 (Remedy-Reach Receipt, issue #917). A remedy only works on the surface
# it lives on, and the recurring failure is to diagnose a gap correctly, place
# the remedy where it cannot fire, then describe the shortfall as legitimate.
# Whenever the report proposes a remedy-layer action, it must carry a
# 'retrospect:remedy_reach' fence with at least one row stating reach / surface /
# unreached axis. Structure only: this gate proves the reach question was
# answered on the record, not that the answer is right.
#
# Same Stage-4 carve-out and fence discipline as Gate-8 (a positive-presence
# gate must not retroactively block a completed cycle).
#
# Trigger and coverage read different oracles because neither one alone is
# sound. The card carries counts, not identities, so it cannot say WHICH
# findings owe a row — two remedy findings would satisfy a count-based check
# with a single row naming whichever was easiest to answer for. But
# REMEDY_FINDING_IDS alone cannot say WHETHER a row is owed: a card declaring a
# remedy total while no findings-table row parses as a remedy action leaves the
# ID list empty and the gate silent, which is exactly the self-inconsistent
# report that most needs asking. So the card's remedy total decides whether a
# receipt is required, and REMEDY_FINDING_IDS decides who it must name.
# Grepping the report body for the action tokens works for neither — the card
# enumerates all six by name (`- memory: 0`), which fires on the 0-friction path.
GATE11_VIOLATION=""
if [ "$STAGE4_AFTER_REPORT" != "1" ]; then
  rr_card_total=$(printf '%s\n' "$DIST_CARD" | awk -F': *' '
    /^-[[:space:]]*(memory|claude_md_draft|skill_idea|hook_code):/ {
      v=$2; gsub(/[^0-9]/, "", v); if (v != "") t += v
    }
    END { print t + 0 }')
  if [ "${#REMEDY_FINDING_IDS[@]}" -gt 0 ] || [ "$rr_card_total" -gt 0 ]; then
    re_rb='^[[:space:]]*<!--[[:space:]]*retrospect:remedy_reach begin[[:space:]]*-->[[:space:]]*$'
    re_re='^[[:space:]]*<!--[[:space:]]*retrospect:remedy_reach end[[:space:]]*-->[[:space:]]*$'
    rr_begin=$(printf '%s\n' "$MOST_RECENT_BLOCK" | grep -cE "$re_rb" || true)
    rr_malformed=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v sb="$re_rb" -v se="$re_re" '
      $0 ~ sb { if (ins) nested=1; ins=1; next }
      $0 ~ se { ins=0; next }
      END { print (ins || nested) ? 1 : 0 }')
    if [ "$rr_begin" -lt 1 ]; then
      rr_owed="${REMEDY_FINDING_IDS[*]:-none parsed from the findings table, but the distribution card declares $rr_card_total}"
      GATE11_VIOLATION="Stage 3 report proposes a remedy-layer action (finding(s) ${rr_owed}) but has no '<!-- retrospect:remedy_reach begin/end -->' fence (issue #917) — for each such finding state whether the remedy's surface fires where the finding was uttered: '- finding #N: reach=full|partial|none | surface: <layer> | unreached: <axis or none> | worse_axis: yes|no|na'"
    elif [ "$rr_malformed" -gt 0 ]; then
      GATE11_VIOLATION="remedy_reach fence is malformed (an unterminated or nested 'retrospect:remedy_reach begin') — emit exactly one well-formed begin/end fence before Stage 3"
    elif [ "$rr_begin" -gt 1 ]; then
      GATE11_VIOLATION="Stage 3 report has $rr_begin remedy_reach fences — emit exactly one; multiple fences let a reaching remedy mask a non-reaching one"
    else
      RR_BLOCK=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v sb="$re_rb" -v se="$re_re" '
        $0 ~ sb { ins=1; buf=""; next }
        $0 ~ se { if (ins) last=buf; ins=0; next }
        ins { buf = buf $0 "\n" }
        END { printf "%s", last }')
      # Every remedy-layer finding needs its OWN complete row, and complete means
      # all four fields the contract names. Each one carries a distinct load:
      # `reach=full` alone stands in for an axis nobody wrote down; a missing
      # `surface:` hides the action-to-layer mismatch this gate exists to expose
      # (a `hook_code` remedy whose surface is a MEMORY.md entry); and
      # `worse_axis:` is where the author has to say out loud that the axis the
      # remedy cannot reach is the larger-damage one. Accepting a row without
      # them would reproduce this PR's own defect class — a contract asking for
      # more than the gate checks.
      #
      # Each label is anchored to its delimiter — right after 'finding #N:' or
      # after the '|' that closes the previous field. An unanchored '.*reach='
      # accepts 'unreach=full', 'nonsurface:', and 'not_unreached:', which is
      # the same substring-boundary hole the receipt exists to close.
      rr_row_tail='[[:space:]]*\|[[:space:]]*surface:[[:space:]]*[^|]*[^[:space:]|][[:space:]]*\|[[:space:]]*unreached:[[:space:]]*[^|]*[^[:space:]|][[:space:]]*\|[[:space:]]*worse_axis:[[:space:]]*(yes|no|na)([^A-Za-z]|$)'
      rr_missing=""
      for rr_id in "${REMEDY_FINDING_IDS[@]}"; do
        if ! printf '%s\n' "$RR_BLOCK" | grep -qE \
          "^[[:space:]]*-?[[:space:]]*finding[[:space:]]*#${rr_id}:[[:space:]]*reach=(full|partial|none)${rr_row_tail}"; then
          rr_missing="$rr_missing #$rr_id"
        fi
      done
      # Card-only trigger: no finding IDs parsed, so nobody can be named. Ask
      # for one well-formed row rather than passing the inconsistency through.
      if [ "${#REMEDY_FINDING_IDS[@]}" -eq 0 ] && ! printf '%s\n' "$RR_BLOCK" | grep -qE \
        "^[[:space:]]*-?[[:space:]]*finding[[:space:]]*#[0-9]+:[[:space:]]*reach=(full|partial|none)${rr_row_tail}"; then
        rr_missing=" (card declares $rr_card_total remedy action(s); no findings-table row parsed as one)"
      fi
      if [ -n "$rr_missing" ]; then
        GATE11_VIOLATION="remedy_reach fence is present but has no complete row for finding(s)${rr_missing} — each finding proposing memory / claude_md_draft / skill_idea / hook_code needs its own '- finding #N: reach=full|partial|none | surface: <layer> | unreached: <axis or none> | worse_axis: yes|no|na', with all four fields present; one row cannot answer for a sibling finding whose remedy lives on a different surface"
      fi
    fi
  fi
fi

# Gate-9 (silent-pass candidate coverage, issue #772). For each HARD grep-detected
# silent-pass candidate (from the hoisted scan), the report MUST cover it via
# EITHER a structured `covers: <class-id>` token in a findings-table Rationale
# cell (mirrors the backing_repo: parse) OR the class-id listed inside a
# '<!-- retrospect:dismissed_candidates begin/end -->' fence. A bare prose mention
# of the keyword (e.g. "credential") does NOT satisfy coverage — the `covers:`
# token is required (H3 precision). SOFT matches are front-load hints and NEVER
# block, so only field-2 == "hard" rows are evaluated here.
declare -a GATE9_VIOLATIONS=()
if [ -n "$SILENT_PASS_MATCHES" ]; then
  DISMISSED_BLOCK=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk '
    /<!--[[:space:]]*retrospect:dismissed_candidates begin[[:space:]]*-->/ { capture=1; next }
    /<!--[[:space:]]*retrospect:dismissed_candidates end[[:space:]]*-->/ { capture=0 }
    capture { print }
  ')
  # Scanner rows are `<class-id>\t<severity>\t<evidence-snippet>`; the evidence
  # field is not consumed by this gate, so `_` absorbs it (and any remainder).
  while IFS=$'\t' read -r sp_id sp_sev _; do
    [ -z "$sp_id" ] && continue
    [ "$sp_sev" = "hard" ] || continue
    sp_covered=false
    # (a) `covers: <class-id>` structured token, class-id bounded so a longer id
    #     with the same prefix cannot false-satisfy it.
    if printf '%s\n' "$MOST_RECENT_BLOCK" | grep -qE "covers:[[:space:]]*${sp_id}([^A-Za-z0-9_-]|$)"; then
      sp_covered=true
    fi
    # (b) class-id explicitly listed inside the dismissed_candidates fence.
    if [ "$sp_covered" = "false" ] && [ -n "$DISMISSED_BLOCK" ] \
       && printf '%s\n' "$DISMISSED_BLOCK" | grep -qF "$sp_id"; then
      sp_covered=true
    fi
    if [ "$sp_covered" = "false" ]; then
      GATE9_VIOLATIONS+=("silent-pass candidate '${sp_id}' (hard) neither reported (covers: ${sp_id}) nor dismissed")
    fi
  done <<< "$SILENT_PASS_MATCHES"
fi

# Gate-10 (critic-root coverage, issue #772). Fires only when the critic tier is
# OWED (eligible = hard candidate OR strong-correction floor). The eligible-AND-
# critic-DID-NOT-run case is already blocked by Gate-8c (NEW-1); Gate-10 handles
# the critic-RAN-WITH-roots case: every critic root-id parsed from the subagent's
# own transcript return must be covered in the findings OR explicitly folded with
# a non-empty reason (`folded: <root-id> because <reason>`). Empty roots (critic
# ran, 0 roots) → accept (SL30 / H1). A bare `folded: <id>` with no reason does
# NOT count as coverage.
GATE10_ELIGIBLE=false
if [ "${GATE9_HARD_COUNT:-0}" -gt 0 ] || [ "${LIVE_STRONG_UC_COUNT:-0}" -gt "${GATE8_SIGNAL_TOLERANCE:-1}" ]; then
  GATE10_ELIGIBLE=true
fi
declare -a GATE10_VIOLATIONS=()
if [ "$GATE10_ELIGIBLE" = "true" ] && [ "$CRITIC_RAN" = "true" ] && [ -n "$CRITIC_ROOTS" ]; then
  # Strip the display-only `retrospect:critic_roots` fence before matching. That
  # fence lists every root verbatim for display (stage3-reporting contract), so
  # leaving it in `MOST_RECENT_BLOCK` would let any root trivially satisfy
  # coverage without a real findings mention or fold — making Gate-10 a no-op.
  # Mirrors the Gate-9 DISMISSED_BLOCK strip (this awk is the inverse: it drops
  # the fenced region and keeps the rest).
  GATE10_MATCH_BLOCK=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk '
    /<!--[[:space:]]*retrospect:critic_roots begin[[:space:]]*-->/ { skip=1; next }
    /<!--[[:space:]]*retrospect:critic_roots end[[:space:]]*-->/ { skip=0; next }
    !skip { print }
  ')
  while IFS= read -r root_id; do
    [ -z "$root_id" ] && continue
    root_covered=false
    # Valid fold: `folded: <root-id> because <non-empty reason>`.
    if printf '%s\n' "$GATE10_MATCH_BLOCK" | grep -qE "folded:[[:space:]]*${root_id}[[:space:]]+because[[:space:]]+[^[:space:]]"; then
      root_covered=true
    else
      # Genuine coverage: the root-id appears on a line that is NOT a `folded:
      # <id>` line (a findings-table row / covers-style mention). Excluding the
      # folded line prevents a reason-less `folded: <id>` from masquerading as
      # coverage.
      other=$(printf '%s\n' "$GATE10_MATCH_BLOCK" | grep -F "$root_id" | grep -vE "folded:[[:space:]]*${root_id}([^A-Za-z0-9_-]|$)")
      [ -n "$other" ] && root_covered=true
    fi
    if [ "$root_covered" = "false" ]; then
      GATE10_VIOLATIONS+=("critic root '${root_id}' neither covered in findings nor folded-with-reason")
    fi
  done <<< "$CRITIC_ROOTS"
fi

# Gate-12 (denied-action coverage, issue #1013). Every other lane keys on
# something that HAPPENED. An action the user refused has no outcome, so it
# leaves no error, no correction and no confession — and selection by ease of
# recall never reaches it. In the motivating session the scan was genuinely
# exhaustive (13,324 records, all 56 is_error bodies read individually) and all
# five friction slots still went to already-confessed incidents, while the
# largest-damage item (a rejected approval reopened by an adjacent utterance,
# ~295M objects) appeared in no lane.
#
# The oracle is the LIVE TRANSCRIPT, not the agent's fence — same reason Gate-10
# reads the critic's subagent return rather than a pasted block: a report cannot
# self-certify that it enumerated what it was supposed to enumerate. The scan is
# the shared structural enumerator pre-scan lane 6 uses
# (hooks/_lib/_transcript.py::scan_user_rejections — toolDenialKind plus
# is_error plus the runtime's fixed refusal sentence), so lane and gate cannot
# drift into two definitions of "rejected".
#
# SUPPLY GATE, deliberately weak: one disposed row clears it. It forces the
# unconfessed candidates onto the record; it cannot judge which one deserved a
# finding slot, and a single `disposition: dismissed` satisfies the letter of it
# (issue #1013's own stated limit). The real lever stays the externalized critic
# (Gate-8c/Gate-10); this is the cheap backstop for when the critic does not run.
#
# Same Stage-4 carve-out and fence discipline as Gate-8/Gate-11 — a
# positive-presence gate must not retroactively block a completed cycle.
#
# Fail-open when the scan cannot run at all: no python3, an unreadable
# transcript, or any scanner error yields 0 and the gate stays silent.
#
# An OVERSIZE transcript is the one exception (issue #1231). It used to fold
# into that same 0, which made "the report owes nothing" and "the oracle never
# read the session" the same answer — and the bound is reached only by long
# sessions, which is where refusals accumulate. The scan now says -1 for it, and
# the gate asks for an acknowledgement rather than a receipt: it cannot name a
# count, so it cannot demand a row per rejection, but it can refuse to let the
# lane report zero from a scan that never ran.
GATE12_VIOLATION=""
if [ "$STAGE4_AFTER_REPORT" != "1" ] && command -v python3 >/dev/null 2>&1; then
  DENIED_COUNT=$(python3 - "$_SP_LIB" "$TRANSCRIPT_PATH" "$TELEMETRY_SESSION_ID" 2>/dev/null <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
try:
    from _transcript import scan_cursor_path, scan_user_rejections
    # Resumable, keyed on this hook and the session: each Stop reads only the
    # bytes appended since the previous one (an empty session id disables
    # persistence and the scan runs under the per-call budget alone).
    cursor = scan_cursor_path("retrospect-mix-check", sys.argv[3] if len(sys.argv) > 3 else "")
    recs = scan_user_rejections(sys.argv[2], cursor_path=cursor)
    # -1 is INDETERMINATE (more unread bytes than this call's budget), kept
    # distinct from 0 so the gate can tell "none" from "not caught up".
    print(-1 if recs is None else len(recs))
except Exception:
    print(0)
PY
)
  DENIED_COUNT=$(printf '%s' "$DENIED_COUNT" | tr -cd '0-9-')
  case "$DENIED_COUNT" in
    -1) ;;                       # the only negative the scanner emits
    ''|*[!0-9]*) DENIED_COUNT=0 ;;
  esac
  DENIED_INDETERMINATE=0
  if [ "$DENIED_COUNT" = "-1" ]; then
    DENIED_INDETERMINATE=1
    DENIED_COUNT=0
  fi
  if [ "$DENIED_COUNT" -gt 0 ] || [ "$DENIED_INDETERMINATE" = "1" ]; then
    re_db='^[[:space:]]*<!--[[:space:]]*retrospect:denied_actions begin[[:space:]]*-->[[:space:]]*$'
    re_de='^[[:space:]]*<!--[[:space:]]*retrospect:denied_actions end[[:space:]]*-->[[:space:]]*$'
    da_begin=$(printf '%s\n' "$MOST_RECENT_BLOCK" | grep -cE "$re_db" || true)
    da_malformed=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v sb="$re_db" -v se="$re_de" '
      $0 ~ sb { if (ins) nested=1; ins=1; next }
      $0 ~ se { ins=0; next }
      END { print (ins || nested) ? 1 : 0 }')
    if [ "$da_begin" -lt 1 ] && [ "$DENIED_INDETERMINATE" = "1" ]; then
      GATE12_VIOLATION="the rejection scan has not caught up with this session's transcript — more than REJECTION_SCAN_MAX_BYTES (20 MiB) of it was still unread at this Stop, so 0 denied actions is what a scan that never ran returns, not a finding (issue #1231); the Stage 3 report must still carry one '<!-- retrospect:denied_actions begin/end -->' fence, holding either the rows recovered by an unbounded re-scan or the line '- scan: indeterminate | rescan: done|skipped (<reason>)'"
    elif [ "$da_begin" -lt 1 ]; then
      GATE12_VIOLATION="the live transcript carries $DENIED_COUNT structurally-rejected tool call(s) but the Stage 3 report has no '<!-- retrospect:denied_actions begin/end -->' fence (issue #1013) — a refused action has no outcome and therefore no confession, which is precisely why the friction scan misses it; emit pre-scan lane 6 with one row per rejection: '- denied: \"<verbatim question>\" | tool: <name> | source: user_rejection | confessed: yes|no | disposition: promoted (finding #N)|noted|dismissed (<reason>)'"
    elif [ "$da_malformed" -gt 0 ]; then
      GATE12_VIOLATION="denied_actions fence is malformed (an unterminated or nested 'retrospect:denied_actions begin') — emit exactly one well-formed begin/end fence before Stage 3"
    elif [ "$da_begin" -gt 1 ]; then
      GATE12_VIOLATION="Stage 3 report has $da_begin denied_actions fences — emit exactly one; multiple fences let a disposed rejection mask an ignored one"
    else
      DA_BLOCK=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk -v sb="$re_db" -v se="$re_de" '
        $0 ~ sb { ins=1; buf=""; next }
        $0 ~ se { if (ins) last=buf; ins=0; next }
        ins { buf = buf $0 "\n" }
        END { printf "%s", last }')
      # "Ignored ALL of them" is the block condition: one disposed row clears the
      # gate. The WHOLE row is matched, not just its disposition — a lone
      # `disposition: noted` line carries no evidence that any rejection was
      # actually enumerated, and buying the gate off with one such line is the
      # same silent drop the lane exists to prevent. Anchoring at the list
      # marker also keeps a prose sentence containing the word "dismissed"
      # from standing in for a row.
      da_row_re='^[[:space:]]*-[[:space:]]*denied:[[:space:]]*".*"[[:space:]]*\|'
      da_row_re="$da_row_re"'[[:space:]]*tool:[[:space:]]*[A-Za-z][A-Za-z0-9_-]*[[:space:]]*\|'
      da_row_re="$da_row_re"'[[:space:]]*source:[[:space:]]*user_rejection[[:space:]]*\|'
      da_row_re="$da_row_re"'[[:space:]]*confessed:[[:space:]]*(yes|no)[[:space:]]*\|'
      da_row_re="$da_row_re"'[[:space:]]*disposition:[[:space:]]*(promoted|noted|dismissed)([^A-Za-z]|$)'
      # On an INDETERMINATE scan the gate cannot name a count, so it cannot
      # demand a row per rejection. What it can demand is an affirmative
      # sentence — either recovered rows, or a stated re-scan outcome. Both
      # shapes are unreachable from an empty fence, which is the shape a
      # silently-zero lane produces (issue #1231).
      da_indet_re='^[[:space:]]*-[[:space:]]*scan:[[:space:]]*indeterminate[[:space:]]*\|'
      da_indet_re="$da_indet_re"'[[:space:]]*rescan:[[:space:]]*(done|skipped)([^A-Za-z]|$)'
      if [ "$DENIED_INDETERMINATE" = "1" ] \
         && ! printf '%s\n' "$DA_BLOCK" | grep -qE "$da_row_re" \
         && ! printf '%s\n' "$DA_BLOCK" | grep -qE "$da_indet_re"; then
        GATE12_VIOLATION="denied_actions fence is present but says nothing about a scan that could not run (more than REJECTION_SCAN_MAX_BYTES unread at this Stop, issue #1231) — carry either the rows an unbounded re-scan recovered, or the line '- scan: indeterminate | rescan: done|skipped (<reason>)'; an empty fence is what a silently-zero lane looks like"
      elif [ "$DENIED_INDETERMINATE" != "1" ] && ! printf '%s\n' "$DA_BLOCK" | grep -qE "$da_row_re"; then
        GATE12_VIOLATION="denied_actions fence is present but carries no schema-valid disposed row for the $DENIED_COUNT transcript rejection(s) — a row must be '- denied: \"<verbatim question>\" | tool: <name> | source: user_rejection | confessed: yes|no | disposition: promoted (finding #N)|noted|dismissed (<reason>)'; a bare 'disposition:' line is not a row, and an enumerated-but-undisposed rejection is the same silent drop the lane exists to prevent"
      fi
    fi
  fi
fi

# Decide block.
should_block=false
reason_parts=()

if [ "$GATE_1" = "FAIL" ]; then
  should_block=true
  reason_parts+=("Gate-1 verdict in distribution card = FAIL")
fi
if [ "$GATE_2" = "FAIL" ]; then
  should_block=true
  reason_parts+=("Gate-2 verdict in distribution card = FAIL")
fi
if [ "$GATE_3" = "FAIL" ]; then
  should_block=true
  reason_parts+=("Gate-3 verdict in distribution card = FAIL")
fi
# Gate-4 (External-Repo Authorization): FAIL or missing + ⚠ EXTERNAL: prefix in Rationale → block.
# PASS or WARN → pass (WARN means external=true but per-action approval is procedural at Stage 4).
# NA → pass (no upstream_feedback or issue findings).
if [ "$GATE_4" = "FAIL" ]; then
  should_block=true
  reason_parts+=("Gate-4 verdict in distribution card = FAIL")
fi
# If gate_4_verdict is absent but the Rationale contains the ⚠ EXTERNAL: marker, block:
# Stage 2.5 should have written gate_4_verdict: WARN for external findings; its absence
# with an EXTERNAL marker indicates Stage 2.5 was skipped or incomplete.
if [ -z "$GATE_4" ]; then
  if printf '%s\n' "$MOST_RECENT_BLOCK" | grep -qF '⚠ EXTERNAL:'; then
    should_block=true
    reason_parts+=("gate_4_verdict key absent but ⚠ EXTERNAL: marker found in Rationale — Stage 2.5 Gate-4 may have been skipped")
  fi
fi
if [ "$GATE_1" = "MISSING" ] || [ "$GATE_2" = "MISSING" ]; then
  should_block=true
  reason_parts+=("distribution card missing gate_1_verdict or gate_2_verdict key")
fi
if [ "${#GATE4B_VIOLATIONS[@]}" -gt 0 ]; then
  should_block=true
  for v in "${GATE4B_VIOLATIONS[@]}"; do
    reason_parts+=("Gate-4b: $v")
  done
fi
if [ "${#GATE1_VIOLATIONS[@]}" -gt 0 ]; then
  should_block=true
  for v in "${GATE1_VIOLATIONS[@]}"; do
    reason_parts+=("Gate-1: $v")
  done
fi
if [ "${#GATE2_VIOLATIONS[@]}" -gt 0 ]; then
  should_block=true
  for v in "${GATE2_VIOLATIONS[@]}"; do
    reason_parts+=("Gate-2: $v")
  done
fi
if [ "${#GATE3_VIOLATIONS[@]}" -gt 0 ]; then
  should_block=true
  for v in "${GATE3_VIOLATIONS[@]}"; do
    reason_parts+=("Gate-3: $v")
  done
fi
if [ "${#SHORT_ROW_VIOLATIONS[@]}" -gt 0 ]; then
  should_block=true
  for v in "${SHORT_ROW_VIOLATIONS[@]}"; do
    reason_parts+=("Schema: $v")
  done
fi
if [ -n "$GATE7_VIOLATION" ]; then
  should_block=true
  reason_parts+=("Gate-7: $GATE7_VIOLATION")
fi
if [ -n "$GATE8_VIOLATION" ]; then
  should_block=true
  reason_parts+=("Gate-8: $GATE8_VIOLATION")
fi
if [ "${#GATE9_VIOLATIONS[@]}" -gt 0 ]; then
  should_block=true
  for v in "${GATE9_VIOLATIONS[@]}"; do
    reason_parts+=("Gate-9: $v")
  done
fi
if [ "${#GATE10_VIOLATIONS[@]}" -gt 0 ]; then
  should_block=true
  for v in "${GATE10_VIOLATIONS[@]}"; do
    reason_parts+=("Gate-10: $v")
  done
fi
if [ -n "$GATE11_VIOLATION" ]; then
  should_block=true
  reason_parts+=("Gate-11: $GATE11_VIOLATION")
fi
if [ -n "$GATE12_VIOLATION" ]; then
  should_block=true
  reason_parts+=("Gate-12: $GATE12_VIOLATION")
fi

# Gate-4b demotions ride along with a block reason when one exists (they are a
# separate finding about separate rows, and dropping them because some other
# gate fired would lose them) and go out as a systemMessage otherwise.
GATE4B_DEMOTION_TEXT=""
if [ "${#GATE4B_DEMOTIONS[@]}" -gt 0 ]; then
  for v in "${GATE4B_DEMOTIONS[@]}"; do
    GATE4B_DEMOTION_TEXT="${GATE4B_DEMOTION_TEXT}
- ${v}"
  done
  GATE4B_DEMOTION_TEXT="[retrospect-mix-check] Gate-4b demotion — visibility unresolved, Stop not blocked:${GATE4B_DEMOTION_TEXT}"
  if [ "$should_block" = "true" ]; then
    reason_parts+=("Gate-4b (advisory, not the block cause): $GATE4B_DEMOTION_TEXT")
  fi
fi

if [ "$should_block" = "true" ]; then
  _log="$(praxis_resolve_writable logs retrospect-mix-blocked.log)"
  command -v praxis_rotate_log >/dev/null 2>&1 && praxis_rotate_log "$_log"
  echo "$(date -Iseconds) session=$SESSION_ID blocked_retrospect_mix_check" >> "$_log" || true

  # Build reason string with ' | ' separator.
  reason=""
  for part in "${reason_parts[@]}"; do
    if [ -z "$reason" ]; then
      reason="$part"
    else
      reason="$reason | $part"
    fi
  done

  full_reason="Retrospect mix-check gate triggered. ${reason}. Fix guide: Gate-1 → relabel finding category via skills/retrospect/references/stage1-2-analysis.md; Gate-2 → supply either (a) 5-line 'not <action>: <reason>' rationale (Schema A) or (b) 1-2 'not-others: <dim-tags>' lines (Schema B, issue #285) in Stage 2.5; Gate-3 verdict → return to skills/retrospect/references/stage2.5-audit.md and re-evaluate evidence robustness for 2-action findings; Gate-3 backing_repo → return to skills/retrospect/references/stage1-2-analysis.md action assignment (step 7) and add 'backing_repo: <owner/repo>' to Rationale cell; Gate-4 → return to skills/retrospect/references/stage2.5-audit.md Gate-4 and re-run external-repo classification; ensure gate_4_verdict is emitted in the distribution card; Gate-7 → post-compaction session: emit a '<!-- retrospect:transcript_receipt begin/end -->' fence with the real full-transcript scan output (or the 'retrospect:transcript_receipt_skipped: transcript unreachable' line when the jsonl is genuinely unreachable); include both 'is_error_count: N' and 'content_error_count: N' fields; when content_error_count > 0 add a '<!-- retrospect:content_error_enum begin/end -->' block with per-signal promote/note/dismiss disposition rows (issue #670); Gate-8 → emit a '<!-- retrospect:suppression_ledger begin/end -->' fence carrying 'worst_agent_failure:', 'self_adversarial:', and 'critic_diff:' lines (the Stage 2 self-incrimination pass plus conditional externalized critic tier record, mandatory on every path incl. the clean one), and do not claim none-found/clean when live transcript signals exceed tolerance — surface or justify those signals before Stage 3; Gate-11 → emit a '<!-- retrospect:remedy_reach begin/end -->' fence with one row per remedy-layer finding ('- finding #N: reach=full|partial|none | surface: <layer> | unreached: <axis or none> | worse_axis: yes|no|na'), naming the axis the remedy's surface structurally cannot reach instead of describing the shortfall as legitimate; Gate-12 → run pre-scan lane 6 (denied_actions) and emit a '<!-- retrospect:denied_actions begin/end -->' fence with one disposed row per structurally-rejected tool call in the transcript ('- denied: \"<verbatim question>\" | tool: <name> | source: user_rejection | confessed: yes|no | disposition: promoted (finding #N)|noted|dismissed (<reason>)') — a refused action has no outcome, so nothing was written about it and the friction scan cannot reach it. See skills/retrospect/references/stage1-2-analysis.md self-incrimination pass and skills/retrospect/references/stage3-reporting.md."
  # shellcheck disable=SC2034  # read by the EXIT trap installed in sourced record_fire.sh
  PRAXIS_FIRE_DECISION=block
  jq -n --arg r "$full_reason" '{decision: "block", reason: $r}'
  exit 0
fi

if [ -n "$GATE4B_DEMOTION_TEXT" ]; then
  jq -n --arg m "$GATE4B_DEMOTION_TEXT" '{systemMessage: $m}'
fi

exit 0
