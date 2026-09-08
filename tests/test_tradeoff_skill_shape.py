"""Shape invariants for ``skills/tradeoff/SKILL.md`` (issue #1385).

``scripts/run-tests.sh`` never executes a skill, so nothing can regression-test
the *quality* of a tradeoff scoring run. What is lockable is the structure the
skill's correctness rests on, and these three properties are exactly that:

  - the option-discovery probe list stays at three, so discovery keeps a
    termination condition rather than becoming an open search;
  - the convention lookup stays ahead of the scoring step, so a fork that a
    rule already settles never reaches the table;
  - both worked examples stay present, since they are the only recorded
    output the skill's behaviour can be read against.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "skills" / "tradeoff" / "SKILL.md"


def _lines() -> list[str]:
    return SKILL_MD.read_text(encoding="utf-8").splitlines()


def _heading_line(pattern: str) -> int:
    """1-indexed line of the first heading matching ``pattern``."""
    rx = re.compile(pattern)
    for number, line in enumerate(_lines(), start=1):
        if line.startswith("#") and rx.search(line):
            return number
    raise AssertionError(f"no heading matching {pattern!r} in {SKILL_MD}")


def test_skill_file_exists() -> None:
    assert SKILL_MD.is_file(), f"{SKILL_MD} is missing"


def test_discovery_has_exactly_three_probes() -> None:
    """The probe list is the termination condition; a fourth entry removes it."""
    lines = _lines()
    start = _heading_line(r"Step 1:") - 1
    end = _heading_line(r"Step 2:") - 1
    numbered = [
        line for line in lines[start:end] if re.match(r"^\d+\. \*\*", line)
    ]
    assert len(numbered) == 3, (
        f"expected exactly 3 discovery probes, found {len(numbered)}: "
        f"{numbered!r}. Discovery stops after three probes by design — see "
        "CLAUDE.md 'Loops terminate by design, not by judgement'."
    )


def test_convention_lookup_precedes_scoring() -> None:
    """A fork a rule already settles must never reach the scoring table."""
    discovery = _heading_line(r"Step 1:")
    scoring = _heading_line(r"Step 2:")
    assert discovery < scoring, (
        f"the discovery/convention step is at line {discovery} but scoring is "
        f"at {scoring}; scoring must not come first."
    )


def test_both_worked_examples_are_present() -> None:
    """One example terminates on a precedent, the other builds the table."""
    lines = _lines()
    start = _heading_line(r"^## Examples")
    cases = [
        line
        for line in lines[start:]
        if line.startswith("### Case ")
    ]
    assert len(cases) == 2, (
        f"expected 2 worked examples under '## Examples', found {len(cases)}: "
        f"{cases!r}"
    )


def test_report_step_forbids_a_totals_row() -> None:
    """Summing self-authored ordinals is what the grading scheme exists to stop."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "No totals row." in text, (
        "the Report step must state that no totals/average/ranking row is "
        "produced; without it the four axes read as summable."
    )
