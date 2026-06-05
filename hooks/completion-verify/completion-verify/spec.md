# Stop Hook Completion Evidence Verification

Supported hosts: all

`hooks/completion-verify.sh` fires on every `Stop` event and blocks assistant
turns that declare completion without same-turn verification evidence.

### Why this exists

Memory-based feedback alone (`feedback_test_pass_not_done.md` and friends) was
insufficient — the same evidence-less "✅ done" pattern recurred across
sessions, costing one extra round-trip every time. A hook moves enforcement
from "Claude tries to remember" to "Claude is structurally blocked from
shipping unverified completion claims."

### What is blocked

When the last 10 lines of the last assistant message match `CLAIM_PATTERNS`
(완료 / 작업 완료 / `done.` / `finished.` / `all done` / `implementation
complete` / etc.), the hook checks the **current turn** — i.e., everything
since the last real user input — for verification evidence.

The turn passes only if **all** of the following hold:

| Gate | Condition |
|------|-----------|
| L1 | A `Bash` tool_use occurred in this turn |
| L3 | Its `tool_result.content` matches `EVIDENCE_PATTERNS` (`X passed`, `tests passed`, `\bPASS\b`, `exit code 0`, `lint clean`, `테스트.*통과`, `✅`, etc.) |
| L2 | At least one `EVIDENCE_PATTERNS`-matching span from that `tool_result` is paste'd verbatim in the assistant message text — e.g. `12 passed`, `tests passed`, `lint clean`, `✅` |

A claim with no Bash, with Bash but no evidence signal, or with evidence but
the verify token not quoted, all block. Tool results from non-Bash tools
(e.g. `Read`, `Write`) do **not** count as evidence — only an actually
executed Bash command qualifies. Span-based paste detection is decoration-
agnostic — pytest's `============= 12 passed in 0.85s =============` border
output passes when the assistant cites `12 passed in 0.85s`.

### Response

When blocked, the hook emits:

```json
{
  "decision": "block",
  "reason": "Completion claim detected without same-turn verification evidence. ..."
}
```

and appends an entry to `${PRAXIS_HOME:-$HOME/.praxis}/scope-confirm/stop-triggered.log`.

### Fail-safe paths

The hook exits 0 (passes) when any of:

- `stop_hook_active` is true (re-entry guard)
- `transcript_path` is missing or unreadable
- The transcript is empty or contains no parseable assistant text
- The claim does not appear in the last 10 lines (mid-message 완료 mention)
- `jq` is not installed

### Why "same turn" specifically

Cross-turn carry-over (verifying in turn N, claiming in turn N+1) is the
exact pattern this hook is designed to prevent — it lets stale evidence
silently age out. Strict same-turn enforcement matches the global `~/.claude/CLAUDE.md`
"Verification Before Completion" rule that requires verification commands in
the *immediately preceding* turn.

### No escape hatch

Unlike `side-effect-scan` (`# side-effect:ack` marker), this hook
intentionally has **no bypass**. False positives should be reported as a new
issue, not papered over with a marker — the pattern this hook catches is the
same pattern the marker would re-enable.

### Tests

`tests/test_completion_verify.sh` covers 12 cases: 8 acceptance scenarios
(same-turn pass, no-Bash claim, no-evidence claim, no-paste claim,
mid-message claim ignored, non-Bash tool ignored, realistic pytest
output, Korean evidence) and 4 fail-safes (`stop_hook_active`, missing
transcript, empty file, malformed JSONL). Run before editing the hook:

```bash
./tests/test_completion_verify.sh
```
