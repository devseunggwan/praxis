#!/usr/bin/env python3
"""PostToolUse(AskUserQuestion) hook: observe-only re-clarification-loop telemetry.

Issue #740 (outcome-proxy signal 2 of 3, deep-interview spec
`.omc/specs/deep-interview-issue-737-outcome-proxy.md` Round 2) — a
"re-clarification loop" is a session where the AI (not the user) fires
AskUserQuestion repeatedly on what a human would judge to be the same topic,
forcing the user to clarify more than once. This hook does NOT attempt that
topic judgment itself (see "Same-topic detection" below); it appends one
fire-ledger record per AskUserQuestion call so `bypass-review fire-rate` can
compute a per-session call count as a COARSE proxy for the loop signal.

Explicitly NOT a reuse of `hooks/completion-verify/retrospect-mix-check`'s
user-correction-marker regex (impl.sh L607-668, L732-746) — that regex was
built for retrospect's own Stop-hook self-validation (did the AI's own
response get corrected), a different question from "did the AI ask the same
clarifying question more than once." Reusing it here would measure the wrong
thing. This hook is a fresh, independent detector.

Same-topic detection (design decision — see PR body "## 설계 결정"): the issue
left three candidates open — (a) exact header/question text match, (b) text
similarity/embedding, (c) coarse total AskUserQuestion-call-count per session
with no topic clustering at all. This hook implements (c): every
AskUserQuestion call in a session appends one record; a session's total call
count is read later by bypass-review as the loop proxy (threshold >=2 calls).
(a)/(b) would require adding new fields to the shared fire-ledger record
shape (header/question text) that every other hook's records lack, and would
need a text-similarity threshold with its own tuning/accuracy burden. (c)
needs zero schema change and reuses the existing per-session grouping that
strike_count / external_write_revert_count already rely on. The accuracy
cost of skipping topic clustering entirely is documented in bypass-review's
OUTCOME PROXY LIMITATIONS section — a nonzero/>=2 count means "the AI asked
AskUserQuestion more than once in this session," NOT proof those calls were
about the same topic.

Emission point (design decision): PostToolUse(AskUserQuestion), not a Stop-
hook transcript sweep. This hook fires once per AskUserQuestion call and
appends one record immediately, mirroring bypass-telemetry's per-event
append-as-you-go pattern (hooks/postuse-correction/bypass-telemetry) rather
than re-parsing the whole transcript at session end (which retrospect-mix-
check already does for its own unrelated purpose — duplicating that parse
here would couple two independent detectors to the same expensive scan for
no shared benefit).

Behavior:
  tool_name != "AskUserQuestion"  -> no-op, exit 0
  tool_name == "AskUserQuestion"  -> one fire-ledger record via
                                      `_fire_ledger.record_session_fire`
                                      (granularity="rich", decision="pass"
                                      — every call is inherently the
                                      countable event, there is no
                                      interesting/uninteresting split to
                                      encode in decision)
  Malformed JSON stdin             -> no-op, exit 0
  PRAXIS_FIRE_TELEMETRY_DISABLE=1  -> no-op (fire-ledger's existing shared
                                      opt-out — no new opt-out var needed)

This hook NEVER blocks. It always exits 0 (PostToolUse cannot influence a
tool call that already completed regardless).

Note: `@fail_open` also appends its own COARSE record for this hook name
(session_id="", granularity="coarse") after every invocation — that is
automatic and unrelated to the RICH record this hook writes explicitly. See
`hooks/_lib/_fire_ledger.py`'s `record_session_fire` docstring for the
dedup contract bypass-review's reader side applies.
"""
from __future__ import annotations

import sys

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "askuserquestion-loop-signal"
_HOOK_ROLE = "postuse-correction"


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "AskUserQuestion":
        return 0

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0

    _fire_ledger.record_session_fire(
        _HOOK_NAME, _HOOK_ROLE, "pass", session_id, "AskUserQuestion"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
