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
5. **Delegating a workaround is inventing one.** Principle 4 binds the agent's own hands; it does not license handing the user a menu of ways around the block instead. The one route the agent MAY relay is the gate's own — the literal `Bypass (if truly needed): <VAR>=1 with a one-line reason comment explaining why` line the blocking hook printed (`hooks/_lib/block_message.py`). That is praxis' designed escape hatch, and withholding it would hide praxis' own affordance from the person the gate is protecting — though relaying it authorizes nothing on its own, because principle 3 still requires the user's explicit approval of the action itself. Everything the agent *originates* is forbidden: asking the user to add a permission rule, walking them through a `.claude/settings.json` edit, offering to move the file out of the guarded path, or proposing any mechanism the gate's message did not itself offer. The test is authorship, not approval — the user saying yes to an agent-originated route permanently widens the guard for every later session, so the line sits at who proposed the route. See [`docs/hook/RULE-BACKSTOP-GAPS.md`](docs/hook/RULE-BACKSTOP-GAPS.md) gap #4: no hook backstops this today (issue #1009).

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

### Claims that terminate in prose

Every hook fires on a tool call. An evidence claim whose only carrier is a
sentence — "all three surfaces below confirm it", "the run printed the success
string" — emits no tool call, so there is nothing for a `PreToolUse` or `Stop`
hook to intercept: the block the sentence points at arrives through a tool the
gate would have to trust, and the misreading happens in the line written above
it. For this class **the discipline is the whole remedy, and no gate is
coming.** Rules already cover the individual failures (`Own-greencheck and
SUT-comment are not evidence`); they were in force, and the failures happened
anyway, in prose. A further restatement that implies enforcement exists makes
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
