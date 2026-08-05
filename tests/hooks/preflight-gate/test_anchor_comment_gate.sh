#!/usr/bin/env bash
# test_anchor_comment_gate.sh — coverage for
# hooks/preflight-gate/anchor-comment-gate/impl.py
#
# The hook runs on two events, and the cases are grouped the same way:
#
#   PreToolUse  — structure only, decided from the command's body text. No
#                 network, so no fake `gh` is involved at all.
#                 block → exit 2 + the rendered block message on stderr
#   PostToolUse — the published comment is fetched back and re-checked. A fake
#                 `gh` on PATH answers both lookups; findings go to stderr and
#                 the hook exits 0 unless PRAXIS_ANCHOR_GATE_STRICT=1.
#
# The coverage advisory runs real `git`, so its case runs inside a temp repo
# with an `origin/main` remote-tracking ref.
#
# Usage: bash tests/hooks/preflight-gate/test_anchor_comment_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/anchor-comment-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

SHA=abc1234def5678
STALE=0000000aaaa111

FIX=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Anchor fixtures
# ---------------------------------------------------------------------------

# write_anchor <file> <sha> [omit-field]
#   omit-field: history | unverified | evidence | table | heading
write_anchor() {
  local file="$1" sha="$2" omit="${3:-}"
  {
    if [ "$omit" != "heading" ]; then
      echo "### 검증 — \`$sha\` (rev 1)"
      echo ""
    fi
    if [ "$omit" != "table" ]; then
      echo "| # | 항목 | 방법 | 결과 |"
      echo "|---|---|---|---|"
      echo "| 1 | impl.py 가 앵커를 검사한다 | hook 실행 | PASS(실환경) |"
      echo ""
    fi
    if [ "$omit" != "unverified" ]; then
      echo "<details><summary><b>미검증 없음</b></summary>"
      echo ""
      echo "</details>"
      echo ""
    fi
    if [ "$omit" != "evidence" ]; then
      echo "<details><summary>1. impl.py 검사 — hook 실행</summary>"
      echo ""
      echo '$ echo ok'
      echo "ok"
      echo "</details>"
      echo ""
    fi
    if [ "$omit" != "history" ]; then
      echo "<details><summary>갱신 이력</summary>"
      echo ""
      echo "- rev 1 — \`$sha\`: 최초 검증."
      echo "</details>"
    fi
  } >"$file"
}

write_anchor "$FIX/ok.md" "$SHA"
write_anchor "$FIX/ok2.md" "$SHA"
write_anchor "$FIX/no-history.md" "$SHA" history
write_anchor "$FIX/no-unverified.md" "$SHA" unverified
write_anchor "$FIX/no-evidence.md" "$SHA" evidence
write_anchor "$FIX/stale.md" "$STALE"

# ---------------------------------------------------------------------------
# Fake gh — PostToolUse lookups only
# ---------------------------------------------------------------------------

# make_fake_gh <mode> <body-file>
#   ok        — `gh api …/issues/comments/<id> --jq .body` prints the fixture;
#               `gh pr view … --jq` prints "<sha> <baseRefName>"
#   ghes      — same, but answers only when --hostname reached `gh api` and the
#               host prefix reached `gh pr view -R H/owner/repo`
#   api-error — the comment fetch fails (auth-style)
#   pr-error  — the comment fetch works, the PR lookup fails
make_fake_gh() {
  local mode="$1" body_file="$2" d
  d=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
  case "$mode" in
    ok)
      cat >"$d/gh" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "api" ]; then cat "$body_file"; exit 0; fi
echo "$SHA main"
exit 0
EOF
      ;;
    ghes)
      cat >"$d/gh" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "api" ]; then
  case " \$* " in
    *" --hostname ghe.example "*) cat "$body_file"; exit 0 ;;
  esac
  echo "gh: --hostname not forwarded to api" >&2; exit 1
fi
case " \$* " in
  *" ghe.example/owner/repo "*) echo "$SHA main"; exit 0 ;;
esac
echo "gh: host prefix not forwarded to pr view" >&2; exit 1
EOF
      ;;
    api-error)
      cat >"$d/gh" <<'EOF'
