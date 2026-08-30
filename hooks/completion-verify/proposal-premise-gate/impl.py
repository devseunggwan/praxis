#!/usr/bin/env python3
"""Stop hook advisory: prose proposal block with an unprobed code-checkable
premise.

Issue #846. The two existing falsification gates are PreToolUse-only and
keyed on `AskUserQuestion` evaluative markers:

  - `advisory-nudge/output-block-falsify-advisory` — fires on
    `AskUserQuestion` / `Bash`
  - `advisory-nudge/pre-output-falsification-gate` — Lane A requires an
    evaluative marker (`Recommended` / `권장` / `safer`) AND negative
    evidence in the recent transcript

Neither can fire on a **plain prose** proposal block — a 7-item prioritized
improvement table surfaced as assistant text with no `AskUserQuestion` call
and no `Recommended` marker. In the motivating session, 2 of 7 items
collapsed within minutes of an adversarial pass: one item ("no tier routing
exists") was falsified by a single grep, and the rationale for the SAME item
(a deliberately-reverted mechanism) sat in a source comment reachable by the
identical grep. Probe cost was ~30s each; the omission was a classification
error ("proposal, therefore opinion, therefore unverified"), not a cost
decision.

## Design — mirrors `negative-existence-verdict-gate`'s presence-enforcement

Rather than judge premise correctness (infeasible at this layer — the hook
cannot re-run the agent's reasoning), this hook enforces PRESENCE of probe
evidence for each code-checkable claim inside a detected proposal block:

  1. **Shape** — a heading/bold-label line or a markdown table header
     containing a proposal keyword (우선순위 / 제안 / Priority / Recommendation
     / 개선). Cheap early exit: absent shape → silent, no further scanning.
  2. **Item lines** — within a shaped document, only NUMBERED-LIST-ITEM
     lines and TABLE-DATA-ROW lines are scanned for a premise marker. This
     is deliberately narrower than "any paragraph in the message" — it
     targets exactly the structural unit the motivating case exposed (each
     table row / list item WAS a discrete claim), and keeps ordinary prose
     mentioning a proposal keyword once, elsewhere in the message, from
     ever entering the scan.
  3. **Premise marker** — negative-existence phrasing (`does not exist`,
     `is missing`, `없습니다`, `not wired`, `no ... path`) OR a cost/impact
     claim (`burns`, `costs`, `태웁니다`, `N%`).
  4. **Probe-evidence check** — an item line carrying a premise marker is
     "probed" if the current turn contains a `Grep`/`Read`/`Bash`/`Glob`
     tool call whose input (pattern/path/file_path/command) shares an
     identifier-like token (`[A-Za-z0-9_/.-]{4,}`, common stopwords
     excluded) with that line. No overlap (or no extractable token at all,
     e.g. an all-Korean claim with no ASCII identifier) counts as unprobed
     — the crude, substring-only overlap is explicitly acknowledged as an
     approximation biased toward advising rather than staying silent (see
     "Honest limitation" in spec.md), which is acceptable for a
     non-blocking advisory.

Tier: advisory only (`{"systemMessage": ...}`, exit 0). This NEVER blocks —
the issue's own proposal designates it advisory, unlike the sibling
`negative-existence-verdict-gate` (which defaults to a hard block).

Fail-open contract:
  - Malformed / missing stdin JSON → exit 0
  - Missing / unreadable / empty transcript → exit 0
  - No last assistant text → exit 0
  - stop_hook_active=true → exit 0 (re-entrancy guard)
  - Any uncaught exception → exit 0 (`@fail_open`)

Scope note (turn-boundary, not session-boundary): probe evidence is checked
against the CURRENT turn only (`load_current_turn`), matching every sibling
completion-verify Stop hook's scanning convention. The issue's proposal
language says "no probe this session" — narrowing to the current turn is a
structural convention inherited from the sibling hooks, not a design choice
specific to this gate; see spec.md "Honest limitation".
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

_PREFIX = "[proposal-premise-gate]"
_HOOK_NAME = "proposal-premise-gate"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_PROPOSAL_PREMISE_BYPASS"

# ---------------------------------------------------------------------------
# (1) Proposal-block shape
# ---------------------------------------------------------------------------

_SHAPE_KEYWORDS_KO = ("우선순위", "제안", "개선")
_SHAPE_KEYWORDS_EN = ("priority", "recommendation")


def _line_has_shape_keyword(stripped: str, lower: str) -> bool:
    if any(k in stripped for k in _SHAPE_KEYWORDS_KO):
        return True
    return any(k in lower for k in _SHAPE_KEYWORDS_EN)


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    if not s or "-" not in s or "|" not in s:
        return False
    return all(c in "-:| " for c in s)


def _has_proposal_shape(text: str) -> bool:
    """True if the text contains a heading/table carrying a proposal keyword."""
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        is_heading = stripped.startswith("#") or (
            stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4
        )
        if is_heading and _line_has_shape_keyword(stripped, lower):
            return True
        # Table header: this line carries `|`, the NEXT line is a separator row.
        if "|" in stripped and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            if _line_has_shape_keyword(stripped, lower):
                return True
    return False


# ---------------------------------------------------------------------------
# (2) Item-line extraction (numbered list items / table data rows)
# ---------------------------------------------------------------------------

_NUM_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+\S")


def _is_item_line(line: str) -> bool:
    s = line.rstrip()
    if not s.strip():
        return False
    if _NUM_ITEM_RE.match(s):
        return True
    if s.count("|") >= 2 and not _is_table_sep(s):
        return True
    return False


# ---------------------------------------------------------------------------
# (3) Premise marker — negative-existence OR cost/impact
# ---------------------------------------------------------------------------

_NEG_KO = ("없습니다", "없음", "미구현", "안 되어 있", "연결되어 있지 않")
_NEG_EN_SUBSTR = ("does not exist", "is missing", "not wired")
_NO_PATH_RE = re.compile(r"\bno\b(?:\s+\S+){1,3}\s+path\b", re.IGNORECASE)

_COST_KO = ("태웁니다", "비용이", "비용을")
_COST_EN_SUBSTR = ("burns", "costs")
_PERCENT_RE = re.compile(r"\d+%")


def _has_premise_marker(line: str) -> bool:
    lower = line.lower()
    if any(k in line for k in _NEG_KO):
        return True
    if any(k in lower for k in _NEG_EN_SUBSTR):
        return True
    if _NO_PATH_RE.search(line):
        return True
    if any(k in line for k in _COST_KO):
        return True
    if any(k in lower for k in _COST_EN_SUBSTR):
        return True
    return bool(_PERCENT_RE.search(line))


# ---------------------------------------------------------------------------
# (4) Probe-evidence check — key-token overlap against this-turn tool inputs
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9_/.-]{4,}")
_STOPWORDS = frozenset(
    {
        "does", "exist", "exists", "missing", "wired", "path", "cost", "costs",
        "burns", "this", "that", "have", "with", "from", "into", "case", "item",
        "code", "only", "true", "false", "item1", "item2",
    }
)

_PROBE_TOOLS = frozenset({"Grep", "Read", "Bash", "Glob"})
_PROBE_INPUT_KEYS = ("pattern", "path", "glob", "file_path", "command")


def _key_tokens(line: str) -> list[str]:
    return [
        t.lower() for t in _TOKEN_RE.findall(line)
        if t.lower() not in _STOPWORDS
    ]


def _gather_probe_haystack(turn: list[dict]) -> str:
    parts: list[str] = []
    for ev in turn:
        msg = ev.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != "assistant" \
                or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in _PROBE_TOOLS:
                continue
            inp = block.get("input")
            if not isinstance(inp, dict):
                continue
            for key in _PROBE_INPUT_KEYS:
                v = inp.get(key)
                if isinstance(v, str):
                    parts.append(v)
    return " ".join(parts).lower()


def _is_probed(line: str, haystack: str) -> bool:
    tokens = _key_tokens(line)
    if not tokens:
        return False  # no extractable identifier — cannot confirm a probe
    return any(tok in haystack for tok in tokens)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def count_unprobed_premises(text: str, turn: list[dict]) -> int:
    """Return the number of item-line premises with no probe evidence.

    Returns 0 immediately when the text carries no proposal shape at all —
    the cheap early exit that keeps ordinary prose out of the scan.
    """
    if not _has_proposal_shape(text):
        return 0
    haystack = _gather_probe_haystack(turn)
    count = 0
    for line in text.split("\n"):
        if not _is_item_line(line):
            continue
        if not _has_premise_marker(line):
            continue
        if not _is_probed(line, haystack):
            count += 1
    return count


_MESSAGE_TMPL = (
    f"{_PREFIX} Prose proposal contains {{n}} code-checkable premise(s) with "
    f"no probe in the current turn. Probe before locking. Bypass: {_BYPASS_ENV}=1"
)


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    payload = read_payload()
    if payload is None:
        return 0
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

    n = count_unprobed_premises(last_text, turn)
    if n == 0:
        return 0

    emit_stop_advisory(_MESSAGE_TMPL.format(n=n))

    session_id = payload.get("session_id")
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, _fire_ledger.DECISION_ADVISE,
        session_id if isinstance(session_id, str) else "", "Stop",
    ):
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
