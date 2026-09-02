# Example flow (codex-review-wrap)

A worked end-to-end walkthrough of [`../SKILL.md`](../SKILL.md): worktree
selection (Steps 1-3), the review run (Step 4), every Step 5 gate firing
on concrete findings across multiple rounds, and the 5j round loop.

```
user: /codex-review-wrap

[Step 1] git worktree list result:
  0: /Users/dev/project/my-repo               (main)
  1: /Users/dev/project-wt/my-repo-feature-1  (issue-1-feature)

[Step 2] AskUserQuestion →
  "어느 worktree 를 review 할까요?"
  0: /Users/dev/project/my-repo (main)
  1: /Users/dev/project-wt/my-repo-feature-1 (issue-1-feature)

user selects: 1

[Step 3] Review target: /Users/dev/project-wt/my-repo-feature-1 (branch: issue-1-feature)
  ⚠ cwd (/Users/dev/project/my-repo) ≠ review target

[Step 4] cd /Users/dev/project-wt/my-repo-feature-1
         → node {install_path}/scripts/codex-companion.mjs review

[Step 5 — Round 1 — counter update (5f, first action)]:
  ledger: rounds_per_region: query.sql:filter_clause | round=1 | cumulative=1
  ledger: rounds_per_region: cli.sh:parse_prompt      | round=1 | cumulative=1
  (cumulative ≤ threshold=4 → no advisory emitted yet)

[Step 5 — Round 1 — sibling check (5d-i)]: AskUserQuestion fired:
  User: "이 PR은 praxis#199 (shell 버전)의 Python port입니다."
  → sibling identified: praxis#199 on branch issue-199-hook-shell

[Step 5 — Round 1 — classify + verify (5a → 5b)] Codex returned 3 findings:
  - F1: rename `query()` → `run_query()`           [structural — no premise check, still gated by 5i]
  - F2: change WHERE col_a = 1 → col_b = 1         [fact-modifying — verify column exists]
  - F3: drop the `--state all` flag                [fact-modifying — verify CLI accepts the value]
  Verify F2: DESCRIBE my_table → col_b not present
    → ledger: rejected: query.sql:L42 | round=1 | col_a → col_b | reason: col_b absent in DESCRIBE
    (evidence-rejected → not put through the 5i gate)
  Verify F3: gh search issues --help → --state accepts only {open, closed}

[Step 5i — approval gate] 2 findings survive to the gate (F3 fact-modifying, F1 structural)
  1 finding rejected on evidence: F2 (col_b absent) — reported, not offered as 적용
  AskUserQuestion (1 call, 2 questions):
    Q1 "F3 — cli.sh:L10 --state 값 수정"   (question text starts with the finding ID)
       body: cli.sh:parse_prompt
             변경: "--state all" → "--state open"
             판정: apply — 현재 값은 CLI 가 거부함
             Probe: gh search issues --help → --state {open|closed}  ("all" 미지원)
             flip: none
       options: 적용 (Recommended) / 미적용 / 후속이슈
       적용 description: "Falsified: '--state all 도 허용된다' 가설 반증 — probe 출력에 all 없음"
    Q2 "F1 — query() → run_query() 리네임"
       body: query.sql:query
             변경: query() → run_query()
             판정: apply — 호출부 3곳 동시 수정 필요
             Probe: n/a (structural)
             flip: none
       options: 적용 (Recommended) / 미적용 / 후속이슈
       적용 description: "Falsified: '호출부가 이미 run_query 를 쓴다' 가설 반증 —
         grep -n 'query(' src/ → 3곳 모두 구 이름 사용"
  User: Q1=적용, Q2=후속이슈
  → apply F3; ledger: applied: cli.sh:L10 | round=1 | "--state all" → "--state open"
  → skip F1;  ledger: rejected: query.sql:query | round=1 | query() → run_query() | reason: user: deferred to follow-up
               ledger: deferred: query.sql:query | round=1 | finding=F1(rename) | issue=pending
  (아직 커밋하지 않음 — 실행 순서상 5d-ii/iii 가 5e 앞에 온다)
  Round 종료 시 deferred 1건에 대해 구현 접근안 리뷰 surface → 승인 시에만 이슈 생성

[Step 5d] Cross-check sibling: praxis#199 (branch issue-199-hook-shell)
  Apply same test for F3 against sibling:
    cd /path/to/praxis-wt/issue-199-hook-shell
    gh search issues --help → --state accepts only {open, closed}
    sibling hook also uses "--state all" on line 8 → same defect confirmed
  ledger: sibling-applied: praxis#199 | round=1 | finding=F3(--state all) | result=same defect
  ⚠ 형제 구현체 동일 결함 발견:
     현재 PR: praxis#200 — finding: F3 (--state all)
     형제 PR:  praxis#199 — 동일 결함 확인 근거: hook.sh:L8에서 "--state all" 사용 확인
  → surface to user for separate approval before applying sibling fix

[Step 5e — commit, after 5d completes]
  Commit F3 with trailer:  Premise-Verified: gh search issues --help (excerpt)

[Step 5j — round-continuation gate] fire condition check:
  (a) applied this round = 1 (F3)                          → pass
  (b) scope=auto, worktree clean after commit → next round resolves to branch
      diff; F3's edit confirmed present in it                 → pass
  (c) interactivity check re-run for this round             → pass
  AskUserQuestion:
    "라운드 1 완료 — 이번 라운드 1건 적용, 누적 1라운드. Codex 리뷰를
     한 번 더 실행할까요?"
    옵션: 추가 라운드 실행 / 현재 라운드로 충분 — Step 6 으로 진행
  User: 추가 라운드 실행
  → ledger: round-continued: target=/Users/dev/project-wt/my-repo-feature-1#issue-1-feature | from=1 | applied=1 | decision=continue | to=2
  → re-enter Step 4 (Steps 1–3 skipped, 5d-i and 4a not re-asked — the sibling-id:
    and review-path: rows for this target exist, PR-state re-checked)

[Step 5 — Round 2] Codex now re-suggests changing WHERE col_a = 1 → col_b = 1
  Scan ledger: rejected entry on query.sql:L42 with same A → B transition exists
  → flip fires (re-proposal of rejected); halt and surface to user

[Step 5 — Round 2 alt] Codex now suggests "--state open" → "--state all"
  Scan ledger: applied entry on cli.sh:L10 reverses → flip fires (applied flip); halt

[Step 5j — Round 2, gate skipped] the flip halted before any edit landed:
  (a) applied this round = 0                                → fail
  → gate does not fire; no round-continued: row is written; proceed to Step 6.
  (Had the user resolved the flip and applied the surviving side, (a) would
   pass and the gate would fire — a flip halt is not a termination path.)

[Step 5j — alternative branch, NOT a continuation of the block above]
  The block above ended at Step 6, so no round 3 follows it. This branch shows
  what a round 3 would look like on the other path: the user resolves the round-2
  flip, applies the surviving side ({C} = 1), the gate fires, the answer is
  추가 라운드 실행, and Step 4 is re-entered.

  [Round 3, gate skipped] Codex returned 0 findings
    5h still ran this round and synthesized nothing either → {C} = 0 → gate does
    not fire → Step 6. (Had 5h synthesized a truncation and the user approved it,
    {C} would be 1 and the gate would fire — "Codex 0 findings" alone is not a
    skip condition.)

[Step 5f — Diminishing-returns example] PRAXIS_DIMINISHING_RETURNS_N=4 (default)
  Rounds 1–4: counter increments silently
    ledger: rounds_per_region: cli.sh:parse_prompt | round=1 | cumulative=1
    ledger: rounds_per_region: cli.sh:parse_prompt | round=2 | cumulative=2
    ledger: rounds_per_region: cli.sh:parse_prompt | round=3 | cumulative=3
    ledger: rounds_per_region: cli.sh:parse_prompt | round=4 | cumulative=4
  Round 5 (cumulative = threshold+1 = 5): advisory emitted once, then 5a continues normally
    ledger: rounds_per_region: cli.sh:parse_prompt | round=5 | cumulative=5
    Advisory: this is round 5 on cli.sh:parse_prompt. Findings to date suggest
    the underlying surface enumeration may be incomplete. Consider pausing
    to re-enumerate cases up-front before continuing.
  Round 6+: counter still increments, advisory NOT re-emitted
    ledger: rounds_per_region: cli.sh:parse_prompt | round=6 | cumulative=6

[Step 5g — critic pre-lock probe check, F2 scenario]
  Critic finding (round-1): "PRAXIS_ASK_END_STRICT is a fabricated precedent —
    it does not exist in hooks/*.py"
  → gate fires: negative-claim form "does not exist" detected
  → critic required to run probe before surfacing:
    Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py →
      28: Deprecated: PRAXIS_ASK_END_STRICT=1 is still respected when explicitly set
      452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
  → probe disproves claim → critic must retract:
    Retracted: "PRAXIS_ASK_END_STRICT is fabricated"
    Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py →
      line 28, 30, 31, 417, 451, 452, 457 found
    Finding: variable exists — claim withdrawn.

[Step 5g — critic pre-lock probe check, F1 scenario]
  Critic finding (round-3): "both --literal-pathspecs and --super-prefix are
    boolean flags and cannot take a value argument"
  → gate fires: negative-claim form "cannot take a value" (has no runtime effect variant)
  → critic required to run probe per flag before surfacing:
    Probe: man git | grep -A2 '\-\-literal-pathspecs' → [no value argument — boolean confirmed]
    Probe: man git | grep -A2 '\-\-super-prefix' → --super-prefix=<path> [takes a value — NOT boolean]
  → second probe partially disproves the grouped claim
  → critic surfaces retracted + refined finding:
    "--literal-pathspecs is boolean (confirmed). --super-prefix takes a value (claim retracted for this flag)."

[Step 5h — SoT enumeration audit]
  Parent doc (current PR): skills/phase-router/SKILL.md
  Scan for citation signals:
    - "Test 1-7" cited at heading "## Acceptance Criteria"  → citation signal: enumerated test IDs
    - "Phase 0, Phase 1" cited at heading "## Prerequisites" → citation signal: enumerated phase IDs
  Locate sibling SoTs:
    - Test SoT: skills/phase-router/SKILL.md#test-definitions → Read → finds Test 1–9 (conditional Tests 8, 9)
    - Phase SoT: skills/phase-router/SKILL.md#phase-applicability → Read → finds Phase 0, 1, 1a, 2
  Compare:
    - Tests: parent_count=7, source_count=9 → truncation detected (missing Test 8, Test 9)
    - Phases: parent_count=2, source_count=4 → truncation detected (missing Phase 1a, Phase 2)
  Emit synthesized findings:
    ⚠ SoT truncation detected — [#395]:
      Parent: skills/phase-router/SKILL.md:Acceptance Criteria
      Sibling SoT: skills/phase-router/SKILL.md:Test definitions
      Source rows: 9  |  Parent-cited rows: 7
      Missing: Test 8 (conditional — cache miss path), Test 9 (conditional — retry path)
    ⚠ SoT truncation detected — [#395]:
      Parent: skills/phase-router/SKILL.md:Prerequisites
      Sibling SoT: skills/phase-router/SKILL.md:Phase applicability matrix
      Source rows: 4  |  Parent-cited rows: 2
      Missing: Phase 1a, Phase 2
  → both findings flow through 5a classification (structural), 5c flip check, then apply cycle
```
