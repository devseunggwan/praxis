"""cited-rule-gate parses headings and citations in linear time (issue #1496).

The gate parses every heading of the rule files on each mutating call. The
earlier heading pattern `(.+?)\\s*#*\\s*$` and the citation splitter
`\\s*[·;|]\\s*` backtracked super-linearly on long whitespace or `#` runs, so one
such line in a rule file stalled every call until the hook timeout.

The legacy patterns stay here as the oracle: on every short line over the
alphabet that exercises their edges, the rewrite must return what they did.

Run: python3 -m pytest tests/test_cited_rule_gate_parse.py -q
"""
from __future__ import annotations

import importlib.util
import itertools
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))

_spec = importlib.util.spec_from_file_location(
    "cited_rule_gate",
    REPO_ROOT / "hooks" / "advisory-nudge" / "cited-rule-gate" / "impl.py",
)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)  # type: ignore[union-attr]

_LEGACY_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_LEGACY_SPLIT_RE = re.compile(r"\s*[·;|]\s*")

# Generous against a loaded CI runner; the legacy patterns took seconds.
_LINEAR_BUDGET_S = 0.5


def _elapsed(fn: Callable[[str], object], arg: str) -> float:
    start = time.perf_counter()
    fn(arg)
    return time.perf_counter() - start


def test_pathological_heading_lines_finish_fast() -> None:
    for line in ("# a" + " " * 5000 + "b", "# " + "#" * 20000 + " x", "#" + " " * 20000):
        assert _elapsed(gate.heading_names, line) < _LINEAR_BUDGET_S, line[:10]


def test_pathological_citation_finishes_fast() -> None:
    assert _elapsed(gate._candidates, "a" + " " * 20000 + "b") < _LINEAR_BUDGET_S


def test_heading_names_pinned() -> None:
    text = "\n".join([
        "# Plain",
        "###### Six levels",
        "####### Seven is not a heading",
        "#NoSpace",
        "    # Four-space indent is code",
        "   # Three-space indent",
        "## Closing run ##",
        "## Closing run with trailing space ###   ",
        "## C#",
        "## a#b inner hash",
        "# ###",
        "#\tTab after hashes",
        "## Coding Style `[E0]`",
        "## Two spans `[E2]` `R7`",
        "```",
        "# Inside a fence",
        "```",
    ])
    assert gate.heading_names(text) == {
        "Plain", "Six levels", "Three-space indent", "Closing run",
        "Closing run with trailing space", "C", "a#b inner hash", "#",
        "Tab after hashes", "Coding Style `[E0]`", "Coding Style",
        "Two spans `[E2]` `R7`", "Two spans",
    }


def _legacy_heading(line: str) -> str | None:
    m = _LEGACY_HEADING_RE.match(line)
    return m.group(1) if m else None


def test_heading_text_matches_legacy_on_every_short_line() -> None:
    alphabet = " #a\t`　"
    for n in range(1, 8):
        for chars in itertools.product(alphabet, repeat=n):
            line = "".join(chars)
            assert gate._heading_text(line) == _legacy_heading(line), repr(line)


def _legacy_candidates(name: str) -> list[str]:
    parts = [name, *_LEGACY_SPLIT_RE.split(name), *name.split(",")]
    out: list[str] = []
    for part in parts:
        part = gate._normalize(part)
        out += [part, gate._strip_trailing_code(part).strip(gate._QUOTES)]
    return [c for c in out if c]


def test_candidates_match_legacy_on_every_short_name() -> None:
    alphabet = " ·;|a,`"
    for n in range(1, 7):
        for chars in itertools.product(alphabet, repeat=n):
            name = "".join(chars)
            assert gate._candidates(name) == _legacy_candidates(name), repr(name)
