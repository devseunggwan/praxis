#!/usr/bin/env bash
# test_menu_mutation_tier.sh — coverage for
# hooks/advisory-nudge/menu-mutation-tier-advisory/impl.py
#
# Synthesizes Claude Code PreToolUse(AskUserQuestion) payloads and asserts:
#   advisory → exit 0 + stderr non-empty  (default mode)
#   block    → exit 2 + stderr non-empty  (PRAXIS_MENU_MUTATION_TIER_STRICT=1)
#   pass     → exit 0 + stderr empty
#
# The hook inspects only options[].label + options[].description — no
# transcript, no git, no subprocess.
#
# Usage: bash tests/hooks/advisory-nudge/test_menu_mutation_tier.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/menu-mutation-tier-advisory/impl.py"
SPEC="$ROOT_DIR/hooks/advisory-nudge/menu-mutation-tier-advisory/spec.md"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# Build a JSON payload for an AskUserQuestion tool call.
# $1 = JSON array whose entries are either a label string or a
#      [label, description] pair.
build_payload() {
  local options_json="$1"
  python3 - "$options_json" <<'PY'
import json, sys
entries = json.loads(sys.argv[1])
options = []
for e in entries:
    if isinstance(e, list):
        label, desc = e[0], e[1]
    else:
        label, desc = e, ""
    options.append({"label": label, "description": desc})
payload = {
    "session_id": "test-session",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "Which?",
                "header": "Choose",
                "multiSelect": False,
                "options": options,
            }
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PY
}

# run_case name expected mode payload
#   expected: advisory | block | pass
#   mode:     default            → no STRICT env set
#             strict             → PRAXIS_MENU_MUTATION_TIER_STRICT=1
#             strictval=<value>  → PRAXIS_MENU_MUTATION_TIER_STRICT=<value>
run_case() {
  local name="$1" expected="$2" mode="$3" payload="$4"
  local err_file rc

  err_file=$(mktemp)
  case "$mode" in
    strict)
      echo "$payload" | PRAXIS_MENU_MUTATION_TIER_STRICT=1 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    strictval=*)
      echo "$payload" | PRAXIS_MENU_MUTATION_TIER_STRICT="${mode#strictval=}" "$HOOK" >/dev/null 2>"$err_file"
      ;;
    *)
      echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
      ;;
  esac
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    advisory) [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0 ;;
    block)    [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0 ;;
    pass)     [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0 ;;
    *) echo "INTERNAL: unknown expected '$expected'" >&2; ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# (a) ADVISORY — every candidate sits in the mutating tier
# ---------------------------------------------------------------------------

# The 2026-08-11 incident menu, replayed verbatim. Its third option is an
# abandonment ("다음 정기 실행에 맡김"), which must NOT count as the safe tier —
# if it did, the hook would be silent on the exact menu it exists for.
INCIDENT='[["두 소스 다 트리거 (Recommended)", "두 소스 모두 지금 트리거한다"], ["한 건만 먼저", "한 건만 먼저 처리한다"], ["다음 정기 실행에 맡김", "이번에는 아무것도 하지 않는다"]]'
run_case "2026-08-11 incident menu"          advisory default "$(build_payload "$INCIDENT")"

run_case "EN all-prod menu"                  advisory default "$(build_payload '["deploy to prod (all tenants)", "deploy to prod (one tenant)"]')"
run_case "KO 실 고객 all-mutating"             advisory default "$(build_payload '["실 고객 테넌트 전체 트리거", "실 고객 한 곳만 트리거"]')"
run_case "mixed-script prod 트리거"            advisory default "$(build_payload '["prod 트리거 전체", "prod 트리거 부분"]')"
run_case "uppercase EN mutation token"       advisory default "$(build_payload '["APPLY TO PROD", "APPLY TO PROD PARTIALLY"]')"
run_case "delete/truncate menu"              advisory default "$(build_payload '["delete the table", "truncate the table"]')"

