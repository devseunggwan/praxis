# Ethos

Why praxis exists. The values and principles that gate every skill, hook, and
manifest in this repository. Implementation choices (`DESIGN.md`) and component
graph (`ARCHITECTURE.md`) descend from these — they do not override them.

## Design Principles

- **CLAUDE.md is the interface**: no config files — project instructions define routing
- **SRP per skill**: each skill has one responsibility
- **Discipline over convenience**: Iron Laws gate each phase, no skipping

## Autonomy vs Convention

| Domain | AI authority | Example |
| --- | --- | --- |
| **Problem exploration** | Active judgment expected | Hypothesis choice, debug direction, falsification strategy, tool selection |
| **Convention** | Follow as defined; no autonomous override | Issue creation path, branch/worktree workflow, external-mutation tool layer, code patterns |

### Key principles
1. **Convention authority is not delegated.** Rules represent trade-offs already made by the team; the agent does NOT re-evaluate them at runtime.
2. **Scale is not an exemption.** "Too small to follow the workflow" == "too big to follow it" — both claim authority over the rule's scope.
3. **Disclosure is not compliance.** Telling the user about a bypass before doing it does not authorize it. Explicit per-action approval required.
4. **Hook blocks are signals, not failures.** Follow the suggested fallback, do not invent a workaround.
5. **Delegating a workaround is inventing one.** Principle 4 binds the agent's own hands; it does not license handing the user a menu of ways around the block instead. The one route the agent MAY relay is the gate's own — the literal `Bypass (if truly needed): <VAR>=1 with a one-line reason comment explaining why` line the blocking hook printed (`hooks/_lib/block_message.py`). That is praxis' designed escape hatch, and withholding it would hide praxis' own affordance from the person the gate is protecting — though relaying it authorizes nothing on its own, because principle 3 still requires the user's explicit approval of the action itself. Everything the agent *originates* is forbidden: asking the user to add a permission rule, walking them through a `.claude/settings.json` edit, offering to move the file out of the guarded path, or proposing any mechanism the gate's message did not itself offer. The test is authorship, not approval — the user saying yes to an agent-originated route permanently widens the guard for every later session, so the line sits at who proposed the route. See [`docs/hook/RULE-BACKSTOP-GAPS.md`](docs/hook/RULE-BACKSTOP-GAPS.md) gap #4: the prose and menu lanes have no hook backstop (issue #1009); the follow-up `Edit`/`Write` to a settings file is announced by `settings-path-advisory` (issue #1337), which asks the agent to say who asked for the change.

## Hook Ethos

Hooks exist because text rules in CLAUDE.md or memory entries alone have
historically failed at the moments they were needed most. These three
principles govern *whether* a hook should exist at all, and *what* it is
allowed to do to the user's session. The mechanisms by which a hook
implements these principles live in [`DESIGN.md`](DESIGN.md).

- **Spec defines, hook enforces.** Each hook is the structural enforcement of
  a rule that already exists in `CLAUDE.md` or a memory entry. Memory-based
  feedback alone has historically failed (≥5 recurrences) — hooks replace the
  memo when the pattern proves recurrent.
- **Fail-open on infrastructure errors.** Missing `jq` / `python3`, malformed
  JSON stdin, unreadable transcript, unknown tool name → exit 0. The hook
  never breaks Claude Code; it only nudges.
- **No agent-attachable bypass for high-stakes gates.** `pre-merge-approval-gate`
  intentionally has no marker; `completion-verify` and `retrospect-mix-check`
  same. Bypass marker (`# side-effect:ack`, `# title-length:ack`) exists only
  where the false-positive cost outweighs the silent-bypass risk.
