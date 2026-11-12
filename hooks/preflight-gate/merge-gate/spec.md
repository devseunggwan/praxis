# Merge Gate

Supported hosts: all

`hooks/preflight-gate/merge-gate/impl.py` holds every praxis check on the merge
lifecycle. They were six hooks until issue #1650; each is now a module next to
`impl.py`, keeps its own verdict, messages and environment variables, and its
section below is the spec it had as a hook.

| Event | Check | Module | Was |
| --- | --- | --- | --- |
| PreToolUse(Bash) | approval: ask before every `gh pr merge` | `hooks/preflight-gate/merge-gate/approval.py` | `pre-merge-approval-gate` |
| PreToolUse(Bash) | worktree: block a `--delete-branch` merge whose head is checked out | `hooks/preflight-gate/merge-gate/worktree.py` | `gh-merge-worktree-precondition` |
| PreToolUse(Bash) | briefing: deny a merge with no Pre-Merge Reporting briefing and answer | `hooks/preflight-gate/merge-gate/briefing.py` | `momentum-rule-retrieval-gate` (`merge` trigger) |
| PreToolUse(AskUserQuestion) | refetch: re-read a PR's live state before a merge question | `hooks/preflight-gate/merge-gate/refetch.py` | `pr-state-refetch-gate` |
| PreToolUse(AskUserQuestion) | menu: a merge menu without a review option | `hooks/preflight-gate/merge-gate/menu.py` | `merge-menu-review-options-advisory` |
| Stop / SubagentStop | claim: a merge/PR state claim with no fresh state query | `hooks/preflight-gate/merge-gate/claim.py` | `merge-state-claim-gate` |

## How the checks combine

`impl.py` picks the checks for the event and runs each one's `main()` on the
same payload, in the order of the table. The answer follows the dispatcher's
PreToolUse lane (`hooks/_lib/_dispatch.py`): the first deny (exit 2 or
`permissionDecision: "deny"`) ends the run and is the answer, otherwise the
first ask is; stderr from every check that ran is kept. A check that raises
fails open with a one-line stderr note naming it.

The briefing check runs only on the hosts its old hook was registered for
(`claude`, `codex`, `hermes`); on any other host the Bash checks are approval
and worktree. `member_host()` is None in a standalone run (tests, replay
scripts), which runs every check.

On Hermes, `merge-gate` is in `HUMAN_APPROVAL_HOOKS`
(`hooks/_lib/_hermes.py`): its one ask is the approval check's, which is the
human's consent to the merge. The refetch, menu and claim checks never ask.

### Environment variables

Every variable kept its name and meaning; the section of the check that reads
it has the detail.

| Variable | Check |
| --- | --- |
| `PRAXIS_MOMENTUM_BYPASS=1` | briefing (also silences momentum-rule-retrieval-gate) |
| `PRAXIS_MOMENTUM_MERGE_ADVISORY=1` | briefing |
| `PRAXIS_MOMENTUM_STRICT=1` / `PRAXIS_MOMENTUM_ACK=1` | briefing (also momentum-rule-retrieval-gate) |
| `PRAXIS_MERGE_APPROVAL_PROVIDER=<path>` | approval, briefing |
| `PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE=1` | worktree |
| `PRAXIS_PR_STATE_REFETCH_STRICT=1` | refetch |
| `PRAXIS_MERGE_MENU_REVIEW_STRICT=1` | menu |
| `PRAXIS_MERGE_CLAIM_STRICT=1` / `PRAXIS_MERGE_CLAIM_BYPASS=1` | claim |

### Tests

```bash
bash tests/hooks/preflight-gate/test_merge_gate_approval.sh
bash tests/hooks/preflight-gate/test_merge_gate_worktree.sh
bash tests/hooks/preflight-gate/test_merge_gate_briefing.sh
bash tests/hooks/preflight-gate/test_merge_gate_refetch.sh
bash tests/hooks/preflight-gate/test_merge_gate_menu.sh
bash tests/hooks/preflight-gate/test_merge_gate_claim.sh
python3 -m pytest tests/hooks/preflight-gate/test_merge_gate.py
```

The first six run each module on its own; `test_merge_gate.py` covers the
routing and folding in `impl.py`.

## Check: approval (was `pre-merge-approval-gate`)

Former title: PreToolUse Pre-Merge Approval Gate.

`hooks/preflight-gate/merge-gate/approval.py` fires on every PreToolUse(Bash) event and
intercepts `gh pr merge` invocations. The gate emits
`permissionDecision: "ask"` so the user sees the merge attempt and must approve
it in the Claude Code permission UI. This applies to every session — there is
no environment-variable exemption.

### Why this exists

Merge is shared-state and irreversible. A task prompt containing a
"fire-and-forget" or "no STOP gate" directive — historically written for agents
dispatched via `cmux-delegate` — can bleed into a session where the user is
actively reading responses. This hook removes the ambiguity by gating every
`gh pr merge` unconditionally: no directive in a prompt can stand in for the
per-PR approval.

Issue #180 originally paired this gate with a `CMUX_DELEGATE=1` background-agent
exemption. Issue #1055 removed it. Nothing in the repository ever set that
variable, and the delegated worker now runs with a live stdin (#1054 item A /
PR #1057), so a prompt surfaced inside a delegated pane is answered by the same
human — the premise "the delegation intent is the approval" no longer holds.

The per-PR approval rule is already codified as *No Approval Transfer Across
Companion PRs* and *Pre-Merge Reporting* ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)). This hook
adds structural enforcement so the rule fires even when memory-based feedback
is not retrieved.

### What is blocked

| Scenario | Action |
| ---------- | -------- |
| Any session, any `gh pr merge` | `permissionDecision: "ask"` |
| A single numbered merge the user approved in a bulk answer (see below) | silent pass-through |
| Any env-var prefix (`env FOO=1 gh pr merge`) | `ask` — no environment variable exempts a merge |
| `# merge-approval:ack` marker (or any comment text) | `ask` — no agent-attachable bypass exists by design |
| Non-merge gh commands (`gh pr view`, `gh pr list`, etc.) | silent pass-through |
| `git commit -m "merge note"` (merge in message, not a gh call) | silent pass-through |

### Trigger

1. `tool_name == "Bash"` — non-Bash tools exit 0 silently.
2. Tokenize with `_hook_utils.safe_tokenize` + `iter_command_starts` +
   `strip_prefix` and scan every command segment.
3. Any segment whose `argv[0..2] == ("gh", "pr", "merge")` triggers the check
   (`gh` global flags such as `-R/--repo`/`--hostname`/`--color` are skipped
   so `gh -R owner/repo pr merge` is detected correctly).
