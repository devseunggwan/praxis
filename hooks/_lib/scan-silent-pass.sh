#!/usr/bin/env bash
# Shared silent-pass scanner for the retrospect completeness gates.
#
# Single source of truth for detection, called by BOTH the Stop mix-check hook
# (Gate-9 candidate coverage) and, best-effort, the PreToolUse marker hook
# (front-load). Detection MUST never diverge between the two callers, so both
# invoke this one script.
#
# Contract:
#   scan-silent-pass.sh --transcript <jsonl-path> --catalog <json-path>
# Output (stdout), one line per matched class:
#   <class-id>\t<severity>\t<evidence-snippet>
# Exit 0 always (fail-open: a scan error must never break the calling hook).
#
# Design constraints (from the approved plan):
#   - grep-only over raw JSONL text; NO per-object jq walk over the transcript
#     (jq is used ONLY to read the small catalog file). Keeps the PreToolUse
#     front-load inside its 5s budget.
#   - Self-trigger safety via PRECISE EXCLUSION, never a blanket fenced-code
#     strip: a blanket strip would blind credential-display to a secret shown
#     inside a ``` fence (re-opening the headline case). Instead we exclude only
#     lines that contain an exact fabricated fixture literal, an allowlist entry,
#     or a catalog STRUCTURAL marker (the catalog file's own JSON field keys /
#     filename) — so a retrospect that merely *reads or quotes this catalog* does
#     not self-inject, while real conduct (a different secret value even inside a
#     fence, or a genuine `aws secretsmanager get-secret-value` invocation) still
#     matches. NOTE: we deliberately do NOT exclude by the raw grep_pattern
#     string — a literal pattern's text is identical to real usage, so excluding
#     it would blind the scanner to the very conduct it targets.

set -uo pipefail

TRANSCRIPT=""
CATALOG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --transcript) TRANSCRIPT="${2:-}"; shift 2 ;;
    --catalog) CATALOG="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

command -v jq >/dev/null 2>&1 || exit 0
[ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ] || exit 0
[ -n "$CATALOG" ] && [ -f "$CATALOG" ] || exit 0

# Filter the transcript to assistant-role records once. All catalog classes
# scope to assistant output (displayed text OR tool_use command input, both of
# which live in assistant records). Coarse role filtering + a specific pattern
# does the discrimination; this is a single linear grep pass, not a jq walk.
#
# DELIBERATELY UNBOUNDED (issue #1183 review round 1): this scanner's contract
# is SESSION-scoped conduct detection — a severity=hard hit (credential
# display, sanctioned-path bypass) must block at Stop no matter how early in
# the session it happened, and the churn co-occurrence pair rarely lands
# inside any small tail window. A `tail -n 400` bound like the repo's other
# transcript readers use would silently pass a credential displayed 500 lines
# ago, so the whole transcript is read: ONE linear grep pass (grep rejects
# non-matching lines in C), which stays inside the front-load budget where a
# per-line jq walk did not. The role match tolerates whitespace after the
# colon so a pretty-printed or re-serialized record is not silently skipped.
ASSISTANT_LINES=$(grep -E '"role":[[:space:]]*"assistant"' "$TRANSCRIPT" 2>/dev/null || true)
[ -n "$ASSISTANT_LINES" ] || exit 0

