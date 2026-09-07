"""Regression fixture for the bypass-delegation clause (issue #1009).

The #1009 rule lives only in prose — no hook backstops its prose and menu
lanes (see `docs/hook/RULE-BACKSTOP-GAPS.md` gap #4; the follow-up `Write`
lane has `settings-path-advisory`, #1337), so the prose itself is the
artifact under test. Its whole content is a *distinction*: praxis' own sanctioned
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
  - A clause that keeps every keyword and REVERSES the policy fails both
    halves — deletion controls alone cannot see that case.
  - RULE-BACKSTOP-GAPS.md carries gap row #4 at HIGH cost and records that
    the containment is prose, not a hook.

Test override: `PRAXIS_TEST_REPO_ROOT` points the checks at another checkout.
Used to prove the suite is not vacuous — pointed at a copy with principle 5
and gap #4's RECORD stripped out (the table row and the follow-up bullet, both
of which the checks read), every repo-reading check fails; against this tree
they pass. Two boundaries worth stating, because both were measured rather
than assumed: stripping the table row ALONE leaves
`test_gap_4_is_recorded_as_unhooked` green, since the sentence it reads lives
in the follow-up list; and `test_an_inverted_clause_fails_both_checks` stays
green under any strip, because it feeds the predicates a literal and never
opens the repo.
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
    """Clause explicitly permits relaying praxis' OWN bypass line.

    The permission verb and the ownership must be asserted together. Keyword
    presence alone passes a clause stating the opposite policy — see
    `INVERTED_CLAUSE` below.
    """
    return "Bypass (if truly needed)" in clause and bool(
        re.search(r"\bMAY\s+relay\b", clause)
    ) and bool(re.search(r"gate's own|praxis' own|hook printed", clause))


def _forbids_origination(clause: str) -> bool:
    """Clause explicitly forbids agent-originated routes around the guard.

    The prohibition must attach to origination: `originates … is permitted` is
    this rule inverted, not a statement of it, so a prohibition verb is
    required inside the same sentence as the keyword.
    """
    return "settings.json" in clause and bool(
        re.search(r"permission rule", clause)
    ) and bool(re.search(
        r"originat\w*[^.]{0,40}?\b(forbidden|prohibited|MAY NOT|must not|never)\b",
        clause,
        re.I,
    ))


# A clause carrying every keyword either predicate looks for while stating the
# opposite policy. Both predicates returned True on it before the relationship
# requirements above were added, so the suite would have passed a wholesale
# policy inversion of the rule it exists to pin.
INVERTED_CLAUSE = (
    "**Anything goes.** The agent MAY hand the user whatever route is "
    "convenient: add a permission rule, walk them through a "
    "`.claude/settings.json` edit, or relay the gate's `Bypass (if truly "
    "needed)` line. Every route the agent originates is permitted."
)


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


def test_an_inverted_clause_fails_both_checks():
    """The keyword-presence failure mode, pinned in both directions.

    Deleting a half proves each predicate reacts to absence. It does not prove
    the predicate reads the policy: a clause that keeps every keyword and
    reverses the rule is the case a presence check cannot see.
    """
    assert not _grants_relay(INVERTED_CLAUSE)
    assert not _forbids_origination(INVERTED_CLAUSE)


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
    assert (
        "Gap #4, prose and menu lanes → prose containment, deliberately no hook"
        in text
    ), (
        "the follow-up list must say the prose and menu lanes of gap #4 have "
        "no hook, so the prose clause is not mistaken for enforcement"
    )
    # The follow-up-write lane is the one lane that is a tool call; #1337
    # item 3 hooked it. The record must name the hook AND keep the other two
    # lanes unhooked, so neither half of the split is mistaken for the whole.
    assert "Gap #4, follow-up-write lane → `settings-path-advisory`" in text, (
        "the follow-up list must record which lane of gap #4 the settings "
        "advisory covers"
    )
