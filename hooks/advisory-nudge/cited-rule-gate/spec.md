# PreToolUse Cited Rule Gate

Supported hosts: claude

Reference: [Autonomy vs Convention (ETHOS.md)](../../../ETHOS.md#autonomy-vs-convention)

`hooks/advisory-nudge/cited-rule-gate/impl.py` warns (or, in strict mode,
asks) before a mutating call when the assistant text written since the
previous tool call names no rule section (issue #1487).

## Why this exists

The rule set opens with "before any action, name the section that governs it;
if you cannot, re-read it first". Nothing enforced it. In the session behind
the issue, no assistant text carried a rule-citation line until late in the
session, when the user pointed it out. The violations in that stretch (a
direct commit to a protected branch, a push attempt to it, a recommendation to
bypass a push guard, a fallback to an unapproved virtualenv) were all caught by
the user, and each maps to a section the agent could name once asked.

`momentum-rule-retrieval-gate` covers merges, dispatches and force-pushes
only, and the other gates check the content of specific calls. Nothing checked
that retrieval happened at all before an ordinary mutation.

## Trigger

All three:

| # | Condition | Source |
| --- | --- | --- |
| 1 | The call is mutating | `is_mutating_call` in `hooks/_lib/_mutating_call.py`, shared with `approval-premise-reread-gate` |
| 2 | The current tool_use is found in the last 400 transcript lines | `tool_use_id` in the payload |
| 3 | The window text holds no citation line naming a known heading | below |

**Window.** The assistant text blocks between the last user record before the
current assistant message and the current tool_use. Thinking blocks and
sidechain records are not part of it. A tool result of the current message's
own tool_uses does not close the window, so one citation written before a
parallel batch covers every call in the batch.

**Citation line.** A line that starts, after list bullets, quotes and emphasis
(`-`, `*`, `+`, `>`, `_`), with a configured prefix, `Rule:` by default,
followed by a section name. A prefix in the middle of a line does not count.

**Known heading.** The name must equal a markdown heading of a readable rule
file, ignoring repeated whitespace and the heading's trailing inline code
spans, so `Rule: Scope Discipline` matches `## Scope Discipline` followed by an
`[E1]` code span. Headings inside code fences are not headings. One line may
cite several sections separated by `·`, `;`, `|` or `,`; the whole remainder
is tried first, because a heading may itself hold a comma. When no rule file
is readable, any non-empty name passes, since there is nothing to check it
against.

## Configuration

| Env var | Effect |
| --- | --- |
| `PRAXIS_CITED_RULE_PREFIXES` | Comma-separated citation prefixes, for a locale that writes its own word. Default `Rule:` |
| `PRAXIS_CITED_RULE_FILES` | `os.pathsep`-separated rule files whose headings are accepted. Replaces the default: `~/.claude/CLAUDE.md`, then `CLAUDE.md` and `AGENTS.md` in the session `cwd` |

## Modes

| Env var | Effect |
| --- | --- |
| (unset) | Advisory: stderr text + `additionalContext`, exit 0. The call proceeds. |
| `PRAXIS_CITED_RULE_STRICT=1` | Ask: `permissionDecision: ask` with the same text. |
| `PRAXIS_HOOK_BYPASS_CITED_RULE=1` | Silent. |

## Tier

Advisory by default, on the corpus replay below. The issue asked for the fire
rate before choosing between ask and advisory. Every mutating call in the
local corpus would fire, because no assistant text in it carries a citation
line, so an `ask` default would put a median of 48 prompts into every session.
An approval asked that often is approved without reading, which removes the
point of asking. Strict mode is there for an operator who has adopted the
citation convention and wants the stop.

## Recurrence evidence

Replay over every local Claude Code transcript (`~/.claude/projects/*/*.jsonl`,
868 files), evaluating the gate at each main-chain tool_use against the 400
transcript lines before it, with the current rule files as the heading source:

| Measure | Count |
| --- | --- |
| Tool uses | 181,882 |
| Mutating per `is_mutating_call` | 142,391 (Bash 133,903, file edit 7,586, MCP 902) |
| Carrying any `Rule:` citation line | 0 |
| Would fire | 142,391, in 747 of 747 sessions with a mutation |
| Fires per session | median 48, p90 516, max 4,907 |

Positive control for the zero: a scan of every record containing `Rule:`
found none in an assistant text block at the start of a line; the four
assistant occurrences are mid-line.

About 9% of the Bash fires come from the classifier reading `cd`, `export` or
`set` as mutating: removing segments that are only those commands leaves
122,345 of the 133,903. Most of the remaining gap to real mutations is
commands the allowlist does not know (`sed -n`, `npx tsc`), which the shared
classifier asks about by design.

## Fail-open

- malformed stdin, a non-dict `tool_input`, or a missing `tool_use_id` or
  `transcript_path` → exit 0
- an unreadable transcript → exit 0
- the current tool_use absent from the last 400 lines → exit 0
- any uncaught exception → swallowed by `fail_open`, exit 0

## Known limitations

- **The check is on form, not on fit.** A real heading that does not govern the
  call passes. Matching a section to a call is judgement the hook cannot make.
- **Heading source is the current files.** A heading renamed since a transcript
  was written reads as unknown in the replay; at runtime the files are read on
  every call.
- **A long window can be cut.** When the text since the previous tool call
  runs past 400 transcript lines, a citation above the cut is not seen and the
  hook fires.
- **Claude host only.** The window is read from the Claude Code transcript
  shape by `tool_use_id`; Codex and Cursor transcripts are not read.
- **Ancestor rule files are not read.** Only `CLAUDE.md` and `AGENTS.md` in
  the session `cwd` itself are, besides the user file.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| --- | --- | --- |
| `momentum-rule-retrieval-gate` | merge, dispatch, force-push; surfaces the rules itself | Both can fire on a merge; that one names the rules, this one asks the agent to |
| `approval-premise-reread-gate` | production-marked mutations; asks for the approval premise | Same classifier, different question |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_cited_rule_gate.sh
```
