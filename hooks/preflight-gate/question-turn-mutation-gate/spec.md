# PreToolUse Question-Turn Mutation Gate

Supported hosts: claude

Reference: [Autonomy vs Convention, ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/question-turn-mutation-gate/impl.py` asks before a
mutating call made in a turn that the user opened with a question or a
challenge (issue #1486).

## Why this exists

Observed sequence, from one session transcript:

1. The user challenged the agent, in effect "you are supposed to follow these
   rules, so what is this". The message carried no question mark.
2. The agent replied with one sentence and, in the same turn, issued eight
   tool calls, four of them state-changing: a worktree add, an upstream
   change, a branch reset, a dependency install.
3. The ninth call ran a CLI that opened a pull request.
4. The user interrupted with "answer first".

Every other gate keys on what a call is. A mutation that is individually
allowed is still wrong when it is the reply to a question. A check for "no text
since the user message" would not have caught this: the one-line
acknowledgement satisfies it.

## Trigger

All three, the cheapest first:

| # | Condition | Source |
| --- | --- | --- |
| 1 | The call is not made inside a subagent (`agent_id` absent from the payload) | payload |
| 2 | The call is mutating: `is_mutating_call` from `hooks/_lib/_mutating_call.py` | `tool_name`, `tool_input` |
| 3 | The human message that opened the current turn reads as a question or a challenge (see *Classifier*) | `transcript_path` |

All three → `permissionDecision: "ask"` with the reason "this turn was opened
by a question; answer it and end the turn before acting". Every mutating call
in that turn asks, not only the first: a text reply written in between does
not change what the turn was opened by.

A subagent is skipped because its turn is opened by its delegator, not by the
human; the main transcript's latest human message says nothing about it.

## The opening message

`read_last_user_message(transcript_path, human_only=True)`: the most recent
user-role record with text, skipping tool results, sidechain records, and
records the host injected rather than the human typed (`isMeta` skill bodies
and slash-command expansions, `isCompactSummary`, an `origin.kind` other than
`human` such as a task notification). A skill loaded mid-turn therefore does
not lift the gate, and only the next typed message does.

## Classifier

Tagged blocks (`<system-reminder>...</system-reminder>`, command echoes) and
fenced code are dropped first; they carry no ask of the user's own. Then only
the **last sentence** is read, because that is where a message puts what it
asks for: "why did it fail? fix it." is an instruction, "fix it. why did it
fail?" is a question. In order:

| Step | Rule | Result |
| --- | --- | --- |
| 1 | A challenge phrase anywhere in the sentence (table below) | question |
| 2 | A request form: English opener `please`, `can you`, `could you`, `would you`, `will you`, `can we`, `could we`, `let's`; Korean ending `줘`, `주세요`, `줄래`, `해봐`, `하자`, `부탁` ... | instruction |
| 3 | Ends with `?` or `？` | question |
| 4 | English interrogative opener: `why`, `what`, `how`, `who`, `whom`, `whose`, `where`, `which`, `is`, `are`, `was`, `were`, `did`, `does`, `has`, `have` and their negated forms | question |
| 5 | Korean interrogative ending: `뭐야`, `뭐지`, `인가요`, `인지`, `는지`, `나요`, `까요`, `습니까`, `냐`, `거야`, `건가`, `거지`, `어때`, `맞나`, `아닌가`, `않나`, `었나`, `는건데` ... | question |
| 6 | Anything else | instruction |

Challenge phrases: `what is this`, `what's this`, `what the`, `why did you`,
`why didn't you`, `why are you`, `why would you`, `supposed to`, `i told you`,
`didn't i`, `i already said`, `answer first`, `answer me`, `이게 뭐`, `뭐하는`,
`했잖아`, `라고 했`, `하라고 했`, `말했잖`, `먼저 답`, `답부터`, `대답부터`,
`어쩌자는`, `어쩌라는`, `뭐하자는`.

Deliberately absent, each because it opens an instruction as often as a
question: `do`, `can`, `could`, `will`, `should`, `when` as English openers;
`했어`, `있어`, `없어` (a statement and a question differ only by intonation);
a bare `니까` (`하라니까` repeats an order).

The full lists are the module constants in `impl.py`; the table above is a
reading aid, not a second source.

### Other languages

`PRAXIS_QUESTION_TURN_MARKERS` adds comma-separated markers, matched
case-insensitively as substrings of the last sentence, for a locale the
built-in English and Korean lists do not cover. It can only add asks, never
remove one, so it widens a detector rather than opening a bypass.

## Mutating calls

`is_mutating_call` is shared with `approval-premise-reread-gate`. It is
fail-closed: a Bash command is read-only only when every segment is a
recognised read-only invocation (`git status`, `grep`, `kubectl get`, ...), so
an unrecognised command asks. See *Known limitations* 1 for what that costs
here.

## Lifting

The next human message opens a new turn. Nothing else lifts the gate: no
marker, no state file, no count of approved calls.

## Fail-open

A missing or unreadable transcript, a malformed payload, or a transcript with
no human message yet is silent.

## Tier

`ask`, with no bypass marker or bypass env. Approving the ask is the user's
decision to let an action ride on a question; an agent-attachable token would
let the agent make it.

## Hosts

Claude only. Cursor's `preToolUse` accepts `ask` but does not enforce it, and
Codex reports an `ask` hook as failed and lets the call through (see
`cross-tool-reroute-gate/spec.md`, *Known limitations* 4). The transcript
reader also parses the Claude Code record shape only.

## Recurrence evidence

Replay over every local Claude Code transcript (864 main-session files, 9,942
human turns), classifying each turn's opening message and running
`is_mutating_call` on each tool call made before the next human message:

| Measure | Count |
| --- | --- |
| Human turns classified as question or challenge | 1,371 |
| Of those, turns with at least one mutating call (gate fires) | 1,071 in 301 sessions |
| Asks those turns would raise (every mutating call) | 9,312 |
| Fire turns holding at least one clear write (Edit/Write, MCP write, or a Bash write verb such as `git commit`, `rm`, `gh pr create`, a redirect) | 561 |

The originating incident is in the corpus: its challenge ends in `어쩌자는건데`
("so what are you going to do"), carries no question mark, and the gate asks on
its first mutating call, the worktree add.

The replay script is not committed: it imports this `impl.py` and
`_mutating_call.py` and walks each transcript once.

## Known limitations

1. **Probes ask too.** `is_mutating_call` treats any Bash command it does not
   recognise as mutating, so `cd <repo> && git log`, `sed -n`, and an inline
   `python3 -c` read ask as well. In the replay, 510 of the 1,071 fire turns
   held no clear write at all, and about 70% of the 9,312 asks landed on such
   probes. Narrowing it means either widening the shared read-only allowlist
   or matching a write-verb list here instead; both are open.
2. **Every call asks.** A turn with eight mutating calls asks eight times.
   Asking only until one mutating call in the turn has run would cut the
   replay's 9,312 asks to 1,071.
3. The classifier is lexical and reads one sentence. A question that ends in a
   pasted log line, or an instruction that ends in a rhetorical question, is
   misread. Korean `는건데` also closes some statements
   ("the Slack thread is what I meant").
4. How a headless run (`claude -p`) resolves the ask was not tested. A prompt
   to an automated worker that ends in a question would reach it on every
   mutating call.
