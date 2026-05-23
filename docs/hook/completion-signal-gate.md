# Stop Hook Completion-Signal Retrieval Gate

Supported hosts: all

`hooks/completion-signal-gate.py` fires on every `Stop` event and emits an
advisory to stderr when the last assistant turn contains a completion-signal
phrase without an evidence-block indicator in the same turn, or when a
cross-plugin slash command is surfaced in the wrong repo context.

### Why this exists

The 2026-05-23 retrospect session identified `effective_repeat=6` for the
Loaded≠Retrieved family: completion-signal phrases ("실질적 수정은 없습니다
... 머지하셔도 무방합니다", "done", "all set") were authored without
same-turn verification evidence despite 5+ MEMORY.md entries accumulating
for the root cause. Two concrete events triggered this hook:

**Event 1 — Premature completion claim**: After codex-review-wrap ran on
PRs #388/#389/#390, the assistant reported "실질적 수정은 없습니다.
머지하셔도 무방합니다." for PR #389/#390 without running per-PR verification.
User push-back required re-running per-PR tests (15/15 PASS, 24→27/27).
`completion-verify.sh` did not catch this because its `CLAIM_PATTERNS`
require a narrower terminal-position match; this hook catches the broader
claim vocabulary.

**Event 2 — Cross-plugin slash command surfacing**: While working in the
praxis repo, the assistant surfaced `/release` (a laplace-dev-hub skill) as
an option. The flat `available-skills` namespace across multiple loaded
plugins means foreign-namespace commands get suggested without repo-context
filtering. This sub-rule emits an advisory when either form appears in
praxis cwd output:

1. A namespaced `/plugin:command` whose plugin prefix is a known foreign
   namespace (`laplace-dev-hub:release`, `oh-my-claudecode:ralph`, ...).
2. A **bare** `/command` whose slug is in the curated `_KNOWN_FOREIGN_SKILLS`
   set — the original Event 2 trigger (`/release` with no prefix). Scope is
   intentionally narrow to avoid false positives on `/bin`, `/usr`, and
   unrelated nouns; only high-confidence foreign skill slugs are listed.

See also: `completion-verify.sh` (hard-block Stop hook for narrower
completion-claim patterns), `output-block-falsify-advisory.py` (PreToolUse
advisory for `(Recommended)` proposals).

References: issue [#392](https://github.com/devseunggwan/praxis/issues/392).

### What is detected

#### Rule 1 — completion-signal without evidence-block

When the last assistant turn's text contains a completion-signal phrase AND
no evidence-block indicator is present in the same turn, an advisory is emitted.

**Completion-signal phrases (EN, case-insensitive, ASCII word-boundary):**

| Phrase | Example |
|--------|---------|
| `no fixes needed` | "No fixes needed here." |
| `ready to merge` | "This PR is ready to merge." |
| `all set` | "All set." |
| `done` | "Implementation done." |
| `complete` | "Review complete." |

**Completion-signal phrases (KR, substring/regex):**

| Pattern | Example |
|---------|---------|
| `실질적 수정.*없` | "실질적 수정은 없습니다." |
| `머지하셔도` | "머지하셔도 무방합니다." |
| `완료\b` | "작업 완료." |
| `결함 없음` | "결함 없음." |
| `이상 없음` | "이상 없음." |

**Evidence-block indicators (any of these suppresses the advisory):**

| Indicator | What counts |
|-----------|-------------|
| Bash tool call | Any `tool_use` with `name == "Bash"` in the current turn |
| Read tool call | Any `tool_use` with `name == "Read"` in the current turn |
| Cited output | A `$ command → output` line in the assistant message |

#### Rule 2 — plugin-context anchoring (Event 2)

Fires in either of two forms when the cwd's active plugin is `praxis`
(detected via `.claude-plugin/marketplace.json` or git remote slug):

1. **Namespaced form**: `/namespace:command` where the namespace is one of the
   known foreign plugins (`laplace-dev-hub`, `oh-my-claudecode`, `omc`,
   `codex`, `scheduler`, `gemini`, `laplace-wiki`).
2. **Bare form**: `/command` (no namespace) where the slug is in
   `_KNOWN_FOREIGN_SKILLS`. Conservative curated set scoped to slugs that
   are unambiguously foreign — `release`, `hub-bulk-release`, `hub-scan-issues`,
   `dev-to-prod-pr`. Add to the set in `completion-signal-gate.py` when new
   high-confidence foreign skill names emerge; do not include ambiguous words.

### Response

Both rules emit advisory text to **stderr only**. No JSON is written to
stdout. The hook always exits 0 — it never blocks.

**Rule 1 advisory (stderr):**

```
[praxis:completion-signal-gate] completion-signal phrase detected in last turn without an evidence-block (Bash tool result, Read tool call, or cited '$ command → output' line).
[praxis:completion-signal-gate] Rule: CLAUDE.md 'Verification Before Completion' — run a real verify command (test/lint/build/probe) and paste its output BEFORE declaring completion.
[praxis:completion-signal-gate] Trigger: matched completion-signal token in last assistant turn. Add evidence or remove the completion phrase to suppress this advisory.
```

**Rule 2 advisory (stderr):**

```
[praxis:completion-signal-gate] cross-plugin slash command(s) /laplace-dev-hub:close-hub-issue surfaced while cwd plugin is 'praxis'.
[praxis:completion-signal-gate] Rule: CLAUDE.md 'Plugin-context anchoring' — do not surface skill commands from foreign plugin namespaces. Verify you are working in the correct repo/plugin context before recommending slash commands.
```

**Exit code:** `0` in all cases.

### Tier: advisory (v1)

v1 is advisory-only — no `permissionDecision` JSON, no blocking. The advisory
fires as a `system-reminder` that Claude sees as additional context in the next
turn.

**Tier promotion path (deferred — follow-up issue):**

Once false-positive rates are measured over 1+ week of real sessions:

1. **ask tier**: add `permissionDecision: ask` JSON to stdout when Rule 1
   triggers. Claude must acknowledge before the Stop completes.
2. **block tier**: upgrade to `decision: block` (matching `completion-verify.sh`
   response shape). Appropriate only after ask tier validates low false-positive
   rate.

To promote, update `hooks/completion-signal-gate.py` to emit:

```json
{"decision": "block", "reason": "..."}
```

to stdout (not stderr) and re-run `scripts/build-plugin-manifests.py`.
No change to `hooks/hooks.json` entry is required for tier promotion.

### Parsing guarantees

| Condition | Behavior |
|-----------|----------|
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `stop_hook_active` is true | exit 0 (silent pass, re-entry guard) |
| Missing / unreadable `transcript_path` | exit 0 (silent pass) |
| Empty transcript file | exit 0 (silent pass) |
| No assistant messages in current turn | exit 0 (silent pass) |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |
| Any uncaught exception | exit 0 (silent pass, no crash) |

The hook uses no external dependencies (stdlib only: `json`, `os`, `re`,
`subprocess`, `sys`, `pathlib`).

### Relationship to `completion-verify.sh`

`completion-verify.sh` enforces a hard block when specific terminal-position
completion claims appear without same-turn Bash+evidence+paste (L1+L2+L3
triple gate). This hook is complementary:

- **Broader phrase vocabulary** — catches `ready to merge`, `no fixes needed`,
  `이상 없음`, `결함 없음` that `completion-verify.sh` does not match.
- **Weaker evidence gate** — any Bash or Read tool call suppresses this hook;
  `completion-verify.sh` additionally requires the evidence span to be pasted
  verbatim in the message (L2).
- **Advisory vs block** — this hook emits stderr advisory only; the sibling
  hard-blocks. Both fire on the same Stop event, independently.

### Tests

```bash
python3 -m pytest tests/hooks/test_completion_signal_gate.py -v
```

Covers 48 cases:

**Rule 1 trigger (15 phrases — EN + KR):**
- EN: `no fixes needed`, `ready to merge`, `all set`, `done`, `complete`
  (case-insensitive variants included)
- KR: `실질적 수정은 없습니다. 머지하셔도 무방합니다.`, `머지하셔도 됩니다`,
  `완료`, `결함 없음`, `이상 없음`

**Rule 1 suppression (3 evidence-block types):**
- Bash tool call in turn → suppressed
- Read tool call in turn → suppressed
- Cited `$ command → output` line → suppressed

**False-positive cross-checks (5 normal-completion samples):**
- FP1: Bash + evidence signal → no advisory
- FP2: No completion phrase → no advisory
- FP3: Read tool + completion phrase → suppressed
- FP4: Bash lint-clean → no advisory
- FP5: Mid-task assistant message (no completion phrase) → no advisory

**Event 1 reproduction:**
- Exact issue quote "실질적 수정은 없습니다. 머지하셔도 무방합니다." → advisory

**Rule 2:**
- Foreign `/laplace-dev-hub:close-hub-issue` in praxis cwd → advisory

**Fail-safe paths (4):**
- Malformed JSON stdin → exit 0
- `stop_hook_active: true` → exit 0
- Missing `transcript_path` → exit 0
- Empty transcript → exit 0

**Internal unit tests:**
- `_has_completion_signal`: 15 parametrized cases (true/false, EN/KR, word-boundary)
- `_has_evidence_block`: 4 parametrized cases
