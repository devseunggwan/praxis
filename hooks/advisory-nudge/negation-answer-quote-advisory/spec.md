# negation-answer-quote-advisory

Supported hosts: all

`hooks/advisory-nudge/negation-answer-quote-advisory/impl.py` runs on
`PreToolUse(Bash)` and `PreToolUse(mcp__.*)`. It fires a soft `ask` gate when an
external write is about to go out while the newest free-text
`AskUserQuestion` answer opens with a negation marker and nothing since has
quoted that answer back.

## Decision predicate

Ask when **all** of these hold:

1. The pending call is an external write, by the shared surface predicate in
   `_lib/_external_write_body.py`: `gh issue|pr comment|create|edit|review`, a
   write-method `gh api` on a comments/reviews endpoint, Slack send/post/update,
   or a Notion page write.
2. Within the scanned tail, an `AskUserQuestion` returned a **free-text** answer
   whose first token is a negation marker and which is not phrased as a
   question.
3. No assistant text block and no later `AskUserQuestion` question contains the
   answer's first 20 characters verbatim.

`ask`, never deny. Whether the chosen reading was right is the user's call and
this hook cannot judge it; what it checks is mechanical — whether the reading
was shown to the person before the write left.

Bypass: `PRAXIS_NEGATION_ANSWER_BYPASS=1`.

## Why this exists

Issue #1441. A free-text answer opening with a negation marker is a correction,
and a correction usually has more than one reading. In the recorded incident the
answer was `아니지 bump 만 언급해요 다른거도 있는데` — either "drop the lines that
are not about the bump" or "add the other PRs". The agent took the second,
re-asked with a question that presupposed it and quoted nothing, got `post`
back, and sent two messages to a shared channel where one had been asked for.
The user's next message was a strike.

No hook fired anywhere on that path. `external-write-falsify-check` and
`source-citation-probe-gate` scan the **outgoing** body; none of the
external-write hooks reads the **incoming** answer, so a misread correction
reaches the channel with nothing in between.

## Why free-text answers only

The runtime records the two answer kinds under different prefixes:

| Answer kind | Result prefix |
| ----------- | ------------- |
| Option picked | `Your questions have been answered: "<q>"="<label>". You can now continue…` |
| Free text | `The user answered: "<q>"="<text>". Read the answers carefully…` |

Only the second is a correction the agent had to interpret. An option label was
authored by the agent itself, so there is no reading to surface — keying on it
would fire on the agent's own words. Measured over 851 local transcripts: 2236
option answers, of which 6 open with a negation marker and all 6 are labels the
agent wrote (`아니오, 독립적인 버그픽스 (권장)`).

## Measured corpus

Scope: `~/.claude*/projects/*/*.jsonl`, deduplicated by real path — 851
transcripts, 8338 external writes, 95 free-text answers in 44 transcripts.

| Vocabulary | Armed | Advisories | Precision |
| ---------- | ----- | ---------- | --------- |
| Negation marker first | 2 | 2 | 1 of 2 |
| … and not a question | 1 | 1 | 1 of 1 |

The one dropped by the question exclusion is `아니 로컬에서 랙이 걸리는거
너때문인지?` — the user asking the agent something, not correcting a proposal.
The one that remains is the incident. One advisory per 8338 external writes is
the intended rate: this gate is silent until the specific shape occurs.

The quote check never disarmed in the corpus (0 of 2). With two samples that
measures nothing about the disarm path; the tests cover it instead.

## Input surface enumerated (per `praxis:surface-enumeration`)

Each row is a test case in
`tests/hooks/advisory-nudge/test_negation_answer_quote_advisory.sh`.

| Variant | Expected |
| ------- | -------- |
| Free-text negation answer, write, nothing quoted | ask |
| Same answer quoted in the next question | silent |
| Same answer quoted in assistant prose | silent |
| Free-text answer with no negation marker | silent |
| Option-label answer opening with `아니오` | silent |
| `아니면` (disjunction, not refusal) | silent |
| Negation marker but phrased as a question | silent |
| English `no, …` | ask |
| `note …` / `nobody …` (substring of `no`) | silent |
| Answer shorter than the 8-character floor | silent |
| Multi-question result, negation in the second pair | ask |
| Escaped quotes inside the answer text | ask |
| Rejection result (no pair at all) | silent |
| `InputValidationError` result | silent |
| Question still unanswered | silent |
| `gh pr comment` (Bash write surface) | ask |
| `gh pr view` (read) | silent |
| `Write` to a local file | silent |
| Bypass env set | silent |
| Missing transcript | silent |

`아니(?!면)` and `no(?![a-z])` are negative-lookahead scoped because Python's
`\b` is Unicode-aware: in the mixed Korean text these answers are written in,
`\bno\b` offers no boundary to lean on and `\b아니\b` does not separate `아니지`
from `아니면`.

## Fail-open contract

- Malformed stdin, non-write tool call, missing `transcript_path` → exit 0.
- Unreadable transcript, or a tail past `load_recent_events`' byte bound →
  exit 0, silent. This differs from `rejected-mutation-reconsent-gate`, which
  asks on an indeterminate scan: a standing refusal keeps its force for the
  whole session, while a correction is consumed by the work that follows it, so
  an answer older than the tail is mostly noise rather than a live hazard.
- Any uncaught exception → exit 0 (`@fail_open`).

## Scan bound

The tail is `load_recent_events(min_events=150)` — the turn boundary alone is
not enough, because the answer, the re-ask and the write sat in one turn in the
incident. An answer older than that window is not seen.
