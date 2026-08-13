#!/usr/bin/env bash
# tests/test_delegate_launch.sh — how cmux-delegate hands the prompt over (#981 Work B)
#
# The prompt used to arrive on stdin. Claude Code's workspace trust dialog
# consumes piped stdin, so a delegation into an untrusted directory — which is
# what a freshly created worktree always is — lost its prompt with no error
# anywhere: the worker sat in an empty REPL and the delegator saw only a
# missing report.
#
# These are static document checks against SKILL.md and ARCHITECTURE.md. The
# load-bearing one is the ABSENCE assertion: a future edit that restores the
# pipe would reopen the defect while every presence check kept passing.
#
# Run:  bash tests/test_delegate_launch.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/cmux-delegate/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. The claude branch passes the prompt as an argument
# ---------------------------------------------------------------------------

assert_present \
  "claude receives the prompt as a positional argument" \
  '{budget_flag} \ "$(cat "$PROMPT_FILE")"'

assert_present \
  "the size guard names the limit it enforces" \
  'if [ "$(wc -c < "$PROMPT_FILE")" -lt "$ARGV_LIMIT" ]; then'

# The policy, not the number. A literal would encode this host's page size into a
# file that runs on every host — which is the defect the derivation replaces:
# Linux caps a single argument at MAX_ARG_STRLEN = 32 pages, so 262144 was under
# the cap here (16KiB pages) and over it on any 4KiB-page host.
assert_present \
  "the limit is derived from the platform page size" \
  "_PAGE=\$(getconf PAGE_SIZE 2>/dev/null || echo 4096)"

assert_present \
  "the derivation keeps a page of headroom below the per-string cap" \
  "ARGV_LIMIT=\$(( 32 * _PAGE - _PAGE ))"

assert_present \
  "the binding constraint is named so the next editor does not re-flatten it" \
  "MAX_ARG_STRLEN = 32"

assert_absent \
  "no host-specific literal survives" \
  "ARGV_LIMIT=262144"

# Falling back to the pipe is the defect's own path. It may happen — an
# oversized prompt has nowhere else to go — but it may not happen quietly.
assert_present \
  "the stdin fallback announces itself" \
  "신뢰되지 않은 경로라면 프롬프트가 유실됩니다"

# ---------------------------------------------------------------------------
# 2. The reason, so the next editor does not undo it
# ---------------------------------------------------------------------------

assert_present \
  "the mechanism is stated: the dialog eats stdin" \
  "그 다이얼로그가 파이프된 stdin 을 소비합니다"

assert_present \
  "the exemption the interactive path does not get is stated" \
  "stdout 이 TTY 가 아닐 때** 건너뛰어진다고"

assert_present \
  "the loss is tied to the liveness verdict that now surfaces it" \
  "waiting-input"

assert_present \
  "codex is declared unprobed rather than assumed safe" \
  "codex 에 신뢰 프롬프트가 있는지는 확인하지 않았습니다"

# ---------------------------------------------------------------------------
# 3. The file-based principle is clarified, not contradicted
#
# The Core principle forbids inline `-p`. Reading a file into argv is not that,
# and the gemini branch has always been argv-shaped — but a reader who meets
# only the prohibition will "fix" the claude branch back to a pipe.
# ---------------------------------------------------------------------------

assert_present \
  "the prohibition survives" \
  "인라인 \`-p\` 절대 사용 금지"

assert_present \
  "what it actually forbids is spelled out" \
  "프롬프트 텍스트를 명령문에 박는 것"

# ---------------------------------------------------------------------------
# 4. The pre-launch warning
# ---------------------------------------------------------------------------

# The env prefix is the whole point: --account sends the worker to a different
# CLAUDE_CONFIG_DIR, and a probe without it reads the delegator's own config and
# reports someone else's trust state with full confidence.
assert_present \
  "Step 5a consults the trust helper under the worker's own account" \
  'eval "$({claude_env} sh "${CLAUDE_PLUGIN_ROOT}/skills/cmux-delegate/cwd-trust.sh" "{cwd}")"'

assert_absent \
  "the probe never runs unprefixed" \
  'eval "$(sh "${CLAUDE_PLUGIN_ROOT}/skills/cmux-delegate/cwd-trust.sh"'

assert_present \
  "the warning tells the human what to do" \
  "cmux 탭에서 한 번 키를 눌러 주세요"

assert_present \
  "ancestor trust is reported as unverified, not as permission" \
  "상속 여부는 미확인"

# ---------------------------------------------------------------------------
# 5. The pipe is gone from the claude branch (regression guard)
#
# Checked against the raw file rather than the normalized buffer: this is about
# one exact line, and `assert_absent` over a whole SKILL.md would also match the
# codex branch and the documented fallback, both of which legitimately pipe.
# ---------------------------------------------------------------------------

CLAUDE_PIPE_COUNT="$(grep -c 'cat "\$PROMPT_FILE" | {claude_env} claude' "$SKILL")"

# Exactly one: the announced oversize fallback. Two would mean the primary path
# still pipes; zero would mean the fallback lost its warning-paired invocation.
_assert_record "the claude branch pipes only on the announced fallback" \
  "$([ "$CLAUDE_PIPE_COUNT" -eq 1 ] && echo 1 || echo 0)" \
  "expected exactly 1 piped claude invocation, found $CLAUDE_PIPE_COUNT"

# ---------------------------------------------------------------------------
# 6. ARCHITECTURE.md carries the same split
#
# Checked with a second buffer rather than a second assert_lib_init: that
# function resets PASS/FAIL/FAILED_NAMES, so re-calling it discards every
# result above — a failing assertion in §1-§5 would vanish and the file would
# summarise as green. Measured: FAIL=1 before a re-init, FAIL=0 after.
# ---------------------------------------------------------------------------

ARCH="$ROOT_DIR/ARCHITECTURE.md"
[ -f "$ARCH" ] || { echo "FAIL: target not found at $ARCH" >&2; exit 1; }

# Same wrap-insensitive normalization assert_lib_init performs on its target.
ARCH_NORMALIZED="$(sed -e ':a' -e 'N' -e '$!ba' -e 's/\n[[:blank:]]*/ /g' "$ARCH")"

assert_in_arch() {  # $1 = name, $2 = fixed-string pattern
  if grep -Fq -- "$2" <<<"$ARCH_NORMALIZED"; then
    _assert_record "$1" 1 ""
  else
    _assert_record "$1" 0 "pattern not found in ARCHITECTURE.md: $2"
  fi
}

assert_in_arch \
  "the SoT distinguishes interactive from non-interactive" \
  "Interactive launches do not use the stdin column"

assert_in_arch \
  "the interactive claude row is argv-shaped" \
  '`claude --model {m} --permission-mode {p} "$(cat $F)"`'

assert_in_arch \
  "codex is unverified here too" \
  "Unverified — not probed"

# The non-interactive row is safe only under a condition it does not state. Left
# implicit, a caller reads the row as self-sufficient and reintroduces #981 on
# the path the table calls safe.
assert_in_arch \
  "the stdin column names its precondition" \
  "carries a precondition the command does not state"

assert_in_arch \
  "and says who has to satisfy it" \
  "obliges the caller to supply one of the two"

assert_lib_summary
