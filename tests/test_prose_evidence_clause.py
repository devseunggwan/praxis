"""Regression fixture for the prose-evidence clause (issue #1044).

The #1044 rule is the one class that cannot have a hook: an evidence claim
whose only carrier is a sentence emits no tool call, so the prose is not merely
the current containment (as in `test_bypass_delegation_clause.py`) but the
whole remedy by construction. That makes the prose the artifact under test.

Its content is two-part — the class must be *named as unreachable*, and the two
compose-time questions must survive, since a clause that names the class while
dropping the questions leaves a reader with a diagnosis and no procedure. Both
parts are pinned, and each is proved load-bearing by re-running its predicate
against a mutation that removes only that part.

The retrospect side is pinned too: `reach=none` + `worse_axis: yes` needs a
stated destination on both surfaces that define the receipt, or a retrospect
hitting the combination proposes the reachable half and drops the rest — the
exact behaviour the issue was filed over.

Test override: `PRAXIS_TEST_REPO_ROOT` points the checks at another checkout.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _repo_root() -> Path:
    """Walk up to the checkout root (the dir holding ETHOS.md)."""
    override = os.environ.get("PRAXIS_TEST_REPO_ROOT")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for cand in here.parents:
        if (cand / "ETHOS.md").is_file() and (cand / "hooks").is_dir():
            return cand
    raise AssertionError(f"no praxis checkout root above {here}")


REPO_ROOT = _repo_root()
ETHOS = REPO_ROOT / "ETHOS.md"
RETRO_SKILL = REPO_ROOT / "skills" / "retrospect" / "SKILL.md"
RETRO_REPORTING = (
    REPO_ROOT / "skills" / "retrospect" / "references" / "stage3-reporting.md"
)
ANCHOR = "ETHOS.md#claims-that-terminate-in-prose"


def _clause(text: str) -> str:
    """Return the `Claims that terminate in prose` section, or '' if absent."""
    m = re.search(
        r"^### Claims that terminate in prose\s*$(.*?)(?=^#{1,3} |\Z)",
        text,
        re.M | re.S,
    )
    return m.group(1).strip() if m else ""


# --- the two parts, as independent predicates ------------------------------
# Predicates read a whitespace-normalized copy: the prose is hard-wrapped, so
# every phrase they look for can straddle a newline, and a raw-text match would
# go False on a pure re-wrap that changed no words.


def _norm(clause: str) -> str:
    return re.sub(r"\s+", " ", clause)


def _names_the_class_unreachable(clause: str) -> bool:
    """Clause states that no hook can reach this class — not that one is owed.

    A section that merely restates the evidence rules would satisfy a keyword
    check while implying enforcement exists, which is the failure mode the
    issue names; so the absence of a gate must be asserted, not implied.
    """
    text = _norm(clause)
    return bool(re.search(r"no gate is coming", text, re.I)) and bool(
        re.search(r"discipline is the whole remedy", text, re.I)
    ) and bool(re.search(r"nothing for a[^.]{0,60}hook to intercept", text))


def _carries_both_compose_time_questions(clause: str) -> bool:
    """Clause carries the authorship question AND the count question."""
    text = _norm(clause)
    return bool(re.search(r"[Ww]ho authored[^?]{0,80}\?", text)) and bool(
        re.search(r"count in my sentence match the number of blocks", text)
    )


# --- must fire: the shipped prose satisfies both parts ---------------------

def test_ethos_clause_exists():
    assert _clause(ETHOS.read_text(encoding="utf-8")), (
        "ETHOS.md has no `Claims that terminate in prose` section"
    )


def test_clause_names_the_class_as_unreachable():
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    assert _names_the_class_unreachable(clause), (
        "the clause must state that no hook can fire on this class, not "
        "restate the evidence rules as though enforcement existed"
    )


def test_clause_carries_both_compose_time_questions():
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    assert _carries_both_compose_time_questions(clause), (
        "compose time is the only surface left, so both questions — who "
        "authored/triggered the output, and does the count match — must survive"
    )


# --- must stay silent: each part is load-bearing ---------------------------
# Remove one part from a copy; the matching predicate must go False while the
# other stays True. A predicate that survives its own deletion tests nothing.

def test_dropping_the_unreachability_fails_only_that_check():
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    mutated = re.sub(r"and no gate is\s+coming", "and a gate is owed", clause)
    assert not _names_the_class_unreachable(mutated)
    assert _carries_both_compose_time_questions(mutated)


def test_dropping_the_questions_fails_only_that_check():
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    mutated = re.sub(r"^\d\..*?(?=^\d\.|\Z)", "", clause, flags=re.M | re.S)
    assert not _carries_both_compose_time_questions(mutated)
    assert _names_the_class_unreachable(mutated)


# --- retrospect destination ------------------------------------------------
# Both surfaces that define the receipt must route the combination, because a
# retrospect reads SKILL.md and only follows the reference on demand.

def test_retrospect_surfaces_route_reach_none_worse_axis_yes():
    for path in (RETRO_SKILL, RETRO_REPORTING):
        text = path.read_text(encoding="utf-8")
        assert "`reach=none`" in text and "worse_axis: yes" in text, (
            f"{path.name} must name the reach=none + worse_axis: yes "
            "combination, or a retrospect hitting it has no destination"
        )
        assert ANCHOR in text, (
            f"{path.name} must link the combination to the ETHOS.md class "
            "instead of leaving the remedy to be re-derived"
        )
