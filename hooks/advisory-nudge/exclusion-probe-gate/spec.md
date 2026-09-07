# PreToolUse Exclusion-Probe Gate

Supported hosts: claude, codex

`hooks/advisory-nudge/exclusion-probe-gate/impl.py` (run in-process by the
`Edit|Write` dispatch group's `hooks/_dispatch.sh`, #1168) intercepts
`Write` and `Edit` tool calls and
emits a **stderr advisory** (or a hard deny in strict mode) when the artifact
content embeds an **exclusion directive** whose asserted **verification is not
backed by a cited probe**.

### Why this exists

A committed, pushed artifact carried (issue #807):

```
DELIBERATELY EXCLUDED (verified via HMS -- do NOT add these)
```

The `verified via HMS` claim was never actually run, and both exclusion
grounds were false. The migration shipped incomplete, and the surviving
`do NOT add these` directive would have justified deleting the source data on
the false premise "migration complete".

The damaging property is that the content is **self-authored**: not an external
quote, so sibling-grep or an external falsifier cannot catch it, and the
self-written `verified` marker actively suppresses a later reader's
re-verification. This hook is the structural enforcement layer for the global
behavioral-contract rule **Information Accuracy → Layer 2 → Author-exempt
verification trap**, which failed prompt-layer retrieval three generations
running (`feedback_prompt_layer_retrieval_failure_threshold`).

### Error class (deductive, not induced)

Per issue #807's central constraint, coverage is derived by **deductive
enumeration of the error class**, not induced from the single observed
`DELIBERATELY EXCLUDED` instance (the sister hook `mcp-describe-gate`, since removed, went
blind to a whole error-class arm by inducting from one retrospect instance):

> **Self-authored content that excludes a target from an artifact while
> asserting a verification it did not actually perform (no cited probe).**

Firing requires **both** axes to co-occur within a ±3-line block window, with
**no** probe citation nearby:

| Axis | Meaning | Examples |
| ---- | ------- | -------- |
| **A — exclusion directive** | imperative or intentful declarative telling a reader an item is purposely absent | `do NOT add`, `do not include`, `don't add`, `must not add`, `never re-add`, `deliberately excluded/omitted/skipped`, `excluded on purpose`; KO `의도적 제외`, `추가하지 말 것`, `포함하지 않음`, `일부러 제외`, `제외 대상` |
| **B — verification claim** | self-asserted grounding that would block re-verification | `verified via/with/by/against/using`, `confirmed via`, `checked with`, `validated against`, `cross-checked with`, `tested against`; KO `확인함`, `검증됨`, `대조 완료` |

Axis A alone is legitimate far too often (rule prohibitions, API-usage
comments, "what was NOT verified" PR sections), so the **conjunction** with a
concrete verification claim is what isolates the error class.

### What is emitted

| Condition | Behavior |
| --------- | -------- |
| Exclusion directive + live verification claim in window, no probe cited | Advisory to stderr (exit 0) |
| Same, with `PRAXIS_EXCLUSION_PROBE_STRICT=1` | Hard deny (exit 2) |
| Exclusion directive alone (no claim) | Silent |
| Probe citation (`Probe: <cmd> -> <output>`) in the window | Silent — substantiated |
| Negated claim (`not verified`, `has not been verified via`, `검증하지 않`) | Silent — negation governs the verification verb |
| Normative-document path (`CLAUDE.md`/`AGENTS.md`/`SKILL.md`/`spec.md`/…) | Silent — path skipped |
| Test / fixture path (`tests/`, `test_*`, `fixtures/`, `*_test.*`, `*.spec.*`) | Silent — path skipped |
| Directive inside a fenced code block / inline code / blockquote | Silent — masked as illustrative |
| Non-target tool (Bash, Read, …) | Silent |
| `PRAXIS_EXCLUSION_PROBE_SKIP=1` | Silent for all content |
| Malformed JSON stdin / any exception | Silent (fail-open) |

### Probe citation (exculpating)

A `Probe: <command> -> <one-line output>` citation (the
`skills/codex-review-wrap` Step 5g format) within the block window means the
exclusion is backed by a real observation → pass. Per issue **T0**, this is an
**existence** check only: whether the cited probe actually supports the
exclusion is a semantic judgment a regex cannot make.

### Design compromises and boundaries

- **T0 — verification-claim proxy.** The issue's firing condition is
  "exclusion directive ∧ (verification claim ∨ grounding narration)". The
  free-form *grounding narration* branch is deliberately narrowed to the
  explicit verification-claim tokens: an arbitrary rationale sentence is not
  regex-detectable without an unacceptable false-positive rate.
- **T0 — normative-path skip.** `CLAUDE.md` / `AGENTS.md` / `SKILL.md` /
  `spec.md` / `ETHOS.md` / `GEMINI.md` / `.cursorrules` are skipped wholesale —
  prohibition is their whole purpose, not a factual exclusion claim about an
  artifact.
- **Masking boundary.** Fenced code, inline code, and blockquote regions are
  masked before scanning so an illustrative example of a bad directive inside a
  doc does not fire. The trade-off: a *genuine* directive deliberately wrapped
  in a fence is not caught. This is the same threat-model-boundary posture as
  the hand-rolled-shell-parser lesson — surfaced here rather than chased with
  ever-more masking corner cases.
- **T1 — surface scope.** Write and Edit only. For Write the new `content` is
  scanned; for Edit the **post-edit full file** is scanned (current file with
  `old_string`→`new_string` applied), so a directive that already lives in the
  file plus an Edit that only adds the verification claim still co-occur — the
  two axes are checked against the resulting file, not the `new_string`
  fragment alone (fails open to the fragment if the file is unreadable). Bash
  heredoc / `gh … --body` writes are a **deferred follow-up**: covering them
  would be the third occurrence of the gh-body/heredoc extractor
  (`block-pr-without-caller-evidence`, `block-personal-asset-leak`) and should
  trigger a shared-helper refactor (DRY rule-of-three) rather than a third
  local copy in this PR.
- **Negation is verb-local.** A claim is treated as negated only when the
  negation governs the verification verb directly (allowing auxiliary/adverb
  fillers: `has not been verified via`, `was never actually confirmed with`).
  A negation that belongs to the exclusion directive itself (`do NOT add X,
  confirmed via Y`) does not suppress the claim.
- **Code-span masking is length-aware.** Fenced blocks close only on a fence
  run at least as long as the opener (a 4-backtick fence is not closed by an
  inner 3-backtick line), and inline-code masking handles multi-backtick
  delimiters (a stray backtick inside a `` ``double`` `` span does not end it).
- **T1 — advisory-first severity.** The detection target is natural language
  and the initial false-positive rate is unmeasured; escalation to a hard block
  is gated on measuring that rate in the field (mirrors `path-probe-gate`).

### Env vars

| Variable | Effect |
| -------- | ------ |
| `PRAXIS_EXCLUSION_PROBE_STRICT=1` | Escalate advisory to hard deny (exit 2) |
| `PRAXIS_EXCLUSION_PROBE_SKIP=1` | Disable the hook entirely for this session |

### Relationship to sibling hooks

| Hook | Overlap |
| ---- | ------- |
| `path-probe-gate` | None — that gate is about *where* a Write lands; this is about the *content* of the write. Shares the advisory-first + strict-escalation shape. |
| `output-block-falsify-advisory` | Complementary — that fires on confidence-anchoring tokens in output blocks; this fires on the exclusion+verification conjunction in artifact content. |
| `external-write-falsify-check` | Complementary (opt-in, off by default) — that scans `gh`/Slack/Notion write bodies for hypothesis markers; this scans file content for unprobed exclusions. |

### Fail-open

Exit 0 on every infrastructure error: malformed JSON stdin, non-target tool,
empty/absent content, `python3` unavailable (shell wrapper), any uncaught
exception (shared `@fail_open` guard).

### Tests

```bash
bash tests/hooks/advisory-nudge/test_exclusion_probe_gate.sh
```

Covers: the cause instance; every Axis-A × Axis-B variant (EN imperatives,
EN intentful declaratives, KO forms); Edit surface; adjacent-line proximity;
probe-cited pass (arrow + same-line); false-positive surfaces (directive-alone,
normative paths, test/fixture paths, fenced/blockquote masking, negated claim,
PR "not verified" section); strict deny; skip env; non-target tool; empty
content; malformed stdin; and the `@fail_open` structural assertion.
