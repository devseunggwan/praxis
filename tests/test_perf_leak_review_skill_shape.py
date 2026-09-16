"""Shape invariants for the perf-leak-review skill and its brief (#1429).

`scripts/run-tests.sh` never executes a skill, so the reviewer's judgement is
not testable here. Two structural properties are, and they are the ones the
closed-list design rests on:

  - the brief's class headings are exactly the validator's class constants. The
    brief is what the reviewer is told and the validator is what rejects it, so
    a drift between them means the reviewer is asked for a class that will be
    refused.
  - the brief names no identifier the fixture corpus declares. A brief that
    quotes a fixture's own symbol is measuring memorisation rather than
    detection, and the live recall number would be worthless.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "perf-leak-review"
SKILL_MD = SKILL_DIR / "SKILL.md"
BRIEF = SKILL_DIR / "references" / "reviewer-brief.md"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "perf-leak-review"

# Declared-symbol patterns per fixture language. Matching declarations rather
# than every word keeps the check on the identifiers a brief could actually
# leak, instead of on shared English.
DECLARATION_RES = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*=", re.M),
    re.compile(r"^\s*(?:export\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", re.M),  # shell function
)

# Shared vocabulary a fixture happens to declare and a class definition cannot
# avoid. Each is a plain English word, not a corpus-specific identifier.
GENERIC_NAMES = frozenset({"main", "size", "apply", "current", "render"})


def _validator_classes() -> dict[str, str]:
    path = SKILL_DIR / "validate_findings.py"
    spec = importlib.util.spec_from_file_location("validate_findings", path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.DEFECT_CLASSES)


def _brief_headings() -> dict[str, str]:
    found: dict[str, str] = {}
    for line in BRIEF.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^### (C\d) — (.+)$", line)
        if match:
            found[match.group(1)] = match.group(2).strip()
    return found


def _fixture_symbols() -> set[str]:
    """Every symbol the corpus declares — in `repo/` and in what the diff adds.

    A diff-added symbol leaks exactly as badly as a pre-existing one: naming it
    in the brief tells the reviewer what to look for in the change it is being
    scored on.
    """
    symbols: set[str] = set()
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file():
            continue
        rel = f"/{path.relative_to(FIXTURES)}"
        if "/repo/" in rel:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif path.name == "change.diff":
            # Added lines only, with the leading "+" stripped so the
            # declaration patterns see ordinary source lines.
            text = "\n".join(
                line[1:]
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
        else:
            continue
        for pattern in DECLARATION_RES:
            symbols.update(pattern.findall(text))
    return {s for s in symbols if s not in GENERIC_NAMES}


def test_skill_and_brief_exist() -> None:
    assert SKILL_MD.is_file()
    assert BRIEF.is_file()


def test_brief_headings_match_the_validator_classes() -> None:
    assert _brief_headings() == _validator_classes()


def test_every_class_has_an_example_block() -> None:
    """A class definition without a worked example is prose, not a signature."""
    text = BRIEF.read_text(encoding="utf-8")
    sections = re.split(r"^### C\d — ", text, flags=re.M)[1:]
    assert len(sections) == 5, len(sections)
    for section in sections:
        assert "```" in section, section.splitlines()[0]
        assert "Signal:" in section, section.splitlines()[0]


def test_brief_names_no_fixture_identifier() -> None:
    """Anti-overfit: the brief must not quote the corpus it is scored against."""
    brief = BRIEF.read_text(encoding="utf-8")
    leaked = sorted(
        symbol
        for symbol in _fixture_symbols()
        if re.search(rf"\b{re.escape(symbol)}\b", brief)
    )
    assert not leaked, (
        f"the brief names fixture identifiers {leaked} — a class-level sentence "
        "is allowed, a corpus symbol is overfitting"
    )


def test_brief_forbids_tools_other_than_read_grep_glob() -> None:
    brief = BRIEF.read_text(encoding="utf-8")
    assert "Read, Grep, Glob only" in brief
    for forbidden in ("Bash", "Write", "Edit"):
        assert forbidden in brief, f"{forbidden} must be named as forbidden"


def test_brief_pins_the_envelope_contract() -> None:
    brief = BRIEF.read_text(encoding="utf-8")
    for token in ('"repo_root"', '"findings"', '"grade": "candidate"'):
        assert token in brief, token
    assert '"findings": []' in brief, "the empty-list answer must be spelled out"


def test_skill_declares_report_only_and_the_closed_list() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "Report-only" in text
    for name in _validator_classes():
        assert f"| {name} |" in text, f"{name} missing from the skill's class table"


def test_skill_frontmatter_lists_no_mutation_tool() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    front = text.split("---")[1]
    for forbidden in ("Write", "Edit", "git commit", "git apply", "gh pr"):
        assert forbidden not in front, f"{forbidden} must not be in allowed-tools"


def test_every_fixture_meta_is_valid_json() -> None:
    for path in sorted(FIXTURES.glob("*/meta.json")):
        json.loads(path.read_text(encoding="utf-8"))