#!/usr/bin/env bash
echo "gh: authentication required" >&2
exit 1
EOF
      ;;
    pr-error)
      cat >"$d/gh" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "api" ]; then cat "$body_file"; exit 0; fi
echo "gh: could not resolve PR" >&2
exit 1
EOF
      ;;
  esac
  chmod +x "$d/gh"
  echo "$d"
}

# ---------------------------------------------------------------------------
# Real git repo for the coverage advisory
# ---------------------------------------------------------------------------

REPO=$(cd "$(mktemp -d)" && pwd -P) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
git -C "$REPO" init -q -b main
git -C "$REPO" -c user.name=test -c user.email=test@example.com \
  commit -q --allow-empty -m init
git -C "$REPO" update-ref refs/remotes/origin/main HEAD
echo hello >"$REPO/untouched-by-anchor.md"
git -C "$REPO" add untouched-by-anchor.md
git -C "$REPO" -c user.name=test -c user.email=test@example.com \
  commit -q -m "add file no table row mentions"

cleanup() { rm -rf "$REPO" "$FIX"; }
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

COMMENT_URL="https://github.com/owner/repo/pull/42#issuecomment-999"

build_payload() {
  python3 -c '
import json, sys
payload = {
    "tool_name": sys.argv[3],
    "hookEventName": sys.argv[2],
    "tool_input": {"command": sys.argv[1]},
}
if sys.argv[4]:
    payload["tool_response"] = {"output": sys.argv[4]}
print(json.dumps(payload))
' "$1" "$2" "$3" "$4"
}

# run_case <name> <expect> <event> <fake-gh-dir> <command> [cwd] [tool] [output]
#   expect: block  — exit 2 + rendered block message
#           pass   — exit 0, and (PostToolUse) no finding on stderr
#           warn:X — exit 0 and stderr contains X
run_case() {
  local name="$1" expect="$2" event="$3" gh_dir="$4" command="$5"
  local cwd="${6:-$FIX}" tool="${7:-Bash}" output="${8:-}"
  local payload out rc path_prefix

  payload=$(build_payload "$command" "$event" "$tool" "$output")
  path_prefix="$PATH"
  [ -n "$gh_dir" ] && path_prefix="$gh_dir:$PATH"

  out=$(cd "$cwd" && echo "$payload" | PATH="$path_prefix" "$HOOK" 2>&1 1>/dev/null)
  rc=$?

  local ok=1
  case "$expect" in
    block)
      # exit 2 alone is not enough — a crash is also non-zero. The rendered
      # block message must be present.
      [ "$rc" -eq 2 ] && [[ "$out" == *"ANCHOR VERIFICATION COMMENT"* ]] || ok=0
      ;;
    strict-block)
      [ "$rc" -eq 2 ] && [[ "$out" == *"[anchor-gate]"* ]] || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] && [[ "$out" != *"[anchor-gate] 게시된 앵커에 문제"* ]] || ok=0
      ;;
    warn:*)
      [ "$rc" -eq 0 ] && [[ "$out" == *"${expect#warn:}"* ]] || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] (rc=$rc, stderr=$out)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ===========================================================================
# PreToolUse — structure only
# ===========================================================================

run_case "1 pass: well-formed anchor" \
  pass PreToolUse "" "gh pr comment 42 --body-file $FIX/ok.md"

run_case "1b pass: same anchor via gh api PATCH (--field body=@file)" \
  pass PreToolUse "" "gh api --method PATCH /repos/owner/repo/issues/comments/999 -F body=@$FIX/ok.md"

run_case "1c block: 슬래시 없는 PATCH 엔드포인트도 인식 (필드 누락 앵커)" \
  block PreToolUse "" "gh api -X PATCH repos/owner/repo/issues/comments/999 -F body=@$FIX/no-history.md"

# Neither the body nor its shape is knowable before the post; PostToolUse
# decides. Silence would read as "checked and clean".
run_case "1d warn: PATCH body 가 stdin (-F body=@-) → 해독 불가 경고" \
  "warn:해독하지 못해" PreToolUse "" \
  "gh api --method PATCH /repos/owner/repo/issues/comments/999 -F body=@-"

