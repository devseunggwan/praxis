#!/bin/sh
# Standard block-message format for praxis preflight-gate hooks (issue #439).
#
# Shell equivalent of hooks/_lib/block_message.py for any pure-shell hook.
# Source this file, then call praxis_emit_block. The five-field format is
# byte-identical to the Python helper so a hook ported between languages
# renders the same text. Field semantics live in
# docs/hook/block-message-format.md.
#
#   . "$(dirname "$0")/_lib/block_message.sh"
#   praxis_emit_block \
#     "gh search --state all" \
#     "gh search subcommands only accept --state {open|closed}, not 'all'" \
#     "omit --state, or run two calls (--state open then --state closed)" \
#     "" \
#     "CLAUDE.md → GitHub CLI usage; docs/hook/block-gh-state-all.md"
#
# Args (positional):
#   1 rule_name     — rendered uppercased in the header label
#   2 why           — one line naming the violated convention
#   3 correct_path  — specific next action (skill / command / pattern)
#   4 bypass_env    — env var that bypasses the gate; empty string ("") to
#                     omit the Bypass line entirely (gate has no bypass)
#   5 reference     — CLAUDE.md section, docs/ path, or wiki link
#   6 bypass_hint   — optional; reason-comment guidance on the Bypass line
#
# praxis_format_block writes the message to stdout (for capture); the message
# carries NO trailing newline beyond the last field line. praxis_emit_block
# writes it to stderr. Neither exits — the caller owns the exit code.

praxis_format_block() {
  _bm_rule="$1"
  _bm_why="$2"
  _bm_correct="$3"
  _bm_bypass="$4"
  _bm_reference="$5"
  _bm_hint="${6:-with a one-line reason comment explaining why}"

  # Uppercase the rule name for the header label (POSIX tr).
  _bm_rule_upper=$(printf '%s' "$_bm_rule" | tr '[:lower:]' '[:upper:]')

  printf '⚠️ %s blocked\n' "$_bm_rule_upper"
  printf '\n'
  printf 'Why: %s\n' "$_bm_why"
  printf 'Correct path: %s\n' "$_bm_correct"
  if [ -n "$_bm_bypass" ]; then
    printf 'Bypass (if truly needed): %s=1 %s\n' "$_bm_bypass" "$_bm_hint"
  fi
  printf 'Reference: %s' "$_bm_reference"
}

praxis_emit_block() {
  praxis_format_block "$@" >&2
  printf '\n' >&2
}
