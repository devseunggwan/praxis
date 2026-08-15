"""Regression fixture for the bypass-delegation clause (issue #1009).

The #1009 rule lives only in prose — no hook backstops it (see
`docs/hook/RULE-BACKSTOP-GAPS.md` gap #4), so the prose itself is the artifact
under test. Its whole content is a *distinction*: praxis' own sanctioned
`Bypass (if truly needed): …` line MAY be relayed to the user, while a route
the agent originates (permission rule, `.claude/settings.json` edit, moving the
file out of the guarded path) MAY NOT. A one-directional test would let the
opposite error in, so both halves are pinned, and each half is proved
load-bearing by re-running the same checker against a mutation that deletes
only that half.

Checks:
  - ETHOS.md principle 5 exists in the Autonomy-vs-Convention key-principles
    list and states BOTH halves of the distinction.
  - Deleting either half from a copy makes the corresponding check fail
    (the control: a checker that passes a half-written clause is broken).
  - RULE-BACKSTOP-GAPS.md carries gap row #4 at HIGH cost and records that
    the containment is prose, not a hook.

Test override: `PRAXIS_TEST_REPO_ROOT` points the checks at another checkout.
Used to prove the suite is not vacuous — pointed at a copy with principle 5
and gap row #4 stripped out, all 7 checks fail; against this tree they pass.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _repo_root() -> Path:
    """Walk up to the checkout root (the dir holding ETHOS.md).

    Resolved by search rather than a fixed `parent.parent` so the file runs
    identically from `tests/` and from a scratch copy during authoring.
    """
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
GAPS = REPO_ROOT / "docs" / "hook" / "RULE-BACKSTOP-GAPS.md"


def _principle_5(text: str) -> str:
    """Return the text of numbered principle 5, or '' if absent."""
    m = re.search(r"^5\.\s+(.*?)(?=^\d+\.\s|^#|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


# --- the two halves of the distinction, as independent predicates ----------

def _grants_relay(clause: str) -> bool:
    """Clause explicitly permits relaying praxis' OWN bypass line."""
    return "Bypass (if truly needed)" in clause and bool(
        re.search(r"\bMAY\b|permit|allowed", clause)
    )


def _forbids_origination(clause: str) -> bool:
    """Clause explicitly forbids agent-originated routes around the guard."""
    return "settings.json" in clause and bool(
        re.search(r"permission rule", clause)
    ) and bool(re.search(r"originat", clause, re.I))


# --- must fire: the shipped prose satisfies both halves --------------------

def test_ethos_principle_5_exists():
    clause = _principle_5(ETHOS.read_text(encoding="utf-8"))
    assert clause, "ETHOS.md Autonomy-vs-Convention list has no principle 5"


def test_principle_5_grants_the_sanctioned_relay():
    clause = _principle_5(ETHOS.read_text(encoding="utf-8"))
    assert _grants_relay(clause), (
        "principle 5 must state that praxis' own `Bypass (if truly needed): …` "
        "line MAY be relayed — suppressing it would hide praxis' own affordance"
    )


def test_principle_5_forbids_agent_originated_routes():
    clause = _principle_5(ETHOS.read_text(encoding="utf-8"))
    assert _forbids_origination(clause), (
        "principle 5 must forbid the agent originating a route around the "
        "guard (permission rule, .claude/settings.json edit)"
    )


# --- must stay silent: each half is load-bearing ---------------------------
# Delete one half from a copy; the matching predicate must go False while the
# other stays True. A predicate that survives its own deletion is not testing
# anything.

def test_deleting_the_relay_grant_fails_only_that_check():
    clause = _principle_5(ETHOS.read_text(encoding="utf-8"))
    mutated = clause.replace("Bypass (if truly needed)", "some escape hatch")
    assert not _grants_relay(mutated)
    assert _forbids_origination(mutated)


def test_deleting_the_origination_ban_fails_only_that_check():
    clause = _principle_5(ETHOS.read_text(encoding="utf-8"))
    mutated = re.sub(r"originat\w*", "mentions", clause, flags=re.I)
    mutated = mutated.replace("settings.json", "a config file")
    assert not _forbids_origination(mutated)
    assert _grants_relay(mutated)


# --- gap row ---------------------------------------------------------------

def test_gap_row_4_recorded_high():
    row = ""
    for line in GAPS.read_text(encoding="utf-8").splitlines():
        if line.startswith("| 4 |"):
            row = line
            break
    assert row, "RULE-BACKSTOP-GAPS.md has no gap row #4"
    assert "**HIGH**" in row, "gap #4 must be ranked HIGH"
    assert "permanently widens the guard" in row, (
        "gap #4 cost cell must record WHY it outranks #1 — acceptance widens "
        "the guard permanently rather than costing one rejection"
    )


def test_gap_4_is_recorded_as_unhooked():
    text = GAPS.read_text(encoding="utf-8")
    assert "Gap #4 → prose containment, deliberately no hook" in text, (
        "the follow-up list must say gap #4 has no hook, so the prose clause "
        "is not mistaken for enforcement"
    )
