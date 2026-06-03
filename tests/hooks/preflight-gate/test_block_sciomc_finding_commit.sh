#!/bin/bash
# test_block_sciomc_finding_commit.sh — coverage for hooks/preflight-gate/block-sciomc-finding-commit/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads + scratch transcript
# fixtures and asserts:
#   block  → rc=2, stderr non-empty (BLOCKED: prefix)
#   silent → rc=0, stderr empty
#
# Usage: bash tests/hooks/preflight-gate/test_block_sciomc_finding_commit.sh

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-sciomc-finding-commit/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Transcript fixtures use the REAL Claude Code transcript JSONL schema
# (entries carry `type` + `message.content[]` blocks). Finding markers are
# only honored when they appear in assistant-authored content — assistant
# `text` blocks or Agent/Task subagent `tool_result`s. Markers inside user
# turns, system-reminders, or Read/Skill tool-results (loaded docs) are NOT a
# finding (praxis issue #573). The role-aware parser ignores the simplified
# pre-#573 fixture shape, so these fixtures must use the real schema.

# Fixture: assistant emits sciomc structured finding markers (true positive)
TX_FINDING="$TMPDIR/tx-finding.jsonl"
cat > "$TX_FINDING" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Running sciomc analysis"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[FINDING:F1] sibling-deviant pattern detected"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: assistant emits a [CONFLICTS:] marker
TX_CONFLICTS="$TMPDIR/tx-conflicts.jsonl"
cat > "$TX_CONFLICTS" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"sciomc output:"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[CONFLICTS: design choice in PR body vs sibling convention]"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: [CONFLICTS:] payload that includes "user-stated design" text — must still block
TX_CONFLICTS_USD="$TMPDIR/tx-conflicts-usd.jsonl"
cat > "$TX_CONFLICTS_USD" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"sciomc output:"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[CONFLICTS: user-stated design vs sibling convention]"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: assistant emits a [STAGE_COMPLETE:N] marker
TX_STAGE_COMPLETE="$TMPDIR/tx-stage-complete.jsonl"
cat > "$TX_STAGE_COMPLETE" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"sciomc output:"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[STAGE_COMPLETE:2] stage 2 done"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: assistant emits 의미 mismatch marker
TX_UIMI_MISMATCH="$TMPDIR/tx-uimi-mismatch.jsonl"
cat > "$TX_UIMI_MISMATCH" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"의미 mismatch detected in sibling convention analysis"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: assistant emits 의미 충돌 marker
TX_UIMI_CONFLICT="$TMPDIR/tx-uimi-conflict.jsonl"
cat > "$TX_UIMI_CONFLICT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"의미 충돌: 두 sibling 간 설계 불일치"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: 의미 충돌 + Hangul particle attachment (조사 결합)
TX_UIMI_CONFLICT_PARTICLE="$TMPDIR/tx-uimi-conflict-particle.jsonl"
cat > "$TX_UIMI_CONFLICT_PARTICLE" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"의미 충돌이 감지됩니다 — sibling 간 설계 충돌"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: 의미 mismatch + Hangul particle attachment (조사 결합)
TX_UIMI_MISMATCH_PARTICLE="$TMPDIR/tx-uimi-mismatch-particle.jsonl"
cat > "$TX_UIMI_MISMATCH_PARTICLE" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"의미 mismatch가 있습니다 — 정렬 필요"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# Fixture: assistant finding, then a `gh pr view --json body` re-fetch recorded
# as an assistant Bash tool_use (consensus re-fetch AFTER the finding)
TX_FINDING_REFETCH="$TMPDIR/tx-finding-refetch.jsonl"
cat > "$TX_FINDING_REFETCH" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"sibling-deviant pattern detected"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_b1","name":"Bash","input":{"command":"gh pr view 8299 --json body --jq .body"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"compared with user design"}]}}
EOF

# Fixture: no finding markers (prose-only, no structured tokens)
TX_NORMAL="$TMPDIR/tx-normal.jsonl"
cat > "$TX_NORMAL" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"normal fix work"}]}}
EOF

# Fixture: removed-prose-only markers (FP regression)
TX_FP_PROSE="$TMPDIR/tx-fp-prose.jsonl"
cat > "$TX_FP_PROSE" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"deep-dive 하자"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"cross-validation 결과를 확인했다"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"sciomc 라는 도구가 있음"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"scientist-agent 가 실행됨"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Stage 2 analysis 시작"}]}}
EOF

# ---------------------------------------------------------------------------
# Role-aware corpus fixtures (praxis issue #573) — surface enumeration of
# every place a finding marker can appear in a transcript.
# ---------------------------------------------------------------------------

