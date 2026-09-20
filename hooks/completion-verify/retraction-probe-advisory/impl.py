#!/usr/bin/env python3
"""Stop hook advisory: a retraction that quotes no probe from its own turn.

Issue #1442.

A final message that retracts an earlier verdict ("틀렸습니다", "철회합니다",
"I was wrong") is itself a claim. `completion-verify` gates completion claims
and `negative-existence-verdict-gate` gates absence claims; a retraction is
neither, so it can go out resting on a probe that measured something other
than what the retracted verdict measured. In the recorded case the verdict was
right, the retraction was wrong, and the retracting turn's only probe read a
success outcome as a refutation of a claim about permissions.

## Decision predicate

Advise when all three hold for the turn's last assistant text:

1. A line outside a `>` quote, not phrased as a question, carries first-person
   retraction vocabulary.
2. The turn ran at least one tool.
3. No line of any tool output from this turn (at least `_MIN_QUOTE_CHARS`
   characters once stripped) appears verbatim in the message.

A turn with no tool call is left to `completion-verify`'s evidence gate: there
is no probe to name, only one to run. Whether the quoted probe measured the
same predicate as the retracted verdict is not judged — the advisory asks only
for the minimum that makes a retraction checkable.

The vocabulary is narrower than the issue's list, by measurement: over 14112
local turns the bare noun `정정` and `correction:` matched mostly reports of
correction *work* ("앵커를 rev 5 로 정정"), putting sampled precision near 43%;
the first-person forms below sampled about 37 of 40 as genuine retractions.

Advisory only: `{"systemMessage": ...}`, exit 0. Bypass:
`PRAXIS_RETRACTION_PROBE_BYPASS=1`.

Fail-open contract: malformed stdin, missing transcript, no last assistant
text, `stop_hook_active`, or any uncaught exception → exit 0.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_stop_advisory  # type: ignore[import-not-found]  # noqa: E402
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    load_stop_turn,
    stop_last_assistant_text,
)

_PREFIX = "[retraction-probe-advisory]"
_HOOK_NAME = "retraction-probe-advisory"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_RETRACTION_PROBE_BYPASS"

# Shorter output lines ("OK", "exit=0", a bare number) occur in prose by chance.
_MIN_QUOTE_CHARS = 12

_RETRACTION_RE = re.compile(
    r"틀렸습니다|틀렸고|철회(?:합니다|했습니다)|정정(?:합니다|입니다)"
    r"|\bI was wrong\b|\bmy (?:judge?ment|earlier \w+) was wrong\b|\bI retract\b",
    re.IGNORECASE,
)

# Same question exemption as pr-claim-mutation-gate: asking is not asserting.
_QUESTION_RE = re.compile(r"[?？]\s*$|했나요|됐나요|했습니까")


def retraction_line(text: str) -> str | None:
    """First line asserting a retraction, skipping `>` quotes and questions."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(">") or _QUESTION_RE.search(line):
            continue
        if _RETRACTION_RE.search(line):
            return line
    return None


def _tool_result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _blocks(event: dict, role: str) -> list[dict]:
    message = event.get("message", {})
    if not isinstance(message, dict) or message.get("role") != role:
        return []
    content = message.get("content", [])
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def turn_tool_outputs(turn: list[dict]) -> tuple[int, list[str]]:
    """(tool calls, tool output texts) the MAIN chain ran this turn.

    A main-session turn carries a delegated agent's own events inline, marked
    `isSidechain`, and both counts here are claims about the retracting agent:
    a subagent's run is not a probe the main agent made, and a line quoted out
    of one is not evidence the main agent measured anything. Counting them
    fires the advisory on a turn whose main chain ran no tool at all, and
    silences it whenever a retraction happens to quote a subagent's output.
    `extract_last_assistant_text` excludes them on the same grounds. On the
    SubagentStop path the marker is already gone (`load_stop_turn` drops it as
    the per-agent tail is parsed), so this filter is a no-op there.
    """
    main = [event for event in turn if not event.get("isSidechain")]
    calls = sum(
        1
        for event in main
        for block in _blocks(event, "assistant")
        if block.get("type") == "tool_use"
    )
    outputs = [
        _tool_result_text(block)
        for event in main
        for block in _blocks(event, "user")
        if block.get("type") == "tool_result"
    ]
    return calls, outputs


def quotes_an_output(text: str, outputs: list[str]) -> bool:
    for output in outputs:
        for raw in output.splitlines():
            line = raw.strip()
            if len(line) >= _MIN_QUOTE_CHARS and line in text:
                return True
    return False


def is_unprobed_retraction(text: str, turn: list[dict]) -> bool:
    if retraction_line(text) is None:
        return False
    calls, outputs = turn_tool_outputs(turn)
    if calls == 0:
        return False
    return not quotes_an_output(text, outputs)


_MESSAGE = (
    f"{_PREFIX} This message retracts an earlier verdict, and it quotes no "
    "output from a tool run in this turn. A retraction is a claim too: quote "
    "the probe that measured the same thing the retracted verdict measured, or "
    "mark the retraction as unverified. A success outcome is not a refutation "
    f"of a claim about the path that produced it. Bypass: {_BYPASS_ENV}=1"
)


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    payload = read_payload()
    if not isinstance(payload, dict):
        return 0
    if payload.get("stop_hook_active"):
        return 0  # avoid re-entrant loops

    turn = load_stop_turn(payload)
    if not turn:
        return 0
    last_text = stop_last_assistant_text(payload, turn)
    if not last_text or not is_unprobed_retraction(last_text, turn):
        return 0

    emit_stop_advisory(_MESSAGE)

    session_id = payload.get("session_id")
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, _fire_ledger.DECISION_ADVISE,
        session_id if isinstance(session_id, str) else "", "Stop",
    ):
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
