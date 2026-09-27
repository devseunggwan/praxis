# Stop Relayed-Blocked-Command Gate

`hooks/completion-verify/relayed-blocked-command-gate/impl.py` runs on `Stop`.
It blocks when a `PreToolUse` hook **blocked a Bash command during this turn**
and the final assistant message proposes that the user run that same command.
A settings permission rule's refusal is not a hook block (§1 below).

Supported hosts: all

## Why this exists

A block stops one mutation. Handing the blocked command to the user stops
nothing: the mutation still happens, by other hands, and the hook built to
prevent it is spent. [`ETHOS.md`](../../../ETHOS.md#key-principles) principle 5
already forbids every bypass route the agent originates, and the only one it
lets the agent relay is the blocking hook's own `Bypass (if truly needed)` line.

`bypass-route-signal` watches the same principle, but it matches bypass-route
nouns (permission rules, settings files, env vars) and only records telemetry.
The command itself is not one of its nouns, so a menu whose first option was
"push it yourself" passed it silently.

The incident: a guard blocked `git push origin main`. The next message reported
the block, then offered as its recommended option:

```text
! git -C <path> push origin main
```

`!` is the host's prefix for a command the user runs in the session.

## What is detected

Three conditions, all required.

1. **A Bash call was blocked this turn.** The turn is read directly
   (`load_stop_turn`): a `tool_result` with `is_error: true` in a record whose
   `toolDenialKind` is `permission-rule`, resolved to the Bash `tool_use` of the
   same turn. There is no whole-transcript scan: only this turn's blocks
   matter, and the size-bounded rejection scan returns nothing on a transcript
   past 20 MB, which silenced the first draft of this gate on the very session
   it was built from. A user's own
   refusal (`user-rejected`) is out of scope: proposing that the user run
   something they just refused is not a bypass of anything. So is a settings
   permission rule, which the runtime records under the same kind but words as
   `Permission to use <tool> ...`: such a rule leaves the command to the user by
   design. An auto-mode classifier refusal carries its own kind
   (`automode-blocked`) and is never read.
2. **The final message carries that command in code.** Each mutating segment of
   the blocked command (read-only segments are dropped with
   `_mutating_call.bash_is_readonly`) reduces to a *signature*: its positional
   tokens in order, without flags, the argument right after a flag, env
   assignments, redirects or a leading `!`. A code line (fenced, or an inline
   code span) matches when the signature is an ordered subsequence of its
   tokens. The relayed line in the incident is not a substring of the blocked
   one, because it adds `-C <path>`; the subsequence test is what catches it.
3. **The line is framed as the user's to run.** Either it starts with `!`
   (`! git ...` or the glued `!git ...`), or a user-run phrase such as
   `직접 실행해 주세요`, `입력하시`, `run it yourself` or `from your terminal`
   sits in the line's *scope* and is not followed by a negation (`지 않`,
   `지 마`, `not`). The scope depends on what the code line is:
   - A **command line** (a fenced line, or a line that is nothing but the code
     span, after any list marker, quote or `$` prompt) takes its paragraph
     (blank lines outside a fence separate paragraphs). When that paragraph
     carries no frame, the nearest non-empty paragraph above is read too, but
     only when it ends with a colon (`:` or `：`): `Run this in your
     terminal:`, a blank line, then the fence is one utterance, and the colon
     is what says so.
   - A **code span inside prose** takes only the sentence around it
     (sentences end at `.`, `!`, `?` or their CJK forms followed by
     whitespace; punctuation inside code spans does not count). So in
     `` `git push origin main` was blocked. Please run `gh pr create` instead
     from your terminal. `` the frame in the second sentence does not reach the
     span in the first, which reports rather than relays.

   A bare `직접 실행` is not a frame: agents use it to narrate their own runs.
   Likewise `터미널에서` / `프롬프트에` alone only name a place (`제가
   터미널에서 확인했더니 ...`); they frame a relay only with a request form
   (`실행/입력/치/넣/돌려` + `해 주세요`, `하시`, `하세요`) within two words.

Exempt: a code line carrying an env assignment whose name appears as `NAME=` in
the refusal text of a hook that blocked this turn. That is the blocking hook's
own bypass line, the one relay ETHOS allows, and it has to be relayed: hooks
read the variable from their own process environment, so a prefix on the
agent's command never reaches them.

## What is emitted

A Stop `{decision: block}` whose reason names the relayed line, says why it is
a bypass, and points at this spec. English leads, a Korean label follows.

## Parsing guarantees (fail-open)

- Malformed payload, missing transcript, `stop_hook_active`: silent.
- A turn the loader cannot bound (no turn boundary within its 8 MB tail) reads
  as empty, so the gate is silent. This gate blocks, so it does not block on
  evidence it did not read.
- Any exception: `@fail_open`.
- Bypass: `PRAXIS_RELAYED_BLOCK_BYPASS=1`.

## Measured on a transcript corpus

Replayed over 859 local session transcripts before adoption. 1114 turns had a
Bash call blocked by a hook, and 12 final messages matched. Classified from the
refusal text and the relayed line (four also read in full context), every match
handed a hook-blocked mutation to the user: a push to a protected branch, a
`--no-verify` commit, a plain commit offered because "the hook only fires on the
agent's calls", closes of resources the agent did not own, and merges the merge
briefing gate had stopped. The last kind is kept on purpose: relaying the merge
is how the briefing gets skipped.

Eight earlier matches were false positives and shaped the rules above: six
relayed the blocking hook's own bypass variable, and two were an agent
narrating that it had "run it directly". Five more followed a settings
permission rule, which the first exclusion now drops.

## Known limits

- Turn scope. A block in one turn relayed in a later turn is not seen.
- A fenced relay whose introduction ends without a colon and sits above a
  blank line (`Run this in your terminal.`, blank, fence) is not seen: the
  paragraph above is carried only on a colon. Without the blank line, or with
  a `!` prefix, it is.
- A report of the block and a user-run phrase in one *sentence* (`\`git push
  origin main\` was blocked, so run it from your terminal`) reads as a relay.
  It is one: the sentence hands the command over.
- Sentence splitting is by terminator, so a user-run phrase after an
  abbreviation's period (`e.g.`) in the same sentence is read as the next
  sentence.
- Signatures drop the argument after every flag, so a blocked
  `git push -f origin main` and a relayed `git push origin main` match. That is
  the intended direction: the relay is still the blocked mutation.
- Bash only. A blocked MCP or file-edit call has no command a user could run.

## Relationship to sibling hooks

- `bypass-route-signal`: same principle, different surface. It counts bypass
  nouns and never blocks; this gate matches the blocked command and blocks.
- `denied-action-report-gate`: asks that a block be reported. This gate does not
  object to the report, only to the relay.

## Tests

`tests/hooks/completion-verify/test_relayed_blocked_command_gate.py`: signature
extraction, the incident shape, framed and unframed variants (colon carry,
sentence scope, place nouns, prohibitive negation, the glued `!git`), the
sanctioned bypass line, turn and kind scoping, and end-to-end runs through the
binary.