# (BLOCK) Agent/Task subagent tool-result carrying real sciomc finding output.
# The tool_use (Agent) and its tool_result are resolved by tool_use_id.
TX_AGENT_RESULT="$TMPDIR/tx-agent-result.jsonl"
cat > "$TX_AGENT_RESULT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_agent1","name":"Agent","input":{"subagent_type":"sciomc","prompt":"analyze the design"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_agent1","content":"[FINDING:F2] sibling-deviant detected by subagent analysis"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# (BLOCK) design-flip regression: assistant emits [CONFLICTS:] then commits
# with no re-fetch in between — the original sciomc gate intent must hold.
TX_DESIGN_FLIP="$TMPDIR/tx-design-flip.jsonl"
cat > "$TX_DESIGN_FLIP" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[CONFLICTS: user PR-body literal vs sibling convention] — flipping the literal"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_e1","name":"Edit","input":{"file_path":"/x/q.sql","old_string":"a","new_string":"b"}}]}}
EOF

# (SILENT) user-turn skill-load: SKILL.md documentation naming the tokens.
TX_SKILL_LOAD_DOC="$TMPDIR/tx-skill-load-doc.jsonl"
cat > "$TX_SKILL_LOAD_DOC" <<'EOF'
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"<command-message>praxis:sciomc loaded</command-message> The output schema documents [FINDING:Fn], [CONFLICTS: A vs B] and [STAGE_COMPLETE:3] tokens."}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# (SILENT) system-reminder block referencing the tokens (e.g. CLAUDE.md context).
TX_SYSTEM_REMINDER="$TMPDIR/tx-system-reminder.jsonl"
cat > "$TX_SYSTEM_REMINDER" <<'EOF'
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"<system-reminder>The sciomc skill defines [FINDING:], [CONFLICTS:] and [STAGE_COMPLETE:2] markers in its output schema. 의미 충돌 also appears here.</system-reminder>"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# (SILENT) Read tool-result returning a SKILL.md file body containing the tokens.
TX_READ_RESULT_DOC="$TMPDIR/tx-read-result-doc.jsonl"
cat > "$TX_READ_RESULT_DOC" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_read1","name":"Read","input":{"file_path":"/x/skills/sciomc/SKILL.md"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_read1","content":"## Output schema\nEmit [FINDING:Fn] per finding, [CONFLICTS: A vs B] on design conflict, [STAGE_COMPLETE:N] per stage. sibling-deviant 의미 충돌 의미 mismatch."}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF

# run_case name expectation payload_json [env_vars...]
run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  local env_args=()
  for kv in "$@"; do env_args+=("$kv"); done

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env -u CLAUDE_HOOK_BYPASS_SCIOMC_GATE python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "^BLOCKED:" || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

echo "test_block_sciomc_finding_commit"

# ---------------------------------------------------------------------------
# BLOCK cases — finding marker present + no consensus re-fetch after
# ---------------------------------------------------------------------------

run_case "git commit + finding + no refetch (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: flip literal'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit -am + finding + no refetch (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am 'fix: stuff'\"},\"transcript_path\":\"$TX_FINDING\"}"

# --allow-empty does NOT prevent staged content from being committed — must block
run_case "git commit --allow-empty with staged content (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --allow-empty -m 'trigger ci'\"},\"transcript_path\":\"$TX_FINDING\"}"

# --allow-empty-message likewise does not prevent staged content — must block
run_case "git commit --allow-empty-message with staged content (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --allow-empty-message -m ''\"},\"transcript_path\":\"$TX_FINDING\"}"

# `--amend` inside the -m message value is NOT the amend flag — must still block
run_case "amend word inside message still blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'docs: explain the --amend flag'\"},\"transcript_path\":\"$TX_FINDING\"}"

# `-m --amend`: the message VALUE is exactly "--amend" — must NOT be exempted
run_case "git commit -m --amend (value is --amend, not flag) blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m --amend\"},\"transcript_path\":\"$TX_FINDING\"}"

# `-am --amend`: clustered flag consumes "--amend" as the message value — must block
run_case "git commit -am --amend (clustered, value is --amend) blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am --amend\"},\"transcript_path\":\"$TX_FINDING\"}"

