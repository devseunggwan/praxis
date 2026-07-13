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

TMP="$(mktemp -d)"
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

# --- sanctioned-path-bypass (hard) -------------------------------------------
mk_assistant "ran aws secretsmanager get-secret-value --secret-id foo/bar" > "$TMP/bypass_pos.jsonl"
run_case "sanctioned_bypass_positive" "sanctioned-path-bypass" "$TMP/bypass_pos.jsonl"

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

# --- scope: a tool_result (user role) is out of assistant scope ---------------
printf '{"isSidechain":false,"message":{"role":"user","content":[{"type":"tool_result","content":"aws_secret_access_key: %s"}]}}\n' \
  "$FAKE_SECRET" > "$TMP/user_scope.jsonl"
run_case "user_role_out_of_scope" "" "$TMP/user_scope.jsonl"

printf -- '--- silent-pass-catalog: %d pass / %d fail ---\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
