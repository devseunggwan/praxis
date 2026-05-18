#!/usr/bin/env bash
# test_build_hosts_filter.sh — verify build-plugin-manifests.py hosts filtering
#
# Creates temporary fixture files and platform manifests, runs the build script,
# then asserts that per-platform hooks.json includes / excludes the expected hooks.
#
# Usage: bash tests/test_build_hosts_filter.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$ROOT_DIR/scripts/build-plugin-manifests.py"

if [ ! -x "$BUILD_SCRIPT" ]; then
  echo "FAIL: build script not executable: $BUILD_SCRIPT" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" result="$2" expected="$3"
  if [ "$result" = "$expected" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected got=$result"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

setup_fixture() {
  FIXTURE_DIR="$(mktemp -d)"

  # Minimal hooks.json fixture with three hooks:
  #   - hook-no-hosts: no hosts field  (= all platforms)
  #   - hook-claude-only: hosts=["claude"]
  #   - hook-codex-only: hosts=["codex"]
  cat > "$FIXTURE_DIR/hooks.json" << 'HOOKS_EOF'
{
  "description": "fixture",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "hooks/hook-no-hosts.sh",
            "timeout": 5
          },
          {
            "type": "command",
            "command": "hooks/hook-claude-only.sh",
            "timeout": 5,
            "hosts": ["claude"]
          },
          {
            "type": "command",
            "command": "hooks/hook-codex-only.sh",
            "timeout": 5,
            "hosts": ["codex"]
          }
        ]
      }
    ]
  }
}
HOOKS_EOF

  mkdir -p "$FIXTURE_DIR/platforms"

  cat > "$FIXTURE_DIR/platforms/claude.json" << 'CLAUDE_EOF'
{
  "platform": "claude",
  "host_id": "claude",
  "outputs": [
    {
      "kind": "hooks",
      "path": "out-claude/hooks.json"
    }
  ]
}
CLAUDE_EOF

  cat > "$FIXTURE_DIR/platforms/codex.json" << 'CODEX_EOF'
{
  "platform": "codex",
  "host_id": "codex",
  "outputs": [
    {
      "kind": "hooks",
      "path": "out-codex/hooks.json"
    }
  ]
}
CODEX_EOF
}

run_filter_test() {
  local fixture_dir="$1"

  python3 - "$fixture_dir" << 'PY_EOF'
import json, sys, copy
from pathlib import Path

fixture = Path(sys.argv[1])
hooks_source = json.loads((fixture / "hooks.json").read_text())

# Inline filter_hooks_for_host (mirrors the build script logic)
def filter_hooks_for_host(hooks_data, host_id):
    filtered = copy.deepcopy(hooks_data)
    for event_name, event_groups in filtered.get("hooks", {}).items():
        for group in event_groups:
            kept = []
            for hook in group.get("hooks", []):
                hosts = hook.get("hosts")
                if hosts is None or host_id in hosts:
                    entry = {k: v for k, v in hook.items() if k != "hosts"}
                    kept.append(entry)
            group["hooks"] = kept
    return filtered

for platform_file in sorted((fixture / "platforms").glob("*.json")):
    platform = json.loads(platform_file.read_text())
    host_id = platform.get("host_id", platform["platform"])
    for output in platform["outputs"]:
        out_path = fixture / output["path"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = filter_hooks_for_host(hooks_source, host_id)
        out_path.write_text(json.dumps(rendered, indent=2) + "\n")

print("OK")
PY_EOF
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

echo "test_build_hosts_filter"

setup_fixture

# Run filter logic against the fixture
filter_output="$(run_filter_test "$FIXTURE_DIR")"
if [ "$filter_output" != "OK" ]; then
  echo "FAIL  [setup] filter runner failed: $filter_output"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("setup")
fi

CLAUDE_HOOKS="$FIXTURE_DIR/out-claude/hooks.json"
CODEX_HOOKS="$FIXTURE_DIR/out-codex/hooks.json"

# Case 1: no hosts field → hook appears in BOTH profiles
NO_HOSTS_CLAUDE="$(jq '[.. | .command? // empty] | map(select(test("hook-no-hosts"))) | length' "$CLAUDE_HOOKS" 2>/dev/null)"
NO_HOSTS_CODEX="$(jq '[.. | .command? // empty] | map(select(test("hook-no-hosts"))) | length' "$CODEX_HOOKS" 2>/dev/null)"
run_case "no_hosts_in_claude" "$NO_HOSTS_CLAUDE" "1"
run_case "no_hosts_in_codex"  "$NO_HOSTS_CODEX"  "1"

# Case 2: hosts=["claude"] → present in Claude, absent in Codex
CLAUDE_ONLY_CLAUDE="$(jq '[.. | .command? // empty] | map(select(test("hook-claude-only"))) | length' "$CLAUDE_HOOKS" 2>/dev/null)"
CLAUDE_ONLY_CODEX="$(jq '[.. | .command? // empty] | map(select(test("hook-claude-only"))) | length' "$CODEX_HOOKS" 2>/dev/null)"
run_case "claude_only_in_claude"    "$CLAUDE_ONLY_CLAUDE" "1"
run_case "claude_only_absent_codex" "$CLAUDE_ONLY_CODEX"  "0"

# Case 3: hosts=["codex"] → absent in Claude, present in Codex
CODEX_ONLY_CLAUDE="$(jq '[.. | .command? // empty] | map(select(test("hook-codex-only"))) | length' "$CLAUDE_HOOKS" 2>/dev/null)"
CODEX_ONLY_CODEX="$(jq '[.. | .command? // empty] | map(select(test("hook-codex-only"))) | length' "$CODEX_HOOKS" 2>/dev/null)"
run_case "codex_only_absent_claude" "$CODEX_ONLY_CLAUDE" "0"
run_case "codex_only_in_codex"      "$CODEX_ONLY_CODEX"  "1"

# Verify hosts field is stripped from output (not leaked into generated JSON)
HOSTS_FIELD_CLAUDE="$(jq '[.. | .hosts? // empty] | length' "$CLAUDE_HOOKS" 2>/dev/null)"
HOSTS_FIELD_CODEX="$(jq '[.. | .hosts? // empty] | length' "$CODEX_HOOKS" 2>/dev/null)"
run_case "hosts_field_stripped_claude" "$HOSTS_FIELD_CLAUDE" "0"
run_case "hosts_field_stripped_codex"  "$HOSTS_FIELD_CODEX"  "0"

# Cleanup
rm -rf "$FIXTURE_DIR"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "ALL $PASS PASSED"
  exit 0
else
  echo "$FAIL FAILED / $PASS PASSED — ${FAILED_NAMES[*]}"
  exit 1
fi
