# PostToolUse Response-Language Drift Nudge

`hooks/advisory-nudge/response-language-nudge/impl.py` runs on `PostToolUse`
for every tool (manifest matcher `.*`) and, when `PRAXIS_RESPONSE_LANGUAGE`
names Korean, checks the last assistant text block of the current turn for
Hangul ratio. Below the threshold, it emits one
`hookSpecificOutput.additionalContext` nudge; at or above it, and whenever
the env var is unset, it is silent.

## Why this exists (issue #1476)

A one-session measurement (268 text blocks) found the response-language
drift concentrated in **narration between tool calls**, not in the messages
a Stop hook grades: `AskUserQuestion` bodies in the same stretch stayed on
the specified language while the prose around tool calls did not. Neither
existing reachable surface catches that shape:

- **Stop** only sees the turn's final message — every drifted narration line
  in between has already reached the user by the time Stop fires.
- **PreToolUse deny** can block the *next* tool call, never the text that
  already went out ahead of it.

`PostToolUse` is the earliest point after a drifted block where a hook can
still speak — not before the drift is displayed (nothing reaches that), but
before the assistant writes its next line. This is the "사후 교정" surface
named in the issue body; `postcompact-context` (same env var) is the other
reachable surface, for the compaction boundary this hook does not see.

## Trigger criteria

The hook emits when **all** are true:

1. `PRAXIS_RESPONSE_LANGUAGE` is set to a non-blank value.
2. That value normalizes to Korean — `ko` / `kr` / `korean` / `ko-kr` /
   `ko_kr` (case-insensitive) or the literal `한국어`. Any other value is an
   unsupported language for this hook (YAGNI — issue #1476 scopes the ratio
   check to Korean only) and the hook is silent, not "no rule".
3. The payload carries `session_id` and a readable `transcript_path`.
4. The current turn (events since the last real user input) has a last
   assistant message with text content.
5. After stripping code fences, inline code, URLs, filesystem paths, and
   snake_case/camelCase identifiers, at least 20 non-whitespace characters
   of prose remain.
6. The Hangul-character fraction of that remaining prose is below `0.3`.
7. That assistant message's transcript `uuid` was not already nudged this
   session (dedup — see *State*).

## Scope (acceptance criteria)

Chat prose only. Commit messages, PR titles, and `tool_input` are never
read — only the current turn's last assistant text-content blocks. This
mirrors the issue's stated scope, not an incidental side effect of using the
transcript: the hook reads no other field of the payload for this purpose.

## Stripping and the ratio

Removed, in order, before the ratio is computed:

1. ```` ```code fences``` ```` (non-greedy, multiline)
2. `` `inline code` ``
3. `https://` / `http://` URLs
4. path-shaped tokens (`/…`, `./…`, `../…`, `~/…`, `C:\…`)
5. `snake_case` identifiers (a word carrying an internal underscore)
6. `camelCase` identifiers (lowercase-leading, an internal uppercase letter)

The ratio is `hangul_chars / non_whitespace_chars` over what remains. A block
that is entirely code or entirely stripped tokens lands under the 20-char
floor and is ignored rather than scored — dividing by a near-zero
denominator would otherwise produce a ratio that means nothing.

## State (dedup)

One JSON file per session at
`resolve_cache_file("response-language-nudge-<session_id>.json")`
(`PRAXIS_RESPONSE_LANGUAGE_NUDGE_FILE` overrides the path, test-only),
holding `nudged_uuids` — a bounded (64) list of assistant-message transcript
`uuid`s already nudged. The read-modify-write is serialized with
`state_lock` (the same contract as `second-failure-advisory`): two
`PostToolUse` events from ONE assistant message that issued several tool
calls in parallel must nudge once for the shared narration, not once per
tool call.

A message with no `uuid` (a shape this hook has not observed live) skips the
dedup check entirely and nudges unconditionally on every qualifying
`PostToolUse` event for that message — over-nudging on an unobserved shape is
the fail-open direction; silently going undetected on a real drift is not.

## Response format

Success path:

```json
{
  "continue": true,
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "[response-language-nudge] The last narration drifted from the specified response language (ko) — Hangul ratio 4% of the prose portion. Continue in ko for all user-facing prose, tool-call narration included. (...)"
  }
}
```

`hookEventName` echoes the event actually delivered (`payload.hook_event_name`,
falling back to `"PostToolUse"`), mirroring `second-failure-advisory` — the
harness accepts a reply only under the event it delivered.

Every other path (env unset/unsupported, malformed payload, missing
transcript, block too short, ratio at/above threshold, already nudged,
state-save failure, uncaught exception) is **silent** — no stdout, no
stderr, exit 0. The hook never blocks.

## Configuration

| Env var | Default | Scope | Effect |
| --------- | --------- | ------- | -------- |
| `PRAXIS_RESPONSE_LANGUAGE` | unset | config | Opt-in; must normalize to Korean for this hook to run at all (see *Trigger criteria*) |
| `PRAXIS_RESPONSE_LANGUAGE_NUDGE_FILE` | `~/.praxis/cache/response-language-nudge-<sid>.json` | test override | Dedup-state file path |

## Fail-open contract

- `PRAXIS_RESPONSE_LANGUAGE` unset, blank, or non-Korean → silent
- malformed JSON stdin → silent
- missing `session_id` / `transcript_path`, or transcript not a readable
  file → silent
- no assistant text in the current turn → silent
- remaining prose under 20 characters after stripping → silent
- Hangul ratio `>= 0.3` → silent
- message already nudged this session → silent
- state-file write failure → silent (no nudge; a lost write costs one
  missed nudge, never a duplicate one)
- uncaught exception in inner logic → swallowed, exit 0

## Host filtering

No `hosts` restriction: the `PostToolUse` event and `transcript_path`
payload field this hook reads are not Claude Code-specific in the way
`SessionStart`'s `compact` matcher is, so the manifest carries no `hosts`
key and the hook is delivered on every host the harness supports.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `postcompact-context` (SessionStart, matcher `compact`) | re-injects the same env var's value at the compaction boundary | Complementary — that hook addresses the boundary where the instruction is lost from context; this hook addresses drift that happens without any compaction at all |
| `second-failure-advisory` (PostToolUseFailure) | repeated-failure advisory, same `additionalContext` / state-lock / dedup shape | None functionally — this hook borrows its state-file and emission conventions, not its trigger |

## Known limitations

| Case | Behaviour |
| ------ | ----------- |
| A language other than Korean | Silent — no detector implemented for it yet (YAGNI, issue #1476 scope) |
| Transcript lag (the harness may not have flushed the very latest assistant text by the time `PostToolUse` fires) | The hook reads whatever the transcript holds at call time; a message written and not yet flushed is invisible to this event, same as every other transcript-reading `PostToolUse`/`PreToolUse` hook in this repo |
| An assistant message with no `uuid` | Dedup skipped for that message only; every qualifying tool call from it nudges |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_response_language_nudge.sh
```

Cases cover: env unset (silent, both hooks unaffected — shared fixture with
`postcompact-context`), non-Korean value (silent), Korean aliases
(`ko`/`KR`/`Korean`/`한국어`), compact re-injection carrying the language line
(cross-checked against `postcompact-context`), an English narration block
(nudge once), a Korean block and a code-only block (silent — positive and
negative control), a block under 20 chars after stripping (silent), and
dedup across two `PostToolUse` events sharing one assistant message `uuid`.