# Signal carried only in the description (elliptical label). This is why the
# hook reads label + description, not label alone.
DESC_ONLY='[["전체", "prod 테넌트 전체에 적용한다"], ["일부", "일부만 먼저 적용한다"]]'
run_case "mutation signal in description"    advisory default "$(build_payload "$DESC_ONLY")"

# ---------------------------------------------------------------------------
# (b) PASS — a non-mutating tier IS on the menu
# ---------------------------------------------------------------------------
run_case "preview option present"    pass default "$(build_payload '["prod 트리거 전체", "preview 에서 먼저 검증", "prod 트리거 부분"]')"
run_case "dev option present"        pass default "$(build_payload '["deploy to prod", "deploy to dev first"]')"
run_case "sandbox option present"    pass default "$(build_payload '["apply to prod", "run in a sandbox"]')"
run_case "dry-run option present"    pass default "$(build_payload '["트리거 실행", "dry-run 으로 먼저"]')"
run_case "KO 보고만 present"          pass default "$(build_payload '["실 고객 트리거", "보고만 하고 멈춤"]')"
run_case "KO 조회만 present"          pass default "$(build_payload '["prod 삭제", "조회만 해서 영향 확인"]')"
run_case "review lever present"      pass default "$(build_payload '["squash 머지", "code-reviewer 재실행", "머지 후 정리"]')"
run_case "KO 리뷰 lever present"      pass default "$(build_payload '["prod 배포", "리뷰 한번 더", "prod 배포 부분"]')"
run_case "low-blast in description"  pass default "$(build_payload '[["전체", "prod 전체 적용"], ["먼저", "preview 환경에서 먼저 확인"]]')"

# ---------------------------------------------------------------------------
# (c) PASS — menu is not tier-relevant
# ---------------------------------------------------------------------------
run_case "doc tone chooser"          pass default "$(build_payload '["격식 있는 톤", "친근한 톤", "중립적인 톤"]')"
run_case "EN plan chooser"           pass default "$(build_payload '["Plan A", "Plan B", "Plan C"]')"
run_case "no mutation token"         pass default "$(build_payload '["문서만 고친다", "테스트만 더 쓴다"]')"
run_case "empty options"             pass default "$(build_payload '[]')"
run_case "single option"             pass default "$(build_payload '["prod 트리거"]')"

# Whole-token discipline: an inflected form is not the token. Neither of these
# is a mutation signal, so the menu is not tier-relevant.
run_case "false-positive: merged/merger"   pass default "$(build_payload '["view merged PRs", "contact the merger"]')"
run_case "false-positive: deployment doc"  pass default "$(build_payload '["read the deployment doc", "read the applying guide"]')"

# ---------------------------------------------------------------------------
# (d) Abandonment is NOT a tier — the issue's open question, pinned
# ---------------------------------------------------------------------------
# An abandonment option does not suppress: the remaining candidates are still
# uniformly mutating, and the user still has no safe way to verify.
run_case "abandon '다음 정기' does not suppress"  advisory default "$(build_payload '["prod 트리거 A", "prod 트리거 B", "다음 정기 실행에 맡김"]')"
run_case "abandon '하지 않음' does not suppress"  advisory default "$(build_payload '["실 고객 적용 전체", "실 고객 적용 일부", "아무것도 하지 않음"]')"
run_case "abandon 'skip it' does not suppress"  advisory default "$(build_payload '["deploy to prod", "deploy to prod partially", "skip it for now"]')"

# ...but it is also not a candidate: a binary go/no-go menu leaves exactly one
# real candidate, which IS a genuine "whether", so no nudge.
run_case "binary 머지/대기 → not a tier menu"     pass default "$(build_payload '["squash 머지", "대기"]')"
run_case "binary deploy/cancel → not a tier menu" pass default "$(build_payload '["deploy to prod", "cancel"]')"
run_case "binary 배포/하지 않음 → not a tier menu"  pass default "$(build_payload '["prod 배포", "하지 않음"]')"

