#!/bin/bash
# tests/test_cmux_browser.sh — cmux-browser wrapper coverage
#
# Uses a mock `cmux` to simulate selector-required errors without needing a
# real browser surface.  Verifies that the wrapper adds --selector usage hints
# and that non-selector errors pass through unchanged.
#
# Run:  ./tests/test_cmux_browser.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WRAPPER="$REPO_ROOT/skills/cmux-browser/cmux-browser"

if [ ! -x "$WRAPPER" ]; then
  echo "FAIL: wrapper not executable: $WRAPPER" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

MOCK_DIR=$(mktemp -d)
STDERR_FILE=$(mktemp)
trap 'rm -rf "$MOCK_DIR" "$STDERR_FILE"' EXIT

# ── Mock cmux ────────────────────────────────────────────────────────────────
#
# Simulates two behaviors:
#   browser <surface> get <type>              → exit 1, "requires a selector"
#   browser <surface> get <type> [selector]  → exit 0, "mock-output-for-<type>"
#   everything else                           → exit 1, "not_found"
#
# The key: after consuming "get" and <type>, any remaining non-flag token is a
# positional selector, and --selector/-s also counts.
#
cat > "$MOCK_DIR/cmux" << 'MOCK'
#!/bin/bash
if [[ "${1:-}" == "browser" ]]; then
  shift
  # Consume optional surface specifier(s)
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --surface) shift 2 ;;
      surface:*|workspace:*) shift ;;
      get)
        shift                  # consume "get"
        get_type="${1:-}"
        shift                  # consume the type — MUST shift before scanning rest
        # Only these types require --selector in real cmux
        needs_selector=false
        case "$get_type" in
          html|text|value|attr|count|box|styles) needs_selector=true ;;
        esac
        if $needs_selector; then
          has_selector=false
          skip_next=false
          for arg in "$@"; do
            if $skip_next; then skip_next=false; continue; fi
            case "$arg" in
              --selector|-s) has_selector=true; skip_next=true ;;
              --attr|--property|--index) skip_next=true ;;
              --*) ;;
              *) has_selector=true ;;
            esac
          done
          if ! $has_selector; then
            echo "Error: browser get ${get_type} requires a selector" >&2
            exit 1
          fi
        fi
        echo "mock-output-for-${get_type}"
        exit 0
        ;;
      frame)
        shift  # consume "frame"
        # Simulate the "requires a selector" variant of the frame error.
        # (The binary has two strings; the one intercepted by the wrapper is
        #  "requires a selector or 'main'", not "requires <selector|main>".)
        echo "Error: browser frame requires a selector or 'main'" >&2; exit 1 ;;
      find)
        shift  # consume "find"
        find_type="${1:-}"; shift  # consume the find sub-type (nth, first, …)
        # find nth: needs both --index and --selector; simulate selector-missing error
        # when --selector/-s is absent.
        # Pure integers are treated as positional --index values, NOT selectors.
        has_selector=false
        skip_next=false
        for arg in "$@"; do
          if $skip_next; then skip_next=false; continue; fi
          case "$arg" in
            --selector|-s) has_selector=true; skip_next=true ;;
            --index|--name) skip_next=true ;;
            --*) ;;
            [0-9]*) ;;          # bare integer = positional index, not a selector
            *) has_selector=true ;;
          esac
        done
        if ! $has_selector; then
          echo "Error: browser find ${find_type} requires a selector" >&2; exit 1
        fi
        echo "mock-output-for-find-${find_type}"; exit 0 ;;
      click|hover|focus|dblclick|check|uncheck|scroll-into-view|highlight|type|fill|select)
        subcmd="$1"; shift
        # These cmux subcommands require --selector (or a positional CSS value).
        # --text/--value/--attr/--index values must NOT count as selectors — they
        # are payload flags whose values are consumed by the next iteration.
        has_selector=false
        skip_next=false
        for arg in "$@"; do
          if $skip_next; then skip_next=false; continue; fi
          case "$arg" in
            --selector|-s) has_selector=true; skip_next=true ;;
            --text|--value|--attr|--property|--index|--name) skip_next=true ;;
            --*) ;;
            *) has_selector=true ;;  # positional CSS selector
          esac
        done
        if ! $has_selector; then
          echo "Error: browser ${subcmd} requires a selector" >&2; exit 1
        fi
        echo "mock-output-for-${subcmd}"; exit 0 ;;
      *) break ;;
    esac
    # NOTE: no trailing shift — each case above manages its own shift(s).
    # A trailing shift here would double-consume tokens after surface:* or --surface.
  done