run_case "1e warn: --input JSON 본문 → 해독 불가 경고" \
  "warn:해독하지 못해" PreToolUse "" \
  "gh api --method PATCH /repos/owner/repo/issues/comments/999 --input payload.json"

run_case "2 block: 갱신 이력 토글 누락" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/no-history.md"

run_case "2b block: 미검증 토글 누락" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/no-unverified.md"

run_case "2c block: 표 행 1 의 근거 토글 누락" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/no-evidence.md"

# A bare <summary> renders as plain text and a fenced one is an example —
# neither is a toggle the reader can open.
printf '### 검증 — `%s` (rev 1)\n\n| # | 항목 | 결과 |\n|---|---|---|\n| 1 | x | PASS(실환경) |\n\n<summary>미검증 없음</summary>\n<summary>1. 근거</summary>\n<summary>갱신 이력</summary>\n' \
  "$SHA" >"$FIX/bare-summary.md"
run_case "2d block: <details> 없는 맨 <summary> 는 토글이 아님" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/bare-summary.md"

write_anchor "$FIX/fenced-toggle.md" "$SHA" history
printf '\n```\n<details><summary>갱신 이력</summary>\n</details>\n```\n' >>"$FIX/fenced-toggle.md"
run_case "2e block: 코드펜스 안에 인용된 토글은 토글이 아님" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/fenced-toggle.md"

run_case "4 pass: 바이패스 토큰" \
  pass PreToolUse "" "gh pr comment 42 --body-file $FIX/no-history.md # anchor-gate: 오프라인, 사유 기록"

PRAXIS_HOOK_BYPASS_ANCHOR_GATE=1 \
  run_case "4b pass: 바이패스 환경변수" \
  pass PreToolUse "" "gh pr comment 42 --body-file $FIX/no-history.md"

# The one-line notice rides the same `gh pr comment` as the anchor. Blocking
# it would break the update procedure this hook exists to protect — this is
# the false-positive case that matters most.
run_case "5 pass: 1줄 갱신 고지 (앵커 아님)" \
  pass PreToolUse "" \
  "gh pr comment 42 --body \"검증 갱신 — $SHA rev 2 · 항목 3 재실행 → $COMMENT_URL\""

run_case "5b pass: 일반 코멘트" \
  pass PreToolUse "" "gh pr comment 42 --body \"리뷰 반영했습니다\""

run_case "5c pass: 앵커와 무관한 gh 명령" \
  pass PreToolUse "" "gh pr view 42 --json state"

run_case "5d pass: gh 아닌 명령" \
  pass PreToolUse "" "echo '### 검증 — abc (rev 1)'"

# --edit-last edits "the last comment of the CURRENT USER", which the update
# notice displaces — so from rev 2 it rewrites the notice and leaves the anchor
# stale. That is the exact defect this hook was built for.
run_case "10 block: --edit-last 로 앵커 갱신 시도" \
  block PreToolUse "" "gh pr comment 42 --edit-last --body-file $FIX/ok.md"

# A compound command must not get a pass on the strength of its first anchor.
run_case "11 block: 두 번째 앵커가 malformed 인 compound" \
  block PreToolUse "" \
  "gh pr comment 42 --body-file $FIX/ok.md && gh pr comment 43 --body-file $FIX/no-history.md"

# The reason is what makes the bypass auditable; a bare marker waives that too.
run_case "12 block: 사유 없는 맨 바이패스 마커는 우회가 아님" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/no-history.md # anchor-gate:"

# safe_tokenize splits on newlines, which shreds a quoted multi-line body — the
# whole `gh pr comment` segment disappears and the anchor is never examined.
# The lenient second pass is what catches it.
MULTILINE_BODY="$(cat "$FIX/no-history.md")"
run_case "13 block: 멀티라인 인라인 --body 앵커" \
  block PreToolUse "" "gh pr comment 42 --body '$MULTILINE_BODY'"

# The first non-option token after a global flag is that flag's VALUE, not the
# subcommand — a GHES anchor update would otherwise skip the parser entirely.
run_case "14 block: gh --hostname H api …  (전역 플래그 뒤의 서브커맨드)" \
  block PreToolUse "" \
  "gh --hostname ghe.example api --method PATCH /repos/owner/repo/issues/comments/999 -F body=@$FIX/no-history.md"

