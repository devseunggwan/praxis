# PreToolUse Output-Block Falsification Advisory + Ask-Escalation

Supported hosts: all

`hooks/output-block-falsify-advisory.py` fires on every `PreToolUse` event
for `AskUserQuestion` and `Bash` tool calls. It detects two surfaces where
a self-authored proposal block is about to be surfaced without a falsification
check and either asks for confirmation or emits an advisory reminder.

### Why this exists

The global `~/.claude/CLAUDE.md` rule **"Output-Block-Level Falsification Gate"** instructs:

> Before surfacing a self-authored proposal as a complete output block, run
> an explicit falsification test on its premise. If a concrete invalidating
> link/artifact exists — STOP. Do not surface the proposal.

And **"Self-Falsify Before Recommendation Lock"** adds:

> When labeling an option as `(Recommended)`, design and execute a disconfirming
> test of the recommendation's own premise BEFORE surfacing. State explicitly
> 'if this recommendation is wrong, what observation should be missing?' and
> confirm that observation is in fact missing.

Despite these rules being loaded into context (4+ memory entries accumulated
2026-05-03 through 2026-05-13), the retrieval trigger does not fire at the
specific moment the proposal block is authored. A 2026-05-17 retrospect session
confirmed 4/4 `(Recommended)` surfaces lacked verifiable Falsified: evidence.

Text rules and MEMORY.md entries alone have proven insufficient to prevent
recurrence. A structural hook moves the gate to the tool-call use-site.