- **Every hook has a sunset date.** `review_by` in the manifest fails CI once
  it passes; a hook survives by re-audit, not by default — rules in
  [`CONTRIBUTING.md → Evidence and sunset review`](CONTRIBUTING.md#evidence-and-sunset-review).

### Claims that terminate in prose

Every hook fires on a tool call. An evidence claim whose only carrier is a
sentence — "all three surfaces below confirm it", "the run printed the success
string" — emits no tool call, so there is nothing for a `PreToolUse` or `Stop`
hook to intercept: the block the sentence points at arrives through a tool the
gate would have to trust, and the misreading happens in the line written above
it. For this class **the discipline is the whole remedy, and no gate is
coming.** The *Own green check and SUT comment are not evidence* rule below
already covers the individual failures; it was in force, and the failures
happened anyway, in prose. A further restatement that implies enforcement exists makes
the gap harder to see rather than smaller, so the class is named here instead.
A schema check on the evidence table is the same failure with an extra step —
it would confirm the shape of a block whose sentence is the part that lied.

The only surface left is compose time. Before pasting an evidence block, answer
both; neither needs a tool.

1. **Who authored — and who triggered — the thing that produced this output?**
   A success string printed by the change under test is the system authoring
   its own oracle: the predicate and the claim it supports share a hand. A run
   the author started themselves is not organic traffic, and does not discharge
   a verification anchor that asked for it.
2. **Does the count in my sentence match the number of blocks below it?** A
   genuine, unedited, faithfully transcribed block still misleads when the
   sentence above it claims three surfaces and two are pasted — and the missing
   one is reliably the surface that was hardest to verify.

## Rules praxis carries

Hook specs and skills cite the rules below by name. They started as prose in
the author's always-loaded `~/.claude/CLAUDE.md`; most failed there at the
moment they were needed and were then given a structural backstop in this
repository — which makes praxis, not that private file, the place a reader
can check what a rule says. One sentence each; the last column lists the
hooks and skills whose own spec cites the rule (a row marked *none* has no
backstop yet).

| Rule | What it requires | Carried by |
| --- | --- | --- |
| **Git Commit & Title Rules** | `type(scope): description`, title ≤ 50 characters, lowercase, no trailing period — [`CONTRIBUTING.md` → Commit conventions](CONTRIBUTING.md#commit-conventions) | `commit-title-format-check`, `commit-title-length-check` |
| **Issue-Driven Worktree Workflow** | Every code change lives in its own issue, branch, and worktree; nothing is edited on `main` / `dev` / `prod` | `pre-edit-protected-branch-guard`, `worktree-edit-gate` |
| **Pre-Merge Reporting** | Before asking to merge, brief the user on what changed, what review found, and how it was verified; a trivial PR may use a two-line report | `merge-briefing`, `pre-merge-approval-gate`, `block-manufactured-action-menu`, `momentum-rule-retrieval-gate` |
| **No Approval Transfer Across Companion PRs** | Approval of one PR or action never carries to a sibling; each mutation gets its own ask | `pre-merge-approval-gate`, `momentum-rule-retrieval-gate`, `external-write-falsify-check` |
| **Executing actions with care** | A shared-state mutation is confirmed per action, even after a generic go-ahead | `block-manufactured-action-menu` |
| **Verification Before Completion** | The verification command runs in the same turn as the completion claim it supports | `completion-verify`, `completion-signal-gate` |
| **Information Accuracy** | Layer 2: a data-driven claim shows the command output first; Layer 3: own-authored content is verified like external content (the *author-exempt verification trap*); a checkmark is a citation to something read this session | `count-assertion-verify`, `source-citation-probe-gate`, `artifact-verdict-evidence-gate` |
| **Own green check and SUT comment are not evidence** | A success string printed by the change under test, or a run the author started, does not discharge a verification claim | `approval-premise-reread-gate`, this file (above) |
| **Loaded ≠ Retrieved** | A rule or value being in context is not the same as retrieving it at the moment of use; external enum / literal / catalog names come from a verified source, never from a naming pattern | `external-api-literal-trigger`, `momentum-rule-retrieval-gate`, `block-ask-end-option` |
| **Falsification Gates** | Before acting on an analysis, ask "if this is wrong, what observation should be missing?" and look for it | `caller-probe-gate` |
| **Output-Block-Level Falsification Gate** (also *Self-Falsify Before Recommendation Lock*) | Before surfacing a `(Recommended)` option or a self-authored proposal block, run a falsification test on its premise and record it as a `Falsified:` line; if an invalidating artifact exists, stop | `output-block-falsify-advisory`, `pre-output-falsification-gate`, `completion-signal-gate`, `codex-review-wrap` |
| **One-Probe-Before-Action Gate** | Run one probe before repeating a status check or re-issuing an action | `pre-output-falsification-gate` |
| **External-Surface Write Requires Falsification** | Text posted to a PR, issue, Slack, or Notion is verified fact, not hypothesis; retraction costs more than the check | `external-write-falsify-check`, `codex-review-wrap` |
| **Every `$` block is a transcription, never a composition** | A command line shown above an output block is the line that produced it | `composed-command-gate` |
| **External Discussion Fidelity — lock-boundary re-fetch** | Live state (PR state, a Slack thread) is re-fetched at the boundary where a decision depends on it | `pr-state-refetch-gate` |
| **Read-only prod calls auto-proceed** | A read-only production fetch needs no approval; anything that captures or forwards a secret does | `secret-print-redaction-advisory` |
| **Rule Conflict Precedence — CLAUDE.md over Skills** | When a skill body and a CLAUDE.md rule disagree, the rule wins | `block-ask-end-option` |
| **Look Up the Answer Before You Offer a Menu** | A choice that a registry, convention, or sibling implementation already settles is looked up, not put to the user | *none* — deliberately unhooked; `docs/hook/RULE-BACKSTOP-GAPS.md` gap #5 (issue #1009) |
| **Implementation-approach review before issue creation** | Scope, target repos, PR count, and verification plan are approved before issues are created for them | `codex-review-wrap` |
| **Convention Survey Before Design** | Survey at least two sibling implementations before designing a new hook or skill | [`DESIGN.md` → Adding a new hook](DESIGN.md#adding-a-new-hook), [`CONTRIBUTING.md`](CONTRIBUTING.md#adding-or-modifying-a-hook) |
| **Suppression needs a reason and approval** | Skipping a verification flag (`--no-verify` and kin) requires a stated reason **and** per-instance user approval — neither alone | `verify-commit-flag-override` |