# ratification token outside the -m value must NOT bypass
run_case "user-approved token outside message (semicolon) still blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: x'; echo '[user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# subshell-grouped commit: shlex tokenizes binary as "(git" — must still block
# (regression guard: the prior raw-regex caught this; token parser must too)
run_case "subshell-grouped git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"(git commit -m 'fix: sub')\"},\"transcript_path\":\"$TX_FINDING\"}"

# command-substitution wrapped commit: binary tokenizes as "\$(git" — must still block
run_case "command-substitution git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \$(git commit -m 'fix: subst')\"},\"transcript_path\":\"$TX_FINDING\"}"

# QUOTED command-substitution: bash executes the inner commit even inside double
# quotes (shlex folds it into one token) — hybrid span scan must catch it
run_case "quoted command-substitution echo (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"\$(git commit -m subst)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# assignment with quoted command-substitution
run_case "assignment quoted command-substitution (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"out=\\\"\$(git commit -m subst)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# quoted command-substitution of a NON-commit must NOT match
run_case "quoted command-substitution git status (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"\$(git status)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# nested command-substitution containing a commit (paren-depth scan)
run_case "nested command-substitution git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"\$(echo \$(git commit -m nested))\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# SINGLE-quoted substitution is a literal — bash does NOT execute it → silent
run_case "single-quoted substitution literal (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo '\$(git commit -m x)'\"},\"transcript_path\":\"$TX_FINDING\"}"

# apostrophe inside double quotes is literal — substitution still executes → block
run_case "apostrophe in double-quoted substitution (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"it's \$(git commit -m x)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# space-less shell separator: `true;git commit` must tokenize git as its own token
run_case "no-space semicolon separator before git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"true;git commit -m 'fix: chained'\"},\"transcript_path\":\"$TX_FINDING\"}"

# space-less && separator
run_case "no-space && separator before git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status&&git commit -m 'fix: andand'\"},\"transcript_path\":\"$TX_FINDING\"}"

# a literal ';' INSIDE the quoted message must NOT be treated as a separator
run_case "semicolon inside message value still blocks (no token)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: a;b'\"},\"transcript_path\":\"$TX_FINDING\"}"

# NEW: [CONFLICTS:] marker (Layer 2 addition — sciomc emits this for design conflicts)
run_case "[CONFLICTS:] marker blocks commit" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: use sibling pattern'\"},\"transcript_path\":\"$TX_CONFLICTS\"}"

# [CONFLICTS:] payload containing "user-stated design" must not self-satisfy re-fetch check
run_case "[CONFLICTS: user-stated design] does not self-satisfy re-fetch (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: apply finding'\"},\"transcript_path\":\"$TX_CONFLICTS_USD\"}"

# NEW: [STAGE_COMPLETE:N] marker with digit
run_case "[STAGE_COMPLETE:2] marker blocks commit" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: stage 2 result'\"},\"transcript_path\":\"$TX_STAGE_COMPLETE\"}"

# NEW: 의미 mismatch Korean praxis-unique marker
run_case "의미 mismatch marker blocks commit" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: align'\"},\"transcript_path\":\"$TX_UIMI_MISMATCH\"}"

# NEW: 의미 충돌 Korean praxis-unique marker
run_case "의미 충돌 marker blocks commit" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: resolve'\"},\"transcript_path\":\"$TX_UIMI_CONFLICT\"}"

# Korean particle attachment: 의미 충돌이 / 의미 mismatch가 must also block
run_case "의미 충돌이 (particle attachment) blocks commit" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: resolve'\"},\"transcript_path\":\"$TX_UIMI_CONFLICT_PARTICLE\"}"

run_case "의미 mismatch가 (particle attachment) blocks commit" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: align'\"},\"transcript_path\":\"$TX_UIMI_MISMATCH_PARTICLE\"}"

# ---------------------------------------------------------------------------
# Role-aware corpus BLOCK cases (praxis issue #573) — markers in genuine
# agent-authored content must STILL block (no false-negative regression).
# ---------------------------------------------------------------------------

# Agent/Task subagent tool-result with a real finding — true positive, blocks.
run_case "Agent subagent tool-result finding blocks commit" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: apply subagent finding'\"},\"transcript_path\":\"$TX_AGENT_RESULT\"}"

# Design-flip: assistant emits [CONFLICTS:] then commits without re-fetch —
# the original gate intent must hold (regression guard against the #573 fix
# introducing a false-negative).
run_case "design-flip [CONFLICTS:] in assistant text still blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: flip literal'\"},\"transcript_path\":\"$TX_DESIGN_FLIP\"}"

# ---------------------------------------------------------------------------
# SILENT cases — should not block
# ---------------------------------------------------------------------------

# ---- Role-aware corpus FP cases (praxis issue #573) ----
# Markers that appear only in LOADED documentation / non-agent content must
# NOT block a benign commit.

# user-turn skill-load documenting the token schema → pass
run_case "skill-load SKILL.md doc markers do not block (silent — #573)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'docs: ingest knowledge base'\"},\"transcript_path\":\"$TX_SKILL_LOAD_DOC\"}"

# system-reminder block referencing the tokens → pass
run_case "system-reminder markers do not block (silent — #573)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: tidy'\"},\"transcript_path\":\"$TX_SYSTEM_REMINDER\"}"

# Read tool-result returning a SKILL.md body with the tokens → pass
run_case "Read tool-result SKILL.md body does not block (silent — #573)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'docs: update'\"},\"transcript_path\":\"$TX_READ_RESULT_DOC\"}"

run_case "git commit + finding + gh pr view --json body AFTER (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: aligned'\"},\"transcript_path\":\"$TX_FINDING_REFETCH\"}"

run_case "git commit + no finding in transcript (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: bump'\"},\"transcript_path\":\"$TX_NORMAL\"}"

run_case "git commit + finding + [user-approved] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit + finding + [ratified-by-user] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [ratified-by-user]'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit + finding + [user-ratified] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [user-ratified]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# clustered short option `-am`: ratification token in message value must bypass
run_case "git commit -am with [user-approved] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am 'fix: change [user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# attached form `-am'msg'` tokenizes as -ammsg — token must still be extracted
run_case "git commit -ammsg attached with [user-approved] (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am'done [user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# `-am` content commit WITHOUT a token must still block (escape-hatch is opt-in)
run_case "git commit -am without token still blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am 'fix: real change'\"},\"transcript_path\":\"$TX_FINDING\"}"

# legit --amend is still exempt (it fixes up a prior already-gated commit)
run_case "git commit --amend + finding (silent — exempt)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --amend --no-edit\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git revert + finding (silent — non-content)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git revert abc123\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git merge + finding (silent — non-content)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git merge origin/main\"},\"transcript_path\":\"$TX_FINDING\"}"

# git commit-tree is plumbing (one token "commit-tree") — must NOT match
run_case "git commit-tree plumbing (silent — not a git commit)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit-tree abc123 -m 'tree'\"},\"transcript_path\":\"$TX_FINDING\"}"

# `git --help commit` / `git --version commit` are terminal — no commit runs
run_case "git --help commit (silent — terminal option)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git --help commit\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git --version commit (silent — terminal option)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git --version commit\"},\"transcript_path\":\"$TX_FINDING\"}"

# echo containing "git commit" — no token-level git+commit adjacency
run_case "echo git commit string not a commit (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo 'run git commit later'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "non-Bash tool (silent)" \
  "silent" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"/tmp/x\",\"old_string\":\"a\",\"new_string\":\"b\"}}"

run_case "git status (non-commit, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"},\"transcript_path\":\"$TX_FINDING\"}"

