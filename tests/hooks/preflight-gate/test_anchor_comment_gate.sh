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
# make_fake_gh runs inside a command substitution, so a parent-shell array
# cannot collect its temp dirs — a file survives the subshell.
GH_DIRS_FILE=$(mktemp) || { echo "FATAL: mktemp failed" >&2; exit 1; }

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

# write_anchor_en <file> <sha> [omit-field]
#   The dialect the rule prescribes: English field labels, Korean content, and
#   the literal `Evidence <#> — ` numbering prefix.
#   omit-field: history | unverified | evidence | badrev | dialect
#     dialect — English heading with Korean toggles, i.e. a body in neither
#     dialect. Must not pass by borrowing half of each.
write_anchor_en() {
  local file="$1" sha="$2" omit="${3:-}"
  local mixed=""; [ "$omit" = "dialect" ] && mixed=1
  {
    if [ "$omit" = "badrev" ]; then
      # Prefix right, rev not parenthesized — inside gate scope, heading invalid.
      echo "### Verification — \`$sha\` rev 1"
      echo ""
    else
      echo "### Verification — \`$sha\` (rev 1)"
      echo ""
    fi
    echo "| # | Claim | Result |"
    echo "|---|---|---|"
    echo "| 1 | impl.py 가 앵커를 검사한다 | PASS(live) |"
    echo ""
    if [ "$omit" != "unverified" ]; then
      if [ -n "$mixed" ]; then echo "<details><summary>미검증 없음</summary>"
      else echo "<details><summary>Unverified — 없음</summary>"; fi
      echo "<br>"; echo ""; echo "</details>"; echo ""
    fi
    if [ "$omit" != "evidence" ]; then
      if [ -n "$mixed" ]; then echo "<details><summary>1. impl.py 검사</summary>"
      else echo "<details><summary>Evidence 1 — impl.py 가 앵커를 검사한다</summary>"; fi
      echo "<br>"; echo ""
      echo "훅을 직접 실행하는 것이 이 행의 오라클입니다."
      echo ""
      echo '$ echo ok'
      echo "ok"
      echo ""
      echo "</details>"; echo ""
    fi
    if [ "$omit" != "history" ]; then
      if [ -n "$mixed" ]; then echo "<details><summary>갱신 이력</summary>"
      else echo "<details><summary>History</summary>"; fi
      echo "<br>"; echo ""
      echo "- rev 1 — \`$sha\`: 최초 검증."
      echo ""
      echo "</details>"
    fi
  } >"$file"
}

write_anchor "$FIX/ok.md" "$SHA"
write_anchor "$FIX/ok2.md" "$SHA"
write_anchor_en "$FIX/en-ok.md" "$SHA"
write_anchor_en "$FIX/en-no-history.md" "$SHA" history
write_anchor_en "$FIX/en-no-unverified.md" "$SHA" unverified
write_anchor_en "$FIX/en-no-evidence.md" "$SHA" evidence
write_anchor_en "$FIX/en-badrev.md" "$SHA" badrev
write_anchor_en "$FIX/en-mixed.md" "$SHA" dialect
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
  echo "$d" >>"$GH_DIRS_FILE"
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

cleanup() {
  local d
  while IFS= read -r d; do [ -n "$d" ] && rm -rf "$d"; done <"$GH_DIRS_FILE"
  rm -rf "$REPO" "$FIX" "$GH_DIRS_FILE" "${NOT_ANCHOR:-}" \
    "${ISSUE_URL_GH:-}" "${TWO_GH:-}" "${COVER_GH:-}"
}
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
if sys.argv[4] or sys.argv[5]:
    resp = {"output": sys.argv[4]}
    # argv[5] carries extra tool_response fields as a JSON object, so a case
    # can say the tool call itself failed — the field the hook reads before
    # it decides anything was published.
    if sys.argv[5]:
        resp.update(json.loads(sys.argv[5]))
    payload["tool_response"] = resp
print(json.dumps(payload))
' "$1" "$2" "$3" "$4" "$5"
}