# Build the precise-exclusion fixed-string set: every fabricated positive_fixture
# literal + allowlist entry (fabricated, never in real conduct), plus catalog
# STRUCTURAL markers (JSON field keys + the catalog filename) that appear when
# the catalog file itself is read/quoted into a transcript. Lines containing any
# of these are catalog self-mentions, not real conduct. Raw grep_pattern strings
# are intentionally NOT excluded (a literal pattern == real usage).
EXCLUDE_FILE=$(mktemp 2>/dev/null) || exit 0
trap 'rm -f "$EXCLUDE_FILE"' EXIT
{
  jq -r '
    ( .fabricated_fixture_allowlist[]? ),
    ( .classes[]? | .positive_fixture // empty )
  ' "$CATALOG" 2>/dev/null | grep -v '^$'
  # Bare tokens (no surrounding quotes) so they match whether the catalog is
  # quoted raw ("grep_pattern") or JSON-escaped inside an assistant record
  # (\"grep_pattern\"). These tokens do not occur in real credential / bypass /
  # churn conduct, so the over-exclusion risk is negligible.
  printf '%s\n' \
    'silent-pass-catalog' \
    'grep_pattern' \
    'positive_fixture' \
    'negative_fixture' \
    'cooccurs_with' \
    'fabricated_fixture_allowlist'
} > "$EXCLUDE_FILE"

# Cleaned scan scope: assistant lines minus any catalog self-mention.
if [ -s "$EXCLUDE_FILE" ]; then
  CLEANED=$(printf '%s\n' "$ASSISTANT_LINES" | grep -vF -f "$EXCLUDE_FILE" 2>/dev/null || true)
else
  CLEANED=$(printf '%s\n' "$ASSISTANT_LINES")
fi
[ -n "$CLEANED" ] || exit 0

# Iterate classes from the catalog (jq on the small catalog only). ONE jq
# spawn total (issue #1183) — the per-index loop used to spawn jq 5x per class
# (count + 4 field reads = 16 spawns for 3 classes); a single @tsv projection
# carries the same four fields per class.
# @tsv escapes \ tab \n \r inside a value (keeping one class per line);
# printf %b reverses exactly that set, so a future grep_pattern containing a
# regex backslash survives the round trip intact.
# The tab delimiters are then swapped for the unit separator (US, 0x1f):
# tab is IFS *whitespace*, and `IFS=tab read` collapses a run of tabs — an
# EMPTY MIDDLE FIELD (e.g. a class with no severity) would shift every later
# field left, so the grep would run with the co-occurrence regex as the
# primary pattern. US is non-whitespace, so `read` preserves empty fields;
# it cannot appear in a value (@tsv leaves it untouched, but it is illegal in
# the JSON source text a catalog is written as, and no catalog field carries
# one). Safe to translate AFTER @tsv because every literal tab inside a value
# is already escaped to backslash-t, leaving delimiters as the only real tabs.
jq -r '.classes[]?
       | [(.id // ""), (.severity // ""), (.grep_pattern // ""), (.cooccurs_with // "")]
       | @tsv' "$CATALOG" 2>/dev/null \
| tr '\t' '\037' \
| while IFS=$'\037' read -r id severity pat cooccur; do
  pat=$(printf '%b' "$pat")
  cooccur=$(printf '%b' "$cooccur")
  [ -n "$id" ] && [ -n "$pat" ] || continue

  primary=$(printf '%s\n' "$CLEANED" | grep -E "$pat" 2>/dev/null | head -n 1)
  [ -n "$primary" ] || continue

  if [ -n "$cooccur" ]; then
    # Co-occurrence class: both signatures must be present in the same session.
    printf '%s\n' "$CLEANED" | grep -Eq "$cooccur" 2>/dev/null || continue
  fi

  # Redact any long high-entropy run (>=20 of [A-Za-z0-9/+]) before emitting
  # evidence. The credential-display match REQUIRES >=30 chars of the live secret
  # value, and this evidence is persisted verbatim into the .candidates.json hint
  # file the skill reads — an at-rest, re-echoable copy of the exact secret this
  # hard class exists to suppress. Underscored labels (aws_secret_access_key) and
  # command evidence (bypass/churn) have no such contiguous run and are untouched.
  raw=$(printf '%s' "$primary" | grep -oE "$pat" 2>/dev/null | head -n 1)
  evidence=$(printf '%s' "$raw" | sed -E 's#[A-Za-z0-9/+]{20,}#<REDACTED>#g' | cut -c1-60)
  printf '%s\t%s\t%s\n' "$id" "$severity" "$evidence"
done

exit 0
