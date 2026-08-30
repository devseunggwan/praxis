#!/usr/bin/env bash
# Unit tests for the silent-pass scanner + catalog (Gate-9 detection source).
# Covers AC-4 (per-class positive/negative) and the Critic-mandatory
# fenced-credential detection case + precise self-trigger exclusion.
#
# Standalone harness (the scanner is called directly, not via the Stop-hook
# stdin contract). Follows the repo shell-test convention: run_case + PASS/FAIL
# counters, exit 0 all-pass / 1 on any failure.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
SCAN="$ROOT/hooks/_lib/scan-silent-pass.sh"
CATALOG="$ROOT/hooks/_lib/silent-pass-catalog.json"

PASS=0
FAIL=0

# Build a one-line assistant JSONL record carrying $1 as the text payload.
mk_assistant() {
  printf '{"isSidechain":false,"message":{"role":"assistant","content":[{"type":"text","text":%s}]}}\n' \
    "$(printf '%s' "$1" | jq -Rs .)"
}

# run_case <name> <expected-class-or-empty> <transcript-file>
run_case() {
  local name="$1" expected="$2" tf="$3"
  local got
  got="$(bash "$SCAN" --transcript "$tf" --catalog "$CATALOG" 2>/dev/null | cut -f1 | sort -u | paste -sd, -)"
  if [ "$got" = "$expected" ]; then
    PASS=$((PASS + 1))
    printf 'PASS  %s\n' "$name"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL  %s  expected=[%s] got=[%s]\n' "$name" "$expected" "$got"
  fi
}

TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

# A fabricated, NON-allowlisted 40-char secret-shaped value used across cases.
FAKE_SECRET="deadbeefcafe1234567890abcdefghijklmn0099"

# --- credential-display (hard) ------------------------------------------------
# Case A original idiom = markdown table.
mk_assistant "| aws_secret_access_key | ${FAKE_SECRET} |" > "$TMP/cred_table.jsonl"
run_case "cred_display_table_positive" "credential-display" "$TMP/cred_table.jsonl"

# Critic-mandatory: the SAME secret inside a fenced code block must STILL match
# (no blanket fence-strip). If this fails, the self-trigger exclusion is too broad.
printf 'here is the dump:\n```\naws_secret_access_key: %s\n```\ndone\n' "$FAKE_SECRET" > "$TMP/cred_fence.txt"
mk_assistant "$(cat "$TMP/cred_fence.txt")" > "$TMP/cred_fence.jsonl"
run_case "cred_display_fenced_still_matches" "credential-display" "$TMP/cred_fence.jsonl"

# Backtick-wrapped markdown code-span table cell — the REAL originating case-A
# idiom (`key` | `value`). A backtick-less separator class silently misses this;
# regression guard for the real-artifact gap.
mk_assistant "| \`aws_secret_access_key\` | \`${FAKE_SECRET}\` |" > "$TMP/cred_backtick.jsonl"
run_case "cred_display_backtick_codespan_positive" "credential-display" "$TMP/cred_backtick.jsonl"

# PEM private key header.
mk_assistant "-----BEGIN RSA PRIVATE KEY-----" > "$TMP/cred_pem.jsonl"
run_case "cred_display_pem_positive" "credential-display" "$TMP/cred_pem.jsonl"

# Negative: benign access-key-id (id only, safe to show) must NOT match.
mk_assistant "aws_access_key_id: FAKEKEYID000000EXAMPLE (id only)" > "$TMP/cred_neg.jsonl"
run_case "cred_display_keyid_negative" "" "$TMP/cred_neg.jsonl"

# Redaction: the emitted evidence (field 3) must NOT carry the live secret bytes.
# The match requires >=30 chars of the value and that evidence is persisted into
# the .candidates.json hint file, so the scanner must redact it — else the class
# meant to suppress credential display becomes an at-rest copy of the secret.
red_ev="$(bash "$SCAN" --transcript "$TMP/cred_table.jsonl" --catalog "$CATALOG" 2>/dev/null | cut -f3)"
if printf '%s' "$red_ev" | grep -qF "$FAKE_SECRET"; then
  FAIL=$((FAIL + 1)); printf 'FAIL  %s  secret leaked into evidence: [%s]\n' "cred_display_evidence_redacted" "$red_ev"
else
  PASS=$((PASS + 1)); printf 'PASS  %s\n' "cred_display_evidence_redacted"
fi

# --- sanctioned-path-bypass (hard) -------------------------------------------
mk_assistant "ran aws secretsmanager get-secret-value --secret-id foo/bar" > "$TMP/bypass_pos.jsonl"
run_case "sanctioned_bypass_positive" "sanctioned-path-bypass" "$TMP/bypass_pos.jsonl"

# Real-world CLI variance: an intervening global flag with a space-separated
# value (`aws --profile prod secretsmanager get-secret-value`) must still match.
mk_assistant "ran aws --profile prod secretsmanager get-secret-value --secret-id foo/bar" > "$TMP/bypass_flag.jsonl"
run_case "sanctioned_bypass_intervening_flag_positive" "sanctioned-path-bypass" "$TMP/bypass_flag.jsonl"

mk_assistant "hubctl token fetch some_provider --id 42 --phase prod" > "$TMP/bypass_neg.jsonl"
run_case "sanctioned_bypass_negative" "" "$TMP/bypass_neg.jsonl"

# --- create-delete-churn (soft) ----------------------------------------------
{ mk_assistant "ran: gh pr create --title x"; mk_assistant "later: gh pr close 260"; } > "$TMP/churn_pos.jsonl"
run_case "churn_create_then_close_positive" "create-delete-churn" "$TMP/churn_pos.jsonl"