# run_case <name> <expect> <event> <fake-gh-dir> <command> [cwd] [tool] [output] [tool_response-extra-json]
#   expect: block  — exit 2 + rendered block message
#           pass   — exit 0, and (PostToolUse) no finding on stderr
#           silent — exit 0 with nothing on stdout OR stderr
#           report:X   — exit 2 and stderr contains X (a blocking finding)
#           context:X  — exit 0 and stdout carries X in additionalContext
#                        (advisory/unknown findings: model-visible, not a deny)
#           noreport:X — exit 2 and stderr does NOT contain X
#           nocontext:X — exit 0 and stdout does NOT contain X (the advisory
#                        fired, but not about X — stderr would pass vacuously
#                        now that advisories leave through stdout)
#           demoted:X  — exit 0 and stderr contains X (ADVISORY opt-out)
#           warn:X   — exit 0 and stderr contains X
#           nowarn:X — exit 0 and stderr does NOT contain X
run_case() {
  local name="$1" expect="$2" event="$3" gh_dir="$4" command="$5"
  local cwd="${6:-$FIX}" tool="${7:-Bash}" output="${8:-}" resp_extra="${9:-}"
  local payload out rc path_prefix

  payload=$(build_payload "$command" "$event" "$tool" "$output" "$resp_extra")
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
    report:*)
      [ "$rc" -eq 2 ] && [[ "$out" == *"${expect#report:}"* ]] || ok=0
      ;;
    silent)
      # Nothing on either channel. `pass` only inspects stderr, so it would
      # pass vacuously for a finding that now leaves through stdout.
      local sout
      sout=$(cd "$cwd" && echo "$payload" | PATH="$path_prefix" "$HOOK" 2>/dev/null)
      [ "$rc" -eq 0 ] && [ -z "$sout" ] && [[ "$out" != *"[anchor-gate]"* ]] || ok=0
      ;;
    nocontext:*)
      local ctx
      ctx=$(cd "$cwd" && echo "$payload" | PATH="$path_prefix" "$HOOK" 2>/dev/null)
      [ "$rc" -eq 0 ] && [[ "$ctx" == *'"additionalContext"'* ]] \
        && [[ "$ctx" != *"${expect#nocontext:}"* ]] || ok=0
      ;;
    context:*)
      # stdout, not stderr — run_case captures stderr, so re-run capturing both.
      local both
      both=$(cd "$cwd" && echo "$payload" | PATH="$path_prefix" "$HOOK" 2>/dev/null)
      [ "$rc" -eq 0 ] && [[ "$both" == *'"additionalContext"'* ]] \
        && [[ "$both" == *"${expect#context:}"* ]] || ok=0
      ;;
    noreport:*)
      [ "$rc" -eq 2 ] && [[ "$out" != *"${expect#noreport:}"* ]] || ok=0
      ;;
    demoted:*)
      [ "$rc" -eq 0 ] && [[ "$out" == *"${expect#demoted:}"* ]] || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] && [[ "$out" != *"[anchor-gate] posted-anchor check result"* ]] || ok=0
      ;;
    warn:*)
      [ "$rc" -eq 0 ] && [[ "$out" == *"${expect#warn:}"* ]] || ok=0
      ;;
    nowarn:*)
      [ "$rc" -eq 0 ] && [[ "$out" != *"${expect#nowarn:}"* ]] || ok=0
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

# gh puts a `body=` field beside `--input` into the query string, so the field
# is not the posted body and the defective file it points at must not be graded
# — the same warn as 1e, never a block. Contract asserted in
# tests/hooks/preflight-gate/test_anchor_api_patch_input.py.
run_case "1e2 warn: --input 과 body= 필드 동시 → 필드는 쿼리스트링, 여전히 경고" \
  "warn:해독하지 못해" PreToolUse "" \
  "gh api --method PATCH /repos/owner/repo/issues/comments/999 --input payload.json -F body=@$FIX/no-history.md"

run_case "1f pass: 규약 방언(en) 앵커" \
  pass PreToolUse "" "gh pr comment 42 --body-file $FIX/en-ok.md"

run_case "1g block: en 앵커의 History 토글 누락" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/en-no-history.md"

run_case "1h block: en 앵커의 Unverified 토글 누락" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/en-no-unverified.md"

run_case "1i block: en 앵커의 Evidence 토글 누락" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/en-no-evidence.md"

