# PreToolUse Bash Momentum Rule Retrieval Gate

Supported hosts: claude, codex

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py` intercepts `Bash` tool calls at
high-momentum action points and emits a **stderr advisory** surfacing the
relevant CLAUDE.md rules and memory entries that retrieval failure studies
show are most likely to be skipped under session momentum.

The `dispatch` and `force-push` triggers are advisory-only in default mode.
The `merge` trigger additionally **blocks** (via `permissionDecision: deny`)
when the assistant text preceding the merge lacks the Pre-Merge Reporting
briefing — see [Merge-briefing escalation](#merge-briefing-escalation-issue-797)
below.

### Why this exists

The 2026-05-18 retrospect identified 3 friction events that all converged on
the root cause "Loaded ≠ Retrieved at execution time": rules and memory entries
are in context but fail retrieval at the exact moment a high-stakes action
(multi-PR merge, agent dispatch, force-push) is executed. The *Pre-Merge
Reporting* and *No Approval Transfer Across Companion PRs* rules
([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) and the
`feedback_force_history_rewrite_mutation` memory entry have each been violated
in sessions where they were already loaded. This hook fires at exactly those
trigger points so the retrieval gap is filled by the hook infrastructure
rather than relying on in-context retrieval alone.

### Trigger commands (Phase 1)

Phase 1 keeps the momentum signal simple: **any single matching command
triggers the surface**. Multi-mutation detection (e.g., surfacing only when
N merges occur in rapid succession) is deferred to Phase 2 (separate issue).

| Command pattern | trigger id | Static rule cites | Dynamic memory cites |
| ----------------- | ------------ | ------------------- | ---------------------- |
| `gh pr merge` (any flags) | `merge` | Pre-Merge Reporting + No Approval Transfer | every memory with `momentum:` containing `merge` |
| `cmux new-workspace` (dispatch via cmux) | `dispatch` | Pre-Implementation Surface Enumeration → Multi-PR / multi-worktree shared state + Self-Authored Labels Are Drafts | every memory with `momentum:` containing `dispatch` |
| `git push --force` / `--force-with-lease` / `-f` | `force-push` | trigger header + history-rewrite mutation rule | every memory with `momentum:` containing `force-push` |

### Dynamic memory loading

Memory cites are no longer hardcoded — they are loaded at hook fire time
from the same user-scoped memory directory used by `memory-hint`, resolved
via the shared `hooks/_lib/_memory_dir.py` resolver (#823): `PRAXIS_MEMORY_DIR`
env var when set, otherwise `~/.claude/projects/{slugified-cwd}/memory/` —
slugify replaces every non-alphanumeric character with `-`, per-character
(Claude Code's own project-slug rule). A memory file participates
by adding a flat single-line `momentum:` list to its frontmatter:

```yaml
---
name: my-memory
description: Short rule statement
type: feedback
momentum: [merge]                  # one or more of: merge, dispatch, force-push
---
```

Multi-trigger memories: `momentum: [merge, force-push]`. When a triggered
command matches `merge` AND `force-push` in the same Bash call, the
memory is cited under both surfaces. Memories without a `momentum:` field
(or with an unparseable / empty list) are ignored. The frontmatter parser
is regex-only — multi-line YAML / flow-mapping forms are not supported
and silently skip the memory rather than raise.

### What is emitted

Each triggered surface writes lines to stderr, all prefixed with
`[praxis:momentum-gate]`. The `dispatch` and `force-push` surfaces never block
in default mode. The `merge` surface additionally emits a `permissionDecision:
deny` when the pre-merge briefing is incomplete — see
[Merge-briefing escalation](#merge-briefing-escalation-issue-797).

Example for `gh pr merge --squash`:

```
[praxis:momentum-gate] ── TRIGGER: gh pr merge ──────────────────────────────────
[praxis:momentum-gate]
[praxis:momentum-gate] Rule: Pre-Merge Reporting (CLAUDE.md)
...
```

### Merge-briefing escalation (issue #797)

The `merge` trigger alone is advisory — but a stderr line cannot stop a
`gh pr merge` that skips the Pre-Merge Reporting briefing, and that failure
recurred despite the advisory firing (memory
`feedback_pre_merge_briefing_compound_imperative`, two recurrences; the #795
retrospect confirmed the advisory fired yet the merge ran with 3 of 6 briefing
items and no explicit approve-ask). For the `merge` trigger **only**, the hook
reads the assistant text preceding the merge call and, when fewer than
`MERGE_BRIEFING_MIN_ITEMS` (default **4**) of the 6 Pre-Merge Reporting items
are present, escalates to `permissionDecision: deny` so the merge is blocked
and the agent must produce the briefing before retrying. The stderr advisory
is still emitted alongside the deny.

The 6 items (a single keyword hit marks each present, EN/KO): What changed /
What was verified / What was NOT verified / Risk-blast-radius / Open items /
explicit approve-ask. Keyword groups live in `_BRIEFING_ITEM_GROUPS`
(`impl.py`); EN/KO variants are enumerated there, not duplicated in prose here.

**Vocabulary coverage is EN/KO substring matching, not free-form Korean
(issue #1118).** A real 6-item briefing was blocked at 3/6 because it phrased
group0/2/4 as `무엇이 바뀌는 것` / `검증 안 된` / `열린 항목` — content the
Pre-Merge Reporting rule fully satisfies, but strings the keyword groups did
not yet contain (`_BRIEFING_ITEM_GROUPS` had only `무엇이 변경` / `미검증` /
`미확인` / `남은 항목` / `남은 작업` for those groups). The author was forced to
rewrite the briefing in the hook's dialect rather than fix content — it
recurred 6 times in one session (3 PRs × 2 blocks each). Fixed by adding the
observed variants (`무엇이 바뀌`, `바뀌는 것`, `검증 안 된`, `검증되지 않은`,
`열린 항목`, `남은 것`) to the existing groups.

**Regression fixture provenance.** `tests/fixtures/momentum-merge-natural-korean-wording.jsonl`
is a reconstruction from the issue's per-group miss/HIT lists, not the
original session transcript — the transcript that hit the actual 3/6 block
belongs to a different session and is not available to the fix. The
reconstruction reproduces the same group0/2/4 miss pattern that section
described (verified: pre-fix scores 3/6, post-fix 6/6), but a wording not
covered by the issue's reproduction could in principle still score below
4/6 on the real transcript without this fixture catching it.

**Known limitation — vocabulary chasing, not structural scoring.** This gate
scores by keyword substring, so it stays open-ended: any Korean phrasing not
yet in `_BRIEFING_ITEM_GROUPS` reads as absent even when the content is
present. Scoring by briefing *structure* (numbered or bolded 6-part lists)
was considered and rejected for this fix — it would let a content-free fake
6-item list pass, defeating the gate's purpose — and is deliberately left
unimplemented. Revisit only if vocabulary additions keep failing to prevent
recurrence.

**Window scoping (issue #826).** The mandated Pre-Merge Reporting flow is
*briefing → user approval → merge*, which necessarily places the briefing in
the turn **before** the approving user message — outside the "since the last
human user message" window. Scoring only that window would therefore penalise
the *correct* flow (briefing → "ok" → merge) and pass only the anti-pattern
(briefing + merge crammed into one turn, i.e. auto-proceed without waiting).
The **prior-turn extension** resolves this. When the last human user message is
an approval reply (`ok` / `진행` / `승인` / … — matched against
`_APPROVAL_TOKENS` either as the whole normalized message or as its **final
clause**, issue #1087), the immediately preceding assistant turn is scored
**alone** (post-approval text must not supplement a briefing the user never saw
before approving). Safety gates on the extension:

- **Final-clause matching (issue #1087).** Whole-message equality alone denied
  every approval phrased as prose — `ok, merge it`, `사용해보고 결정하겠습니다.
  머지 진행` — while the barer `ok` passed, so the mandated flow was blocked
  whenever the user wrote a sentence. The message is split on clause boundaries
  (`.!?。…,;·` and newline) and only the LAST clause is matched, which keeps the
  property whole-message equality existed for: a message whose final clause is a
  fresh instruction (`머지 진행 상황 알려주고 파서부터 고쳐줘`, `merge it but
  rebase first`) is not an approval, no matter what appears earlier in it.

- **PR correlation.** The prior-turn briefing must reference the PR actually
  being merged. The merge target is the segment's *first positional* token
  (value-flag arity skipped, including trailing gh global flags like `-R
  org/repo <N>`), classified three ways:
  - **Numeric** (`gh pr merge 833`, `.../pull/833`) → the prior turn must
    reference that `#N` (or bare number).
  - **No positional** (`gh pr merge --squash`, the project standard) → a
    current-branch merge; the real target is **derived from the window's
    mandated Pre-Merge probe** (`gh pr checks/view <N>`, scanned from assistant
    tool_use Bash commands) and the prior turn must reference *that* number.
    When no probe resolves a target, the extension **fails closed** (denies).
  - **Named branch** (`gh pr merge feature-x`) → targets that branch's PR, which
    cannot be mapped to a number without a live `gh` call and is not the current
    branch, so the window probe does not identify it → **fails closed** (denies).
    A prior probe of a *different* PR must not be treated as this branch's target
    (round-3 P1b).
  - Command parsing reuses the trigger tokenizer, so fused global flags
    (`gh --repo=o/r pr merge 833`) resolve the target correctly.

  > **Accepted residual gaps (advisory-hook threat model).** This gate is a
  > self-discipline *nudge* for an honest agent that forgets the briefing (the
  > #795 failure), not an adversarial security boundary — `deny` merely adds
  > teeth, and `PRAXIS_MOMENTUM_MERGE_ADVISORY=1` is a standing escape hatch. Two
  > CLI forms that only an adversarial target-swap would use are therefore left
  > as documented gaps rather than chased across further rounds: (a) an
  > `xargs`-wrapped merge (`… | xargs -n1 gh pr merge`) is not recognized by the
  > trigger tokenizer (`xargs` is not a peeled prefix), so it bypasses the gate
  > entirely; (b) a bare-number reference is not repo-scoped, so a cross-repo
  > `gh -R org/other pr merge 833` after briefing *this* repo's #833 (or a
  > `Closes #833` line in a different PR's briefing) can false-correlate. Both
  > require an explicit-signal redesign to close soundly and are out of scope for
  > the honest-agent nudge.
- **No multi-target / loop transfer.** A compound `gh pr merge A && gh pr merge
  B` (≥2 merge segments) OR a shell loop / `xargs` repeating one segment
  (`for pr in 833 999; do gh pr merge "$pr"; done`, detected via whole-token
  match of `for`/`while`/`until`/`xargs`) skips the extension entirely — one
  approval cannot authorize repeated merges (No Approval Transfer Across
  Companion PRs).
- **Trivial carve-out.** A trivial-PR marker in the *prior* turn is honored the
  same as in the current turn (the 2-line briefing sits before the "ok"), but
  **only once the merge is correlated to the briefed PR** — an unrelated trivial
  briefing about a different PR cannot carve out the merge.

> **Recording-fidelity axis (deferred).** A separate #826 concern — harnesses
> that drop assistant narration so a real briefing scores 0 — is **not** handled
> here. A text-absence heuristic cannot distinguish a non-recording harness from
> a genuine briefing skip that happens to be tool-only (it is gameable by
> suppressing all narration), so it was dropped from this change rather than
> shipped as a weak gate. If needed, address it with an explicit harness signal
> in a follow-up, not an inference.

**Why `deny`, not `ask`:** the sibling `pre-merge-approval-gate` already emits
an unconditional `ask` on *every* `gh pr merge`. A second `ask` would be
redundant with the exact gate that failed to prevent the #795 recurrence (the
user approved the `ask` blindly while the agent never produced the briefing).
`deny` adds the missing teeth — it blocks and feeds the reason back so the
agent self-corrects, rather than re-surfacing an approval the user will again
approve blindly.

