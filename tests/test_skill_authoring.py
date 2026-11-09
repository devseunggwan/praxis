"""Rule 30: SKILL.md shape against docs/skill-authoring.md (#1643).

Each case writes one skill directory that breaks exactly one rule and asserts
that rule's id is the only drift, so a check that fires on everything cannot
pass. The clean fixture is the control: it must produce no drift and no
warning.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "skill_authoring", REPO_ROOT / "scripts" / "skill_authoring.py"
)
assert _spec and _spec.loader
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)

CLEAN_FRONTMATTER = {
    "name": "fixture-skill",
    "description": "Do one thing to a fixture.",
    "when_to_use": 'Triggers on "fixture one", "fixture two", "fixture three".',
}
CLEAN_BODY = """# Fixture

## Overview

Why it exists.

## When to Use

- A fixture is needed.

## Process

### Step 1: Act

Do it.

```bash
### Step not-a-step inside a fence
```

Done when: the act is done.

### Step 2: Report

Say it.

Done when: the report is out.
"""


def _write(
    root: Path,
    *,
    name: str = "fixture-skill",
    frontmatter: dict[str, str] | None = None,
    extra_frontmatter: str = "",
    body: str = CLEAN_BODY,
    references: bool = False,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir()
    fields = dict(CLEAN_FRONTMATTER, name=name)
    fields.update(frontmatter or {})
    lines = ["---"] + [f"{k}: {v}" for k, v in fields.items() if v is not None]
    if extra_frontmatter:
        lines.append(extra_frontmatter.rstrip("\n"))
    lines += ["---", "", body]
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    if references:
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "detail.md").write_text("# Detail\n", encoding="utf-8")
    return skill_dir


def _ids(drifts: list[str]) -> list[str]:
    return [d.split("(", 1)[1].split(")", 1)[0] for d in drifts if "(" in d]


def test_clean_skill_has_no_findings(tmp_path: Path) -> None:
    assert rules.skill_shape_findings(_write(tmp_path)) == ([], [])


@pytest.mark.parametrize(
    ("kwargs", "rule_id"),
    [
        ({"name": "fixture-skill", "frontmatter": {"name": "other-name"}}, "30a"),
        ({"frontmatter": {"description": "Do it. Use when asked."}}, "30b"),
        ({"frontmatter": {"description": 'Do it. Triggers on "x".'}}, "30b"),
        ({"frontmatter": {"when_to_use": None}}, "30c"),
        ({"frontmatter": {"when_to_use": 'Use when "a", "b", "c".'}}, "30c"),
        ({"frontmatter": {"description": "x" * 1025}}, "30d"),
        (
            {
                "frontmatter": {
                    "description": "x" * 1000,
                    "when_to_use": 'Triggers on "a", "b", "c". ' + "y" * 600,
                }
            },
            "30d",
        ),
        ({"extra_frontmatter": "allowed-tools:\n  - Read\n  - Bash"}, "30e"),
        ({"extra_frontmatter": "allowed-tools:\n  - Bash(git push *)"}, "30e"),
        ({"extra_frontmatter": "allowed-tools: Read, Bash(gh pr merge *)"}, "30e"),
        ({"extra_frontmatter": "allowed-tools:\n- Read\n- Bash"}, "30e"),
        ({"body": CLEAN_BODY.replace("Done when: the report is out.", "")}, "30f"),
        ({"body": CLEAN_BODY + "\n" + "padding line\n" * 1500}, "30g"),
        ({"body": CLEAN_BODY.replace("## Overview", "## Background")}, "30h"),
    ],
)
def test_each_rule_fires_alone(tmp_path: Path, kwargs: dict[str, object], rule_id: str) -> None:
    drifts, _ = rules.skill_shape_findings(_write(tmp_path, **kwargs))
    assert _ids(drifts) == [rule_id], drifts


@pytest.mark.parametrize(("lines", "expected"), [(10, []), (11, ["30d"])])
def test_folded_description_is_measured_after_folding(
    tmp_path: Path, lines: int, expected: list[str]
) -> None:
    # 10 lines fold to 999 chars (200 words) while the raw block, with its
    # indentation and newlines, is over 1,024; 11 lines fold to 1,099.
    folded = ">\n  " + "\n  ".join(["word " * 20] * lines)
    drifts, _ = rules.skill_shape_findings(
        _write(tmp_path, frontmatter={"description": folded})
    )
    assert _ids(drifts) == expected


def test_large_body_with_references_passes(tmp_path: Path) -> None:
    body = CLEAN_BODY + "\n" + "padding line\n" * 1500
    assert rules.skill_shape_findings(_write(tmp_path, body=body, references=True)) == ([], [])


def test_user_only_skill_skips_routing_and_section_rules(tmp_path: Path) -> None:
    skill = _write(
        tmp_path,
        frontmatter={"when_to_use": 'Use when the user types "/fixture".'},
        extra_frontmatter="disable-model-invocation: true",
        body="# Fixture\n\n## What to do\n\nRun it.\n",
    )
    assert rules.skill_shape_findings(skill) == ([], [])


def test_keyword_count_outside_range_warns_without_failing(tmp_path: Path) -> None:
    many = ", ".join(f'"k{i}"' for i in range(7))
    drifts, warnings = rules.skill_shape_findings(
        _write(tmp_path, frontmatter={"when_to_use": f"Triggers on {many}."})
    )
    assert drifts == []
    assert len(warnings) == 1 and "7 trigger keywords" in warnings[0]


def test_negative_clause_keywords_are_not_counted(tmp_path: Path) -> None:
    clause = 'Triggers on "a", "b", "c". Do NOT activate on "d", "e", "f", "g".'
    assert rules.skill_shape_findings(
        _write(tmp_path, frontmatter={"when_to_use": clause})
    ) == ([], [])


def test_baselined_skill_is_skipped_while_it_violates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rules, "DONE_WHEN_BASELINE", frozenset({"fixture-skill"}))
    body = CLEAN_BODY.replace("Done when: the report is out.", "")
    assert rules.skill_shape_findings(_write(tmp_path, body=body)) == ([], [])


def test_baselined_skill_that_complies_must_leave_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rules, "SECTIONS_BASELINE", frozenset({"fixture-skill"}))
    drifts, _ = rules.skill_shape_findings(_write(tmp_path))
    assert len(drifts) == 1 and "now passes Rule 30h" in drifts[0]


def test_baseline_entry_without_a_skill_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rules, "SPLIT_BASELINE", frozenset({"gone-skill"}))
    monkeypatch.setattr(rules, "DONE_WHEN_BASELINE", frozenset())
    monkeypatch.setattr(rules, "SECTIONS_BASELINE", frozenset())
    orphans = rules.baseline_orphans({"fixture-skill"})
    assert len(orphans) == 1 and "gone-skill" in orphans[0] and "30g" in orphans[0]


def test_repository_skills_pass() -> None:
    skill_dirs = sorted(
        p.parent for p in (REPO_ROOT / "skills").glob("*/SKILL.md")
    )
    assert skill_dirs, "no skills found under skills/"
    drifts = [d for s in skill_dirs for d in rules.skill_shape_findings(s)[0]]
    drifts += rules.baseline_orphans({s.name for s in skill_dirs})
    assert drifts == []