fi
# fallback for non-get commands or unrecognised paths
echo "Error: not_found: Surface not found or not a browser" >&2
exit 1
MOCK
chmod +x "$MOCK_DIR/cmux"

export PATH="$MOCK_DIR:$PATH"

# ── Test helpers ──────────────────────────────────────────────────────────────

pass() { echo "PASS  [$1]"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  [$1] $2"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); }

assert_contains() {
  local name="$1" pattern="$2" actual="$3"
  if echo "$actual" | grep -q "$pattern"; then
    pass "$name"
  else
    fail "$name" "expected /$pattern/ in: ${actual:0:200}"
  fi
}

assert_not_contains() {
  local name="$1" pattern="$2" actual="$3"
  if ! echo "$actual" | grep -q "$pattern"; then
    pass "$name"
  else
    fail "$name" "did NOT expect /$pattern/ in: ${actual:0:200}"
  fi
}

assert_exit() {
  local name="$1" expected="$2" actual="$3"
  if [ "$actual" -eq "$expected" ]; then
    pass "$name"
  else
    fail "$name" "exit code: expected $expected, got $actual"
  fi
}

# run_wrapper: run wrapper, capture stdout + stderr separately, return exit code
# Usage: run_wrapper [args...]
# Sets: _stdout, _stderr, _ec
run_wrapper() {
  _stdout=$("$WRAPPER" "$@" 2>"$STDERR_FILE")
  _ec=$?
  _stderr=$(cat "$STDERR_FILE")
}

# ── Tests ─────────────────────────────────────────────────────────────────────

echo ""
echo "=== cmux-browser wrapper tests ==="
echo ""

# ── 1. Selector-required types: missing --selector triggers usage hint ─────────

for type in html text value attr count box styles; do
  run_wrapper surface:1 get "$type"

  assert_exit     "exit-code:get-${type}-missing-selector"   1  "$_ec"
  assert_contains "hint:--selector:get-${type}"  "\-\-selector"          "$_stderr"
  assert_contains "hint:usage:get-${type}"       "Usage:"                "$_stderr"
  assert_contains "hint:example:get-${type}"     "surface:1 get ${type}" "$_stderr"
  assert_contains "hint:type-name:get-${type}"   "get ${type}"           "$_stderr"
done

# ── 2. Positional selector: no hint, command succeeds ─────────────────────────

for type in html text; do
  run_wrapper surface:1 get "$type" "h1.title"

  assert_exit         "exit-code:positional-selector:${type}" 0  "$_ec"
  assert_contains     "stdout:positional-selector:${type}"    "mock-output-for-${type}" "$_stdout"
  assert_not_contains "no-hint:positional-selector:${type}"   "\-\-selector"            "$_stderr"
done

# ── 3. --selector flag: no hint, command succeeds ─────────────────────────────

for type in html text; do
  run_wrapper surface:1 get "$type" --selector "h1"

  assert_exit         "exit-code:--selector-flag:${type}" 0  "$_ec"
  assert_contains     "stdout:--selector-flag:${type}"    "mock-output-for-${type}" "$_stdout"
  assert_not_contains "no-hint:--selector-flag:${type}"   "Usage:"                  "$_stderr"
done

# ── 4. --surface variant: equivalent to positional surface ────────────────────

run_wrapper --surface surface:1 get html
assert_exit     "exit-code:--surface-variant:missing-selector"  1  "$_ec"
assert_contains "hint:--surface-variant"  "\-\-selector"  "$_stderr"

run_wrapper --surface surface:1 get html --selector "body"
assert_exit         "exit-code:--surface-variant:with-selector"  0  "$_ec"
assert_not_contains "no-hint:--surface-variant:with-selector"    "Usage:"  "$_stderr"

# ── 5. Non-get selector-required subcommands ──────────────────────────────────

# 5a. Action commands (click, hover): selector only, no payload
for subcmd in click hover; do
  run_wrapper surface:1 "$subcmd"

  assert_exit     "exit-code:${subcmd}-missing-selector"   1  "$_ec"
  assert_contains "hint:--selector:${subcmd}"  "\-\-selector"  "$_stderr"
  assert_contains "hint:usage:${subcmd}"       "Usage:"        "$_stderr"
  assert_contains "hint:subcmd-name:${subcmd}" "${subcmd}"     "$_stderr"
done

# 5b. type: hint must include --text payload (binary errors without text too)
run_wrapper surface:1 type
assert_exit     "exit-code:type-missing-selector"   1  "$_ec"
assert_contains "hint:--selector:type"   "\-\-selector"  "$_stderr"
assert_contains "hint:text-payload:type" "\-\-text"      "$_stderr"

# 5c. fill: selector only — text is optional and not enforced by the binary
run_wrapper surface:1 fill
assert_exit         "exit-code:fill-missing-selector"   1  "$_ec"
assert_contains     "hint:--selector:fill"   "\-\-selector"  "$_stderr"
assert_not_contains "no-text-hint:fill"      "\-\-text"      "$_stderr"

# 5c. select: hint must include --value payload
run_wrapper surface:1 select

assert_exit     "exit-code:select-missing-selector"   1  "$_ec"
assert_contains "hint:--selector:select"  "\-\-selector"  "$_stderr"
assert_contains "hint:value-payload:select" "\-\-value"   "$_stderr"

# 5d. get attr: hint must include both --selector and --attr
run_wrapper surface:1 get attr

assert_exit     "exit-code:get-attr-missing-selector"  1  "$_ec"
assert_contains "hint:--selector:get-attr"  "\-\-selector"  "$_stderr"
assert_contains "hint:--attr:get-attr"      "\-\-attr"      "$_stderr"

# 5f. frame: binary has "requires a selector or 'main'" variant that IS intercepted;
#     hint must show <main|selector> mode, NOT just --selector <css>
# Simulate: "Error: browser frame requires a selector or 'main'"
_fake_tmpfile=$(mktemp)
echo "Error: browser frame requires a selector or 'main'" > "$_fake_tmpfile"
_frame_subcmd=$(sed -n 's/.*browser \(.*\) requires a selector.*/\1/p' "$_fake_tmpfile" | head -1)
rm -f "$_fake_tmpfile"
assert_contains "frame-subcmd-extraction"  "^frame$"  "$_frame_subcmd"

run_wrapper surface:1 frame   # triggers "requires a selector" variant
assert_exit     "exit-code:frame-missing-mode"   1  "$_ec"
assert_contains "hint:frame-has-main"     "main"        "$_stderr"
assert_contains "hint:frame-has-selector" "selector"    "$_stderr"

# 5g. find nth (index given, selector missing): hint must include both --index and --selector
# Mock: find nth with an index token but no --selector still reports "requires a selector"
run_wrapper surface:1 find nth 2

assert_exit     "exit-code:find-nth-missing-selector"  1  "$_ec"
assert_contains "hint:--selector:find-nth"  "\-\-selector"  "$_stderr"
assert_contains "hint:--index:find-nth"     "\-\-index"     "$_stderr"

# 5e. Payload-without-selector: --text/--value must NOT suppress the selector hint
# (mock bug would treat these as selectors and return success — this verifies the fix)
run_wrapper surface:1 type --text "hello"
assert_exit     "exit-code:type-payload-no-selector"  1  "$_ec"
assert_contains "hint:type-payload-no-selector"  "\-\-selector"  "$_stderr"

run_wrapper surface:1 select --value "option-a"
assert_exit     "exit-code:select-payload-no-selector"  1  "$_ec"
assert_contains "hint:select-payload-no-selector"  "\-\-selector"  "$_stderr"

# ── 6. Non-selector error passes through unchanged ────────────────────────────

run_wrapper surface:999 navigate https://example.com
assert_contains     "passthrough:non-selector-error:has-not_found"  "not_found"  "$_stderr"
assert_not_contains "no-hint:non-selector-error"                     "Usage:"     "$_stderr"

# ── 6. get url / get title: no selector required (different error) ─────────────

run_wrapper surface:1 get url
assert_not_contains "no-hint:get-url"   "requires \-\-selector"  "$_stderr"

run_wrapper surface:1 get title
assert_not_contains "no-hint:get-title" "requires \-\-selector"  "$_stderr"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " PASS: $PASS   FAIL: $FAIL"
echo "═══════════════════════════════════════════════════════════════"

if [ ${#FAILED_NAMES[@]} -gt 0 ]; then
  echo ""
  echo "Failed tests:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  echo ""
  exit 1
fi
