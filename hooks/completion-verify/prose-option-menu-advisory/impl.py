#!/usr/bin/env python3
"""Stop hook advisory: an option menu surfaced as prose, outside AskUserQuestion.

Issue #1405.

`block-manufactured-action-menu` fires on `PreToolUse(AskUserQuestion)`, so it
sees only menus that became a tool call — and it catches the inverse case
(a menu whose options are actions the agent should have taken). A menu written
as ordinary assistant text produces no tool event at all, so nothing in the
PreToolUse layer is positioned to read it. The gates that would have applied to
the tool-call path — the `Falsified:` line on a recommendation among them —
are skipped by construction rather than by decision.

The pattern is generation 5 of a recorded one (`recurrence: 5`,
`enforcement: none`): a requirement the project already settles gets handed
back as "(a) … (b) … which way?". Its gen-4 note established that the Stop
surface exists and reads assistant text; only a hook for this pattern was
missing.

## Decision predicate

Advise when all three hold for the turn's last assistant text:

1. Two option-labelled lines — `(a)` / `(b)` / `(c)`, optionally bulleted or
   bolded — sit within 10 lines of each other.
2. The text carries an explicit demand that the reader choose.
3. The turn contains no `AskUserQuestion` tool call — a menu that went through
   the tool already met that path's gates.

Measured over one local corpus: 102 messages across 66 of 439 sessions match.
34% carry a recommendation and only 3% carry a `Falsified:` line, which is the
gap this advisory names.

## Why advisory and not a block

Legitimate menus are inside those 102 — the cost, risk, or scope of an
irreversible action is genuinely the user's call, and a hook cannot separate
that from a settled question handed back. Blocking would refuse the legitimate
half, so this hook only recalls the three self-checks the recorded pattern asks
for at menu-authoring time.

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
    extract_last_assistant_text,
    load_current_turn,
)

_PREFIX = "[prose-option-menu-advisory]"
_HOOK_NAME = "prose-option-menu-advisory"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_PROSE_OPTION_MENU_BYPASS"

# Two labelled options this far apart still read as one menu; further apart and
# the labels are more likely to be enumerating unrelated things.
_MENU_WINDOW_LINES = 10

_OPTION_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?\(?([abc])\)[\s*]")

_CHOICE_DEMAND_RE = re.compile(
    r"(어느 쪽|어느 것|골라|선택해 주시|정해 주시|중 어느|택1|어떤 쪽"
    r"|which (?:one|way|of these)|pick one|choose one|let me know which)",
    re.IGNORECASE,
)


def has_option_menu(text: str) -> bool:
    """True when two option-labelled lines sit within the menu window."""
    lines = text.split("\n")
    marked = [i for i, line in enumerate(lines) if _OPTION_LINE_RE.match(line)]
    return any(
        marked[j + 1] - marked[j] <= _MENU_WINDOW_LINES for j in range(len(marked) - 1)
    )


def demands_a_choice(text: str) -> bool:
    return bool(_CHOICE_DEMAND_RE.search(text))


def turn_has_ask_user_question(turn: list[dict]) -> bool:
    """True when this turn already routed a menu through AskUserQuestion."""
    for event in turn:
        message = event.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if event.get("isSidechain"):
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") == "AskUserQuestion":
                return True
    return False


def is_prose_menu(text: str, turn: list[dict]) -> bool:
    if not has_option_menu(text):
        return False
    if not demands_a_choice(text):
        return False
    return not turn_has_ask_user_question(turn)


_MESSAGE = (
    f"{_PREFIX} An option menu was surfaced as prose, so no AskUserQuestion "
    "gate saw it. Three self-checks before the menu stands:\n"
    "  (1) Is the answer already written in CLAUDE.md, a project instruction, "
    "a registry, or a sibling implementation? Then execute that path and leave "
    "one line naming what decided it.\n"
    "  (2) Does one option skip a MANDATORY skill or step? That is a bypass, "
    "not an option — delete it.\n"
    "  (3) Was a literal the user already gave this session looked up in the "
    "transcript before asking for it again?\n"
    f"  A recommendation still needs its `Falsified:` line. Bypass: {_BYPASS_ENV}=1"
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

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    turn = load_current_turn(transcript_path)
    last_text = extract_last_assistant_text(turn) if turn else ""
    if not last_text:
        return 0

    if not is_prose_menu(last_text, turn):
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
