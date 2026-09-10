# Stop Prose Option-Menu Advisory

Supported hosts: all

`hooks/completion-verify/prose-option-menu-advisory/impl.py` fires on the Stop
event and advises when the turn's last assistant message hands the user an
option menu written as ordinary prose — outside `AskUserQuestion`, and so
outside every gate that surface carries.

## Decision predicate

Advise when **all** of these hold:

1. Two option-labelled lines — `(a)` / `(b)` / `(c)`, optionally bulleted with
   `-` / `*` or wrapped in `**` — sit within 10 lines of each other in the last
   assistant text.
2. That text carries an explicit demand that the reader choose (`어느 쪽`,
   `선택해 주시`, `정해 주시`, `택1`, `which one`, `pick one`, …).
3. The turn contains no `AskUserQuestion` tool call.

Advisory only: `{"systemMessage": ...}`, exit 0. It never blocks. Bypass:
`PRAXIS_PROSE_OPTION_MENU_BYPASS=1`.

The 10-line window is what separates a menu from an enumeration. Two `(a)` /
`(b)` labels far apart in a long message are usually labelling unrelated
things; adjacent, they are options.

## Why this exists

`block-manufactured-action-menu` fires on `PreToolUse(AskUserQuestion)`, so it
can only see a menu that became a tool call — and it catches the *inverse* case
there, a menu whose options are actions the agent should simply have taken. A
menu written as assistant text emits no tool event at all, so no PreToolUse
hook is positioned to read it. The gates the tool-call path carries — the
`Falsified:` line on a recommendation among them — are then skipped by
construction rather than by decision.

The pattern this watches is generation 5 of a recorded one (`recurrence: 5`):
a question the project already answers gets handed back as "(a) … (b) … which
way?". Its generation-4 note is what established that the Stop surface reads
assistant text at all — 7 of the Stop hooks already judge on it — so the
absence of enforcement here was never a structural impossibility, only an
unbuilt hook.

## Measured corpus

Across 439 local sessions, the predicate matches **102 messages in 66
sessions**. Of those, 34% carry a recommendation and only **3%** carry a
`Falsified:` line — the line the tool-call path's own gate would have required
before that recommendation was surfaced. That gap is what the advisory names.

## Why advisory rather than a block

Legitimate menus are inside those 102. When the cost, risk, or scope of an
irreversible action is genuinely the user's call, the menu is the correct
output, and no hook at this layer can separate that from a settled question
handed back — the discriminator is whether an answer exists elsewhere, which is
not observable from the message text. Blocking would refuse the legitimate
half, so the hook recalls the three self-checks instead:

1. Is the answer already written in `CLAUDE.md`, a project instruction, a
   registry, or a sibling implementation? Then execute that path and leave one
   line naming what decided it.
2. Does one option skip a MANDATORY skill or step? That is a bypass wearing the
   shape of a choice — delete it.
3. Was a literal the user already gave this session looked up in the transcript
   before being asked for again? A post-compaction "unknown" is
   session-history-unknown, not world-unknown.

## Relationship to the sibling gates

- `preflight-gate/block-manufactured-action-menu` — same subject, disjoint
  surface (tool call vs. prose) and inverse case (actions-as-options vs. a
  settled decision handed back).
- `advisory-nudge/pre-output-falsification-gate` — requires an evaluative
  marker on an `AskUserQuestion`; a prose menu reaches neither condition.
- `completion-verify/proposal-premise-gate` — the closest sibling in shape: a
  Stop-event advisory reading the last assistant text. It judges the *premises*
  inside a proposal; this one judges whether a *decision* was handed back.

## Fail-open

Malformed or missing stdin, an unreadable or absent transcript, no last
assistant text, `stop_hook_active` (re-entrancy), and any uncaught exception
all exit 0.