# The paired control: the same shape with a real low-blast alternative in the
# abandonment slot must pass.
run_case "safe tier replaces abandon slot"  pass default "$(build_payload '["prod 트리거 A", "prod 트리거 B", "preview 에서 먼저"]')"

# ---------------------------------------------------------------------------
# (d2) A low-blast token next to a high-blast target does not suppress
# ---------------------------------------------------------------------------
# Codex round-1 [P1]: a compound option names the safe step and then the prod
# one, and ends on the shared surface either way. Before the fix, the `dry-run`
# token in the first option silenced the whole menu even in strict mode.
run_case "hybrid dry-run→prod does not suppress"  advisory default "$(build_payload '["Dry-run then deploy to prod", "Deploy directly to prod"]')"
run_case "hybrid preview→prod (KO)"              advisory default "$(build_payload '["preview 확인 후 prod 트리거", "바로 prod 트리거"]')"
run_case "strict: hybrid dry-run→prod blocks"    block strict "$(build_payload '["Dry-run then deploy to prod", "Deploy directly to prod"]')"

# The paired control: a mutation VERB aimed at a safe target is still low-blast.
# Only a high-blast TARGET disqualifies an option, never the verb.
run_case "mutating verb at safe target suppresses"  pass default "$(build_payload '["deploy to prod", "deploy to dev first"]')"
run_case "KO 개발 환경 배포 suppresses"              pass default "$(build_payload '["prod 배포", "개발 환경에 먼저 배포"]')"

# Codex round-1 [P2]: `development` is not the whole token `dev`.
run_case "development recognised as low-blast"   pass default "$(build_payload '["deploy to production", "deploy to development first"]')"
run_case "strict: development → no block"        pass strict "$(build_payload '["deploy to production", "deploy to development first"]')"

# Codex round-1 [P2]: an external write is a shared-state mutation too.
run_case "external write: slack/email menu"      advisory default "$(build_payload '["Send Slack now", "Send email instead"]')"
run_case "external write KO: 전송/발송"           advisory default "$(build_payload '["지금 전체 공지 전송", "일부에게만 발송"]')"
run_case "external write + safe tier suppresses" pass default "$(build_payload '["Send Slack now", "preview the message only"]')"

# ---------------------------------------------------------------------------
# (d3) Abandonment is removed BEFORE low-blast is read
# ---------------------------------------------------------------------------
# CodeRabbit round-1: `skip this run and review later` carries an abandonment
# token AND the low-blast `review`. Reading low-blast first let that one option
# suppress an otherwise all-prod menu.
run_case "abandon+low-blast overlap does not suppress" advisory default "$(build_payload '["deploy to prod now", "deploy to prod partially", "skip this run and review later"]')"
run_case "strict: overlap form blocks"                 block strict "$(build_payload '["deploy to prod now", "deploy to prod partially", "skip this run and review later"]')"
run_case "KO 대기+검토 overlap does not suppress"        advisory default "$(build_payload '["prod 트리거 A", "prod 트리거 B", "대기했다가 나중에 검토"]')"

# ---------------------------------------------------------------------------
# (d4) A stated reason suppresses — the advisory's second option
# ---------------------------------------------------------------------------
# The advisory asks for one of two things: add a safe option, or say why one is
# impossible. Without honouring the second, strict mode is a gate nothing can
# pass but an extra option.
REASON_OK='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"prod 트리거 승인\nSafe-tier-unavailable: preview 클러스터에 이 소스의 커넥터가 없다","header":"트리거","options":[{"label":"두 소스 다 트리거","description":"d"},{"label":"한 건만 먼저","description":"prod 트리거"}]}]}}'
run_case "stated reason suppresses"          pass default "$REASON_OK"
run_case "strict: stated reason suppresses"  pass strict "$REASON_OK"