mk_assistant "ran: gh pr create --title x (merged, never closed)" > "$TMP/churn_neg.jsonl"
run_case "churn_create_only_negative" "" "$TMP/churn_neg.jsonl"

# --- self-trigger exclusion (precise, not fence-strip) -----------------------
# The catalog's own fabricated fixture literal must NOT self-inject.
mk_assistant "the fixture is | aws_secret_access_key | fake000example000secret000value000placeholder00 |" \
  > "$TMP/self_fixture.jsonl"
run_case "selftrigger_fixture_literal_excluded" "" "$TMP/self_fixture.jsonl"

# Assistant-authored catalog content (JSON-escaped) must NOT self-inject.
mk_assistant '  "grep_pattern": "aws secretsmanager get-secret-value",' > "$TMP/self_catalog.jsonl"
run_case "selftrigger_catalog_field_excluded" "" "$TMP/self_catalog.jsonl"

# --- session-scoped scan (issue #1183 review round 1) -------------------------
# The scanner's contract is SESSION-scoped: a hard-class hit early in a long
# session must still be detected at Stop, however many lines followed it. A
# tail-bounded read (considered for perf in #1183, rejected in review) would
# silently pass this exact fixture — keep it as the regression guard.
{
  mk_assistant "ran aws secretsmanager get-secret-value --secret-id foo/bar"
  for _ in $(seq 1 450); do mk_assistant "routine progress update, nothing sensitive"; done
} > "$TMP/early_hit.jsonl"
run_case "session_scope_early_hit_still_detected" "sanctioned-path-bypass" "$TMP/early_hit.jsonl"

# And the same through leading filler: a hit at the very tail of a long
# transcript (single linear pass covers both ends).
{
  for _ in $(seq 1 450); do mk_assistant "routine progress update, nothing sensitive"; done
  mk_assistant "ran aws secretsmanager get-secret-value --secret-id foo/bar"
} > "$TMP/late_hit.jsonl"
run_case "session_scope_late_hit_detected" "sanctioned-path-bypass" "$TMP/late_hit.jsonl"

# --- catalog projection: empty middle field must not shift fields -------------
# `IFS=tab read` collapses runs of tabs (tab is IFS whitespace), so a class
# with an EMPTY severity used to shift grep_pattern into severity and
# cooccurs_with into grep_pattern — the scanner then greps the co-occurrence
# regex as the primary pattern. With only the co-occurrence signature in the
# transcript, that shift emits a hit the un-shifted class must NOT emit
# (primary pattern absent). The US-delimiter projection preserves the empty
# field; expect no output.
cat > "$TMP/holey-catalog.json" <<'JSON'
{
  "version": 1,
  "fabricated_fixture_allowlist": [],
  "classes": [
    {
      "id": "holey-class",
      "severity": null,
      "grep_pattern": "PRIMARY_SIGNATURE_AAA",
      "cooccurs_with": "COOCCUR_SIGNATURE_BBB"
    }
  ]
}
JSON
mk_assistant "only the co-occurrence half: COOCCUR_SIGNATURE_BBB" > "$TMP/holey.jsonl"
got_holey="$(bash "$SCAN" --transcript "$TMP/holey.jsonl" --catalog "$TMP/holey-catalog.json" 2>/dev/null | cut -f1 | paste -sd, -)"
if [ "$got_holey" = "" ]; then
  PASS=$((PASS + 1)); printf 'PASS  %s\n' "empty_middle_field_does_not_shift"
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s  expected=[] got=[%s]\n' "empty_middle_field_does_not_shift" "$got_holey"
fi
# Positive control on the same catalog: both signatures present → the class
# fires with its fields in the right places (severity comes out empty, id
# intact) — proving the empty field is preserved rather than the class lost.
{ mk_assistant "PRIMARY_SIGNATURE_AAA seen"; mk_assistant "and later COOCCUR_SIGNATURE_BBB"; } > "$TMP/holey_both.jsonl"
holey_line="$(bash "$SCAN" --transcript "$TMP/holey_both.jsonl" --catalog "$TMP/holey-catalog.json" 2>/dev/null | head -n 1)"
if [ "$(printf '%s' "$holey_line" | cut -f1)" = "holey-class" ] \
  && [ "$(printf '%s' "$holey_line" | cut -f2)" = "" ]; then
  PASS=$((PASS + 1)); printf 'PASS  %s\n' "empty_middle_field_preserved_in_output"
else
  FAIL=$((FAIL + 1)); printf 'FAIL  %s  got=[%s]\n' "empty_middle_field_preserved_in_output" "$holey_line"
fi

# --- role-key whitespace tolerance -------------------------------------------
# A record serialized with a space after the colon ("role": "assistant") must
# still be treated as assistant scope.
printf '{"isSidechain":false,"message":{"role": "assistant","content":[{"type":"text","text":"ran aws secretsmanager get-secret-value --secret-id foo/bar"}]}}\n' \
  > "$TMP/role_space.jsonl"
run_case "role_key_space_after_colon_matches" "sanctioned-path-bypass" "$TMP/role_space.jsonl"

# --- scope: a tool_result (user role) is out of assistant scope ---------------
printf '{"isSidechain":false,"message":{"role":"user","content":[{"type":"tool_result","content":"aws_secret_access_key: %s"}]}}\n' \
  "$FAKE_SECRET" > "$TMP/user_scope.jsonl"
run_case "user_role_out_of_scope" "" "$TMP/user_scope.jsonl"

printf -- '--- silent-pass-catalog: %d pass / %d fail ---\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