# The shell expands ~ before gh sees it, but the hook reads the raw command.
HOME="$FIX" run_case "15 block: --body-file ~/no-history.md 도 읽어서 검사" \
  block PreToolUse "" "gh pr comment 42 --body-file ~/no-history.md"

# pflag accepts an attached shorthand value; a scan matching only the bare
# token misses it and the anchor is never examined.
run_case "16 block: 결합형 -F<path>" \
  block PreToolUse "" "gh pr comment 42 -F$FIX/no-history.md"

run_case "16b block: 결합형 -b<body> (한 줄 앵커, 필드 누락)" \
  block PreToolUse "" "gh pr comment 42 -b'### 검증 — \`$SHA\` (rev 1)'"

# The body file lives in the cd target, not in the hook's own directory.
run_case "17 block: cd 이후 상대 경로 --body-file" \
  block PreToolUse "" "cd $FIX && gh pr comment 42 --body-file no-history.md"

# A pasted table row inside an evidence toggle must not be read as a claim row
# demanding its own toggle — that would block a correct anchor.
write_anchor "$FIX/evidence-table.md" "$SHA"
printf '\n<details><summary>2. 부수 근거</summary>\n\n```\n| 200 | response | ok |\n```\n</details>\n' \
  >>"$FIX/evidence-table.md"
run_case "18 pass: 근거 토글 안의 숫자 표 행은 claim 행이 아님" \
  pass PreToolUse "" "gh pr comment 42 --body-file $FIX/evidence-table.md"

# The file on disk is the PREVIOUS body; validating it would certify content
# gh will never post.
run_case "19 warn: 같은 명령에서 재작성되는 body 파일은 해독 불가" \
  "warn:해독하지 못해" PreToolUse "" \
  "printf 'x' > $FIX/ok.md && gh pr comment 42 --body-file $FIX/ok.md"
write_anchor "$FIX/ok.md" "$SHA"  # restore — the case above truncated it

# A raw substring search would let an anchor that quotes this marker as
# evidence waive the gate on itself.
write_anchor "$FIX/quotes-marker.md" "$SHA" history
printf '\n<details><summary>2. 바이패스 예시</summary>\n\n```\n# anchor-gate: 오프라인\n```\n</details>\n' \
  >>"$FIX/quotes-marker.md"
run_case "20 block: 본문이 바이패스 마커를 인용해도 우회 아님" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/quotes-marker.md"

# Reader sees a heading with no SHA while freshness checks a hidden one.
printf '### 검증\n\n### 검증 — `%s` (rev 1)\n' "$SHA" >"$FIX/late-heading.md"
tail -n +2 "$FIX/ok.md" >>"$FIX/late-heading.md"
run_case "21 block: SHA 헤딩이 첫 줄이 아님" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/late-heading.md"

# Dedup must not collapse the banned --edit-last form into the innocent one.
run_case "22 block: 같은 본문 두 게시 중 두 번째만 --edit-last" \
  block PreToolUse "" \
  "gh pr comment 42 --body-file $FIX/ok2.md && gh pr comment 42 --body-file $FIX/ok2.md --edit-last"

# No space around && plus a quoted multiline body: safe_tokenize drops the
# open-quote line and shlex.split glues `ok&&gh` into one token.
run_case "24 block: 공백 없는 && + 멀티라인 본문" \
  block PreToolUse "" "echo ok&&gh pr comment 42 --body '$MULTILINE_BODY'"

run_case "8 pass: 비-Bash 도구" \
  pass PreToolUse "" "gh pr comment 42 --body-file $FIX/no-history.md" "$FIX" AskUserQuestion

# ===========================================================================
# PostToolUse — the published comment is the oracle
# ===========================================================================

OK_GH=$(make_fake_gh ok "$FIX/ok.md")
STALE_GH=$(make_fake_gh ok "$FIX/stale.md")
BROKEN_GH=$(make_fake_gh ok "$FIX/no-history.md")

run_case "30 pass: 게시된 앵커가 정상이고 HEAD 와 일치" \
  pass PostToolUse "$OK_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