4. A `--help` / `-h` invocation is not a merge and does not trigger (issue
   #985) — value-flag values are skipped, so `--subject -h` still triggers.
   Heredoc bodies are blanked by `safe_tokenize` for the same reason: a commit
   message quoting `gh pr merge` is prose, not a merge.
5. Emit `permissionDecision: "ask"`.

### Bulk-approved merges (issue #1583)

The prompt stands in for the user's per-PR consent. When the user has already
given it in chat — one briefing turn with a section per PR, one ask listing
them, and an answer approving the set (`momentum-rule-retrieval-gate` spec →
*One answer can approve a listed set*) — asking again per merge adds N clicks
that decide nothing, which is the blind-approval pattern the briefing gate was
built against (#795). The hook therefore loads
`hooks/preflight-gate/merge-gate/briefing.py` and calls its
`bulk_merge_approved(payload)`: one parser decides for both gates.

This is not an agent-attachable bypass. Nothing in the command releases it;
it reads the user's own message (or `AskUserQuestion` answer) from the
transcript. It fails closed: an import error, an exception, or an unreadable
transcript leaves the `ask` in place. A chained or looped merge, a numberless
merge, a PR outside the approved set, or a second merge of the same PR still
asks.

### Host-approved merges (issue #1639)

When the host sets `PRAXIS_MERGE_APPROVAL_PROVIDER` in the hook process
environment, a merge that is not bulk-approved is checked with the host's
provider before the prompt, through `host_merge_decision(payload)` in
`hooks/preflight-gate/merge-gate/briefing.py`, the function the
briefing gate uses (contract, checks and the one-approval-one-merge rule:
that hook's spec → *Host approval provider*). A release skips the prompt; a
refusal keeps it and appends the provider's reason. Under Hermes the prompt
is the desktop approval dialog, so a human who already answered in the host's
own channel is not asked again.

The provider is called and its approval spent once per tool call: whichever
gate asks first records the outcome for the other. It fails closed: an import
error or exception keeps the `ask`. Unset, this step does not run.

### No opt-out marker (deliberate)

Unlike `side-effect-scan` (`# side-effect:ack`), this hook has **no
agent-attachable bypass** and, since #1055, no environment bypass either. The
contract is that a merge ALWAYS surfaces a per-PR approval prompt — a
comment-style marker would let the agent silently self-bypass the same gate it
is meant to enforce. The host approval provider above is not one: the agent
cannot set the hook process environment, and the provider only answers whether
the human approved.

If a legitimate direct-session merge must proceed, approve the surfaced
prompt — that single confirmation is the approval the rule requires.

### Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "gh pr merge detected in a direct interactive session..."
  }
}
```

### Compound cascade advisory (issue #229)

When the ask fires on a compound Bash command that also contains a
state-changing step (e.g. `git fetch && gh pr merge 42` chained with an
`mkdir`/redirect/`curl -o`), the ask reason is suffixed with the shared
`_hook_utils.compound_cascade_hint` text. If the user denies the prompt, all
chained side-effects abort with the merge. Single-command merges (just
`gh pr merge 42`) do not receive the suffix.

### Tests

```bash
bash tests/hooks/preflight-gate/test_merge_gate_approval.sh
```

Covers ASK paths (bare, `--merge`, `--delete-branch`), ASK under
`CMUX_DELEGATE=1` (regression guard for the #1055 exemption removal),
non-merge command SILENT paths,
chained-command ASK paths, quoted-body SILENT (text mentions "gh pr merge"
but is not executed), inline-env ASK, non-Bash tool SILENT, malformed-JSON
SILENT, `gh -R/--repo/--hostname/--color` global-flag handling, and
regression tests confirming the previously-shipped `# merge-approval:ack`
marker no longer bypasses (round 4 finding — agent-attachable bypass
removed by design), and the bulk-approval cases: silent for a listed PR after
a typed or `AskUserQuestion` bulk answer; ask for a PR outside the set, a
combined briefing block, a chained merge, and an unreadable transcript.

## Check: worktree (was `gh-merge-worktree-precondition`)

Former title: PreToolUse gh pr merge Worktree-Precondition Gate.

`hooks/preflight-gate/merge-gate/worktree.py` fires on every
PreToolUse(Bash) event. When the command is `gh pr merge ... --delete-branch`
(or `-d`), it resolves the PR's live head branch via `gh pr view` and checks
whether that branch is still checked out in another `git worktree`. If so,
the merge is guaranteed to fail with `cannot delete branch '<branch>' used by
worktree at '<path>'` — the hook blocks (exit 2) before the doomed command
runs, instead of letting it fail and requiring manual recovery.

### Why this exists

`gh pr merge --delete-branch` fails deterministically when the head branch is
still checked out in a worktree, and the check is cheap to run first — yet the
constraint kept being rediscovered at merge time instead of checked before it.
The praxis `worktree-merge-cleanup` skill is the on-demand home for the
required manual sequence (remove the head-branch worktree, then merge) after
this exact failure mode recurred across PRs #147, #170-173, #175, and #796 in
this project — six documented occurrences (issue #798).

A retrospect session on the PR #796 merge (2026-07-16) root-caused why the
existing `feedback_worktree_context_pre_git_op.md` memory never prevented the
6th recurrence: that memory's `hookable`/`hookKeywords` frontmatter was meant
to surface the rule via the `memory-hint` PreToolUse hook, but `memory-hint`'s
`resolve_memory_dir()` fallback path had an independent bug (see issue #799)
that silently disabled the entire hookable-memory mechanism for this user's
environment. `memory-hint` is a soft textual nudge even when working — this
hook is the structural enforcement layer for the specific, deterministic
`--delete-branch` failure mode, so the check does not depend on any memory
retrieval path at all.

### Detection

1. `tool_name == "Bash"` — non-Bash tools exit 0 silently.
2. Tokenize with `_hook_utils.safe_tokenize` + `iter_command_starts` and scan
   every command segment for `gh pr merge`. `gh` global flags
   (`-R/--repo`/`--hostname`/`--color`) are inherited cobra flags that gh
   accepts in any position — before `pr`, between `pr` and `merge`, and after
   `merge` — so the walker skips them (capturing a repo selection) wherever
   they appear.
3. Within that segment, `-d`/`--delete-branch` must be present — without it,
   `gh` never attempts to delete the local branch, so no worktree conflict
   can occur and the segment is skipped. The scan walks past values consumed
   by value-taking merge flags (`--body -d` is a body of `-d`, not a
   delete-branch request) and stops at `--`.
4. Extract the positional PR identifier (number / URL / branch name), if
   any, walking past value-taking flags — `gh pr merge`'s own
   (`-A/--author-email`, `-b/--body`, `-F/--body-file`, `-t/--subject`,
   `--match-head-commit`) and the inherited global ones, so a post-`merge`
   `-R owner/repo` is never misread as the identifier.
5. Resolve the live head branch:
   `gh pr view <identifier> --json headRefName -q .headRefName`
   (`cwd` from the hook payload). When no identifier was parsed, `gh pr view`
   is called with **no positional argument** — gh then infers the PR from the
   current branch, exactly mirroring what `gh pr merge` itself would do with
   no identifier. A `-R/--repo` value on the merge command (separate or
   `--repo=` inline form) is forwarded to `gh pr view` so the PR resolves in
   the repository the merge actually targets, not the payload `cwd`'s repo.
6. List worktrees: `git worktree list --porcelain`, parsed into
   `{branch_name: worktree_path}`.
7. If the resolved head branch is a key in that map → **block**, citing the
   conflicting worktree path. Otherwise → pass.

### What is blocked

| Scenario | Action |
| ---------- | -------- |
| `gh pr merge N --squash --delete-branch`, head branch checked out in another worktree | block (exit 2) |
| `gh pr merge N --squash -d`, same conflict (short flag) | block (exit 2) |
| `gh pr merge N --squash` (no delete-branch flag) | pass — no local-branch deletion is attempted |
| `gh pr merge N --squash --delete-branch`, head branch not checked out anywhere | pass — no conflict |
| `gh pr merge --squash --delete-branch` (no identifier) | resolved via `gh pr view`'s own current-branch inference, then checked the same way |
| `gh -R owner/repo pr merge N --delete-branch` | detected — global flags are walked past, and the repo selection is forwarded to `gh pr view` |
| `gh pr -R owner/repo merge N -d` / `gh pr merge -R owner/repo -d` | detected — inherited flags are accepted in any position, repo forwarded, and the repo value is never misread as the PR identifier |
| `gh pr merge N --body -d` | pass — `-d` here is the value of `--body`, not a delete-branch flag |
| `gh pr view N ...` / other non-`merge` gh subcommands | pass — subcommand check requires exactly `pr merge` |

### Scope decision — compound commands not special-cased

The `worktree-merge-cleanup` skill's "Unified post-merge cleanup sequence"
explicitly instructs agents NOT to collapse `git worktree remove` +
`gh pr merge` into a single
`&&`-chain (a git hard error mid-chain silently short-circuits later steps
and trailing output from an earlier step is easily misread as success — the
exact failure mode `Bulk Operation Pre-Enumeration` and this project's own
worktree-cleanup guidance warn about). This hook therefore does not scan
earlier segments of the same compound command for a preceding
`git worktree remove` that would have already resolved the conflict — adding
that would require path-order-sensitive reasoning in service of a pattern the
project's own convention already discourages. Run the two steps as separate
Bash calls, per the `worktree-merge-cleanup` skill's "Unified post-merge
cleanup sequence."

### Fail-open

Any infrastructure error exits 0 (allow) — this hook only blocks on a
**positively confirmed** worktree conflict, never on an inability to
determine one:

- `gh` binary missing, `gh pr view` non-zero exit, timeout (5s), or empty
  `headRefName` output → cannot resolve the head branch → pass
- `git` binary missing, `git worktree list` non-zero exit, or timeout (5s) →
  cannot enumerate worktrees → pass
- Malformed stdin JSON → pass
- `PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE` set to any non-empty value → pass
  unconditionally (checked first, before any subprocess call)

Wrapped in `@fail_open` (`_hook_runtime`) as a second layer — any uncaught
exception also exits 0 rather than blocking a legitimate merge.

### Relationship to `pre-merge-approval-gate`

Both hooks fire on `gh pr merge` PreToolUse(Bash) events and share the same
`gh` global-flag-walking / subcommand-detection shape, but check unrelated
preconditions and can both fire on the same command (Claude Code runs
PreToolUse hooks in parallel per Anthropic's docs — `deny`/block from either
hook wins):

| Hook | Checks | Decision |
| ------ | -------- | ---------- |
| `pre-merge-approval-gate` | Whether the command is a `gh pr merge` at all — every session is gated | `ask` (never blocks outright) |
| `gh-merge-worktree-precondition` (this hook) | Whether `--delete-branch`'s target branch is checked out in another worktree | `deny` (exit 2) on a confirmed conflict |

### Tests

```bash
bash tests/hooks/preflight-gate/test_merge_gate_worktree.sh
```

18 cases: block on confirmed conflict (long and short delete-branch flags,
with and without an explicit PR identifier, with `-R`/`--repo=` in every
placement gh accepts — before `pr`, between `pr` and `merge`, after `merge`,
trailing — whose value must be forwarded to `gh pr view` and never misparsed
as the PR identifier, asserted by a repo-required fake `gh`), pass on
no-delete-branch (including `--body -d`, where `-d` is an option value) /
no-conflict / non-Bash / unrelated-command segments, fail-open on
`gh pr view` error / empty output / malformed stdin JSON, and the
`PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE` opt-out. Block
assertions require the stderr message to name the conflicting worktree path,
so a crash cannot masquerade as a gate decision. Worktree
conflict cases run against a real temporary git repo + `git worktree add`
(worktree state cannot be faked without a real `.git`); `gh` calls are
short-circuited via a per-case fake-bin shim prepended to `PATH`.

## Check: briefing (was `momentum-rule-retrieval-gate`, `merge` trigger)

On a `gh pr merge` the check prints the merge rule surface (the same
`[praxis:momentum-gate]` block the momentum hook prints for its own
triggers, from `hooks/_lib/_momentum_surface.py`) and then decides the
briefing escalation below. `PRAXIS_MOMENTUM_STRICT=1` without
`PRAXIS_MOMENTUM_ACK=1` blocks every merge, as it did in the momentum hook.

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

**The merge question is part of the briefing (issue #1597).** The windows this
gate scores for a merge read the assistant's prose blocks *and* each
`AskUserQuestion` question that asks to merge the target PR (`_is_merge_ask`;
`_assistant_text(..., pr)`). The user reads that question before answering it,
so a briefing written inside it is one the user saw. Hosts whose approval tool
carries the whole briefing in the question — Hermes `clarify`, mapped to
`AskUserQuestion` by the adapter — left the prose empty: the first merge of a
bulk set passed only when unrelated prose happened to hold a keyword, and every
later merge was denied, because the serial-merge cut (#1214) leaves only prose
written after the last merge. Other questions are not read: a sibling question
about something else would add keywords no briefing of this merge carried, and
a question that names PRs but not the target briefs another merge even when it
ends in an unnamed "Approve merge?". A question whose answer holds the merge
(`보류`, `_question_answer_holds` — a label that is neither a single nor a bulk
approval) or that the user declined (`is_error` result) is not read either: its answer arrives as a
tool_result, not a human message, so unlike a typed `보류` it does not close the
window, and on a first merge — where no answer check runs — its briefing would
release the merge the user just held. The answer checks (#1402) are otherwise
unchanged, and a question carrying no briefing items still denies.

**`·` lists and Korean section labels (issue #1597).** Korean prose joins a PR
list with `·` (`#1525 · #1526을 머지할까요?`) and may restate it with a count
(`#1524 · #1525 · #1526 세 건을 머지할까요?`). `_REF_LIST` takes `·` as a
separator and `_REF_COUNT` the count, so both read as listed merge asks.
`_SENTENCE_TAIL_RE` also splits clauses on `·`, which cut a picked label like
`승인 — #1524 · #1525 · #1526 머지` into a piece holding only `#1525`; that piece
now names a PR and approves nothing, and a count phrase restating the list
(`#1525 · #1526 두 건 모두 머지`) is dropped the same way, but only when the
count equals the number of PRs the reply names — `두 건만 머지` against a
three-PR ask names none and picks a subset, so it is not read; the reply still needs
one piece that is an approval (`#833 · #999` alone is not). The item vocabulary gained the
section labels these briefings use, colon included — `바뀐 것:`, `확인한 것:`,
`못 한 것:` (as in `확인 못 한 것:`) — because the bare phrase is ordinary prose too (`새로 바뀐 것은
없습니다` released a marked merge in corpus replay before the colon was required).

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

- **Numbered replies (issue #1630).** A user answering several numbered
  questions in one message (`1. approve` / `2. go with your recommendation`)
  puts the merge answer at the merge question's number, which need not be the
  last line, so the final-clause rule read the answer to another question and
  the decision flipped with the order of the lines. When the reply is a
  numbered list (`1.`, `1)`, `(1)`, `1:`, `1번`, with an optional bullet,
  heading or bold around the marker; `1.5` and `10:30` are not markers) and the
  turn it answers has one, the reply is read at the number of the merge ask in
  that turn's **last** numbered list, and that item alone is matched with the
  rules above (whole-token or final clause, a trailing `?` refuses), with a
  reference to the target PR inside it set aside (`1. #999 머지`). The
  whole-reply PR strip in `_is_typed_approval` is skipped for a numbered
  reply answering a numbered turn, since it would also blank a list marker
  equal to the PR number (`2.` for PR #2); a numbered reply to a turn with no
  numbered list keeps the strip with the final-clause rule.

  Reading the turn (`_merge_question_numbers`): fenced blocks are skipped;
  only top-level items count, so a nested sub-list is text of its parent item;
  a list restarts where a number does not exceed the one before it, so a
  numbered six-item briefing followed by numbered questions pairs with the
  questions. An item is the merge ask (`_is_item_merge_ask`) when its **last**
  ask sentence — or, with none, a first line that asks with a noun phrase
  (`1. #999 머지 승인 여부`, `1. Whether to merge #999`) — passes
  `_is_merge_ask` and carries no qualifier or negation
  (`_ITEM_ASK_QUALIFIER_RE`, `_LABEL_NEGATION_RE`): an ask about when, around,
  or instead of the merge (`Re-run CI before merging #999?`, `#999 머지 보류
  여부`, `merge #999 or close it?`) is answered by `yes` without approving the
  merge. An ask naming no PR counts only when the turn names no PR but the
  target, since `1. Approve merge?` in another PR's briefing is that PR's
  question. An item with a line over 400 characters is no ask (it also bounds
  `_ASK_SENTENCE_RE`, which is quadratic on a long line).

  Reading the reply: it is not an approval when the turn's list holds no such
  ask or more than one; when the reply has no item at that number or has it
  twice; when a non-blank line precedes its first item (a quoted question
  list included) or an unindented, unnumbered line follows one (`Actually,
  hold off on the merge`); or when any other item mentions the PR or merging
  without being an approval token (`2. B, and don't merge yet`) — the latest
  word on the merge wins inside one message, as `_held_after` applies it
  across messages. An answer naming another PR, with `#` or bare (`1. For
  #1001, approve`, `1. 1001 approve`), approves nothing here. Across messages,
  `_held_after` also counts a numbered reply to a later turn whose numbered
  list asks to merge the PR as being about the merge, so `1. hold` to a
  re-asked question withdraws an earlier `1. approve`. A URL's dots do not end
  the ask sentence (`1. Approve merge of https://github.com/o/r/pull/999?`).
  What follows the ask is read for qualifiers and negation too — the rest of
  its line, the item's later lines that are not option bullets, and
  unindented prose after the item (`1. Approve merge of #999? Only after the
  release.`, or an unindented `Or hold until Monday?` under it). Option
  bullets under the ask (`- (추천) 승인합니다`) are choices, not conditions.

  The pairing reads the same turn and target `_correlated_prior_turn_text`
  scores (`_last_reply_approves`), and `_answered_after` / `_held_after` pass
  the turn each later message answers. On top of the release the issue asked
  for, two shapes main allowed now deny: `1. hold` / `2. approve` (and `3.
  approve` / `4. ok`) when item 1 is the merge ask, and a numbered reply when
  the merge ask sat outside a numbered follow-up list (`Approve merge?` then
  `1. …?` / `2. …?`, answered `1. ok` / `2. ok`) — those items answer the
  follow-ups. Unchanged: an unnumbered reply, and a numbered reply to a turn
  with no numbered list, keep the final-clause rule; with no turn (an
  `AskUserQuestion` label) `_is_approval_reply` reads as before. Known gaps,
  each a missed release: items on one line (`1 ok, 2 b`) and `1 ok` without
  punctuation are not markers, so they fall back to the final-clause rule; the
  qualifier list is vocabulary (`조건`, `먼저`, `before`, `or`, …), so a plain
  ask that happens to use one of those words is asked again.

- **Lettered choices (issue #1652).** A menu written as prose, or in a host
  question tool whose answer leaves no `"q"="a"` row (Hermes `clarify`
  answered in chat), lists its choices as lettered lines and the user answers
  with the letter alone, which no approval token matched, so every such merge
  was denied and the user asked to type `merge` again. When the reply is one
  letter A-D (any case, surrounding whitespace and one trailing `.` allowed)
  and the turn it answers holds exactly one choice line for that letter
  outside fenced blocks (uppercase letter, optional bullet, bold or
  parenthetical note, then a colon: `A: 머지합니다`, `- A: merge`, `A (추천):
  머지`, `**A**: 머지`, `**A:** merge`), the reply is read as that line
  (`_picked_option`, `_option_label_approves`). It approves when the line has
  no negation (`_LABEL_NEGATION_RE`, note included: `A: 머지하지 않기`, `A:
  보류`, `A (not recommended): merge`), names no PR but the target (note
  included: `A (#1001): merge` refuses), and its lead (the text before the
  first `_LABEL_LEAD_RE` separator, cut at the first sentence end) is either
  an approval token (`A: 승인, 머지`) or merging said plainly, a closed set
  (`_MERGE_LEAD_RE`: `merge`,
  `merge it`, `Merge PR #999`, `squash merge`, `머지`, `머지합니다`, `머지
  진행`, `스쿼시 머지`, …). Only the lead is read, never the whole label
  through the final-clause rule: `A: Check merge status, ok` ends in `ok` and
  refuses. A lead that only starts with the word asks for
  something else and refuses (`A: 머지 진행 상황 알려줘`, `A: 머지 전에
  리베이스`, `A: mergeability check`). Two lines for the same letter (two
  menus in one turn) leave the letter ambiguous and refuse.

  The letter is read where a numbered reply is: in `_is_approval_reply` with
  the turn `_last_reply_approves`, `_answered_after` and `_held_after` pass.
  `_answered_after` also counts a picked line that names merging as the reply
  naming the merge, so a serial merge's menu needs no separate `Approve
  merge?` sentence. `_held_after` counts a letter picking a line of a later
  turn that mentions the PR as being about the merge, so `B` for `B: 보류` to
  a re-asked menu withdraws an earlier `A`. Target correlation, the briefing
  item count, the serial-merge cut and bulk approval are unchanged. Only a
  one-letter reply reads differently: before this change none was an
  approval, so the change can only release a letter that picks an approving
  line, and deny a merge whose earlier letter approval a later letter
  withdrew. Known gap, a missed release: a menu lettered past D, or written
  `A)` / `A.` / `(A)` without a colon, is not read.

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
  > as documented gaps rather than chased across further rounds: an
  > `xargs`-wrapped merge (`… | xargs -n1 gh pr merge`) is not recognized by the
  > trigger tokenizer (`xargs` is not a peeled prefix), so it bypasses the gate
  > entirely. Closing it soundly needs an explicit-signal redesign, out of scope
  > for the honest-agent nudge. (A second gap listed here — an unscoped
  > cross-repo number — is now handled by **Repo scoping** below, down to the
  > residual that section names.)

- **Repo scoping (#1419).** Every repo has a `#999`, so a number alone does not
  identify a PR. When the *merge command* names a repo and the correlated
  approval window names a **different** one, the approval does not transfer:
  both `_correlated_prior_turn_text` (prior-turn path) and the `answered`
  expression in `_merge_escalation_reason` (serial path) bail out via
  `_repo_conflict`.
  - The merge's repo is read from the command **only** — `-R`/`--repo` in any
    spelling (`-R o/r`, `-RO/r`, `--repo=o/r`) or a `…/pull/N` URL argument. A
    leading host is dropped (`github.com/o/r` == `o/r`) and comparison is
    case-insensitive. `cd`-based inference is deliberately absent: `cwd` resets
    between Bash calls, so it would be a guess.
  - The window's repo comes from the same two sources over the correlated turn
    range, last-wins — mirroring `_context_pr_from_window`.
  - **Both sides must resolve, or it is not a conflict.** `None` vs `o/r` is
    left alone. Measured over the local corpus (851 transcripts, 787 executed
    `gh pr merge` calls): 393 merges name a repo — 377 agree with their window,
    **14 differ** — while 123 name none, and **96 of those 123** sit in a window
    that does name one (the ordinary `gh pr checks 999` → `gh pr merge --repo
    o/r 999` shape). Treating `None` as a disagreement would therefore deny 96
    honest merges to reach the same 14.
  - **Residual:** the issue's literal repro — a briefing/approval window whose
    prose names no repo at all, followed by `gh -R org/other pr merge 833` — is
    *not* caught, because the window side is unresolved. What is caught is the
    common shape where the window did probe a repo.
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
  as with the `xargs` gap above.

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

  **The offer carries the condition (issue #1402).** The first deny message is
  where the marker is advertised, and it used to name only the syntax — the
  attestation condition surfaced in the second, marker-specific deny, after the
  marker had already been attached. Read on first contact, the offer was a
  relief that takes a reason string, and a reason slot invites filling rather
  than checking. Both the deny's `bypass_reason_hint` and the `gh pr merge` gate
  checklist in `hooks/_lib/block_message.py` now state it in the same sentence
  as the offer: only when the briefing is already in the window the gate scores
  (this turn, or the turn that named this PR right before the user's approval),
  because the marker attests the briefing was complete, not that one exists.
  This narrows one supply path; markers attached pre-emptively with no block at
  all never read the message.

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

  **A serial merge needs its own answer (issue #1402).** The cut above leaves
  the full-briefing path uncut, and that path had no notion of an answer: a
  briefing written after the first merge, whose approve-ask slot cites the
  answer the first merge already consumed, reached 4 of 6 items and released
  the second merge on text alone. So once a merge has executed, both
  full-briefing releases — the current turn and the correlated prior turn —
  also require an approval of THIS merge after that merge. The target is the
  one `_merge_target_pr` resolves, the way the prior-turn path resolves it,
  except that a numberless merge reads its `gh pr checks/view` probe only after
  the last executed merge — the earlier probe named that merge's PR. The
  approval is either a typed reply that `_is_approval_reply` accepts (its final
  clause is an approval token, with a reference to the PR set aside, so
  `PR #999 머지해줘` counts) and that names the PR or answers a turn that
  named it and either asked to merge that PR, or is answered by an approval
  that names merging itself (`머지 진행`), and no later user message about the
  PR or about merging is something other than an approval — the latest decision
  wins, so `ok` followed by `PR #999 머지 보류` leaves the merge unanswered. An
  `AskUserQuestion` answer withdraws it the same way — a picked label that is
  not an approval, or a declined (`is_error`) question, about the PR or about
  merging — which the typed-message scan cannot see, since that answer arrives
  as a tool_result. A
  merge that repeats or chains merge segments is never answered this way: one
  answer releases one merge.
  Asking to merge is one question sentence (ending in `?`, `할까요`, `될까요`,
  `하시겠` or `해도 되`) with `merge` or `merging` as a whole word, `머지` or
  `병합`, that
  neither negates it nor asks whether it happened or how it stands
  (`머지하지 말까요?`, `머지됐나요?`, `머지했나요?`, `Did we merge PR #999?`,
  `PR #999 merge status?`, `머지 상태`) — in either spelling of the verb, so
  `merging status|state|conflicts|results` is excluded exactly as `merge` is.
  A completion question is excluded the same way (`Is the merge of PR #999
  done?`, `Has merging PR #999 completed?`, `머지 완료됐어?`, `머지 끝났나요?`),
  but only when the completion word is about the merge: a conditional clause
  keeps the ask (`Merge PR #999 once CI is done?`), and so does a request to
  finish it (`머지 완료할까요?`). A progressive question is a state question
  too (`Is GitHub merging PR #999?`, `Are you still merging …?`): a subject
  before `merging` separates it from the gerund in `Is merging PR #999 OK?`,
  which still asks. The sentence must also be about the PR. When the merge
  verb takes PR references as its object — a list right before `머지`/`병합`
  or right after `merge`, with or without a colon (`Approve merging: PR #833
  and #999?`) — the ask is
  about exactly those PRs (`#833, #999 를 머지할까요?` asks about both;
  `PR #999 checks are green, #833 머지할까요?` asks about #833 only). Without
  such an object the sentence must name the PR or no PR at all
  (`PR #999 브리핑 … Approve merge?` counts;
  `PR #833 and #999 are ready; Approve merge #833?` does not). An unnamed ask is not discounted because the turn
  cites other PRs or issues, since briefings do; a turn that briefs two PRs and
  asks once without a number is read as asking about either. The other form is an `AskUserQuestion` tool_result that is not
  `is_error`, whose question names the PR, held to the same merge rule
  (`PR #999 어떻게 할까요?` answered `승인 — 그대로 머지` counts), and whose
  picked label leads with an approval token
  (`승인 — 머지`, `Approve merge`), a reference to the PR itself set aside (`PR #999 머지`),
  with no negation anywhere in the label — a qualifier can reverse the lead
  rather than narrow it, whether the negator follows the verb
  (`승인 — 머지하지 않기`, `Approve — do not merge`), precedes it
  (`승인 — 안 머지`), sits in a consequence clause (`승인 — 머지하면 안 됨`) or
  is a bare English determiner (`Approve — no merge`). A reply is not an approval: an unrelated message, a status
  request that names the PR (`PR #999 머지 상태만 알려줘`), a refusal that does
  not end on an approval token, a reply that ends in a question mark
  (`Approve merge?` typed back, `ok?`), a bare `ok` to a turn about something else or
  to a status line about the PR that asks nothing (`PR #999 checks are green.`,
  `PR #999 was approved; checks are green.`, `PR #999 emergency rollback?`), an answer to an approval ask that
  is not a merge ask (`PR #999 테스트 승인할까요?`), an
  approval picked for a different question, a `보류` pick, or a declined
  question leaves the merge unanswered, and a merge whose target cannot be
  resolved is never answered this way. Keyword tests were tried and dropped — each review round found a
  phrasing that named the PR beside an approval word without agreeing to
  anything. A PR number counts as named when no ASCII identifier character
  touches it, so `#999를` names PR 999 and `v999` does not.

  The `AskUserQuestion` form matters because `_human_user_indices` skips
  tool_result-only entries by design — replayed over 566 local merges, the
  typed-message-only version denied 47 merges the shipped gate allowed, and 13
  of those had been answered through `AskUserQuestion`. With the forms above, a
  replay over 572 local merges denies 45 that the shipped gate allowed. Three of them carry
  a real approval that these forms do not read — typed `둘다 승인` and
  `둘다 머지하세요`, whose final clause is not a bare token, and a picked
  `앵커 작성 후 머지`, which does not lead with one — and a typed `승인` whose turn
  did not name the merged PR; one answer was picked for a CHANGELOG question
  that did not name it either. Each of those costs one more ask. The rest are
  replies that approve nothing (`둘다 진행`, `각각 올리세요`) or merges with
  nothing from the user after the previous one: compaction summaries and task
  notifications are not the user. No new state; a session
  with no executed merge is untouched. A complete briefing that fails only this
  check is denied with its own reason naming the missing answer, not the item
  count.

  The comparison is session-wide, not per repository: a merge in one repo
  followed by a briefing for another still needs its answer, which is the
  same No Approval Transfer rule. The marker floor is deliberately left as
  #1214 shipped it — `merge_serial_rebriefed_passes` pins that a briefing item
  written after the first merge re-arms the marker on its own — so a marked
  merge is the one release this check does not reach.

  **One answer can approve a listed set (bulk merge, issue #1583).** The
  serial check above made every merge after the first wait for its own
  round trip, so N ready PRs cost N briefing → answer cycles even when the
  user had read all N briefings and answered `둘다 머지하세요` — a reply the
  #1402 replay already lists as denied. `_bulk_released` is a second release
  path, checked before the others, and every condition below must hold:

  | Condition | Check |
  | --------- | ----- |
  | the command is that merge and nothing else, with an explicit PR number: no other command chained before or after it (`cd other && …`), no loop, no `GH_REPO`/`GH_HOST` prefix, not numberless | `_merge_pr_number` |
  | the latest decision is an approval: the last typed message, or the `AskUserQuestion` answer after it. Every sentence holds only PR references and approval words, so a deferral or precondition in any sentence (`CI 한번 더 돌려보고. ok`), a condition after a comma, a refusal word, or a trailing `?` voids it; a reply over 300 characters is not read. In an `AskUserQuestion` answer every pair is read — a refusal on any question voids it — and the deciding pair must be a listed merge ask or a label naming the PRs | `_bulk_reply_prs`, `_ask_decision` |
  | the approved set has two or more PRs: the PRs the answer names (`#833 #999 머지`, or by bare number `98, 1118 머지` when the reply has no `#N`/`PR N` reference — beside one, a bare number is an ordinal and voids the reply), else the PRs the listed merge ask names — the PR list is the object of 머지/merge and the ask ending follows the verb directly (`#833, #999 를 머지할까요?`, `Approve merging #833 and #999?`); a merge method may sit between the particle and the verb (`#833 · #999 두 건을 squash 머지할까요?`, `스쿼시로 병합`), since it names how to merge, not another object; `#833, #999 머지 전에 CI 를 다시 돌릴까요?` is not one. A reply naming no PR answers the turn's last question, so the listed ask must be that last question (`… 머지할까요? 그 전에 리베이스해 둘까요?` voids a bare `ok`) | `_listed_merge_ask` |
  | this PR is in the set and has its own section in the briefing turn — from a heading line (a markdown heading, or a line bold end to end) that names exactly one PR, this one (`### PR #833 / PR #1001` is a joint heading even when #1001 is not in the set), to the next heading at the same level or above — with 4 of 6 items inside it, the shared ask counting as its approve-ask item. The first such heading fixes the level; a deeper heading or a `#` comment inside a code fence is content of the section it sits in, a bold lead inside a bullet is an item, a shared block after the last section belongs to no PR, and only a PR's first section counts | `_pr_sections`, `_briefing_item_count` |
  | no merge since the answer targeted a PR outside the set or repeated one, and this PR has not merged since; a clean call that merged inside a chain or condition (`gh pr merge 1001 && echo done`) has no single PR number and ends the set | `_executed_merges(include_skippable=True)` |
  | no repo conflict (as in **Repo scoping**) and no later `AskUserQuestion` hold; the briefed repo is the one this PR's section names it with (`owner/repo#N` or a `…/owner/repo/pull/N` URL — one repo only; two repos for the same number name none), else the window's last read-only probe; after a Bash call since the briefing window opened (before the answer too) that changed directory (`cd`, `pushd`, `popd`) or exported `GH_REPO`/`GH_HOST`, the merge must name the briefed repo with `--repo` | `_section_repo`, `_repo_conflict`, `_changed_directory_after`, `_ask_held_after` |

  The section rule is the part that keeps *No Approval Transfer*: a combined
  paragraph about two PRs (`momentum-merge-serial-unanswered-prior.jsonl`)
  still denies the second merge, because no heading names one PR alone, so
  no PR has a briefing of its own. The answer rule keeps the user's choice
  partial: `#833만 머지` approves a set of one, which this path does not
  release.

  `pre-merge-approval-gate` asks the same question through
  `bulk_merge_approved(payload)` (loaded from this file), so the two gates
  cannot disagree about which merges the user approved. That function fails
  closed: any exception or unreadable transcript returns False and the sibling
  keeps asking.

  Replayed over every recorded `gh pr merge` in local transcripts (339 calls
  across 813 Claude Code and Hermes transcripts, each cut right before the
  merge and fed to the main and branch builds): 0 decision changes, 0
  sibling prompts skipped, 0 errors. No recorded briefing used per-PR
  headings with a listed ask, so the path opens only for briefings written to
  the new shape (`skills/merge-briefing/SKILL.md` → *Bulk merge*); the
  release cases are pinned by the `momentum-merge-bulk-*.jsonl` fixtures.

  An adversarial review round found nine shapes the first version released
  wrongly — a bold bullet naming the other PR in passing taken as its heading, a
  shared trailing block credited to the last PR, a prep question (`… 머지 전에 …
  돌릴까요?`) read as the merge ask, only the last `AskUserQuestion` pair read, a
  승인 to an unrelated question counted, a clarify condition after the comma
  dropped, an earlier deferral sentence ignored, and a merge chained after `cd`
  or retargeted by `GH_REPO`. Each is a `merge_bulk_*_denies` case now; all nine
  fail against that first version and pass against this one. A second round
  found five more — a `#` comment in a code fence or a deeper heading taken as
  the other PR's section, a bare `ok` credited to an earlier listed ask when the
  turn ended on another question, a deferral sentence with no PR number, and a
  `cd` issued as its own call before the merge — and those five cases fail
  against the round-one version and pass now. The fix after round two had no
  independent review round of its own; the round-two probes were re-run on it
  and every shape they flagged now denies.

  **A merge method word and a set across repos (issue #1604).** Two bulk
  briefings a user had read and approved were denied in a Hermes session. The
  ask `#A · #B 두 건을 squash 머지할까요?` put `squash` between the particle
  and 머지, so `_MERGE_OBJECT_RE` found no object and the six-item question
  did not score; `_MERGE_METHOD` now allows `squash`/`rebase`/`스쿼시`/`리베이스`
  there. The second PR of the set lived in another repo, and a `cd` in an
  unrelated call after the briefing left the window with no probe repo, so the
  cd rule compared the merge's `--repo` with `None` and denied — although each
  section heading named its PR as `owner/repo#N`. The section's repo now is the
  briefed repo — read from the heading line only (the body may cite a related
  PR with the same number in another repo) and lowercased like `--repo` values.
  The listed-ask pattern takes the same method
  words, so a bare `ok` to `#A, #B 를 squash 머지할까요?` releases the set. The same change closes a wrong release in the old rule: with
  the window's last probe on repo `a/x`, `gh pr merge 999 --repo a/x` was
  released for a #999 the briefing placed in `b/y`; it now denies
  (`merge_bulk_cross_repo_wrong_repo_denies`). Replayed over every recorded
  `gh pr merge` (290 calls across 93 transcripts): 2 deny → allow, both the
  approved merges of the reporting session, 0 allow → deny, 0 errors.

  **A deny says why a briefing the user saw was not scored (issue #1433).**
  Message-only; no decision moves. When the two deny paths above fire —
  "fewer than 4 of 6 items" and the marker with no briefing — and the latest
  turn in which the assistant wrote text before the last human message holds
  a briefing that would have passed that path's threshold (4 items, or the
  marker's 1), the `why` gains one clause naming the check that excluded it.
  `_excluded_briefing_reason` asks the decision's own questions again, in the
  decision's order, so the named reason is the one that fired:

  | Reason | Check re-asked |
  | ------ | -------------- |
  | one approval releases one merge, and this command runs more than one | `_merge_segments` / `_has_repetition` |
  | the last user message (`…`) is not an approval reply | `_last_reply_approves` |
  | a user message (`…`) came between the briefing and the approval | briefing turn ends before the approval's own prior turn |
  | the briefed turn does not name the PR this merge targets | `_correlated_prior_turn_text` |
  | it precedes a merge that already ran in this session | serial-merge `floor` (marker path only) |

  "The latest turn in which the assistant wrote text" rather than "the turn
  right before the last user message" is deliberate. A user message that
  carries content of its own still empties the turn behind it — briefing →
  interrupt → `잠깐, CI 로그부터` → `ok` leaves the briefing one turn further
  back — so keyed on the turn right before, the clause would never fire on the
  sequences that motivated it. A bare interrupt marker no longer produces that
  shape: issue #1459 stopped counting it as a human message, so briefing →
  interrupt → `ok` is released rather than explained. `continue` after an
  interrupt still denies, and the clause is what names the reason.

  Replayed over every recorded `gh pr merge` in local transcripts (791 calls
  across 867 transcripts, each transcript cut right before the merge and fed
  to the pre-change and changed builds): 0 decision disagreements, 0
  tracebacks. Of the 187 denies, 99 gain the clause — 96 not an approval
  reply, 1 an intervening user message, 1 the briefed turn names a different
  PR, 1 the serial-merge cut. The replay matters because this code runs inside
  a `@fail_open` hook: an exception here would not surface as an error, it
  would release the merge.

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

  The prior-turn extension works inside the same tail without widening it
  (issue #1410). When the approval is the only human message left in the tail,
  the briefing turn's opening message has scrolled out. Every entry before the
  approval still belongs to that turn, so the extension scores from the tail
  head instead of giving up. A briefing pushed entirely past the tail stays
  invisible, and that merge still denies.

- **Injected user entries do not start a new window (issue #940).** A skill body
  loaded mid-turn and the expansion of a slash command both arrive as
  `role: user` with prose content; the harness stamps them `isMeta: true` while
  a genuinely typed message carries no such flag. Counting them would move the
  "since the last user message" boundary past a briefing the user actually saw,
  so a skill invoked between the briefing and the merge made a compliant flow
  look unbriefed. `_human_user_indices` skips them — a structural discriminator,
  not a content heuristic.
- **Host notifications do not start a new window either (issue #1410).** A
  background task finishing arrives as `role: user` text with no `isMeta`, and
  carries `origin: {"kind": "task-notification"}` where a typed message carries
  `origin: {"kind": "human"}`. Waiting on CI in the background between the
  approval and the merge put the notification in the "last user message" slot,
  which is not an approval reply, so the prior-turn extension never opened and a
  briefed, approved merge was denied at 0 of 6 items. `_human_user_indices`
  skips an entry whose `origin.kind` is present and is not `human`. An entry
  with no `origin` is kept: transcripts written before the field existed carry
  none on real user messages. Measured over 3,136 local transcripts: 9,592
  `task-notification` entries, none with `isMeta`; 428 of 3,003 `gh pr merge`
  calls had one as the last user entry.
- **An interrupt marker does not start a new window either (issue #1459).** The
  harness writes `[Request interrupted by user]` (or `… for tool use]`) as a
  `role: user` entry with no `isMeta` and no `origin`, so it was counted as a
  typed message: briefing → interrupt → `ok` → merge was denied although the
  user had just approved. It records that the reply was stopped, not something
  the user said, so `_human_user_indices` skips an entry whose **whole** text is
  the marker. The anchors are load-bearing: the same phrase inside a longer
  message is the user writing about the interrupt, and that message still closes
  the window. Measured over 1,254 local transcripts: 246 bare-marker entries in
  the countable shape (146 `[Request interrupted by user]`, 100 `… for tool
  use]`, no third spelling), against 41 entries carrying the phrase inside
  longer text, which stay counted. `continue` after an interrupt still denies —
  it is a progress signal, not consent — and that split is what the four
  `momentum-merge-interrupt-*` fixtures fix in place.
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
other two are `pre-merge-approval-gate` and `side-effect-scan`. Four more are
conditional: `gh-merge-worktree-precondition` (only with `--delete-branch`),
`commit-title-length-check` (only with `--squash`), `pipefail-advisory` (only
when the merge is piped), and `session-intent` (only when the session never
declared a mutation intent). The checklist keeps that split
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
`tests/hooks/preflight-gate/test_merge_gate_briefing.sh::merge_checklist_on_both_channels`
pins both channels per gate name.

### Host approval provider (issue #1639)

A host can collect the human's merge answer in a channel of its own (a
dashboard button, a chat thread) and relay it into the session as a row the
host wrote. The transcript path cannot read that as an approval: the last user
message is the host's text, whatever the human answered. Replayed through the
Hermes bridge on `main`, a relayed answer was denied with and without
`# briefing-surfaced`.

The host can instead name an executable in the hook process environment:

```text
PRAXIS_MERGE_APPROVAL_PROVIDER=<absolute path>
```

Unset (the default), nothing below runs and every decision is the one `main`
makes. Set, the provider is asked only about a merge the transcript path would
deny, and only when the command is one `gh pr merge` naming its PR number and
exactly one repo on `github.com`, with no chain, loop or `GH_REPO=` prefix.
Every repo reference counts (`--repo`, `-R`, `--repo=`, `-Rowner/name`, a pull
request URL positional), and they must all agree: `gh` merges the URL's repo
over `--repo`, and the last of several `-R`, so a command naming two repos
could merge one the provider never vouched for. A host other than `github.com`
(`-R host/o/r`, a URL on another host) and a session that ran
`export GH_HOST=…` are refused too, since the provider contract names no host.
A merge with no repo runs against whatever checkout the shell or the host's
working directory points at, which can change between the answer and the
call, so the provider is not asked about it.

```text
<provider> merge-approval --repo <owner/name> --pr <N> --session <session_id>
```

stdin is closed; the call is cut off after 5 s, or sooner when the dispatch
group's shared deadline leaves less (a quarter second is kept back to record
the outcome; below 0.5 s the provider is not started, a refusal). On exit 0
its stdout must be one JSON object:

```json
{"approved": true, "ref": "<unique per approval>", "repo": "owner/name",
 "pr": 999, "briefing": "<text the human saw>", "approved_at": 1767225600}
```

The provider vouches for who answered; praxis still judges the answer and
releases only when all of these hold:

- `approved` is the JSON `true`, `pr` is a JSON integer equal to the merge's
  number, and `repo` equals the merge's repo (case-insensitive);
- `briefing` scores at least `MERGE_BRIEFING_MIN_ITEMS` (4) of 6 items with
  `_briefing_item_count`, the counter the transcript path uses;
- `approved_at` is a finite number no older than 30 days and no more than
  5 minutes ahead of the hook's clock (spent refs are kept 30 days, so neither
  a forgotten ref nor a future timestamp can outlive its spent record and be
  replayed);
- `ref` has not released another merge.

Any other outcome (non-zero exit, timeout, output that is not one JSON object,
a mismatch) keeps the deny and appends one line naming the reason:
`host approval provider did not release this merge: <reason>`. When a
provider that exits non-zero prints a JSON object with a string `reason`, that
reason is quoted too, cut to 200 printable characters.

**One approval, one merge.** The release spends the ref, under `state_lock`,
in `$PRAXIS_STATE_DIR` (default `~/.praxis/state`) `merge-approval.json`, with
the session and `tool_use_id` of the call it released. The ref is freed again
only when that call's `tool_result` in the same session's transcript is
`is_error`: a sibling gate blocked it or `gh` failed, so nothing merged. A
call still in flight, or one whose result is not in the transcript, keeps it
spent. A merge that ran and then failed in `gh` (a local `--delete-branch`
step, say) frees it too; the freed ref can only release the same PR again,
which `gh` refuses once it is merged. A payload without `session_id` or
`tool_use_id` is never released.

**Both gates, one decision.** `pre-merge-approval-gate` asks the same
function, `host_merge_decision(payload)`. The first gate to ask for a tool call
records the outcome, refusals included, keyed by session, `tool_use_id`, repo
and PR, for 10 minutes; the second reads it. In one dispatch the gates run in
sequence, so the provider runs once per tool call in either order, and a
provider that would answer differently on a second call cannot split them.

**Not a bypass.** The agent cannot set the hook process environment: an
inline `PRAXIS_MERGE_APPROVAL_PROVIDER=… gh pr merge` prefix never reaches the
hook, as with the other variables here. The provider replaces where the answer
comes from; the briefing check and one-answer-one-merge still hold. A crash in
the check refuses (`host_merge_decision` catches every exception), so it
never releases on an error even though the hook itself is `@fail_open`.
The provider executable and `merge-approval.json` are as safe as the user's
filesystem, like every praxis state file; a host that runs agents able to
write there keeps the provider outside their reach.

Tests: `tests/hooks/_lib/test_host_merge_approval.py`, on the fixtures
`momentum-merge-host-relayed*.jsonl` from
`tests/fixtures/merge_gate_fixtures.py`.

## Check: refetch (was `pr-state-refetch-gate`)

Former title: PreToolUse AskUserQuestion PR-State Re-Fetch Gate.

`hooks/preflight-gate/merge-gate/refetch.py` fires on every
PreToolUse(AskUserQuestion) event. When a question's text names a PR number
alongside a merge-intent keyword, the hook re-fetches that PR's **live**
`state`/`mergeStateStatus` via `gh pr view` before the menu is allowed to
surface. If the live state is already `MERGED` or `CLOSED`, it warns
(default) or blocks (strict mode) — the question's premise is stale.

### Why this exists

A next-step `AskUserQuestion` menu whose text depends on a PR's merge/close
state can go stale mid-turn: the PR gets merged or closed (by another actor,
or by an earlier step in the same turn) and the menu still surfaces
"merge PR #N?"-style options against that outdated premise. The user then has
to notice the mismatch and correct the agent themselves — exactly the kind of
silent premise drift the *External Discussion Fidelity — lock-boundary
re-fetch* rule ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) targets for other surfaces (Slack
threads, PR mergeable status at merge-ask time).

No existing hook covers this surface:

| Hook | Why it doesn't cover this |
| ------ | --------------------------- |
| `merge-state-claim-gate` (Stop) | Only scans the **final assistant message** for a completed-state claim, post-hoc. It cannot see a mid-turn `AskUserQuestion` — by the time the Stop hook runs, the stale question has already been shown to the user. |
| `output-block-falsify-advisory` (PreToolUse/AskUserQuestion) | Emits a static text reminder ("may already be resolved by a merged PR") but never calls `gh` — it cannot tell whether the premise is actually stale, only that it *might* be. |
| `pre-merge-approval-gate` (PreToolUse/Bash) | Gates the `gh pr merge` **command** itself, not the **question** that precedes the decision to run it. By the time this hook would fire, the stale menu has already round-tripped through the user. |

This hook is the live-refetch analogue of `pre-merge-approval-gate`'s
lock-boundary re-fetch pattern (`gh pr view --json mergeable,mergeStateStatus`
immediately before surfacing a merge-approval ask), extended to a different
surface: a PR-state-contingent **question**, not a merge **command**.

### Detection signal (co-occurrence)

Both conditions must hold for the **same** `questions[]` entry (question text

- all of that question's option labels/descriptions, joined):

1. A **PR-number token** — `#\d+` (matches both bare `#123` and `PR #123`;
   the optional `PR` prefix carries no extra information once `#123` is
   present, so it is not tokenized separately).
2. A **merge-intent keyword** — `merge`, `squash` (English, ASCII-letter
   lookaround), or `머지` (Korean, with the same negative-lookahead exclusions
   as `merge-menu-review-options-advisory`'s `_KO_MERGE_RE`: `머지된`
   already-merged/triage-label and `머지하지` "do NOT merge" do not count).

A merge keyword in one question does **not** pair with a PR number in an
unrelated question in the same payload — detection is scoped per-question,
not payload-wide, so a multi-question menu with an unrelated merge-adjacent
question elsewhere does not cross-contaminate.

### False-positive boundary (explicit design decisions)

| Text | Fires? | Why |
| ------ | -------- | ----- |
| `"PR #714 merge할까요?"` | Yes | number + EN merge keyword, same question |
| `"이 변경(#714)을 머지할까요?"` | Yes | number + KO merge keyword, same question |
| `"PR #714 리뷰를 진행할까요?"` | **No** | number present, but no merge/squash/머지 keyword anywhere in the question unit — a review-only menu does not depend on merge state |
| `"머지 충돌을 해결해볼까요?"` | **No** | merge keyword present, but no `#\d+` token anywhere — nothing to re-fetch |
| `"#714 이슈를 닫을까요?"` | **No** (in practice) | number present but no merge keyword — a bare issue-close question is not merge-state-contingent |
| Question: `"PR #714에 대해 어떻게 할까요?"`, option: `"Merge"` | Yes | co-occurrence is checked across the **whole per-question text** (question + all its options), not per-line/per-option — this is the realistic shape (number in the question, verb in an option label) |
| Question A has `"#100"` only; Question B (same payload) has `"merge"` only | **No** | scoped per-question — cross-question pairing is explicitly excluded |

The design deliberately does **not** treat the bare literal `PR` as an
independent third keyword (unlike the issue's illustrative "merge"/"PR"/"머지"
list): requiring `#\d+` already implies the number is almost always adjacent
to the word "PR" in natural phrasing, so a separate `PR`-token requirement
would only widen the match surface (e.g. "PR #714의 diff 크기 확인" — not a
merge decision) without adding recall for the actual target scenario
(merge/hold decision menus).

`gh pr view <N>` on a number that is actually an **issue** number (not a PR)
fails with a non-zero exit — this naturally routes through the fail-open path
below rather than needing separate issue-vs-PR disambiguation logic.

### Live re-fetch

For each candidate PR number (deduplicated, capped at 3 per payload):

```bash
gh pr view <N> --json state,mergeStateStatus,mergeable,isDraft
```

Run with `cwd` set from the hook payload's `cwd` field (falls back to the
hook process's own cwd when absent) so the query targets the correct
worktree's repo, and a 2-second timeout (worst case 3 candidates × 2s = 6s,
under the 8s manifest timeout).

#### Ask-readiness allowlist (issue #1436)

The verdict is an **allowlist**, not a "not obviously resolved" check. It is
the one `praxis:merge-briefing` Step 1 states: ask only when `mergeable` is
`MERGEABLE` **and** `mergeStateStatus` is `CLEAN` or `HAS_HOOKS` — the two
values that mean *mergeable with a passing commit status*. Every other value
names a condition the user should not be asked to decide against, and until
this issue the gate answered only `MERGED` / `CLOSED`, so all of them reached
the menu.

`isDraft` is read on its own line because draft is **not** a value of the
`mergeStateStatus` enum: a draft PR reports `CLEAN` and passes every other
check.

An `isDraft` the response does not carry is not an answer of `false`. Every
other unanswered field routes to a soft reason, and reading this one as "not a
draft" would resolve the one axis `mergeStateStatus` cannot report toward the
ask — so an absent or non-bool value takes the advisory-only row instead.

| Live fields | Result |
| --------------- | -------- |
| `state` is `MERGED` or `CLOSED` | Stale premise — advisory (default) or block (strict) |
| `state` is `OPEN`, `isDraft` is true | Not ready — advisory or block |
| `mergeable` is not `MERGEABLE` (e.g. `CONFLICTING`) | Not ready — advisory or block |
| `mergeStateStatus` is `UNSTABLE` / `BLOCKED` / `BEHIND` / `DIRTY` | Not ready — advisory or block |
| `mergeStateStatus` or `mergeable` is `UNKNOWN` / absent | **Advisory only, never blocks** — see below |
| `isDraft` is absent or not a bool | **Advisory only, never blocks** — draft status unanswered |
| `state` is `OPEN`, not draft, `MERGEABLE` + (`CLEAN` \| `HAS_HOOKS`) | Premise holds — silent pass |
| `state` is some other value this hook does not model | Advisory only — named rather than guessed in either direction |
| `gh` call fails, times out, or returns unparseable JSON | **That PR number is skipped** (fail-open) — cannot determine live state, so neither warn nor block on it |

**`UNKNOWN` advises but never blocks, strict mode included.** GitHub computes
the merge state asynchronously on a freshly-pushed PR, so an unknown answer is
a *not yet*, not a defect — hard-stopping on it would block on GitHub's
latency. It is still not `CLEAN`, so it is not silent either: the advisory
names it and says to re-poll. Strict mode blocks only when at least one
*blocking* reason is present; an advisory-only reason is marked as such in the
message.

A payload with 2+ candidate PR numbers where only some are not ready still
fires — the message lists only those, each with its own reason.

### What is advised / blocked

| Scenario | Action |
| ---------- | -------- |
| Default mode, ≥1 candidate PR is not ask-ready | exit 0 + advisory stderr |
| `PRAXIS_PR_STATE_REFETCH_STRICT=1`, ≥1 candidate PR is not ask-ready for a **blocking** reason | exit 2 (block) |
| `PRAXIS_PR_STATE_REFETCH_STRICT=1`, the only reason is `UNKNOWN` | exit 0 + advisory stderr (never blocks) |
| Any tool name other than `AskUserQuestion` | silent pass-through |
| No question carries the PR-number + merge-keyword co-occurrence signal | silent pass-through (no `gh` call made — zero subprocess cost) |
| All candidate PRs are ask-ready (OPEN, not draft, `MERGEABLE` + `CLEAN`/`HAS_HOOKS`) | silent pass-through |
| `gh` binary missing / call errors / times out / unparseable JSON, for a given PR number | that number is skipped (fail-open); if no other candidate is stale, silent pass-through |
| Malformed / missing payload | silent pass-through (fail-open) |

### Mode and env var behavior

| Env var state | Mode | Exit code on match |
| --------------- | ------ | --------------------- |
| Unset (default) | **Advisory** | 0 + stderr warning |
| `PRAXIS_PR_STATE_REFETCH_STRICT=1` | Strict | 2 (block) |

Default is advisory: the co-occurrence signal is a heuristic (a question can
legitimately mention a PR number and the word "merge" without the menu being
a go/no-go merge decision — e.g. "PR #714 merge 이력을 요약할까요?"), and the
live-state re-fetch, while authoritative once it runs, should not hard-block
until false-positive/false-negative rates are observed in practice. This
mirrors `merge-menu-review-options-advisory` and
`block-manufactured-action-menu`'s advisory-first-then-strict-opt-in
convention for `AskUserQuestion` gates.

### Parsing guarantees (fail-open)

- Malformed JSON payload → exit 0
- `tool_name != "AskUserQuestion"` → exit 0
- `tool_input` absent or not a dict → exit 0
- `questions` absent or not a list → exit 0 (no candidates)
- `options` absent or not a list in a question → that question's options
  contribute nothing to its text unit (question text alone is still scanned)
- No PR-number + merge-keyword co-occurrence in any question → exit 0, zero
  `gh` subprocess calls
- `gh` binary missing, non-zero exit, timeout, or unparseable JSON for a
  candidate number → that number is dropped, not blocked
- Any uncaught exception → exit 0 (via the shared `@fail_open` decorator in
  `hooks/_lib/_hook_runtime.py`)

### Relationship to sibling hooks

| Hook | Overlap |
| ------ | --------- |
| `merge-state-claim-gate` | None — that hook gates the **assertion** of completed state in the final message (post-hoc); this hook gates the **premise** of a mid-turn question (pre-hoc) |
| `output-block-falsify-advisory` | Complementary — that hook's static reminder covers the general "may already be resolved" case across many claim types; this hook adds a live, PR-specific re-fetch with an authoritative yes/no answer |
| `pre-merge-approval-gate` | None — different surface (`gh pr merge` Bash command vs. `AskUserQuestion` menu); same underlying pattern (live `gh pr view` re-fetch at a lock boundary) |
| `merge-menu-review-options-advisory` | None — that hook checks whether a merge-decision menu **offers a review lever**; this hook checks whether the menu's **premise about a specific PR is still true**. Both inspect `AskUserQuestion` merge-related text but for orthogonal purposes. Merge-keyword regex is mirrored from this hook (2nd consumer; see impl.py docstring) |

### Tests

```bash
bash tests/hooks/preflight-gate/test_merge_gate_refetch.sh
```

Covers: co-occurrence signal true positives (EN `merge`, KO `머지`, number-in-
question + verb-in-option shape), false-positive exclusions (number without
merge keyword, merge keyword without number, cross-question non-pairing),
live re-fetch MERGED → advisory, live re-fetch CLOSED → advisory, live
re-fetch OPEN → silent pass, strict mode → block, `gh` missing → fail-open,
`gh` non-zero exit → fail-open, unparseable `gh` JSON → fail-open, non-
AskUserQuestion tool → silent pass, malformed payload → fail-open, multiple
candidate PR numbers with mixed live states.

## Check: menu (was `merge-menu-review-options-advisory`)

Former title: PreToolUse AskUserQuestion Merge-Menu Review-Options Advisory.

The hook implementation lives at
`hooks/preflight-gate/merge-gate/menu.py`; the build
generates the dispatcher `hooks/merge-gate.sh` that the
platform `hooks.json` invokes. It fires on every
PreToolUse(AskUserQuestion) event and inspects `options[].label`. When the menu
is a **merge-decision gate** (an option label names a merge / squash action) but
offers **no review / debate option**, it emits an advisory asking the agent to
re-author the menu with the quality levers a pre-merge gate should offer:
re-run codex-review-wrap, re-run code-reviewer, or open a critic debate.

### Why this exists

A merge-decision `AskUserQuestion` menu is authored ad-hoc by the agent each
turn — there is no skill or template that generates the option set. So the
levers a user often wants at the last gate before an irreversible merge
(a fresh codex review pass, a code-reviewer pass, a critic debate) are routinely
absent, and the user must request them manually each time.

A PreToolUse hook **cannot inject options** into the rendered menu — it can only
allow, block, or advise. This hook takes the advise path: it detects a
merge-decision menu missing the review/debate levers and nudges the agent to
re-issue the `AskUserQuestion` with those options added.

This is the sibling of `block-manufactured-action-menu`, which catches a merge
menu that is *redundant* (surfaced after the user already said "merge"). This
hook catches the complementary defect: a merge menu that is *incomplete*
(missing the review/debate levers). Both inspect `options[].label` on
PreToolUse(AskUserQuestion); neither blocks by default.

### Context-aware reviewer routing (L2, #562)

When the menu *is* a merge gate missing a review option, the hook tailors
**which** reviewer the advisory recommends to the nature of the change being
merged. It reads the branch's changed file paths (`git diff --name-only
<base>...HEAD`) and maps them to a reviewer family, leading the advisory with
the context-specific recommendation before the generic levers.

This runs **only after** the merge-decision + no-review early-returns, so a
non-merge menu pays **zero** subprocess cost. The diff is path-based (no
diff-content read).

| Priority | Path signal | Recommendation (Hybrid: type + example agent) |
| ---------- | ------------- | ----------------------------------------------- |
| 1 (highest) | `auth` / `token` / `secret` / `credential` / `permission` / `oauth` substr, or `.pem` / `.key` suffix | 보안 리뷰 (예: security-reviewer) |
| 2 | `.sql` suffix, or `/sql/` / `migration` substr | 데이터/SQL 리뷰 (예: review-data) |
| 3 | `schema` / `/models/` / `/model/` / `entity` substr, or `.prisma` / `.proto` suffix | 설계 리뷰 (예: review-service-design) |
| 4 | `component` / `/ui/` / `/views/` / `/pages/` substr, or `.tsx` / `.jsx` / `.vue` / `.css` / `.scss` suffix | 디자인/UX 리뷰 (예: designer) |
| — (no match) | anything else | static generic levers (codex-review-wrap / code-reviewer / critic) |

**Multiple matches resolve to a single recommendation** by priority (security >
data > design > ux) — a merge gate surfaces the single most consequential lens,
not a wall of options.

**Hybrid recommendation text** = a portable reviewer *type* ("보안 리뷰") plus a
parenthetical example agent ("예: security-reviewer"). The type conveys intent
on every host; the example is a hint for hosts where that agent exists. This is
why the hook stays `hosts: all` despite naming Claude-ecosystem agents.

**Base ref resolution (nearest fork point)**: among the candidate refs
(`origin/HEAD`, `origin/main`, `origin/dev`, `main`), the hook picks the one
whose **merge-base with HEAD is nearest** — the merge-base that is a descendant
of all the others. `git diff <base>...HEAD` then includes only *this branch's*
changes, not a non-default base's unique commits. This makes routing correct on
multi-base repos (a branch that targets `dev` while the remote default is `main`
resolves to `dev`, the nearer fork point — not `main`). **Fail-open**: a non-git
cwd, no resolvable candidate, a git error, or a timeout yields the static,
family-agnostic advisory — a diff the hook cannot read never crashes it and
never fabricates a routing decision.

**Subprocess budget**: base resolution runs one `git merge-base` per candidate
(which doubles as an existence check) plus one `git merge-base --is-ancestor`
only when a candidate's merge-base differs (the rare multi-base case). Worst
case 5 merge-base + 4 is-ancestor = 9 calls at 0.3s + one `git diff` at 1.5s =
4.2s — kept under the 5s manifest timeout so the Python fail-open path always
runs before the hook runner could kill a hung/locked git. Legit git calls return
in milliseconds; the timeouts only bound the pathological stall (NFS, index.lock).

**Self-consistency with suppression**: routed reviewers whose label may lack a
`review`/`reviewer` substring are added to the review-suppression tokens —
`designer` (the UX agent name) and `security` (a security review option phrased
as `security audit` / `security scan` rather than `security-reviewer`). The
others (`review-data`, `review-service-design`, and the `security-reviewer`
example itself) already suppress via `review` / `reviewer`.

**Base-resolution accuracy (multi-base repos)**: handled by the nearest-fork-point
resolution described above — a branch targeting a non-default base resolves to
that base, so the diff is not over-included. The residual edge is a repo with
two equally-near bases (a branch forked from a point both `main` and `dev` share
identically), which is degenerate; routing then uses whichever candidate is
probed first, still advisory-only.

**Data-signal tightness**: the data family's substrings are deliberately narrow
(`.sql` suffix, `/sql/`, `migration`). Broader tokens were rejected for
mis-routing non-data paths: `etl` matched `getlist.py`, `/load` matched
`src/loader.py`, and `template` matched FE template directories
(`web/templates/Card.tsx`). A SQL template lacking a `.sql` suffix falls to the
generic advisory — the safe direction, since a mis-routed *recommendation* (not
a block) is the only failure mode.

### What is advised

| Scenario | Action |
| ---------- | -------- |
| Default mode, merge-decision option present, no review/debate option present | exit 0 + advisory stderr |
| `PRAXIS_MERGE_MENU_REVIEW_STRICT=1`, merge option present, no review option present | exit 2 (block) |
| Any tool name other than `AskUserQuestion` | silent pass-through |
| No option label names a merge-decision action | silent pass-through |
| A review/debate option is already present | silent pass-through (menu already complete) |
| Empty / missing options | silent pass-through |
| Missing / malformed payload | silent pass-through (fail-open) |

### Detect patterns

#### Merge-decision trigger tokens (option label)

- Korean (regex with negative lookahead): `머지(?!된|하지)` — excludes the
  meaning-inverting `머지된` / `머지하지` inflections (see Known limitations)
- English (ASCII-letter lookaround): `merge`, `squash`

English tokens use ASCII-letter lookaround (`(?<![a-z])merge(?![a-z])`) so
`merged` / `merger` do not trigger, while the mixed-script label `squash 머지`
still matches via the Korean token (Python's Unicode-aware `\w` treats Hangul
as a word character, so `\b` would not split `merge할까요`-style labels — the
lookaround does).

#### Review / debate option tokens (option label)

- Korean (substring): `리뷰`, `검토`
- English (substring): `codex`, `review`, `reviewer`, `critic`, `designer`

Review-token detection uses **substring** matching (not lookaround) on purpose:
a false positive here only *suppresses* the advisory, which is the safe
direction (never nag when a review option may exist). So `reviewer` inside
`code-reviewer` and `review` inside `codex-review-wrap` both correctly count as
a present review option.

`designer` is in the set for self-consistency with L2 routing: the UX family
recommends the `designer` agent, whose name contains no `review`/`reviewer`
substring, so without this token a menu already offering a `designer` option
would still be (wrongly) nudged. The other routed reviewers (`review-data`,
`security-reviewer`, `review-service-design`) already match `review` /
`reviewer`, so no further tokens are needed. (`designer` is a precise enough
word that `redesign` — which lacks the trailing `er` — does not match it.)

### Mode and env var behavior

| Env var state | Mode | Exit code on match |
| --------------- | ------ | ------------------- |
| Neither var set (default) | **Advisory** | 0 + stderr warning |
| `PRAXIS_MERGE_MENU_REVIEW_STRICT=1` | Strict | 2 (block) |

Default is advisory: a merge menu without review options is a *missed
opportunity*, not a violation, and there are legitimate cases (a merge-strategy
chooser `squash / rebase / merge-commit`, or a menu where the user explicitly
declined further review this turn) where the nudge does not apply. Strict mode
exists for users who want the levers always surfaced before any merge gate.

### Known limitations

- A merge-strategy chooser (`squash / rebase / merge-commit`) carries merge
  tokens but is not a go/no-go review gate; in default advisory mode the nudge
  is harmless (informational), but strict mode would block it. Prefer advisory
  mode unless the repo's menus never include strategy choosers.
- Detection is label-only; a review option expressed solely in an option
  *description* (not its label) is not counted as present and may trigger a
  false advisory. Keep review options' intent in the label.
- Review-token suppression is substring-based and runs **only on non-merge
  option labels** — a review lever is a distinct option, not the merge action
  itself. This matters because the suppression set includes broad tokens
  (`security`, `designer`, `review`) that can appear inside a merge label
  describing its change area (`merge security fix`, `merge designer changes`,
  `merge after preview`). If review detection ran on the merge label too, those
  substrings would *defeat* the nudge on exactly the high-stakes merges the
  label describes. Excluding merge labels from review detection fixes that
  inversion (and the older `preview`-inside-a-merge-label false suppression).
  A genuine review option still suppresses because it is a separate, non-merge
  option label (`security audit`, `designer 실행`, `re-run code-reviewer`).
- The Korean merge token `머지` is matched by a regex with a negative lookahead
  (`머지(?!된|하지)`) that excludes two meaning-inverting inflections: `머지된`
  (already-merged, a triage label) and `머지하지` (the `머지하지 말고` "do NOT
  merge" form). The second is a semantic inversion — without the exclusion an
  explicit no-merge label would fire a pre-merge nudge. Real gates still match
  (`머지`, `머지하기` since 하기 ≠ 하지, `머지할까요`, `스쿼시 머지`, `머지 + 정리`).
- The lookahead does not cover every non-gate inflection: a noun-modifier label
  like `머지 충돌 해결` ("resolve merge conflicts") still fires, since `머지` there
  is genuinely merge-related but not a go/no-go gate. Full KO disambiguation is
  out of scope (it leads to unbounded quoting/inflection corner cases); the
  advisory is non-blocking, so an occasional fire on a merge-adjacent label is
  low-harm. Only the two *inverting* inflections above are excluded.

### Parsing guarantees

- Malformed JSON payload → exit 0 (fail-open)
- `tool_name != "AskUserQuestion"` → exit 0
- `tool_input` absent or not a dict → exit 0
- `questions` absent or not a list → exit 0
- `options` absent or not a list in a question → that question skipped
- Empty label set → exit 0
- L2 routing diff unreadable (non-git cwd, base unresolved, git error/timeout)
  → static family-agnostic advisory (routing never crashes the hook)

### Tests

```bash
bash tests/hooks/preflight-gate/test_merge_gate_menu.sh
```

Covers: Korean merge token (`머지`) advisory + strict block; English merge
tokens (`merge`, `squash`) advisory; review-option-present suppression
(`codex` / `code-reviewer` / `review` / `critic` / `리뷰` / `검토`); merge-token
absent pass-through; non-AskUserQuestion tool pass-through; malformed payload
fail-open; empty options pass-through; ASCII-lookaround false-positive
avoidance (`merged` / `merger` do not trigger); mixed-script label
(`squash 머지`) trigger; multi-question payload. L2 routing (real git-diff
path): each reviewer family (security / data / design / ux), priority
resolution (security > data, design > ux), `designer` suppression, no-match
static fallback, non-git cwd fail-open, and strict-mode block carrying the
routed reviewer.

## Check: claim (was `merge-state-claim-gate`)

Former title: Stop Merge-State Claim Gate.

`hooks/preflight-gate/merge-gate/claim.py` runs on the `Stop`
event. It scans the **final assistant message** for a completed merge / PR /
issue / worktree state assertion and, when no fresh state query appears in the
recent transcript, emits a **stdout `{"systemMessage": ...}` JSON advisory**.

### Why this exists

A praxis ultrawork session (#487/#489) hallucinated review/merge state four
times in one session: "PR #495/#497 created, merged, issue closed, worktree
cleaned" — none of which had happened (the PR create was hook-blocked; the cited
numbers were unrelated worktrees). The behavioural remedy lived in memory only,
and the Iron Law "REPEATED PATTERN + MEMORY = FAILED REMEDY -> ESCALATE" was
crossed (issue #503).

PreToolUse hooks cannot see in-flight assistant text (issue #487 finding A3), so
they cannot gate a *claim*. The Stop hook is the exact complement: it sees the
final assistant output and can cross-check it against what the session actually
did.

### What is emitted

Advisory by default — exit 0 + stdout `{"systemMessage": ...}` JSON (issue #647
H3; the old exit-0 stderr form only reached the debug log). The model has
already stopped, so the note reaches the user (transcript-visible; not fed to
the model). `PRAXIS_MERGE_CLAIM_STRICT=1`
escalates to a `{"decision": "block", "reason": ...}` JSON, which re-prompts
the model to verify before stopping.

| Condition | Result |
| --- | --- |
| Final message asserts a completed merge/PR/issue/worktree state AND no fresh state query in the recent transcript | `[merge-state-claim-gate]` advisory |
| Same, but a fresh `gh pr\|issue …` command or GitHub MCP pull_request/issue/merge tool is present in the recent transcript | silent (claim is backed) |
| Final message asserts an **applied-on-branch** state (`applied`/`deployed`/`적용됨`/`배포됨` …) AND no **reachability probe** in the recent transcript — a generic state query does NOT clear this kind (#656) | `[merge-state-claim-gate]` advisory with reachability guidance |
| Applied-on-branch claim WITH a reachability probe (`git merge-base --is-ancestor`, `--json state,baseRefName` query, `git branch --contains`) in the recent transcript | silent (claim is backed) |
| Final message asserts a **still-open / unchanged / no-loss** state for a specific `#N` (`PR #864 는 여전히 OPEN`, `no commits were lost from PR #864`) AND no fresh `gh pr\|issue` query FOR THAT NUMBER (#869) | `[merge-state-claim-gate]` advisory |
| Same unchanged claim, cleared by a `gh pr\|issue view <N>` or GitHub MCP call referencing THAT SAME number | silent (claim is backed) |
| Same unchanged claim, a query for a DIFFERENT `#M` is present — does NOT clear | `[merge-state-claim-gate]` advisory (per-number specificity) |
| Unchanged claim with no `#N` on the line (`the branch still has no open PR`) | silent (deliberate narrowing — #869 scope, see below) |
| Final message has no such claim | silent |
| **Completed-state** claim line is negated (`not`, `yet`, `아직`, `않`, …) — persistence claims (`has not been merged`, `유실 없음`) are covered by the unchanged rows above, not silenced here | silent |
| Future intent only (`I'll create a PR`, `ready to merge`) | silent (completion tokens are past/perfective) |
| `stop_hook_active` is true | silent (re-entry loop guard) |
| Missing/unreadable transcript, malformed stdin | silent (fail-open) |
| `PRAXIS_MERGE_CLAIM_BYPASS` set | silent (opt-out) |

### Claim detection

A claim requires, **on the same line**, both:

- a **subject** token — `PR`, `pull request`, `MR`, `issue`, `이슈`,
  `worktree`, `워크트리`, or `#<number>`; and
- a **completed-state** token — `merged` / `머지했|머지됐|머지됨…`,
  `created`/`opened` / `생성했|만들었|올렸`, `closed` / `닫았|종료했`,
  `removed`/`cleaned`/`deleted` / `정리했|삭제했|제거했`,
  `applied`/`deployed`/`landed`/`blocked since` /
  `적용됐|배포됨|반영됨|차단됨…` (`released` deliberately excluded —
  lock/memory-release prose false-positives).

The **`applied` kind** additionally accepts a **branch/ref subject**
(`dev`, `prod`, `main`, `master`, `release`, `branch`, `브랜치`) — an
applied-on-branch claim ("dev에 적용됨", "deployed to prod") often names only
the target ref, never a PR number (#656). Branch tokens use Hangul-safe
lookarounds, not `\b` (same trap as the `\bPR\b` fix).

Localizing the match to one line cuts false positives on long final messages,
and the past/perfective completion tokens exclude future intent. A negation
token anywhere on the line suppresses the claim. The `applied` kind uses a
narrower negation set with `without` dropped — "Deployed to prod without
incident" is a genuine claim and must still fire (`fail` stays; the
applied-claim-next-to-failing-prose miss is an accepted, documented trade-off).

### Evidence detection

The recent transcript (last 80 events) is scanned for an assistant `tool_use`
that is either:

- a `Bash` command matching `gh … (pr|issue) <subcommand>` (e.g.
  `gh pr view`, `gh issue list`, `gh pr merge`), or
- a GitHub MCP tool whose name matches `pull_request` / `issue` / `merge` /
  `pr_` (e.g. `mcp__github__pull_request_read`, `merge_pull_request`).

Either form is treated as a fresh state query that backs the claim. Both the
`gh` CLI and the GitHub MCP server are covered so the gate behaves correctly in
local and remote-execution environments.

**Reachability evidence (`applied` kind only, #656).** A state query clears
`merged`/`created`/`closed`/`cleaned` but NOT `applied` — the 2026-05-15
incident ran exactly `gh pr view --json state` and still mis-released 3
changes because the merged PR's base was a feature branch (stacked PR). The
`applied` kind is cleared only by a Bash command matching:

- `git merge-base --is-ancestor` (commit-based reachability),
- a `--json` query carrying **both** `state` and `baseRefName` in the same
  command (`gh pr view --json state,baseRefName` — the `--json` context is
  required, so a bare `grep baseRefName` of source code does not clear; and a
  baseRefName-only query never confirms the merge state), or
- `git branch --contains` (short `-r` and long `--merged` intervening flags
  both tolerated).

This is deliberately CLI-only: an MCP `pull_request_read` *returns*
`baseRefName` but does not prove the field was consulted. The regex is
mirrored in `hooks/advisory-nudge/external-write-falsify-check` (2 copies —
DRY extraction deferred to a 3rd consumer per repo convention).

### Negative-polarity persistence claims (issue #869)

2026-07-27 session retrospect finding #2 (HIGH). Across several turns, the
assistant repeated "PR #864 는 여전히 OPEN, 커밋 유실 없음" (still open, no
commits lost) — a pre-compaction snapshot. The PR had actually merged as
`9ac8fa3` and that commit was the base of the in-progress work. Re-query
count over the affected turns: zero.

`_NEGATION_RE` originally treated ANY negated line as safe to silence (a
deliberate false-positive-avoidance choice). But "여전히 OPEN" / "유실 없음"
were never even reaching that check — `_CLAIM_KINDS`' vocabulary
(`merged`/`created`/`closed`/`cleaned`/`applied`) has no token for "OPEN" or
"no loss" at all, so the line was invisible to the hook, negation aside.

A second, independent claim path (`detect_unchanged_claims` /
`_UNCHANGED_STATE_RE`) now recognizes persistence-of-state assertions —
`여전히 OPEN`, `그대로`, `유실 없음`, `남아있`, `변경 없`, EN `still open`,
`no commits lost`, `hasn't been merged`, `remains open` — and, mirroring
`runtime-state-claim-gate`'s "isolation" kind, does **not** run the generic
negation suppressor: the negative surface form (`없음`, `not merged`) IS the
claim, not evidence that the assistant is reporting incompletion.

**Scope narrowing (deliberate, per issue).** This path fires ONLY when a
`#N` token co-occurs with the persistence vocabulary on the SAME LINE. A
numberless line ("the branch still has no open PR", "여전히 열려
있습니다") is left to the existing (silent) generic-negation path — widening
to numberless lines was explicitly rejected to avoid reopening the original
false-positive concern that motivated blanket negation suppression.

**Per-number evidence (`has_fresh_query_for_number`).** Unlike the
positive-kind evidence check (any recent `gh pr|issue` query clears any
positive kind), an unchanged claim about `#864` is cleared ONLY by a query
that itself references `864` — a `gh pr view 111 --json state` call sitting
in the same window does NOT clear a stale claim about `#864`. This mirrors
the applied-kind's stricter-evidence precedent (#656): a looser generic
check would have let the incident's exact stale re-assertion pass had any
unrelated `gh pr view` call happened to appear nearby.

The reference must be **positional and read-only**, not a substring of the
command text:

| Evidence shape | Clears `#864`? |
| --- | --- |
| `gh pr view 864 --json state` / `gh issue view 864` / `gh -R o/r pr view 864` | yes |
| `gh pr view https://github.com/o/r/pull/864` | yes (URL target ends in `/864`) |
| MCP read (`pull_request_read`, `get_issue`, …) with `pullNumber`/`issue_number` = `864` | yes |
| `gh pr view 1864` | no — digits must be a whole number, not a longer one's prefix |
| `gh pr view 111 --repo org/project-864` | no — the digits sit in a slug, not at the target position |
| MCP read with `owner: team864`, `pullNumber: 111` | no — only PR/issue *number* fields count |
| `gh pr merge 864 --squash`, MCP `merge_pull_request` | no — a mutation does not report the post-mutation state the claim asserts |

**Known gap (deliberate).** A combined line ("PR #864 는 OPEN이고 커밋 유실
없음") is cleared as a whole by a state-only read, even though `--json state`
cannot witness a force-push that dropped commits. Requiring commit/head
history evidence for the no-loss subtype specifically would change the
gate's contract beyond issue #869's scope; it is left to a follow-up.

### Relationship to sibling hooks

| Hook | Scope | Overlap |
| --- | --- | --- |
| `completion-signal-gate` | Stop advisory on completion phrases without an evidence block | Complementary — generic "done" claims vs. specific merge/PR state claims |
| `block-pr-without-caller-evidence` / `block-pr-without-precommit-evidence` | PreToolUse gate on PR *creation* | None — those gate the action; this gates the *assertion* of state |

### Parsing guarantees (fail-open)

Returns exit 0 on every infrastructure error — malformed stdin, missing/unreadable
transcript, and any uncaught exception (via the shared `@fail_open` decorator in
`hooks/_lib/_hook_runtime.py`). It never blocks a normal Stop in the default mode.

### SubagentStop (issue #1337)

The hook is also registered on `SubagentStop`, Claude-only (`hosts:
["claude"]`), which fires when a subagent finishes and "use[s] the same
decision control format as Stop hooks" (hooks reference, read 2026-09-06). A
subagent that reports completion is making the same claim on the same surface;
until this registration, nothing graded it.

Two payload differences decide what the hook reads, both handled by
`hooks/_lib/_transcript.py` (`resolve_stop_transcript`, `load_stop_turn`,
`stop_last_assistant_text`) so the three registered gates cannot drift:

| Field | On `Stop` | On `SubagentStop` |
| ------- | ----------- | ------------------- |
| `transcript_path` | the session's | the **parent** session's — reading it here grades the wrong conversation |
| `agent_transcript_path` | absent | the subagent's own, in a nested `subagents/` folder — the **only** transcript this hook will read on the event |
| `last_assistant_message` | final text, ahead of the lagging transcript | same, for the subagent |

`isSidechain` markers are dropped while the agent transcript is parsed: every
event in a per-agent file belongs to that agent, and the filter that keeps a
subagent's events out of the MAIN transcript would otherwise empty the turn
and pass every subagent silently.

**No fallback to the parent.** When the agent transcript is missing or
unreadable — not yet flushed, or a payload that carries no such key — the hook
reads *nothing* and passes. Falling back to `transcript_path` was the first
draft and it reinstated the defect this registration removes: the turn came
from the parent while the claim came from the subagent's
`last_assistant_message`, so a subagent that ran nothing and merely repeated a
number from the parent's output cleared the evidence check against evidence it
never produced. Grading one conversation's claim with another's evidence is
worse than not grading it.

Unchanged by the registration: `stop_hook_active` still ends the re-entrant
loop, and an unreadable transcript or an empty turn still passes. A claim is
never graded against evidence this run did not read.

Not measured live — see [`RUNTIME_CONSTRAINTS.md` entry 7](../../../RUNTIME_CONSTRAINTS.md).

### Tests

```bash
bash tests/hooks/preflight-gate/test_merge_gate_claim.sh
```

50 cases: English/Korean claim without evidence (advisory), claim with `gh`
evidence / GitHub MCP evidence (silent), neutral message (silent), negated claim
(silent), future intent (silent), strict mode (decision: block), bypass (silent),
`stop_hook_active` loop guard (silent), worktree-cleanup claim (advisory),
missing transcript (fail-open), malformed JSON (fail-open), plus the #656
applied-kind family: EN/KO applied claims without evidence (advisory),
**state-only `gh pr view --json state` does NOT clear applied** (advisory — the
incident shape), merge-base / baseRefName evidence (silent), negated applied
(silent), branch-token-only and applied-token-only lines (silent), mixed
merged+applied with state-only evidence (advisory carrying reachability
guidance), applied strict mode (decision: block), and 4 review-fix
regressions: `grep baseRefName` does not clear, long-flag
`branch --merged --contains` clears, `without incident` does not suppress,
`released the lock` prose stays silent, baseRefName-only query does not
clear (codex P2), applied-only advisory drops the (false) generic
"no fresh state query" sentence when a state query exists (CodeRabbit).

Plus 11 negative-polarity persistence cases (#869): motivating incident
verbatim (KR still-open + no-loss, zero re-query → advisory), cleared by a
matching-number `gh pr view 864` / GitHub MCP `pullNumber: 864` query
(silent, 2 cases), NOT cleared by a different-number query (advisory — the
per-number specificity guard), EN "has not been merged" without/with a
matching query (advisory / silent), "no commits lost" phrasing (advisory),
two numberless claims EN/KR that stay silent (deliberate scope narrowing),
a mixed message where a generic query clears a "merged" claim but does NOT
clear an unchanged claim for a different number (advisory — number
specificity), and strict mode on an unchanged-only claim (decision: block).
