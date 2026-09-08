#!/usr/bin/env python3
"""Stop hook: observe-only frequency signal for agent-originated bypass routes.

Issue #1338, `docs/hook/RULE-BACKSTOP-GAPS.md` gap #4 — the **prose lane**.
ETHOS.md principle 5 draws the line at authorship: the agent may relay the
gate's own `Bypass (if truly needed): <VAR>=1 …` line and nothing else. What it
must not originate is a route it invented — asking the user to add a permission
rule, walking them through a `.claude/settings.json` edit, offering to move the
file out of the guarded path. The user saying yes to an agent-originated route
widens the guard permanently, which is why the line sits at who proposed it.

Gap #4 was measured silent on all three lanes on 2026-08-15. Issue #1337 closed
the follow-up-write lane (`settings-path-advisory`, PreToolUse(Edit|Write)).
The prose and menu lanes stayed open. This hook is the prose lane's **meter**,
not its gate.

## Why a meter and not a gate

The issue's own proposal was a `type: "prompt"` hook — a small model asked, on
every Stop, whether the last message offers a bypass route. That buys a model
call per turn and a false-positive rate nobody has measured. The cheaper first
move is to find out how often the shape occurs at all: a deterministic detector
whose only output is a ledger row, audited after a month, deciding whether the
tier is worth anything and what the false-positive floor looks like.

So this hook **judges nothing**. It never blocks, never emits a decision the
model can read, and never tells the user their turn was suspicious. It appends
one telemetry record when the final message carries the shape, and that is
the whole behaviour. `bypass-review fire-rate` (or `/praxis:bypass-report`)
reads the count back.

Records land in their own `bypass-route-events-*.jsonl` family rather than the
fire ledger — see the Storage section below for the measurement that forced
that, and for where the denominator comes from instead.

## Detection model — route noun AND proposal frame, same paragraph

A route noun alone is ordinary prose: "settings.json 을 읽었습니다" names the
file without offering anything. A proposal frame alone is every other sentence
in a working session. The shape gap #4 describes is the conjunction, so both
must appear in the same `\\n\\n`-delimited paragraph:

  (a) **route noun** — the thing that would be widened. Taken from the four
      routes ETHOS principle 5 names, not invented here: a permission rule, an
      allow list, a Claude Code settings file, `disableAllHooks`, or moving a
      file out of a guarded path.
  (b) **proposal frame** — the agent offering it rather than reporting it.

## The relay carve-out is not optional

Principle 5 explicitly permits relaying the gate's own line, and every praxis
block message prints one (`hooks/_lib/block_message.py`). A detector that
counted those would report the agent doing the one thing it is *supposed* to
do, and the measurement would be worthless. Relay lines are therefore removed
from the text before any matching happens — line-scoped, so a paragraph that
relays the gate's line and *also* originates a route still counts.

## Known false positives, recorded rather than engineered away

This hook exists to measure, so the honest move is to name what it will
over-count instead of adding discriminators nobody has evidence for yet:

  * **Prose about this rule.** A retrospect, a PR body, this docstring quoted
    back — anything discussing bypass routes carries the vocabulary. Sessions
    working on praxis itself will over-count, and the audit must segment on it.
  * **Past-tense reports.** "요청하신 대로 PRAXIS_X=1 로 진행했습니다" reports a
    route the user chose. The proposal frames lean present/conditional, but no
    tense analysis is attempted.
  * **Relaying a third party's suggestion.** Quoting a reviewer or a doc that
    names a route reads the same as originating one.

Whether these dominate the count is the question the audit answers. Suppressing
them here on a guess would decide it in advance, and the guess would be
invisible in the number.

## Fail-open contract
  - Malformed / missing stdin JSON → exit 0
  - Missing / unreadable transcript → exit 0
  - No last assistant text → exit 0
  - stop_hook_active=true → exit 0 (re-entrancy guard)
  - `PRAXIS_HOOK_BYPASS_ROUTE_SIGNAL=1` → exit 0 before any work
  - Unwritable telemetry path → exit 0, record dropped (@append_record)
  - Any uncaught exception → exit 0 (@fail_open)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _fire_ledger import (  # type: ignore[import-not-found]  # noqa: E402
    _atomic_append,
    resolve_telemetry_dir,
)
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    extract_last_assistant_text,
    load_current_turn,
)

_HOOK_NAME = "bypass-route-signal"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_ROUTE_SIGNAL"

# ---------------------------------------------------------------------------
# The relay carve-out (issue #1338 control group).
#
# Both shapes praxis' own block messages print: `block_message.py` writes
# `Bypass (if truly needed): VAR=1 <hint>`, and the advisory hooks that build
# their text by hand write a bare `Bypass: VAR=1`. Matched at line scope and
# dropped before anything else runs, so relaying either one cannot register as
# an originated route — while a paragraph that relays AND originates keeps its
# other lines and still counts.
#
# The `VAR=1` tail is what makes this a carve-out rather than a blind spot: a
# line reading `Bypass: 권한 규칙을 추가하면 됩니다` is an originated route that
# merely opens with the word, and stripping it would delete the one shape this
# hook exists to count.
# ---------------------------------------------------------------------------
_RELAY_LINE_RE = re.compile(
    r"^.*(?<![A-Za-z])(?i:Bypass)(?:\s*\(if truly needed\))?\s*:\s*"
    r"[A-Z][A-Z0-9_]*=1(?![A-Za-z0-9_]).*$",
    re.MULTILINE,
)


def strip_relay_lines(text: str) -> str:
    """Blank out the gate's own bypass lines, keeping line structure intact.

    Replaced with an empty string rather than deleted so paragraph boundaries
    (`\\n\\n`) survive — deleting the newline could weld two paragraphs
    together and manufacture a co-occurrence that the message never had.
    """
    return _RELAY_LINE_RE.sub("", text)


# ---------------------------------------------------------------------------
# (a) Route nouns — what would be widened.
#
# Korean tokens match as plain substrings: Hangul has no ASCII word-boundary
# hazard. English tokens carry explicit non-letter guards instead of `\b`,
# because `re` is Unicode-aware and `\b` does not separate an ASCII word from
# adjacent Hangul (`push할까요`) — the trap the surface-enumeration skill names.
# ---------------------------------------------------------------------------
_ROUTE_NOUNS_KO = (
    "권한 규칙",
    "권한 설정",
    "허용 목록",
    "허용목록",
    "가드된 경로",
)
_ROUTE_NOUNS_EN_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    r"permission\s+rule"
    r"|allow[-\s]?list"
    r"|settings\.json"
    r"|disableAllHooks"
    r"|guarded\s+path"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# (b) Proposal frames — the agent offering the route rather than naming it.
#
# `우회` (circumvent) and `bypass`/`work around` are the direct forms. The rest
# are the indirect ones gap #4 actually observed: an addition, an edit, a move,
# or a menu of "options". Each is weak alone, which is why the conjunction with
# a route noun is what fires.
# ---------------------------------------------------------------------------
_PROPOSAL_FRAMES_KO = (
    "추가하",
    "추가해",
    "편집하",
    "수정하",
    "옮기",
    "우회",
    "풀어",
    "넣으면",
    "하시면",
    "방법",
    "옵션",
)
_PROPOSAL_FRAMES_EN_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    r"add|adding|edit|editing|move|moving|bypass|bypassing"
    r"|work\s+around|you\s+could|we\s+can|one\s+option"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)


def _has_route_noun(paragraph: str) -> bool:
    if any(token in paragraph for token in _ROUTE_NOUNS_KO):
        return True
    return bool(_ROUTE_NOUNS_EN_RE.search(paragraph))


def _has_proposal_frame(paragraph: str) -> bool:
    if any(token in paragraph for token in _PROPOSAL_FRAMES_KO):
        return True
    return bool(_PROPOSAL_FRAMES_EN_RE.search(paragraph))


def carries_bypass_route(text: str) -> bool:
    """True if any paragraph pairs a route noun with a proposal frame.

    Paragraph scoping mirrors the Stop-hook siblings (`\\n\\n` split): a route
    noun in one paragraph and a proposal frame three paragraphs later are not
    the shape gap #4 describes, and counting them would inflate the very
    number this hook exists to establish.
    """
    stripped = strip_relay_lines(text)
    for paragraph in stripped.split("\n\n"):
        if _has_route_noun(paragraph) and _has_proposal_frame(paragraph):
            return True
    return False


# ---------------------------------------------------------------------------
# Storage — its own JSONL family, not the fire ledger.
#
# Measured, not assumed: a Stop hook that emits nothing cannot leave a
# distinguishable fire-ledger row. `record_session_fire` returns False under
# `_IN_DISPATCHER` (_fire_ledger.py), and the dispatcher's own
# `record_group_fires` derives each member's decision from `(rc, stdout,
# stderr)` — so a silent member classifies as `pass` and folds into the
# counter file, indistinguishable from every quiet turn. Driving this hook's
# own payload through `hooks/_lib/_dispatch.py Stop` produced an empty ledger
# while a blocking sibling produced a row.
#
# The three ways out were: emit a user-visible `systemMessage`, emit a stderr
# marker the dispatcher classifies as `advise`, or write elsewhere. The first
# puts a line in front of the user at an unmeasured false-positive rate, which
# is the thing this hook exists to measure first. The second records `advise`
# for a hook that advises nobody, making the report's Advise column lie. So:
# its own family, mirroring `bypass-telemetry`'s observe-only writer.
#
# The DENOMINATOR still comes from the fire ledger for free — the dispatcher
# records this hook's automatic `pass` on every Stop under its own name, so
# the hook's `Fires` row in `bypass-review fire-rate` is the turn count these
# matches divide by.
# ---------------------------------------------------------------------------


def resolve_signal_path() -> Path:
    """Today's JSONL path for this hook's records.

    Shares `_fire_ledger.resolve_telemetry_dir()` for the reason
    `bypass-telemetry` does (issue #934): `bypass-review` reads every family
    out of one `telemetry_dir`, and a family that diverted on its own would
    make `--dir` show one side of the ratio without the other.
    """
    override = os.environ.get("PRAXIS_BYPASS_ROUTE_SIGNAL_FILE", "").strip()
    if override:
        return Path(override)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return resolve_telemetry_dir() / f"bypass-route-events-{today}.jsonl"


def append_record(record: dict) -> None:
    """Append one JSON line. Fail-open: any I/O error is swallowed.

    `_atomic_append` refuses a FIFO / device / symlink at the target and opens
    with O_NONBLOCK, so a planted path cannot stall the Stop group this hook
    runs in — standalone the stall would cost this node, in the group it costs
    every sibling waiting behind it.
    """
    try:
        _atomic_append(resolve_signal_path(), [json.dumps(record, ensure_ascii=False)])
    except Exception:  # noqa: BLE001
        return  # fail-open — never delay the stop


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

    if not carries_bypass_route(last_text):
        return 0  # a quiet turn writes nothing; the denominator is the fire ledger

    session_id = payload.get("session_id")
    append_record({
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "session_id": session_id if isinstance(session_id, str) else "",
        "hook": _HOOK_NAME,
        "event": "Stop",
        # No excerpt of the matched text. The record exists to be counted, and
        # a final assistant message is the least redactable thing in a session.
        "matched": True,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