# subshell-grouped non-commit must NOT over-match (prefix-strip is binary-only)
run_case "subshell-grouped git status (silent — not a commit)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"(git status)\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "env var bypass (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix'\"},\"transcript_path\":\"$TX_FINDING\"}" \
  "CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1"

run_case "missing transcript_path (silent — cannot enforce)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix'\"}}"

run_case "malformed JSON (silent — fail-open)" \
  "silent" \
  "not-json"

# ---------------------------------------------------------------------------
# FP-regression cases — removed loose-prose markers must NOT block
# ---------------------------------------------------------------------------

# bare 'sciomc' prose mention — no longer a marker (was FP source)
run_case "bare sciomc prose mention (silent — FP regression)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: doc'\"},\"transcript_path\":\"$TX_FP_PROSE\"}"

# 'scientist-agent' prose — no longer a marker
run_case "scientist-agent prose (silent — FP regression)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: doc'\"},\"transcript_path\":\"$TX_FP_PROSE\"}"

# 'deep-dive' prose — no longer a marker
run_case "deep-dive prose (silent — FP regression)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: doc'\"},\"transcript_path\":\"$TX_FP_PROSE\"}"

# 'cross-validation' prose — no longer a marker
run_case "cross-validation prose (silent — FP regression)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: doc'\"},\"transcript_path\":\"$TX_FP_PROSE\"}"

# loose 'Stage 2 analysis' prose — no longer a marker
run_case "loose Stage N analysis prose (silent — FP regression)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: doc'\"},\"transcript_path\":\"$TX_FP_PROSE\"}"

# [STAGE_COMPLETE: without digit (malformed/bare token) must NOT match — only
# [STAGE_COMPLETE:<digit> is a valid sciomc output token
TX_STAGE_COMPLETE_NO_DIGIT="$TMPDIR/tx-stage-complete-nodigit.jsonl"
cat > "$TX_STAGE_COMPLETE_NO_DIGIT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"[STAGE_COMPLETE: some text without digit after colon]"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"about to commit"}]}}
EOF
run_case "[STAGE_COMPLETE: without digit does not block (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: doc'\"},\"transcript_path\":\"$TX_STAGE_COMPLETE_NO_DIGIT\"}"

# ---------------------------------------------------------------------------
# Uncaught exception fail-open (outer Exception guard)
# ---------------------------------------------------------------------------

# main() now opts into the shared @fail_open guard; verify the decorator is
# applied (fail-open behaviour itself is covered in tests/test_hook_runtime.sh).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
TOTAL=$((PASS + FAIL))
echo "Result: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