**Escalation is contained** (fail-open and false-positive relief):

- No readable transcript (`transcript_path` absent/unreadable) → no escalation
  (fail open). The advisory still fires.
- `PRAXIS_MOMENTUM_MERGE_ADVISORY=1` → demote back to advisory only (keeps the
  stderr reminder, skips the deny). The escape hatch for a mis-scored briefing.
- `# briefing-surfaced` in the merge command (an **in-band** marker, issue #826)
  → demote back to advisory only. The env bypass above is read from the hook
  process `os.environ` and is **not** reachable via a Bash inline `VAR=1 cmd`
  prefix — the hook is spawned by the harness, not as a child of the command. In
  a bridge-session harness that *also* drops assistant text from the transcript
  (so the briefing scores 0), a legitimate briefing-surfaced merge would be
  permanently blocked with no in-band release. A shell-comment marker embedded in
  the command (harmless at exec — bash ignores everything after `#`) is reachable
  in-band. Matched case-insensitively as `#\s*briefing-surfaced`; a trailing
  `: <reason>` is recommended but not required. This is a conscious
  self-attestation, appropriate for a self-discipline nudge (not an adversarial
  boundary): the agent asserts the briefing was surfaced, exactly as the env
  bypass would. The marker is honored only in a **real** unquoted shell comment,
  parsed with a continuous quote/escape scanner (`_split_shell_comment`): a
  quoted `'# briefing-surfaced'`, a mid-word `x#`, a backslash-escaped `\#`, or a
  `#` inside a single-quoted string spanning newlines is data, not an
  attestation, and does not demote (round-1/2/3 codex findings). It is honored
  only for a **single** merge segment with no loop (No Approval Transfer). A
  `# briefing-surfaced` inside a heredoc body (`<<EOF … EOF`) is the one residual
  the scanner does not exclude — accepted under the non-adversarial threat model,
  as with the `xargs`/cross-repo gaps above.

  **The marker attests completeness, never existence (issue #940).** It used to
  short-circuit before the transcript was read, so it released the merge no
  matter what the window contained. Measured over 2026-08-01~03: it rode along
  on **11 of 11** merges and **8 of those had no preceding block at all** — two
  at 322 and 627 events from the last briefing. Once a marker is a command's
  default suffix, the gate it relaxes can never fire, and a per-merge
  re-confirmation never happens; the bypass convention permits a token only
  after the spec is read and the case matched, and one reading does not cover
  the next N merges.

  The marker is therefore evaluated **after** the transcript is scanned, and
  releases the merge only when the window already contains at least
  `MERGE_BRIEFING_MARKER_MIN_ITEMS` (1) recognised briefing item. That keeps its
  original purpose — the item counter reads prose and under-counts a briefing
  phrased outside its vocabulary — while removing the one thing it was never
  meant to do. With zero items the attestation has no referent and the merge is
  denied with a distinct reason naming the marker. An unreadable transcript
  still fails open, which is the bridge-session case the marker was invented
  for: there is nothing to verify against, so nothing is claimed.

  "The window" is whichever one the gate was allowed to score, current turn or
  correlated prior turn — the same window the full-briefing check reads. The
  mandated flow is briefing → approval → merge, which leaves the current turn
  empty, so a marker floor scored against the current turn alone would deny a
  marked merge whose briefing the user did see. The prior turn is read only
  when it is correlated to this merge's PR, so an uncorrelated briefing cannot
  supply the item the floor needs (No Approval Transfer).

  **The window is cut at the last merge that actually ran (issue #1214).** A
  briefing is spent by the merge it releases, so in a serial run — merge, merge,
  merge, one briefing at the top — every later merge was riding evidence that
  already had an owner. The existing transfer guards only ever saw repetition
  inside ONE command (`merge A && merge B`, a `for` loop); a merge per turn
  looked like a fresh single merge each time. `_last_executed_merge` walks the
  same already-loaded entries for `gh pr merge` tool_use blocks, and the marker
  floor scores only what was written after the last one. No new persistent
  state — this is the window calculation, moved.

  A merge counts as *executed* only when its `tool_result` came back without
  `is_error`. A merge this gate blocked, a sibling gate denied, or the user
  declined never ran, so it moves nothing and the legitimate retry passes
  exactly as before — the failure that made #1214 discard its own original
  "one block buys one attachment" design, where the credit was spent by a
  `PreToolUse` that is not an execution.

  **Scoped to the marker floor, deliberately.** Replayed over 207 marker-carrying
  merges from 67 local sessions (2026-08-09 onward, post-#940), the cut as
  shipped changes **3** decisions and every one is a merge only the marker was
  releasing; the 167 attachments that changed nothing are untouched. Extending
  the same cut to the 4-of-6 full-briefing path instead changes **35**, of which
  **32** carried a complete briefing — that only raises the cost of the honest
  path, which is the ground this hook's spec already takes elsewhere: it is a
  self-discipline nudge, not an adversarial boundary.

  **Both directions fail open.** A merge counts as executed only on a clean
  `tool_result`, and only when the shell could not have jumped over it —
  `_SKIPPABLE_KEYWORDS` (`||`, `if`, `elif`, `case`) plus a non-terminal `&&`.
  Each guard closes one direction:

  - `gh pr merge 833; false` — the merge RAN but `is_error` is per Bash
    tool_use, not per subcommand, so the window stays uncut. Crediting an
    `is_error` result instead would let a merge this gate itself blocked spend
    the window, and the legitimate retry could never pass. The same granularity
    is recorded at `hooks/completion-verify/pr-anchor-existence-gate/spec.md`
    as structural rather than a local bug; every gate correlating tool_use to
    tool_result shares it.
  - `true || gh pr merge 833` — the merge did NOT run yet the call exits clean.
    Crediting it would advance the cut past a briefing nothing consumed and deny
    the next marked merge whose briefing was real. Measured over the local
    transcripts, 8 of 834 `gh pr merge` calls carry such a construct.
  - `false && gh pr merge 833; true` — the same phantom through `&&`. The merge
    is skipped, and because the exit status belongs to the LAST command, the
    call still comes back clean, so `is_error` cannot drop it. `&&` therefore
    gets a positional test rather than a flat ban: a `&&`-reached merge is
    credited only when it is the final command, where its own failure IS the
    call's exit status. That buys the invariant *a credited merge really ran*
    without a shell parser, and keeps `cd X && gh pr merge N` — the common
    shape — creditable. Measured over the local transcripts, 587 of 834
    `gh pr merge` calls stay creditable (70%), against 826 (99%) with `&&`
    unrestricted and 431 (52%) with `&&` banned outright.

  `;` stays non-skippable, so a real merge after one is still credited. The
  residual gap runs the other way: `if true; then :; fi; gh pr merge 833` really
  merges and is dropped, because the whole-token `if` scan is command-wide. That
  is the fail-open direction — the guard stays silent rather than spending a
  window it cannot confirm, the same posture as the `xargs`/heredoc gaps above.

  **Coverage ceiling — the 400-line tail.** `_load_turn_entries` reads
  `TRANSCRIPT_SCAN_LINES` (400) lines, so a prior merge pushed past that tail is
  invisible and the gate behaves as it did before (fail-open). Measured over the
  local transcripts: of 719 consecutive `gh pr merge` pairs, **491 (68%) sit
  within 400 lines** and 228 do not (median gap 60 lines). That is a ceiling on
  visibility, not a firing rate — a user message between the two merges starts a
  new window, in which case the defect never arose. Widening the constant is out
  of scope: it is shared, so it would move every other hook's cost too.

- **Injected user entries do not start a new window (issue #940).** A skill body
  loaded mid-turn and the expansion of a slash command both arrive as
  `role: user` with prose content; the harness stamps them `isMeta: true` while
  a genuinely typed message carries no such flag. Counting them would move the
  "since the last user message" boundary past a briefing the user actually saw,
  so a skill invoked between the briefing and the merge made a compliant flow
  look unbriefed. `_human_user_indices` skips them — a structural discriminator,
  not a content heuristic.
- Trivial-PR markers (`typo`, `comment-only`, `single-line`, `오타`, `주석만`,
  `trivial pr`, `2-line report`, …) in the briefing text → no escalation,
  matching the *Pre-Merge Reporting* rule's "Trivial PRs: a 2-line report is
  fine" carve-out.
- Prior-turn briefing correlated to the merged PR (approval reply + PR-number
  match, or numberless merge whose window-derived target matches) → no
  escalation (issue #826, above).

The `dispatch` and `force-push` triggers never emit a decision — escalation is
merge-only, per the issue scope (only the merge failure was reproduced).

**Deny reason carries the whole verb's checklist (issue #873).** The briefing
is one of three gates that fire on every `gh pr merge` whatever the flags — the
other two are `pre-merge-approval-gate` and `side-effect-scan`. Five more are
conditional: `gh-merge-worktree-precondition` (only with `--delete-branch`),
`commit-title-length-check` (only with `--squash`), `pipefail-advisory` (only
when the merge is piped), `session-intent` (only when the session never declared
a mutation intent), and `skill-gate-commands` (only when
`PRAXIS_SKILL_GATED_COMMANDS` lists the verb). The checklist keeps that split
explicit — a conditional gate listed as unconditional sends the reader looking
for a requirement that does not apply to their command.
Before #873 each was discovered by its own separate block, costing a retry turn
apiece — praxis #873 measured six such blocks in one session, at least four of
which already had their satisfaction form documented. Documentation was never
the gap; retrieval at call time was. This hook therefore emits
`verb_gate_checklist("gh pr merge")` from `hooks/_lib/block_message.py`, which
is the single source for that mapping (see
[docs/hook/INDEX.md](../../../docs/hook/INDEX.md)).

**The checklist goes out on both channels (issue #932).** Neither one alone
reaches the model in every case:

- **decision JSON** — `hooks/_lib/_dispatch.py:195-201` surfaces only the
  **first** deny's stdout. These gates run in parallel on the same command, so
  whenever a sibling denies first, a checklist embedded in this hook's
  `permissionDecisionReason` is discarded — precisely the multi-gate case the
  checklist exists for.
- **stderr** — forwarded for **every** hook, but a PreToolUse hook's stderr is
  fed to the model only when the dispatcher exits 2. That is the deny path
  (`:201 return 2`), never the ask path (`:207 return 0`).

A deny is always exit 2, so stderr alone would in fact suffice *here*. #873
shipped stderr-only for both this hook and `output-block-falsify-advisory`,
where the ask path exits 0 and the checklist silently went nowhere; splitting
the two hooks' handling is what let that through, so they now emit identically.
`format_block`'s five-field output is unchanged — the checklist is appended
after it.
`tests/hooks/advisory-nudge/test_momentum_rule_retrieval_gate.sh::merge_checklist_on_both_channels`
pins both channels per gate name.

### Environment variables

| Variable | Effect |
| ---------- | -------- |
| `PRAXIS_MOMENTUM_BYPASS=1` | Skip all output and exit 0 immediately (for scripted batch operations) |
| `PRAXIS_MOMENTUM_MERGE_ADVISORY=1` | Demote the merge-briefing escalation to advisory only (stderr reminder still fires, no `deny`). Read from the hook process env, so it must be set in the **session** environment — an inline `VAR=1 gh pr merge …` prefix never reaches the hook, and the deny message says so (issue #1087). See also the in-band `# briefing-surfaced` command marker (issue #826) |
| `PRAXIS_MOMENTUM_STRICT=1` | Exit 2 (block) instead of exit 0, unless `PRAXIS_MOMENTUM_ACK=1` is also set |
| `PRAXIS_MOMENTUM_ACK=1` | Acknowledge the surface in strict mode; exit 0 after emitting the advisory |

Default mode (no env vars): advisory for `dispatch` / `force-push`; the `merge`
trigger blocks with `deny` only when the pre-merge briefing is incomplete
(above), otherwise advisory.

### Scope (Phase 1 vs Phase 2)

**Phase 1 (this hook):** ANY single matching command triggers the rule surface.
This is intentionally simple — every `gh pr merge` or `git push -f` emits the
relevant reminder, regardless of how many similar commands have already been
issued in the session.

**Phase 2 (separate issue, not yet implemented):** Add multi-mutation detection
to surface the gate only when a momentum pattern is detected (e.g., N merges
in rapid succession within the same session). Phase 2 will require session-state
tracking per PPID or `session_id`.

### Detection logic

The hook uses the same `safe_tokenize` / `iter_command_starts` / `strip_prefix`
pipeline as sibling hooks (`hooks/preflight-gate/pre-merge-approval-gate/impl.py`,
`hooks/advisory-nudge/bash-worktree-existence-advisory/impl.py`). Each segment in the tokenized command
is inspected independently so that compound commands (`cmux new-workspace && gh
pr merge ...`) trigger the appropriate surfaces for each matching segment.

gh global flags (`-R/--repo`, `--hostname`, `--color`) are walked past before
checking the subcommand so that `gh -R owner/repo pr merge` is detected
correctly.

Two argv shapes match `gh pr merge` textually and merge nothing, so neither
counts as a trigger (issue #985):

- **A help invocation** — `--help` / `-h` prints usage and exits. Demanding a
  merge briefing for it blocks the command an agent runs precisely to get the
  merge right. `is_help_invocation` skips value-flag values, so `--subject -h`
  stays a subject and still triggers.
- **A heredoc body** — `safe_tokenize` blanks every body line before the
  per-line split, so a commit message that merely quotes `gh pr merge` is no
  longer read as one. The operator line and everything after the terminator are
  untouched, which is what keeps a real merge on a later line firing.

`git push` force detection scans all tokens after the `push` subcommand for
`--force`, `-f`, and `--force-with-lease` (including `--force-with-lease=<ref>`
prefix-matched form).

### Relationship to sibling hooks

| Hook | Overlap |
| ------ | --------- |
| `pre-merge-approval-gate` | Both fire on `gh pr merge`. The sibling surfaces an **unconditional** `permissionDecision: ask` (per-PR user approval, content-blind). This hook emits the stderr rule reminder and, when the pre-merge briefing is incomplete, a **content-aware** `permissionDecision: deny` (blocks so the agent produces the briefing). Different checks — the sibling gates *user* approval, this hook gates *briefing existence*. When both fire, `deny` wins (more restrictive), and the agent self-corrects. |
| `side-effect-scan` | Fires on `gh pr merge` / `git push` collateral side effects. Complementary. |
| `verify-commit-flag-override` | Fires on `git commit --no-verify`. Different trigger, no overlap. |
| `memory-hint` | Surfaces `hookable: true` memory entries by keyword. The momentum gate specifically surfaces the entries most relevant to merge / dispatch / force-push momentum, as a targeted complement to the general memory-hint scan. |

### Co-firing with the sibling gates (PreToolUse Bash)

Per the [Anthropic hooks docs](https://code.claude.com/docs/en/hooks), all
matching hooks run **in parallel**: array position in `hooks/manifest.json` is
registration order, not firing order, and no hook runs before or after another.

What matters here is therefore co-firing, not sequence. On `gh pr merge` this
hook and `pre-merge-approval-gate` both match. The sibling surfaces a
`permissionDecision: ask` dialog; this hook writes stderr rule reminders. Both
reach the user in the same surface — an `ask` from one hook does not suppress
another hook's output, because there is no chain to short-circuit.

A hook added to this matcher inherits that property automatically. There is no
ordering invariant to preserve, and none can be expressed: a spec that asks
future hooks to register "before" or "after" this one is asking for something
the runtime does not offer.

### Fail-open contract

The hook returns exit 0 on every infrastructure error:

- Malformed JSON stdin
- Non-Bash tool invocation
- Empty or whitespace-only command
- `python3` unavailable (the shell wrapper handles this)
- Any uncaught exception in the inner logic

### Tests

```bash
bash tests/hooks/advisory-nudge/test_momentum_rule_retrieval_gate.sh
```

Cases: gh pr merge trigger, cmux new-workspace trigger, force-push triggers
(--force / -f / --force-with-lease), bypass env var, strict mode (block +
ack), fail-open (non-Bash tool, malformed JSON), silent cases (unrelated
commands), compound command multi-trigger.
