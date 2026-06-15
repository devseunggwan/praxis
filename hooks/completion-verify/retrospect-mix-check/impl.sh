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

INPUT=$(cat)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // ""')
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"')

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

# Identifier check 1: line-anchored '## Retrospect Report' header.
if ! printf '%s\n' "$LAST_TEXT" | grep -qE '^## Retrospect Report'; then
  exit 0
fi

# Identifier check 2: distribution-card fence present.
if ! printf '%s' "$LAST_TEXT" | grep -qF '<!-- retrospect:distribution begin -->'; then
  exit 0
fi
if ! printf '%s' "$LAST_TEXT" | grep -qF '<!-- retrospect:distribution end -->'; then
  exit 0
fi

# Identifier check 3: within the most recent '## Retrospect Report' block, no
# '## Actions Executed' marker (otherwise Stage 4 already ran — too late to gate).
# Extract the most recent block: from the last '^## Retrospect Report' line to
# either next '^## ' heading or end of message.
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

if printf '%s' "$MOST_RECENT_BLOCK" | grep -qF '## Actions Executed'; then
  exit 0
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
  # Stage 2 step 8 documents this as MUST; Stage 4 Action 4 step 0 aborts on absence.
  has_routed_action=false
  for tok in "${unique_actions[@]}"; do
    case "$tok" in
      upstream_feedback|issue) has_routed_action=true; break ;;
    esac
  done
  if [ "$has_routed_action" = "true" ]; then
    normalized_r3=$(printf '%s' "$rationale" | sed 's/<br *\/*>/\n/g')
    if ! printf '%s\n' "$normalized_r3" | grep -qE '^[[:space:]]*backing_repo: [A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*'; then
      GATE3_VIOLATIONS+=("finding #${finding_num}: Proposed Actions contains upstream_feedback or issue but Rationale missing backing_repo: <owner/repo> — return to Stage 2 step 8")
    fi
  fi
done <<< "$TABLE_LINES"

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
  if ! printf '%s\n' "$MOST_RECENT_BLOCK" | grep -qF 'retrospect:transcript_receipt'; then
    GATE7_VIOLATION="post-compaction session but the Stage 3 report has no '<!-- retrospect:transcript_receipt begin/end -->' fence — run the full-transcript friction scan and paste the REAL command output (is_error / user-turn / interrupt counts) in the receipt fence before Stage 3"
  elif printf '%s\n' "$MOST_RECENT_BLOCK" | grep -qF 'retrospect:transcript_receipt_skipped'; then
    : # Skipped variant is legitimate (SKILL.md governs when); no content check.
  else
    # Content-enumeration check [issue #664]: a real receipt is present. A
    # 'grep -c' count proves a scan RAN, not that the errors were READ — the
    # exact gap that let a salient-window pass survive Gate-7 (the receipt's
    # count satisfied the fence while the error contents went unexamined, and
    # the most adverse error was silently dropped). When the receipt declares
    # is_error_count > 0, require a per-event 'retrospect:is_error_enum' block so
    # each error is surfaced with a promote/note/dismiss disposition. This turns
    # invisible silent omission into visible (challengeable) mis-disposition.
    ie_count=$(printf '%s\n' "$MOST_RECENT_BLOCK" \
      | grep -oE 'is_error_count:[[:space:]]*[0-9]+' | grep -oE '[0-9]+' | head -1)
    if [ -n "$ie_count" ] && [ "$ie_count" -gt 0 ] 2>/dev/null; then
      # A bare substring match on 'retrospect:is_error_enum' would itself
      # reintroduce the count-not-content gap one layer down: a prose mention,
      # a lone begin marker, or an empty begin/end pair with no rows would all
      # pass while enumerating nothing. So require the actual fenced block —
      # both begin AND end markers — AND at least one disposition row
      # (promote|note|dismiss) BETWEEN them. The awk scan counts disposition
      # rows only while inside the fence, so rows leaking outside the block do
      # not satisfy the gate.
      ie_begin=$(printf '%s\n' "$MOST_RECENT_BLOCK" | grep -cF 'retrospect:is_error_enum begin')
      ie_end=$(printf '%s\n' "$MOST_RECENT_BLOCK" | grep -cF 'retrospect:is_error_enum end')
      ie_rows=$(printf '%s\n' "$MOST_RECENT_BLOCK" | awk '
        /retrospect:is_error_enum begin/ { inblock=1; next }
        /retrospect:is_error_enum end/   { inblock=0 }
        inblock && /disposition:[[:space:]]*(promote|note|dismiss)/ { c++ }
        END { print c+0 }')
      if [ "$ie_begin" -lt 1 ] || [ "$ie_end" -lt 1 ] || [ "$ie_rows" -lt 1 ]; then
        GATE7_VIOLATION="post-compaction receipt declares is_error_count=$ie_count but has no complete '<!-- retrospect:is_error_enum begin/end -->' block with per-event disposition rows (found begin=$ie_begin end=$ie_end disposition_rows=$ie_rows) — a count proves a scan ran, not that the errors were read; enumerate each is_error inside the fence with a promote/note/dismiss disposition before Stage 3"
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
# NA → pass (no upstream_feedback findings).
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

if [ "$should_block" = "true" ]; then
  mkdir -p "${PRAXIS_HOME:-$HOME/.praxis}/scope-confirm" || true
  echo "$(date -Iseconds) session=$SESSION_ID blocked_retrospect_mix_check" >> "${PRAXIS_HOME:-$HOME/.praxis}/scope-confirm/retrospect-mix-blocked.log" || true

  # Build reason string with ' | ' separator.
  reason=""
  for part in "${reason_parts[@]}"; do
    if [ -z "$reason" ]; then
      reason="$part"
    else
      reason="$reason | $part"
    fi
  done

  full_reason="Retrospect mix-check gate triggered. ${reason}. Fix guide: Gate-1 → relabel finding category; Gate-2 → supply either (a) 5-line 'not <action>: <reason>' rationale (Schema A) or (b) 1-2 'not-others: <dim-tags>' lines (Schema B, issue #285) in Stage 2.5; Gate-3 verdict → return to Stage 2.5 and re-evaluate evidence robustness for 2-action findings; Gate-3 backing_repo → return to Stage 2 step 8 and add 'backing_repo: <owner/repo>' to Rationale cell; Gate-4 → return to Stage 2.5 Gate-4 and re-run external-repo classification; ensure gate_4_verdict is emitted in the distribution card; Gate-7 → post-compaction session: emit a '<!-- retrospect:transcript_receipt begin/end -->' fence with the real full-transcript scan output (or the 'retrospect:transcript_receipt_skipped: transcript unreachable' line when the jsonl is genuinely unreachable). See skills/retrospect/SKILL.md."
  jq -n --arg r "$full_reason" '{decision: "block", reason: $r}'
  exit 0
fi

exit 0