# 헤딩이 아예 없으면 앵커로 인식되지 않아 게이트 범위 밖이다(설계). 범위 안에서
# 헤딩이 무효인 경우 — 접두사는 맞고 rev 가 괄호에 안 싸인 형태 — 를 대신 본다.
run_case "1j block: en 앵커의 rev 표기 무효" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/en-badrev.md"

# 두 방언을 반씩 섞어 통과하는 경로가 없어야 한다.
run_case "1k block: en 헤딩 + ko 토글 혼용" \
  block PreToolUse "" "gh pr comment 42 --body-file $FIX/en-mixed.md"

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

# Same file, two spellings: a literal text match sees `./ok.md` and `ok.md` as
# different paths and would validate the pre-rewrite body.
run_case "19b warn: 리다이렉트 대상과 --body-file 이 표기만 다른 같은 경로" \
  "warn:해독하지 못해" PreToolUse "" \
  "printf 'x' > ./ok.md && gh pr comment 42 --body-file ok.md" "$FIX"
write_anchor "$FIX/ok.md" "$SHA"

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

run_case "31 report(blocking): 게시된 앵커의 SHA 가 현재 HEAD 와 다름 (stale)" \
  "report:와 다름" PostToolUse "$STALE_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

# Positive control for case 36. There, the PR lookup fails and the freshness
# row comes back `unknown`; here the identical path with a working lookup
# produces a verdict instead. Without this pair, "no freshness finding" and
# "freshness never ran" are the same observation.
run_case "31b report: 조회가 성공하면 unknown 이 아니라 판정이 나온다" \
  "noreport:확인 불가" PostToolUse "$STALE_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

run_case "32 report(blocking): 게시된 앵커에 갱신 이력 토글이 없음" \
  "report:갱신 이력 토글" PostToolUse "$BROKEN_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

# The tier is named in the report, so a reader can tell "the rule was broken"
# from "the check never ran" without reading the message body.
run_case "32a report: 구조 결함은 blocking 티어로 라벨링됨" \
  "report:blocking (convention violation — fix it now / 규약 위반" PostToolUse "$BROKEN_GH" \
  "gh pr comment 42 --body-file anchor.md" "$FIX" Bash "$COMMENT_URL"

# The opt-out returns the exit to 0. On PostToolUse that is silence rather than
# a softer warning, which is exactly what opting out of this gate buys.
PRAXIS_ANCHOR_GATE_ADVISORY=1 \
  run_case "32b demoted: ADVISORY=1 이면 같은 결함이 exit 0" \
  "demoted:갱신 이력 토글" PostToolUse "$BROKEN_GH" \
  "gh pr comment 42 --body-file anchor.md" "$FIX" Bash "$COMMENT_URL"

# Exact `1` only, matching the value convention of the var it replaced.
PRAXIS_ANCHOR_GATE_ADVISORY=true \
  run_case "32c report: ADVISORY=true 는 데모트하지 않음 (정확히 1 만)" \
  "report:갱신 이력 토글" PostToolUse "$BROKEN_GH" \
  "gh pr comment 42 --body-file anchor.md" "$FIX" Bash "$COMMENT_URL"

# The URL is what names the target, whatever the command looked like — this is
# the form the old PreToolUse parser could not decode at all.
run_case "33 report: --input 으로 게시해도 URL 로 되읽어 검사" \
  "report:갱신 이력 토글" PostToolUse "$BROKEN_GH" \
  "gh api --method PATCH /repos/owner/repo/issues/comments/999 --input payload.json" \
  "$FIX" Bash '{"html_url":"'"$COMMENT_URL"'"}'

GHES_GH=$(make_fake_gh ghes "$FIX/no-history.md")
run_case "34 report: GHES URL 의 호스트가 두 조회에 모두 전달됨" \
  "report:갱신 이력 토글" PostToolUse "$GHES_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "https://ghe.example/owner/repo/pull/42#issuecomment-999"

API_ERR_GH=$(make_fake_gh api-error "$FIX/ok.md")
run_case "35 context(unknown): 코멘트 조회 실패는 판정하지 않고 사유를 밝힘" \
  "context:조회 실패" PostToolUse "$API_ERR_GH" "gh pr comment 42 --body-file anchor.md" \
  "$FIX" Bash "$COMMENT_URL"