# The marker must be a real line with real content, mirroring the sibling
# `Falsified:` contract — prose mentions and empty markers do not count.
REASON_PROSE='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"prod 트리거 승인 (Safe-tier-unavailable: 인라인 언급)","header":"트리거","options":[{"label":"두 소스 다 트리거","description":"d"},{"label":"한 건만 먼저","description":"prod 트리거"}]}]}}'
run_case "reason marker mid-line does not count"  advisory default "$REASON_PROSE"
REASON_EMPTY='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"prod 트리거 승인\nSafe-tier-unavailable:   ","header":"트리거","options":[{"label":"두 소스 다 트리거","description":"d"},{"label":"한 건만 먼저","description":"prod 트리거"}]}]}}'
run_case "empty reason marker does not count"     advisory default "$REASON_EMPTY"

# Per-question: a reason on q1 must not cover an all-prod q2.
REASON_Q1_ONLY='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q1\nSafe-tier-unavailable: 이 소스는 preview 미지원","header":"a","options":[{"label":"prod 트리거 A","description":"d"},{"label":"prod 트리거 B","description":"d"}]},{"question":"q2","header":"b","options":[{"label":"prod 배포 전체","description":"d"},{"label":"prod 배포 일부","description":"d"}]}]}}'
run_case "reason on q1 does not cover q2"  advisory default "$REASON_Q1_ONLY"