run_case "31 warn: 게시된 앵커의 SHA 가 현재 HEAD 와 다름 (stale)" \
  "warn:와 다름" PostToolUse "$STALE_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

run_case "32 warn: 게시된 앵커에 갱신 이력 토글이 없음" \
  "warn:갱신 이력 토글" PostToolUse "$BROKEN_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

PRAXIS_ANCHOR_GATE_STRICT=1 \
  run_case "32b strict: 같은 결함이 strict 에서는 exit 2" \
  strict-block PostToolUse "$BROKEN_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

# The URL is what names the target, whatever the command looked like — this is
# the form the old PreToolUse parser could not decode at all.
run_case "33 warn: --input 으로 게시해도 URL 로 되읽어 검사" \
  "warn:갱신 이력 토글" PostToolUse "$BROKEN_GH" \
  "gh api --method PATCH /repos/owner/repo/issues/comments/999 --input payload.json" \
  "$FIX" Bash '{"html_url":"'"$COMMENT_URL"'"}'

GHES_GH=$(make_fake_gh ghes "$FIX/no-history.md")
run_case "34 warn: GHES URL 의 호스트가 두 조회에 모두 전달됨" \
  "warn:갱신 이력 토글" PostToolUse "$GHES_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "https://ghe.example/owner/repo/pull/42#issuecomment-999"

API_ERR_GH=$(make_fake_gh api-error "$FIX/ok.md")
run_case "35 warn: 코멘트 조회 실패는 판정하지 않고 사유를 밝힘" \
  "warn:조회 실패" PostToolUse "$API_ERR_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

PR_ERR_GH=$(make_fake_gh pr-error "$FIX/ok.md")
run_case "36 warn: PR 조회 실패 → SHA 신선도 확인 불가" \
  "warn:신선도 확인 불가" PostToolUse "$PR_ERR_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

# An ordinary comment is not an anchor, so nothing is checked.
NOT_ANCHOR=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
printf '리뷰 반영했습니다\n' >"$NOT_ANCHOR/body.md"
PLAIN_GH=$(make_fake_gh ok "$NOT_ANCHOR/body.md")
run_case "37 pass: 게시물이 앵커가 아니면 검사 대상 아님" \
  pass PostToolUse "$PLAIN_GH" "gh pr comment 42 --body '리뷰 반영했습니다'" \
  "$FIX" Bash "$COMMENT_URL"

run_case "38 pass: 코멘트 URL 이 없는 출력은 무시" \
  pass PostToolUse "$BROKEN_GH" "git push origin HEAD" \
  "$FIX" Bash "To github.com:owner/repo.git"

run_case "39 pass: PostToolUse 에서도 바이패스 토큰 유효" \
  pass PostToolUse "$BROKEN_GH" \
  "gh pr comment 42 --body-file anchor.md # anchor-gate: 오프라인, 사유 기록" \
  "$FIX" Bash "$COMMENT_URL"

# The anchor is well-formed and fresh; the repo has a changed file no table row
# names. Advisory only — the command must still proceed.
write_anchor "$REPO/anchor.md" "$SHA"
COVER_GH=$(make_fake_gh ok "$REPO/anchor.md")
run_case "40 warn: 표가 언급하지 않은 변경 파일 → 경고만, 통과" \
  "warn:untouched-by-anchor.md" PostToolUse "$COVER_GH" \
  "gh pr comment 42 --body-file anchor.md" "$REPO" Bash "$COMMENT_URL"

# Case 9 needs raw malformed stdin, not a JSON-wrapped command string.
_malformed_out=$(echo "not json at all <<<" | "$HOOK" 2>&1 1>/dev/null)
_malformed_rc=$?
if [ "$_malformed_rc" -eq 0 ]; then
  echo "PASS  [9 pass: malformed JSON stdin -> fail-open]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [9 pass: malformed JSON stdin -> fail-open] (rc=$_malformed_rc, stderr=$_malformed_out)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("9 malformed JSON stdin")
fi

rm -rf "$NOT_ANCHOR"

# --- summary -----------------------------------------------------------------
echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_NAMES[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