PR_ERR_GH=$(make_fake_gh pr-error "$FIX/ok.md")
run_case "36 context(unknown): PR 조회 실패 → SHA 신선도 확인 불가" \
  "context:신선도 확인 불가" PostToolUse "$PR_ERR_GH" "gh pr comment 42 --body-file anchor.md" \
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
REPO_SHA=$(git -C "$REPO" rev-parse HEAD)
write_anchor "$REPO/anchor.md" "$REPO_SHA"
COVER_GH=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cat >"$COVER_GH/gh" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "api" ]; then cat "$REPO/anchor.md"; exit 0; fi
echo "$REPO_SHA main"
exit 0
EOF
chmod +x "$COVER_GH/gh"
run_case "40 context(advisory): 표가 언급하지 않은 변경 파일" \
  "context:untouched-by-anchor.md" PostToolUse "$COVER_GH" \
  "gh pr comment 42 --body-file anchor.md" "$REPO" Bash "$COMMENT_URL"

# --silent / a URL-stripping --jq / `> /dev/null` publish an anchor while
# printing nothing to follow. Freshness is checked ONLY here, so a silent post
# must not be a silent pass — the comment id in the PATCH endpoint is enough.
ISSUE_URL_GH=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cat >"$ISSUE_URL_GH/gh" <<EOF
#!/usr/bin/env bash
case " \$* " in
  *" .issue_url "*) echo "https://api.github.com/repos/owner/repo/issues/42"; exit 0 ;;
  *" .body "*) cat "$FIX/stale.md"; exit 0 ;;
esac
echo "$SHA main"
exit 0
EOF
chmod +x "$ISSUE_URL_GH/gh"
run_case "41 report: --silent 로 출력이 없어도 엔드포인트의 comment id 로 추적" \
  "report:와 다름" PostToolUse "$ISSUE_URL_GH" \
  "gh api --silent --method PATCH /repos/owner/repo/issues/comments/999 -F body=@anchor.md" \
  "$FIX" Bash ""

# The command-derived id is the second ref source, and it survives a failure
# just as the output URL does: a PATCH that landed before a later segment died
# published a stale anchor, and it prints nothing to fall back on.
run_case "41a report: exit≠0 라도 엔드포인트 comment id 는 살아있다" \
  "report:와 다름" PostToolUse "$ISSUE_URL_GH" \
  "gh api --silent --method PATCH /repos/owner/repo/issues/comments/999 -F body=@anchor.md; false" \
  "$FIX" Bash "" '{"exit": 1}'

# `gh pr comment > /dev/null` leaves no id anywhere. Nothing can be fetched, so
# no check ran at all — which is the `unknown` tier, not a pass. Before the tier
# split this printed to stderr and exited 0, and an exit-0 stderr on PostToolUse
# is discarded before Claude reads it: the report existed and reached no one.
run_case "41b context(unknown): URL 이 사라진 게시는 검사 미실행으로 보고" \
  "context:아예 실행하지 못했습니다" PostToolUse "$OK_GH" \
  "gh pr comment 42 --body-file $FIX/ok.md > /dev/null" \
  "$FIX" Bash ""

# A post that failed published nothing. Without the tool_response check the
# URL-loss branch reads the anchor body out of the *command* and reports an
# anchor that does not exist — a second error on top of the one the user is
# already looking at.
run_case "41d silent: 실패한 게시(exit≠0)는 검사 대상이 아님" \
  silent PostToolUse "$OK_GH" "gh pr comment 42 --body-file $FIX/ok.md" \
  "$FIX" Bash "" '{"exit": 1}'

run_case "41e silent: isError 인 게시도 검사 대상이 아님" \
  silent PostToolUse "$OK_GH" "gh pr comment 42 --body-file $FIX/ok.md" \
  "$FIX" Bash "" '{"isError": true}'

# The other side of 41d. `gh pr comment ...; false` exits 1 with the comment
# URL still in the output — the post succeeded and only a later segment failed.
# A failure-first check would skip a published stale anchor here, so the
# recovered id has to win over the exit code.
run_case "41f report(blocking): 실패한 compound 도 URL 이 남았으면 검사" \
  "report:와 다름" PostToolUse "$STALE_GH" \
  "gh pr comment 42 --body-file anchor.md; false" \
  "$FIX" Bash "$COMMENT_URL" '{"exit": 1}'