# ---------------------------------------------------------------------------
# (d5) The marker is read as prose — fenced blocks and HTML comments are not
# ---------------------------------------------------------------------------
# CodeRabbit round-2: a fenced example's content sits at column 0 exactly like a
# real marker, so documenting the marker suppressed the very advisory that asks
# for it. spec.md and the impl comment both already promised fences do not
# count, so this was code contradicting its own stated contract.
#
# The whole marker surface is enumerated here, not just the reported fence:
# every wrapper that puts a line at column 0 without a reader seeing a stated
# reason. `mid-line` / `empty` / per-question live in (d4) above.
mk_reason() {  # $1 = question body (JSON-escaped)
  printf '{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"%s","header":"트리거","options":[{"label":"두 소스 다 트리거","description":"d"},{"label":"한 건만 먼저","description":"prod 트리거"}]}]}}' "$1"
}
M='Safe-tier-unavailable: preview 클러스터에 커넥터가 없다'
run_case "backtick fence does not count"   advisory default "$(mk_reason "예시:\n\`\`\`text\n$M\n\`\`\`")"
run_case "strict: backtick fence blocks"   block    strict  "$(mk_reason "예시:\n\`\`\`text\n$M\n\`\`\`")"
run_case "tilde fence does not count"      advisory default "$(mk_reason "예시:\n~~~\n$M\n~~~")"
run_case "4-backtick fence does not count" advisory default "$(mk_reason "예시:\n\`\`\`\`\n$M\n\`\`\`\`")"
# An unterminated fence swallows the rest on purpose: the author is showing an
# example, not stating a reason.
run_case "unclosed fence does not count"   advisory default "$(mk_reason "예시:\n\`\`\`\n$M")"
run_case "html comment does not count"     advisory default "$(mk_reason "<!--\n$M\n-->")"
run_case "indented marker does not count"  advisory default "$(mk_reason "예시:\n    $M")"
run_case "inline backticks do not count"   advisory default "$(mk_reason "\`$M\`")"
# Paired control: the fence must not swallow a real marker that follows it, or
# the fix would suppress the feature instead of the bypass.
run_case "marker after closed fence counts" pass default "$(mk_reason "\`\`\`\nnoise\n\`\`\`\n$M")"

# ---------------------------------------------------------------------------
# (d6) The advisory reaches the model, not just the terminal
# ---------------------------------------------------------------------------
# CodeRabbit + codex both flagged this: stderr is invisible to the model
# (CONTRIBUTING.md "Advisory output is not visible to the model"), so an
# advisory asking the composing agent to add a tier could never be acted on.
# The sibling `output-block-falsify-advisory` gates the same event/matcher via
# `emit_decision("ask", …)`; this pins that channel so a regression to
# stderr-only fails here rather than silently.
adv_stdout=$(printf '%s' "$(build_payload "$INCIDENT")" | "$HOOK" 2>/dev/null)
if printf '%s' "$adv_stdout" | grep -q '"permissionDecision": "ask"'; then
  PASS=$((PASS + 1)); echo "PASS [advisory] nudge is emitted on the model-visible channel"
else
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("model-visible channel"); echo "FAIL [advisory] nudge is emitted on the model-visible channel"
fi

# Paired control: a silent menu must emit NO decision JSON. Without this, a
# hook that always emitted "ask" would pass the assertion above.
silent_stdout=$(printf '%s' "$(build_payload '["preview 에서 먼저 확인", "prod 트리거"]')" | "$HOOK" 2>/dev/null)
if [ -z "$silent_stdout" ]; then
  PASS=$((PASS + 1)); echo "PASS [pass] silent menu emits no decision JSON"
else
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("silent emits no JSON"); echo "FAIL [pass] silent menu emits no decision JSON"
fi

# ---------------------------------------------------------------------------
# (d7) Tier 1c — `create` / `update` count only on a shared surface
# ---------------------------------------------------------------------------
# Issue #974 gap 1 (codex round-1 on PR #966): a menu whose every option mutates
# was silent because `create` / `update` were in no vocabulary set at all.
#
# They are gated rather than added to MUTATION_VERBS because they are also
# ordinary English for authoring. The unconditional variant was built and
# measured: it fired on all four negative cases below. Since PR #966 rev 5 the
# default path is `emit_decision("ask", …)`, so each of those is a human
# confirmation prompt, not a log line.
run_case "Tier 1c: create/update a record fires"   advisory default "$(build_payload '["Create the customer record", "Update the customer record"]')"
run_case "strict: Tier 1c record blocks"           block    strict  "$(build_payload '["Create the customer record", "Update the customer record"]')"
run_case "Tier 1c KO: 고객 레코드 생성/갱신"          advisory default "$(build_payload '["고객 레코드 생성", "고객 레코드 갱신"]')"
run_case "Tier 1c: tenants/accounts fire"          advisory default "$(build_payload '["Create the tenant accounts", "Update the tenant accounts"]')"

# The paired control: a safe tier still suppresses a Tier 1c menu, exactly as it
# does a Tier 1b one. Tier 1c changes what counts as mutating, nothing else.
run_case "Tier 1c + safe tier suppresses"          pass default "$(build_payload '["Create the customer record", "Preview the change first"]')"

# Negatives — the measured false fires of the unconditional variant. Each of
# these must stay silent, and this is the block that fails if a future round
# moves `create` / `update` into MUTATION_VERBS_EN.
run_case "Tier 1c neg: local file menu"        pass default "$(build_payload '["Create a new test file", "Update the existing test"]')"
run_case "Tier 1c neg: doc menu"               pass default "$(build_payload '["Create a README section", "Update the changelog"]')"
run_case "Tier 1c neg: KO 문서 생성/갱신"        pass default "$(build_payload '["문서 생성", "문서 갱신"]')"
run_case "Tier 1c neg: prompt wording menu"    pass default "$(build_payload '["Create a shorter title", "Update the wording"]')"

# Negatives for the nouns that were drafted into SHARED_SURFACE_NOUNS and
# removed after probing: their everyday meaning is a repo artifact. Creating a
# migration authors a file; it is `apply` (Tier 1b) that mutates.
run_case "Tier 1c neg: migration file"    pass default "$(build_payload '["Create a migration file", "Update the migration file"]')"
run_case "Tier 1c neg: JSON schema"       pass default "$(build_payload '["Create a JSON schema file", "Update the JSON schema"]')"
run_case "Tier 1c neg: retry policy"      pass default "$(build_payload '["Create a retry policy in the client", "Update the retry policy"]')"
run_case "Tier 1c neg: code namespace"    pass default "$(build_payload '["Create a new namespace", "Update the namespace"]')"

# Negatives for the LOCAL_ARTIFACT veto: a kept shared-surface noun sitting
# inside an authoring artifact is not the shared surface.
run_case "Tier 1c veto: fixture record"   pass default "$(build_payload '["Create a fixture record for the test", "Update the fixture record"]')"
run_case "Tier 1c veto: index variable"   pass default "$(build_payload '["Create an index variable", "Update the index variable"]')"
run_case "Tier 1c veto: test account"     pass default "$(build_payload '["Create a test account", "Update the test account"]')"

# ---------------------------------------------------------------------------
# (d8) Tier 0b — `cancel` abandons only when nothing else is mutating
# ---------------------------------------------------------------------------
# Issue #974 gap 2 (codex round-1 on PR #966). `Cancel the prod deployment`
# aborts an in-flight change to prod, which is itself a mutation, so a menu of
# cancel-vs-proceed has no non-mutating tier. It was silent, and NOT because of
# a vocabulary hole: the option already classified as mutating via `prod`, but
# `_question_triggers` strips abandonment options before it asks the mutation
# question, so the single survivor fell below the two-candidate floor.
run_case "Tier 0b: cancel-with-object fires"   advisory default "$(build_payload '["Cancel the prod deployment", "Proceed with the prod deployment"]')"
run_case "strict: cancel-with-object blocks"   block    strict  "$(build_payload '["Cancel the prod deployment", "Proceed with the prod deployment"]')"
run_case "Tier 0b KO: prod 배포 취소/진행"      advisory default "$(build_payload '["prod 배포 취소", "prod 배포 진행"]')"

# The rule is a new false-fire surface in its own right, so both directions are
# pinned. A bare cancel is still the do-nothing tier — the binary go/no-go that
# (d) protects must not start firing.
run_case "Tier 0b neg: bare Cancel"          pass default "$(build_payload '["deploy to prod", "Cancel"]')"
run_case "Tier 0b neg: 'Cancel this'"        pass default "$(build_payload '["deploy to prod", "Cancel this"]')"
run_case "Tier 0b neg: KO bare 취소"          pass default "$(build_payload '["prod 배포", "취소"]')"
run_case "Tier 0b neg: cancel + safe tier"   pass default "$(build_payload '["Cancel the prod deployment", "Preview the rollout first"]')"

# A no-go that spells out what it declines carries the mutation token too, and
# would have been promoted to a candidate. An explicit negation settles it —
# read only inside the Tier 0b branch, never as a Tier 0 token of its own.
run_case "Tier 0b neg: 'Cancel — do not deploy to prod'"  pass default "$(build_payload '["Deploy to prod", "Cancel — do not deploy to prod"]')"
run_case "Tier 0b neg: KO 취소 — 배포하지 않음"             pass default "$(build_payload '["prod 배포", "취소 — 배포하지 않음"]')"

# The control that keeps the negation token scoped: a global `do not` was tried
# and rejected because it silenced this menu, where the negation attaches to a
# rider rather than to the act. Both options still deploy to prod, so it fires.
run_case "negation token is scoped to cancel"  advisory default "$(build_payload '["Deploy to prod and notify", "Deploy to prod but do not notify"]')"

# ---------------------------------------------------------------------------
# (d9) Gap 3 — the compound-option VERB axis, closed for the destructive verbs
# ---------------------------------------------------------------------------
# Issue #974 gap 3 asked whether anything remains after (d2)'s row 4 closed the
# compound-option TARGET axis. Something did: an option pairing a safe MODE
# token with a mutation verb and naming no high-blast target still suppressed.
# PR #1016 built a fix (safe MODE + ANY mutation verb ⇒ not low-blast) and
# rejected it — it also fired on the canonical single-clause dry-run menus
# below, where the safe token governs the mutation verb as its own object and
# nothing else happens.
#
# The gap closes here on a narrower axis: a DESTRUCTIVE verb (delete / drop /
# truncate / 삭제) naming a shared surface, behind a sequence connector
# ("then" / " 후 ") in the same option as a safe-MODE token, is a second
# unconditional clause — not a dry run. None of the four rejected-fix
# counter-cases below pair a destructive verb with a shared surface, so the
# narrower scope leaves all four silent while closing the residual.
run_case "gap3 fixed: dry-run→delete, no prod, now fires"     advisory default "$(build_payload '["Dry-run then delete the customer table", "Delete the customer table"]')"
run_case "gap3 fixed: KO 드라이런 후 삭제, now fires"           advisory default "$(build_payload '["드라이런 후 고객 테이블 삭제", "고객 테이블 삭제"]')"
run_case "strict: gap3 dry-run→delete blocks"                  block    strict  "$(build_payload '["Dry-run then delete the customer table", "Delete the customer table"]')"

# The row-4 control proving the residual was real and not a broken probe: the
# identical shape with a high-blast target already fired before this fix, in
# both modes, and still does.
run_case "gap3 control: same shape WITH prod"        advisory default "$(build_payload '["Dry-run then delete the prod table", "Delete the prod table"]')"
run_case "strict: gap3 control WITH prod blocks"     block    strict  "$(build_payload '["Dry-run then delete the prod table", "Delete the prod table"]')"

# The four rejected-fix counter-cases from PR #1016, re-measured against this
# narrower discriminator: none pairs a destructive verb with a shared surface,
# so all four stay silent — the broad version's false-positive cost does not
# reproduce here.
run_case "gap3 counter: Dry-run the deploy"        pass default "$(build_payload '["Deploy now", "Dry-run the deploy"]')"
run_case "gap3 counter: Simulate the merge"        pass default "$(build_payload '["Merge now", "Simulate the merge"]')"
run_case "gap3 counter: Report-only on the send"   pass default "$(build_payload '["Send the announcement", "Report-only pass on the send"]')"
run_case "gap3 counter: KO 배포 드라이런"            pass default "$(build_payload '["지금 배포", "배포 드라이런"]')"

# False-positive controls for the new discriminator itself. A destructive verb
# behind a connector that names a LOCAL artifact (not a shared surface) stays
# silent — the same Tier 1c veto `_names_shared_surface` already applies.
run_case "gap3 new-fix control: dry-run→delete a test fixture stays silent" \
  pass default "$(build_payload '["Dry-run then delete the test fixture", "Delete the test fixture"]')"
# A destructive verb with no sequence connector at all — a single-clause dry
# run of a delete — stays silent, same as the deploy/merge/send counters.
run_case "gap3 new-fix control: Dry-run the delete (no connector, no target)" \
  pass default "$(build_payload '["Delete now", "Dry-run the delete"]')"
# Codex P1 on PR #1072. Co-occurrence of connector + destructive verb + shared
# surface is NOT the discriminator: here every delete is simulated because the
# verb is the dry-run's own object, and the post-connector clause only reads.
# The first Tier 1e disqualified this, so strict mode blocked a genuinely safe
# menu — the opposite of what the hook exists to do.
run_case "gap3 P1: dry-run OF the delete, then a read-only clause, stays silent" \
  pass default "$(build_payload '["Delete the customer table", "Dry-run the delete operation, then inspect the customer table"]')"
# The mirror of the case above, and why the rule is per-clause rather than
# order-based: the destructive clause comes FIRST and carries no safe token,
# so the trailing dry-run does not govern it.
run_case "gap3 reverse order: delete first, dry-run after, still fires" \
  advisory default "$(build_payload '["Delete the customer table, then dry-run to confirm", "Delete the customer table"]')"

# ---------------------------------------------------------------------------
# (e) BLOCK — strict mode
# ---------------------------------------------------------------------------
run_case "strict: incident menu"       block strict "$(build_payload "$INCIDENT")"
run_case "strict: EN all-prod"         block strict "$(build_payload '["deploy to prod", "deploy to prod partially"]')"
run_case "strict: preview → pass"      pass  strict "$(build_payload '["deploy to prod", "deploy to preview"]')"

# strict-env value contract: ONLY `=1` (after strip) activates strict.
run_case "strict=1 with whitespace → block"    block "strictval= 1 " "$(build_payload "$INCIDENT")"
run_case "strict=no → advisory (not strict)"   advisory "strictval=no" "$(build_payload "$INCIDENT")"
run_case "strict=0 → advisory"                 advisory "strictval=0" "$(build_payload "$INCIDENT")"
run_case "strict=true → advisory (only 1)"     advisory "strictval=true" "$(build_payload "$INCIDENT")"

# ---------------------------------------------------------------------------
# (f) PASS — non-AskUserQuestion tool / malformed payload (fail-open)
# ---------------------------------------------------------------------------
run_case "non-AskUserQuestion tool"  pass default '{"tool_name":"Bash","tool_input":{"command":"airflow dags trigger prod_dag"}}'
run_case "malformed JSON payload"    pass default 'not-json-at-all'
run_case "tool_input not a dict"     pass default '{"tool_name":"AskUserQuestion","tool_input":"oops"}'
run_case "questions missing"         pass default '{"tool_name":"AskUserQuestion","tool_input":{}}'
run_case "questions not a list"      pass default '{"tool_name":"AskUserQuestion","tool_input":{"questions":"oops"}}'
run_case "options not a list"        pass default '{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q","header":"h","options":"oops"}]}}'
run_case "option not a dict"         pass default '{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q","header":"h","options":["oops","also"]}]}}'

# ---------------------------------------------------------------------------
# (g) Per-question isolation — one question's safe tier must not cover another
# ---------------------------------------------------------------------------
MULTIQ_ISOLATION='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q1","header":"a","options":[{"label":"prod 트리거 전체","description":"d"},{"label":"prod 트리거 부분","description":"d"}]},{"question":"q2","header":"b","options":[{"label":"preview 로 검증","description":"d"},{"label":"dev 로 검증","description":"d"}]}]}}'
run_case "q1 all-prod, q2 safe → still nudges" advisory default "$MULTIQ_ISOLATION"

MULTIQ_CLEAN='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"q1","header":"a","options":[{"label":"Plan A","description":"d"},{"label":"Plan B","description":"d"}]},{"question":"q2","header":"b","options":[{"label":"prod 배포","description":"d"},{"label":"preview 배포","description":"d"}]}]}}'
run_case "no question triggers → pass"         pass default "$MULTIQ_CLEAN"

# ---------------------------------------------------------------------------
# (h) Self-application regression — the hook's own spec must not trip it
# ---------------------------------------------------------------------------
# A lexical detector validated only against its author's own fixtures hides
# FP↔FN cancellation. Feeding the hook its own spec.md — a document dense with
# every token in all three sets — is an independent payload the author did not
# shape around the predicate. It must stay silent.
if [ ! -f "$SPEC" ]; then
  echo "FAIL: spec.md missing: $SPEC" >&2
  FAIL=$((FAIL+1)); FAILED_NAMES+=("self-application: spec.md present")
else
  SELF_PAYLOAD=$(python3 - "$SPEC" <<'PY'
import json, sys
text = open(sys.argv[1], encoding="utf-8").read()
half = len(text) // 2
payload = {
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "spec self-application",
                "header": "spec",
                "options": [
                    {"label": "spec part 1", "description": text[:half]},
                    {"label": "spec part 2", "description": text[half:]},
                ],
            }
        ]
    },
}
print(json.dumps(payload))
PY
)
  run_case "self-application: own spec.md → silent" pass default "$SELF_PAYLOAD"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
