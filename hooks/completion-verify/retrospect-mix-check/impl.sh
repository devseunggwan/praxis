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
_RM_TMP="${TMPDIR:-/tmp}"; _RM_TMP="${_RM_TMP%/}"
MARKER_FILE="${PRAXIS_RETROSPECT_ACTIVE_FILE:-${_RM_TMP}/praxis-retrospect-active-${SESSION_ID}.json}"
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
    mkdir -p "${PRAXIS_HOME:-$HOME/.praxis}/scope-confirm" || true
    echo "$(date -Iseconds) session=$SESSION_ID blocked_retrospect_fence_omission" \
      >> "${PRAXIS_HOME:-$HOME/.praxis}/scope-confirm/retrospect-mix-blocked.log" || true
    fence_reason="Retrospect Stage 3 distribution fence missing (issue #666). This is a retrospect-active session (the retrospect skill was invoked this turn) presenting a findings table, but the assistant message carries no '<!-- retrospect:distribution begin -->' fence and no '## Actions Executed' marker. A free-form or localized Stage 3 report bypasses every mix-check gate (Gate-1..7) because the gates key on the canonical output schema. Re-emit the Stage 3 output per the Output Schema Contract in skills/retrospect/references/stage3-reporting.md: '## Retrospect Report' header -> audit fences -> '<!-- retrospect:distribution begin/end -->' card -> unified findings table."
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
    # a live user-correction marker therefore means the predicate SHOULD have
    # fired. Block a 'critic_diff: not-run' that coexists with live user-correction
    # markers — this converts the verbatim-brief / run-the-critic guidance from
    # unenforced self-feedback into a deterministic format gate. Keyed on
    # user-correction only (not tool-error signals), reusing Gate-8b's
    # GATE8_SIGNAL_TOLERANCE (>1) and its shared user-correction regex.
    #
    # PRECISION CAVEAT (do not overstate): that regex is broad — it matches bare
    # "no"/"stop"/"다시" and so fires on benign phrasings ("no problem", "다시
    # 설명해줘") on a user turn. Because Gate-8c scans the WHOLE retrospected
    # session, any non-trivial multi-turn session will usually accumulate >1 such
    # marker. The practical effect is a FORCING FUNCTION: on a busy session the
    # external critic must actually run (critic_diff != not-run), and 'not-run' is
    # reserved for near-trivial / genuinely quiet sessions. This is deliberate and
    # #715-aligned (default to running the only mechanism with teeth), NOT a
    # high-precision correction detector — the >1 tolerance only absorbs a single
    # stray marker, so a session with exactly one genuine correction may still
    # legitimately mark not-run (a recall gap accepted to avoid single-marker false
    # blocks). Guarded by -z GATE8_VIOLATION so a more specific Gate-8b laundering
    # message wins.
    if [ -z "$GATE8_VIOLATION" ]; then
      sl_critic_line=$(printf '%s\n' "$SLEDGER_BLOCK" | grep -iE '^[[:space:]]*-?[[:space:]]*critic_diff:[[:space:]]*.+' | tail -n 1)
      if printf '%s\n' "$sl_critic_line" | grep -qiE 'critic_diff:[[:space:]]*not-run' && [ "$live_sl_uc_count" -gt "$GATE8_SIGNAL_TOLERANCE" ]; then
        GATE8_VIOLATION="critic_diff is 'not-run' but a live transcript scan finds $live_sl_uc_count user-correction marker(s) (tolerance=$GATE8_SIGNAL_TOLERANCE) — the externalized critic tier predicate (a friction_event required user correction) was satisfied, so the tier must actually run, not be self-skipped. Spawn the READ-ONLY critic, brief it with the verbatim worst_agent_failure, and record its diff in critic_diff; 'not-run' is reserved for the genuinely sub-tolerance user-correction path"
      fi
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
if [ -n "$GATE8_VIOLATION" ]; then
  should_block=true
  reason_parts+=("Gate-8: $GATE8_VIOLATION")
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

  full_reason="Retrospect mix-check gate triggered. ${reason}. Fix guide: Gate-1 → relabel finding category via skills/retrospect/references/stage1-2-analysis.md; Gate-2 → supply either (a) 5-line 'not <action>: <reason>' rationale (Schema A) or (b) 1-2 'not-others: <dim-tags>' lines (Schema B, issue #285) in Stage 2.5; Gate-3 verdict → return to skills/retrospect/references/stage2.5-audit.md and re-evaluate evidence robustness for 2-action findings; Gate-3 backing_repo → return to skills/retrospect/references/stage1-2-analysis.md action assignment (step 7) and add 'backing_repo: <owner/repo>' to Rationale cell; Gate-4 → return to skills/retrospect/references/stage2.5-audit.md Gate-4 and re-run external-repo classification; ensure gate_4_verdict is emitted in the distribution card; Gate-7 → post-compaction session: emit a '<!-- retrospect:transcript_receipt begin/end -->' fence with the real full-transcript scan output (or the 'retrospect:transcript_receipt_skipped: transcript unreachable' line when the jsonl is genuinely unreachable); include both 'is_error_count: N' and 'content_error_count: N' fields; when content_error_count > 0 add a '<!-- retrospect:content_error_enum begin/end -->' block with per-signal promote/note/dismiss disposition rows (issue #670); Gate-8 → emit a '<!-- retrospect:suppression_ledger begin/end -->' fence carrying 'worst_agent_failure:', 'self_adversarial:', and 'critic_diff:' lines (the Stage 2 self-incrimination pass plus conditional externalized critic tier record, mandatory on every path incl. the clean one), and do not claim none-found/clean when live transcript signals exceed tolerance — surface or justify those signals before Stage 3. See skills/retrospect/references/stage1-2-analysis.md self-incrimination pass and skills/retrospect/references/stage3-reporting.md."
  jq -n --arg r "$full_reason" '{decision: "block", reason: $r}'
  exit 0
fi

exit 0