# The URL is discarded, so 41b's `unknown` branch is the one that would fire.
# A bare `; false` with no redirect passes with or without the guard — nothing
# recovers an id there either way — so it cannot tell the two shapes apart.
run_case "41g silent: 같은 실패에 URL 이 없으면 unknown 도 내지 않음" \
  silent PostToolUse "$STALE_GH" \
  "gh pr comment 42 --body-file $FIX/ok.md > /dev/null; false" \
  "$FIX" Bash "" '{"exit": 1}'

run_case "41c context(unknown): 그 보고가 unknown 티어로 라벨링됨" \
  "context:unknown (the check did not run — not a pass / 검사가 실행되지 않았습니다" PostToolUse "$OK_GH" \
  "gh pr comment 42 --body-file $FIX/ok.md > /dev/null" \
  "$FIX" Bash ""

# A compound command can publish two anchors; checking only the first leaves
# the second unexamined on the surface that decides.
TWO_GH=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cat >"$TWO_GH/gh" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "api" ]; then
  case " \$* " in
    *"/comments/999 "*) cat "$FIX/ok.md"; exit 0 ;;
  esac
  cat "$FIX/no-history.md"; exit 0
fi
echo "$SHA main"
exit 0
EOF
chmod +x "$TWO_GH/gh"
run_case "42 warn: 한 명령이 게시한 두 번째 앵커도 검사" \
  "report:갱신 이력 토글" PostToolUse "$TWO_GH" \
  "gh pr comment 42 --body-file a.md && gh pr comment 43 --body-file b.md" \
  "$FIX" Bash "$COMMENT_URL
https://github.com/owner/repo/pull/43#issuecomment-1000"

# The anchor is routinely posted from a worktree that is not the PR branch.
# Measuring against local HEAD there describes a different diff entirely.
git -C "$REPO" switch -q -c sidetrack
echo other >"$REPO/only-on-sidetrack.md"
git -C "$REPO" add only-on-sidetrack.md
git -C "$REPO" -c user.name=test -c user.email=test@example.com \
  commit -q -m "a commit the PR does not contain"
run_case "43 nocontext: 커버리지는 로컬 HEAD 가 아니라 PR head 에 고정" \
  "nocontext:only-on-sidetrack.md" PostToolUse "$COVER_GH" \
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


# --- gh lookup honours the subprocess floor ----------------------------------
# A slice below the floor cannot finish a gh round trip. Spawning one anyway
# spends budget the later group members were counting on and still answers
# "unknown", so the lookup must decline rather than try.
_floor_out=$(python3 - << PYEOF 2>&1
import importlib.util, sys, time
sys.path.insert(0, "$ROOT_DIR/hooks/_lib")
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
from _hook_runtime import MIN_SUBPROC_BUDGET_SEC

def spawned(*a, **k):
    raise AssertionError("gh spawned below the subprocess floor")
mod.subprocess.run = spawned

out, err = mod._gh(["pr", "view"], time.monotonic() + MIN_SUBPROC_BUDGET_SEC / 2)
if out is not None or not err:
    sys.stderr.write("sub-floor lookup did not decline: %r %r\n" % (out, err))
    sys.exit(1)

# Positive control: above the floor the same call DOES reach the subprocess,
# so the guard is declining on the budget rather than on everything.
try:
    mod._gh(["pr", "view"], time.monotonic() + MIN_SUBPROC_BUDGET_SEC * 10)
except AssertionError:
    pass
else:
    sys.stderr.write("above-floor lookup never reached the subprocess\n")
    sys.exit(1)
PYEOF
)
_floor_rc=$?
if [ "$_floor_rc" -eq 0 ] && [ -z "$_floor_out" ]; then
  echo "PASS  gh lookup declines below MIN_SUBPROC_BUDGET_SEC"
  PASS=$((PASS + 1))
else
  echo "FAIL  gh lookup ignores the subprocess floor (rc=$_floor_rc, out=$(echo "$_floor_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("gh lookup honours the subprocess floor")
fi


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
