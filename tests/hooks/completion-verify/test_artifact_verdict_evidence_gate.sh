#!/bin/bash
# Tests for completion-verify/artifact-verdict-evidence-gate (Stop hook, issue #862).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/artifact-verdict-evidence-gate/impl.py"

unset PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE PRAXIS_ARTIFACT_VERDICT_STRICT PRAXIS_HOOK_ERROR_STDERR

PASS=0
FAIL=0

# build_transcript <final_text> -> writes path to $TRANSCRIPT
build_transcript() {
  local final_text="$1"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" <<'PY'
import json, sys
path, final_text = sys.argv[1], sys.argv[2]
events = [
    {"message": {"role": "user", "content": "정리해주세요"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": final_text}]}},
]
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <block|advisory|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  # Advisory/block both arrive as stdout JSON (exit always 0); stderr must
  # stay empty in every case.
  local out err_file
  err_file=$(mktemp)
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  case "$expected" in
    block)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["decision"] == "block", d
assert "[artifact-verdict-evidence-gate]" in d["reason"], d
assert "Verdict-evidence:" in d["reason"], d
' || ok=0
      ;;
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[artifact-verdict-evidence-gate]" in d["systemMessage"], d
assert "decision" not in d, d
' || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc out=<$out> err=<$err>"; FAIL=$((FAIL + 1))
  fi
}

# =====================================================================
# Core regression — the 4 in-scope misjudgements of the 2026-07-26 session
# =====================================================================

# M1: description-similarity merge proposal (bullet list) -> advisory
build_transcript "다음 두 메모리는 통합 후보입니다.

- feedback_retrospect_full_corpus — 같은 룰
- feedback_retrospect_full_session — 중복, 큰 쪽 삭제"
run_case advisory "m1-merge-proposal-no-evidence" '{}'

# M2: deletion candidate table -> advisory
build_transcript "| 후보 | 판정 |
|---|---|
| feedback_atomic_commit | 삭제 후보 |
| feedback_avoid_over_fragmentation | 중복 |"
run_case advisory "m2-deletion-table-no-evidence" '{}'

# M3: CLAUDE.md section dedup candidates -> advisory
build_transcript "정리 대상 섹션입니다.

- L709 Scratchs Repo Workflow — 중복
- L744 Scratch Repo as Verification Ground — 통합 대상"
run_case advisory "m3-section-dedup-no-evidence" '{}'

# M4: promotion queue table with 승계/중복 verdicts -> advisory
build_transcript "| 후보 | 판정 |
|---|---|
| project_mcp_go_port_migration | 승계됨, 삭제 가능 |"
run_case advisory "m4-queue-table-no-evidence" '{}'

# =====================================================================
# Clearing arm — an adjacent Verdict-evidence line silences the gate
# =====================================================================

# Same paragraph (bullet list) -> silent
build_transcript "다음 두 메모리는 통합 후보입니다.

- feedback_a — 중복
- feedback_b — 삭제 후보
Verdict-evidence: sed -n '1,40p' feedback_a.md → 'Facet sibling (병합 아님)' 없음"
run_case silent "clearing-same-paragraph" '{}'

# Immediately-following paragraph (table case — documented divergence) -> silent
build_transcript "| 후보 | 판정 |
|---|---|
| project_mcp_go_port_migration | 삭제 후보 |

Verdict-evidence: sed -n '8,20p' drift_watch.md → '컷오버 완료, last-ported=e4c8edd'"
run_case silent "clearing-next-paragraph-table" '{}'

# Evidence line two paragraphs away -> still fires (adjacency enforced)
build_transcript "| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |

관계없는 문단입니다.

Verdict-evidence: cat foo.md → 내용 없음"
run_case advisory "evidence-too-far-still-fires" '{}'

# =====================================================================
# Trigger requires ALL THREE (marker + framing + list shape)
# =====================================================================

# marker + framing but NO list shape -> silent (prose mention)
build_transcript "이 후보는 중복으로 보입니다. 확인이 필요합니다."
run_case silent "no-list-shape" '{}'

# marker + list shape but NO framing -> silent
build_transcript "- 로그가 중복 출력됩니다
- 재현 조건은 동시 실행입니다"
run_case silent "no-framing" '{}'

# framing + list shape but NO marker -> silent
build_transcript "| 후보 | 상태 |
|---|---|
| foo.md | 정상 |"
run_case silent "no-marker" '{}'

# single bullet (needs >=2) with marker+framing -> silent
build_transcript "정리 대상:

- foo.md 는 중복"
run_case silent "single-bullet-below-threshold" '{}'

# lead-in immediately before the list IS attached (issue #862 M1/M3 shape)
build_transcript "정리 대상입니다.

- foo.md — 중복
- bar.md — 삭제 후보"
run_case advisory "leadin-attaches-to-list" '{}'

# framing two paragraphs away -> silent (only one lead-in paragraph attaches)
build_transcript "정리 대상을 확인했습니다.

관계없는 설명 문단입니다.

- 로그가 중복 출력됩니다
- 재현 조건은 동시 실행입니다"
run_case silent "framing-two-paragraphs-away" '{}'

# =====================================================================
# Fenced blocks are illustrations, not verdicts (codex round 1, P2)
# =====================================================================

# A quoted candidate table inside a fence must NOT fire — this is the shape a
# message documenting the hook itself takes.
build_transcript '훅 발화 조건을 설명합니다.

```
| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |
```

위와 같은 표가 근거 없이 나오면 발화합니다.'
run_case silent "fenced-example-table-does-not-fire" '{}'

# A fenced Verdict-evidence *placeholder* must NOT clear a real adjacent verdict.
build_transcript '| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |

```
Verdict-evidence: <command> → <output>
```'
run_case advisory "fenced-evidence-placeholder-does-not-clear" '{}'

# =====================================================================
# English tokens
# =====================================================================

build_transcript "| candidate | verdict |
|---|---|
| old_port.md | superseded, safe to delete |"
run_case advisory "en-candidate-table-no-evidence" '{}'

build_transcript "| candidate | verdict |
|---|---|
| old_port.md | superseded |

Verdict-evidence: sed -n '1,20p' new_port.md → supersedes old_port"
run_case silent "en-candidate-table-with-evidence" '{}'

# =====================================================================
# Tiers — strict promotion and bypass
# =====================================================================

build_transcript "| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |"
run_case block "strict-promotes-to-block" '{}' PRAXIS_ARTIFACT_VERDICT_STRICT=1

build_transcript "| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |"
run_case advisory "strict-falsey-stays-advisory" '{}' PRAXIS_ARTIFACT_VERDICT_STRICT=0

# Only the exact value `1` promotes — sibling `_STRICT_ENV` convention.
build_transcript "| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |"
run_case advisory "strict-non-one-stays-advisory" '{}' PRAXIS_ARTIFACT_VERDICT_STRICT=FALSE

# Bypass is presence-based across the fleet — `0` still bypasses.
build_transcript "| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |"
run_case silent "bypass-is-presence-based" '{}' PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE=0

build_transcript "| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |"
run_case silent "bypass-env-silences" '{}' PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE=1

# =====================================================================
# Fail-open contract
# =====================================================================

build_transcript "| 후보 | 판정 |
|---|---|
| foo.md | 삭제 후보 |"
run_case silent "stop-hook-active-reentrancy" '{"stop_hook_active":true}'

TRANSCRIPT=/nonexistent/path.jsonl
run_case silent "missing-transcript" '{}'

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
