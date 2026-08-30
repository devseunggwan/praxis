"""Regression fixture for the prose-evidence clause (issue #1044).

The #1044 rule is the one class that cannot have a hook: an evidence claim
whose only carrier is a sentence emits no tool call, so the prose is not merely
the current containment (as in `test_bypass_delegation_clause.py`) but the
whole remedy by construction. That makes the prose the artifact under test.

Its content is three-part — the class must be *named as unreachable*, the two
compose-time questions must survive, and question 1 must keep the self-induced
run (#1044's failure 3, folded in there rather than given its own section).
A clause that names the class while dropping the questions leaves a reader with
a diagnosis and no procedure; a question 1 that keeps only "who authored" still
reads as complete while the failure it also has to carry is gone. Each part is
pinned, and each is proved load-bearing by re-running its predicate against a
mutation that removes only that part.

The retrospect side is pinned too: `reach=none` + `worse_axis: yes` needs a
stated destination on both surfaces that define the receipt, or a retrospect
hitting the combination proposes the reachable half and drops the rest — the
exact behaviour the issue was filed over.

Two rules keep the assertions honest in both directions, since "load-bearing"
and "survives an innocuous reformat" pull against each other:

* every match and every mutation runs on whitespace-normalized text — see
  `_clause` and `_norm`, so a pure re-wrap that changes no words cannot move a
  pattern off its target;
* every mutation goes through `_mutate`, which fails when its pattern matches
  nothing — a silently no-op mutation turns its control into a tautology.

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


def _norm(text: str) -> str:
    """Collapse every whitespace run to one space (idempotent)."""
    return re.sub(r"\s+", " ", text)


def _clause(text: str) -> str:
    """Return the `Claims that terminate in prose` section, or '' if absent.

    The section is returned whitespace-normalized. Normalizing here rather
    than inside each predicate is deliberate: no caller ever holds the raw
    hard-wrapped clause, so no pattern — match or mutation — can be written
    against a line break that a re-wrap is free to move. Re-wrapping the
    paragraph at 92 columns, for one real example, puts a newline between
    `gate` and `is`.
    """
    m = re.search(
        r"^### Claims that terminate in prose\s*$(.*?)(?=^#{1,3} |\Z)",
        text,
        re.M | re.S,
    )
    return _norm(m.group(1)).strip() if m else ""


def _mutate(pattern: str, repl: str, text: str) -> str:
    """`re.sub`, asserting the pattern actually matched something.

    A mutation that silently no-ops leaves its control asserting a property of
    the unmutated prose, which is either a tautology or a spurious failure
    depending on which way the assertion points.
    """
    mutated, count = re.subn(pattern, repl, text)
    assert count, f"mutation matched nothing, so it controls nothing: {pattern!r}"
    return mutated


# --- the three parts, as independent predicates -----------------------------
# Every predicate below takes the normalized clause from `_clause`.


def _names_the_class_unreachable(clause: str) -> bool:
    """Clause states that no hook can reach this class — not that one is owed.

    A section that merely restates the evidence rules would satisfy a keyword
    check while implying enforcement exists, which is the failure mode the
    issue names; so the absence of a gate must be asserted, not implied.
    """
    return bool(re.search(r"no gate is coming", clause, re.I)) and bool(
        re.search(r"discipline is the whole remedy", clause, re.I)
    ) and bool(re.search(r"nothing for a[^.]{0,60}hook to intercept", clause))


def _carries_both_compose_time_questions(clause: str) -> bool:
    """Clause carries the authorship question AND the count question."""
    return bool(re.search(r"[Ww]ho authored[^?]{0,120}\?", clause)) and bool(
        re.search(r"count in my sentence match the number of blocks", clause)
    )


def _folds_in_the_self_induced_run(clause: str) -> bool:
    """Clause keeps the self-induced run — #1044's failure 3.

    That failure (a run the author started themselves discharging an anchor
    that asked for organic evidence) was folded into question 1 instead of
    getting its own section, so this clause is the only place it lives. Both
    halves are required, because either alone is deletable without a reader
    noticing: `who triggered` in the question, and the sentence that says what
    a self-started run does not buy.
    """
    return bool(
        re.search(r"[Ww]ho authored\b[^?]{0,60}\bwho triggered\b[^?]{0,60}\?", clause)
    ) and bool(
        re.search(r"run the author started themselves is not organic traffic", clause)
    ) and bool(re.search(r"does not discharge a verification anchor", clause))


# --- must fire: the shipped prose satisfies all three parts -----------------

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


def test_clause_folds_in_the_self_induced_run():
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    assert _folds_in_the_self_induced_run(clause), (
        "question 1 is where the self-induced run was folded in, so it must "
        "keep the `who triggered` half and the sentence saying a run the "
        "author started is not organic traffic"
    )


# --- must stay silent: each part is load-bearing ---------------------------
# Remove one part from a copy; the matching predicate must go False while the
# others stay True. A predicate that survives its own deletion tests nothing.

def test_dropping_the_unreachability_fails_only_that_check():
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    mutated = _mutate(r"and no gate is coming", "and a gate is owed", clause)
    assert not _names_the_class_unreachable(mutated)
    assert _carries_both_compose_time_questions(mutated)
    assert _folds_in_the_self_induced_run(mutated)


def test_dropping_the_questions_fails_only_that_check():
    # The numbered block physically contains the self-induced run, so that
    # predicate necessarily goes with it; independence is claimed against the
    # unreachability half, which sits in the paragraphs above.
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    mutated = _mutate(r"\s\d\.\s\*\*.*$", "", clause)
    assert not _carries_both_compose_time_questions(mutated)
    assert _names_the_class_unreachable(mutated)


def test_dropping_the_self_induced_run_fails_only_that_check():
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    mutated = _mutate(r" — and who triggered —", "", clause)
    mutated = _mutate(
        r" A run the author started themselves is not organic traffic,"
        r" and does not discharge a verification anchor that asked for it\.",
        "",
        mutated,
    )
    assert not _folds_in_the_self_induced_run(mutated)
    # The shortened question still reads as a complete question 1, which is
    # exactly why the check above has to exist separately.
    assert _carries_both_compose_time_questions(mutated)
    assert _names_the_class_unreachable(mutated)


# --- a pure re-wrap must change nothing ------------------------------------

def test_predicates_survive_a_pure_rewrap():
    """Re-wrapping the clause at any width changes no verdict.

    The prose is hard-wrapped, so a later editor is free to reflow it; a
    word-for-word-identical reflow that turned CI red would train the next
    reader to treat this file as noise.
    """
    clause = _clause(ETHOS.read_text(encoding="utf-8"))
    words = clause.split(" ")
    for width in range(60, 101):
        lines, line = [], ""
        for word in words:
            if line and len(line) + 1 + len(word) > width:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}" if line else word
        lines.append(line)
        rewrapped = "\n".join(lines)
        assert _norm(rewrapped).strip() == clause, f"re-wrap at {width} lost words"
        rewrapped = _norm(rewrapped).strip()
        assert _names_the_class_unreachable(rewrapped)
        assert _carries_both_compose_time_questions(rewrapped)
        assert _folds_in_the_self_induced_run(rewrapped)
        assert not _names_the_class_unreachable(
            _mutate(r"and no gate is coming", "and a gate is owed", rewrapped)
        )


# --- retrospect destination ------------------------------------------------
# Both surfaces that define the receipt must route the combination, because a
# retrospect reads SKILL.md and only follows the reference on demand.

COMBINATION = re.compile(r"`reach=none` with `worse_axis: yes`")
ROUTING_WINDOW = 800


def _routes_the_combination(text: str) -> bool:
    """Text names the combination and states, right there, where it goes.

    Scoped to the routing paragraph on purpose. A bare `worse_axis: yes`
    substring is already present in `stage3-reporting.md` at base, inside the
    `retrospect:remedy_reach` fence template (`... | worse_axis: yes|no|na`),
    so a whole-file check for it pins prose this rule did not write. What has
    to exist is the destination — stay at `note`, cite the ETHOS class — and
    it has to sit next to the combination that sends a reader looking for it.
    """
    norm = _norm(text)
    m = COMBINATION.search(norm)
    if not m:
        return False
    window = norm[m.end():m.end() + ROUTING_WINDOW]
    return "`note`" in window and ANCHOR in window


def test_retrospect_surfaces_route_reach_none_worse_axis_yes():
    for path in (RETRO_SKILL, RETRO_REPORTING):
        text = path.read_text(encoding="utf-8")
        assert COMBINATION.search(_norm(text)), (
            f"{path.name} must name the reach=none + worse_axis: yes "
            "combination, or a retrospect hitting it has no destination"
        )
        assert _routes_the_combination(text), (
            f"{path.name} must send the combination somewhere — the finding "
            "stays `note`, and the ETHOS.md class is cited — beside the "
            "combination itself, not left to be re-derived"
        )


def test_dropping_the_routing_paragraph_fails_the_check():
    """The routing paragraph, not an incidental substring, is what is pinned."""
    for path in (RETRO_SKILL, RETRO_REPORTING):
        norm = _norm(path.read_text(encoding="utf-8"))
        m = COMBINATION.search(norm)
        assert m, f"{path.name}: nothing to mutate"
        mutated = norm[:m.start()] + norm[m.end() + ROUTING_WINDOW:]
        assert not _routes_the_combination(mutated), (
            f"{path.name}: the check survives deleting the paragraph it pins"
        )
    # And the reason scoping was needed: with that paragraph gone,
    # stage3-reporting.md still carries `worse_axis: yes` in its fence
    # template, so an unscoped substring check would have stayed green.
    norm = _norm(RETRO_REPORTING.read_text(encoding="utf-8"))
    m = COMBINATION.search(norm)
    assert "worse_axis: yes" in norm[:m.start()] + norm[m.end() + ROUTING_WINDOW:]
