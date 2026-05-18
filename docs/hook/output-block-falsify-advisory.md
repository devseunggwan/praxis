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
[#290](https://github.com/devseunggwan/praxis/issues/290) (ask escalation).

### What is detected

| Tool | Trigger condition | Decision |
|------|-----------------|----------|
| `AskUserQuestion` | Option `label` contains exact `(Recommended)` or `(추천)` AND question body has no `Falsified:` line | `permissionDecision: ask` |
| `AskUserQuestion` | Option `label` contains exact `(Recommended)` or `(추천)` AND question body has `Falsified:` line | Silent pass |
| `AskUserQuestion` | Option `label` contains `(recommended)` (case-insensitive, not exact) | Advisory stderr |
| `Bash` | Command matches a bulk-action mutation keyword (see table below) | Advisory stderr |
| Any other tool | — | Silent pass-through |
| Malformed payload / missing field | — | Silent fail-open |

#### AskUserQuestion: (Recommended) marker and Falsified: gate

`(Recommended)` and `(추천)` in option labels are the canonical signal for a
self-authored proposal block about to be surfaced. When these exact tokens
(case-sensitive, including parentheses) are detected, the hook checks the
`question` field of each question object for a line that starts with `Falsified:`
(exact prefix at line start, as in `Falsified: checked no existing PR — none found`).

- **`Falsified:` present** → silent pass. The model has provided verifiable
  evidence of a disconfirming test.
- **`Falsified:` absent** → `permissionDecision: ask`. The model must add the
  falsification line and retry.

Only `options[].label` is scanned for the marker — `options[].description` text
that incidentally contains `(Recommended)` does not trigger the gate.

#### Bash: bulk-action mutation keywords

| Type | Patterns detected |
|------|-----------------|
| English (regex, case-insensitive) | `close\s+all`, `delete\s+all`, `merge\s+all`, `reject\s+all`, `approve\s+all` |
| Korean (substring) | `전부 닫`, `모두 닫`, `전부 삭제`, `모두 삭제`, `전부 머지`, `모두 머지`, `다 머지`, `전부 클로즈`, `모두 클로즈` |

Bulk-action commands often reflect a downstream consequence of a proposal block
whose premise was not falsified ("close all linked issues" after a misframed
proposal). The advisory fires conservatively: only mutation-verb patterns are
matched; read-only commands (`git log --all`, `gh pr list`) do not fire.

### Response shape

#### Ask-escalation (AskUserQuestion with exact `(Recommended)` / `(추천)`, no `Falsified:`)

**JSON to stdout:**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "(Recommended) 라벨이 있으나 question body 에 'Falsified: <disconfirming test 결과>' 가 없음. global `~/.claude/CLAUDE.md` Self-Falsify Before Recommendation Lock 룰. 추가 후 재시도."
  }
}
```

**Exit code:** `0`.

#### Advisory (Bash bulk-action, or case-insensitive `(recommended)` only)

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
|-----------|----------|
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
bash tests/test_output_block_falsify_advisory.sh
```

Covers 26 cases:

**Ask-escalation (AskUserQuestion, issue #290):**
- Option label `(Recommended)` + no `Falsified:` in question body → `permissionDecision: ask`
- Option label `(추천)` + no `Falsified:` → `permissionDecision: ask`
- Option label `(Recommended)` + `Falsified:` line present → silent pass
- `(Recommended)` appears in `options[].description` only (not label) → silent pass (false positive guard)
- Non-recommended option labels → silent pass

**Advisory (AskUserQuestion, case-insensitive fallback):**
- Option label `(recommended)` lowercase only → advisory emitted

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