References: issue [#221](https://github.com/devseunggwan/praxis/issues/221) (advisory),
[#290](https://github.com/devseunggwan/praxis/issues/290) (T1 ask escalation),
[#369](https://github.com/devseunggwan/praxis/issues/369) (T2 confidence-anchoring extension).

### What is detected

| Tool | Trigger condition | Decision |
| ------ | ----------------- | ---------- |
| `AskUserQuestion` (T1) | Option `label` contains exact `(Recommended)` or `(추천)` AND question body has no `Falsified:` line | `permissionDecision: deny` (ASK_MSG) |
| `AskUserQuestion` (T1) | Option `label` contains exact `(Recommended)` or `(추천)` AND question body has `Falsified:` line | Silent pass |
| `AskUserQuestion` (T2, issue #369) | Option `label` OR `description` contains a confidence-anchoring framing token AND question body has no `Falsified:` line | `permissionDecision: ask` (ANCHORING_ASK_MSG) |
| `AskUserQuestion` (T2) | Same as above + `Falsified:` present | Silent pass |
| `AskUserQuestion` (T3) | Option `label` contains case-insensitive `(recommended)` only | Dead under new precedence — T2's bare `recommend(ed\|s)?` token catches it first as ask |
| `Bash` | Command matches a bulk-action mutation keyword (see table below) | Advisory stderr |
| Any other tool | — | Silent pass-through |
| Malformed payload / missing field | — | Silent fail-open |

#### AskUserQuestion T1: (Recommended) marker and Falsified: gate

`(Recommended)` and `(추천)` in option labels are the canonical signal for a
self-authored proposal block about to be surfaced. When these exact tokens
(case-sensitive, including parentheses) are detected, the hook checks the
`question` field of each question object for a line that starts with `Falsified:`
(exact prefix at line start, as in `Falsified: checked no existing PR — none found`).

- **`Falsified:` present** → silent pass. The model has provided verifiable
  evidence of a disconfirming test.
- **`Falsified:` absent** → `permissionDecision: deny` with ASK_MSG (hard block — issue #393). The model
  must add the falsification line and retry.

T1 scans `options[].label` ONLY — description is scanned by T2 (below).

#### AskUserQuestion T2: confidence-anchoring framing (issue #369)

T1's literal-marker scope leaves two bypass paths observed in practice:

1. **Confidence-anchoring framing without `(Recommended)` marker** — option
   labels/descriptions using `가장 안전한` / `safer` / `자연스러운` / `prefer this`
   etc. carry the same ranking signal but bypass T1.
2. **Description-field placement** — anchoring framing placed in
   `options[].description` (not label) bypasses T1's label-only scan.

T2 closes both: it scans `label` AND `description` for these tokens.

**EN token set** (case-insensitive, ASCII word-boundary lookarounds —
Python's `\b` is Unicode-aware and would misfire on Hangul-adjacent ASCII):

- Single-word: `safer`, `safest`, `clearly`
- Multi-word: `natural fit`, `natural choice`, `obvious choice`, `default to`,
  `default choice`, `prefer this`
- Bare marker: `recommend(?:ed|s)?`

**KO substring set** (plain substring — Hangul has no ASCII boundary issue):

`안전한`, `가장 안전`, `자연스러운`, `당연히`, `분명히`, `추천`, `기본값`

**Satisfaction**: identical to T1 — a `Falsified:` line in question body
silent-passes T2. The model can either remove the anchoring framing or add
the falsification line.

**Precedence**: T1 fires first when both could trigger (literal marker in
label + anchoring in description). T2's `ANCHORING_ASK_MSG` differs from
T1's `ASK_MSG` so downstream parsers can distinguish which tier escalated.

#### Bash: bulk-action mutation keywords

| Type | Patterns detected |
| ------ | ----------------- |
| English (regex, case-insensitive) | `close\s+all`, `delete\s+all`, `merge\s+all`, `reject\s+all`, `approve\s+all` |
| Korean (substring) | `전부 닫`, `모두 닫`, `전부 삭제`, `모두 삭제`, `전부 머지`, `모두 머지`, `다 머지`, `전부 클로즈`, `모두 클로즈` |

Bulk-action commands often reflect a downstream consequence of a proposal block
whose premise was not falsified ("close all linked issues" after a misframed
proposal). The advisory fires conservatively: only mutation-verb patterns are
matched; read-only commands (`git log --all`, `gh pr list`) do not fire.

### Response shape

#### T1 Deny-escalation (AskUserQuestion with exact `(Recommended)` / `(추천)`, no `Falsified:`) — issue #393

**JSON to stdout** (message constant: `ASK_MSG`):

> Issue #682: message upgraded from instance-level ("add Falsified: and retry")
> to template-level — instructs Claude to bake the `Falsified:` line into the
> AskUserQuestion compose template for every future `(Recommended)` call, not
> just fix the current instance. Identified by the `[pre-author-template]` ASCII
> marker in the message body.

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "(Recommended) 라벨이 있으나 question body 에 'Falsified: <disconfirming test 결과>' 가 없음. CLAUDE.md Self-Falsify Before Recommendation Lock 룰. [pre-author-template] 이번 호출뿐 아니라 앞으로 (Recommended) 라벨을 붙일 때마다 AskUserQuestion 작성 직전(도구 호출 전) 에 첫 칼럼 시작 'Falsified: <검증 결과>' 줄을 템플릿에 포함하라 — 인스턴스 수정이 아닌 템플릿 수정이 필요하다. 'Falsified:' 는 자기 줄 첫 칼럼에서 시작해야 한다 (startswith 검사) — 질문문 중간/불릿/코드펜스 내부 배치는 미검출."
  }
}
```

#### T2 Ask-escalation (issue #369 — confidence-anchoring framing, no `Falsified:`)

**JSON to stdout** (message constant: `ANCHORING_ASK_MSG`):

> Issue #682: same template-level upgrade as T1. The `[pre-author-template]`
> marker identifies this as template-level guidance (not instance-level).

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "옵션 라벨/설명에 confidence-anchoring framing 토큰 (safer/safest/natural/obvious/clearly/default/prefer/recommend/안전한/자연스러운/당연히/분명히/추천/기본값) 이 있으나 question body 에 'Falsified: <disconfirming test 결과>' 가 없음. CLAUDE.md Output-Block-Level Falsification Gate. [pre-author-template] 이번 호출뿐 아니라 앞으로 confidence-anchoring 토큰을 옵션 라벨/설명에 쓸 때마다 AskUserQuestion 작성 직전(도구 호출 전) 에 첫 칼럼 시작 'Falsified: <검증 결과>' 줄을 템플릿에 포함하라 — 인스턴스 수정이 아닌 템플릿 수정이 필요하다. 'Falsified:' 는 자기 줄 첫 칼럼에서 시작해야 한다 (startswith 검사) — 질문문 중간/불릿/코드펜스 내부 배치는 미검출."
  }
}
```

**Exit code:** `0`.

#### Advisory (Bash bulk-action only)

Note: case-insensitive `(recommended)` alone previously emitted advisory
stderr; under issue #369 it is now caught by T2 (ask) before the
case-insensitive fallback fires. The advisory path remains for Bash
bulk-action keywords only.

**Advisory message** (emitted to stderr, never stdout):

```
[output-block-falsify-advisory] Surfacing a recommendation/bulk-action
proposal? Run the output-block falsification gate first: is the proposal's
premise already addressed by in-flight work, a merged PR, or a parallel
proposal in this session? If yes — STOP and cite the invalidating link
instead of surfacing the proposal.
```

**Exit code:** `0`.

### Parsing guarantees

| Condition | Behavior |
| ----------- | ---------- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `tool_name` not `AskUserQuestion` or `Bash` | exit 0 (silent pass) |
| Missing `questions` / `options` / `command` fields | exit 0 (silent pass) |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |
| Any uncaught exception | exit 0 (silent pass, no crash) |

The hook uses no external dependencies (no PyYAML, no third-party packages).
All parsing is done with the Python standard library only.

### Tests

```bash
bash tests/hooks/advisory-nudge/test_output_block_falsify_advisory.sh
```

Covers 47 cases (44 pre-#682 + 3 new):

**T1 deny-escalation (AskUserQuestion, issue #290/#393):**

- Option label `(Recommended)` + no `Falsified:` in question body → `permissionDecision: deny` (ASK_MSG)
- Option label `(추천)` + no `Falsified:` → `permissionDecision: deny`
- Option label `(Recommended)` + `Falsified:` line present → silent pass
- Non-recommended option labels → silent pass

**T2 ask-escalation (AskUserQuestion, issue #369):**
- KO `가장 안전한` in `options[].description` + no `Falsified:` → `ask` (ANCHORING_ASK_MSG) — in-vivo regression for the ai-dotfiles PR #84 session
- EN `safer` / `safest` / `prefer this` / `obvious choice` in label or description → `ask`
- KO `자연스러운` / `안전한` / `당연히` in description → `ask`
- Mixed Hangul/ASCII (`prefer this 옵션`) → `ask` — word-boundary regression
- `(Recommended)` in description-only (no marker in label) → `ask` (replaces former false-positive-guard pass)
- T2 anchoring + `Falsified:` line → silent pass
- T1 precedence: literal `(Recommended)` + anchoring description → `deny` with ASK_MSG (T1 wins, issue #393)

**T2 negative cases (must not fire):**

- `preferential treatment` (no token in set) → pass
- Bare `safe` (only `safer`/`safest` in set) → pass
- `unsafer` (word-boundary regression) → pass

**Pass (AskUserQuestion):**
- Option labels without any marker → silent pass
- Empty options → silent pass

**Advisory (Bash):**
- `merge all`, `close all`, `delete all` (English) → advisory emitted
- Korean: `모두 삭제`, `전부 머지`, `다 머지` → advisory emitted

**Pass (Bash):**
- `git status`, `gh pr list --state open` (read-only) → silent pass
- `git log --all` (--all flag, no mutation verb) → silent pass
- `disclose all`, `enclose all` (word-boundary regression) → silent pass

**Edge:**
- Malformed JSON stdin → exit 0, silent pass
- Empty payload → exit 0, silent pass
- Unknown tool name → exit 0, silent pass
- Non-string command (int / null) → exit 0, silent pass

**Template-level message cases (issue #682):**

- `(Recommended)` + no `Falsified:` → deny message contains `pre-author-template` ASCII marker
- confidence-anchoring (`safer`) + no `Falsified:` → ask message contains `pre-author-template` ASCII marker
- `(Recommended)` + column-0 `Falsified:` present → silent pass (regression — template-level message change must not break satisfaction)
