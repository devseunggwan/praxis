# Stop Retraction-Probe Advisory

Supported hosts: all

`hooks/completion-verify/retraction-probe-advisory/impl.py` fires on the Stop
event and advises when the turn's last assistant message retracts an earlier
verdict without quoting any output from a tool run in that turn.

## Decision predicate

Advise when **all** of these hold:

1. A line of the last assistant text — not inside a `>` quote, not phrased as
   a question — carries first-person retraction vocabulary: `틀렸습니다`,
   `틀렸고`, `철회합니다` / `철회했습니다`, `정정합니다` / `정정입니다`,
   `I was wrong`, `my judgement was wrong`, `my earlier <noun> was wrong`,
   `I retract`.
2. The turn ran at least one tool.
3. No line of any tool output from this turn, at least 12 characters once
   stripped, appears verbatim in the message.

Advisory only: `{"systemMessage": ...}`, exit 0. It never blocks. Bypass:
`PRAXIS_RETRACTION_PROBE_BYPASS=1`.

A turn with no tool call is left alone: there is no probe to name, only one to
run, and `completion-verify` already owns the "claim with no evidence" case.
The 12-character floor keeps short output lines (`OK`, `exit=0`, a bare
number) from counting as a quote when they occur in prose by chance.

## Why this exists

`completion-verify` gates a completion claim on same-turn evidence, and
`negative-existence-verdict-gate` gates "X is absent". A retraction is
neither: it is a positive claim that a prior claim was false, and nothing
checks that the evidence behind it is of the same kind as the evidence behind
what it retracts.

The recorded case (issue #1442): the agent read a workflow file and an
org-membership response, concluded the account could not run a command gated
on author-or-assignee, and named the fix (assign yourself, then run it). The
user ran the command successfully. In the same turn the agent fetched the
issue's `state` and `labels` — which confirmed the command ran — and wrote
"my judgement was wrong", seven times. The issue timeline showed the user had
assigned themselves eight seconds before running the command, exactly the
path the agent had named. A success outcome was read as a refutation of a
claim about permissions, and the permission predicate was never re-measured.

The hook does not judge whether the quoted probe measured the same predicate
as the retracted verdict. It asks only for the minimum that makes a
retraction checkable: name the probe.

## Measured corpus

Scope: `~/.claude*/projects/*/*.jsonl`, deduplicated by real path — 868
transcripts, 14112–14115 turns (the measuring session's own transcript grew
between the two runs), each turn's last main-chain assistant message.

| Vocabulary | Advisory would fire | Sampled precision |
| ---------- | ------------------- | ----------------- |
| Issue's list (`틀렸습니다`, `정정`, `철회`, `I was wrong`, `correction:`, `retract`) | 862 turns (6.1%) | ~17 of 40 lines were retractions |
| This hook's first-person forms | 334 turns (2.37%), 156 sessions | ~37 of 40 lines were retractions |

The bare noun `정정` was 78% of the issue list's vocabulary matches, and most
of those lines report correction *work* — "앵커를 rev 5 로 정정했습니다",
"정정 댓글 + 새 스크린샷" — rather than retract a verdict. The first-person
verb forms keep the retractions and drop the work reports. The three misses in
the narrowed sample call someone else's statement wrong (a reviewer's
workaround, a code comment) or re-scope an item; they are advisory noise, not
harm.

Of the 1091 turns with narrowed vocabulary, 722 already quote a same-turn
output line and 35 ran no tool; 334 are the population above.

## Why advisory rather than a block

Many of the 334 cite their evidence in prose ("Lambda 로그가
`removed 37 rows → 0` 으로 삭제 성공을 명시합니다") without a verbatim output
line, and some retractions rest on a probe from an earlier turn. Neither is
observable as wrong from the message text, so a block would refuse legitimate
retractions. The advisory recalls the check at the moment the retraction is
written.
