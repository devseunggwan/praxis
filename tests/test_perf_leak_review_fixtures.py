"""Fixture-corpus invariants for perf-leak-review (AC-3, AC-7) (#1429).

Recall is measured live, so what CI can hold is the corpus the live run reads.
Two properties matter most and both are checked here rather than described:

  - every fixture's `change.diff` actually applies to its own `repo/`. The
    paired C2 fixtures share one byte-identical diff across two divergent
    trees, and that invariant is exactly the kind that reads as true in prose
    while being false on disk (the r6 approval condition).
  - the pair's clean half releases the handle without the word `close`
    anywhere in its tree, so a reviewer that greps for `close` instead of
    reading the repository fails it. Grep-passes-by-accident is the failure
    mode the pair exists to catch.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "perf-leak-review"

EXPECTED_DIRS = {
    "c1-n-plus-one",
    "c2-unclosed-resource",
    "c2-closed-elsewhere",
    "c3-unbounded-cache",
    "c4-load-then-filter",
    "c5-leaked-listener",
    "clean",
}
PAIR = ("c2-unclosed-resource", "c2-closed-elsewhere")


def _meta(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name / "meta.json").read_text(encoding="utf-8"))


def _tree_text(root: Path) -> str:
    """Every tracked byte under `root`, concatenated for whole-tree matching."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def test_inventory_is_exactly_the_expected_seven() -> None:
    found = {p.name for p in FIXTURES.iterdir() if p.is_dir()}
    assert found == EXPECTED_DIRS, f"unexpected fixture set: {sorted(found)}"


def test_every_fixture_has_the_three_parts() -> None:
    for name in sorted(EXPECTED_DIRS):
        base = FIXTURES / name
        assert (base / "repo").is_dir(), f"{name}: repo/ missing"
        assert (base / "change.diff").is_file(), f"{name}: change.diff missing"
        assert (base / "meta.json").is_file(), f"{name}: meta.json missing"


def test_meta_carries_the_required_keys() -> None:
    for name in sorted(EXPECTED_DIRS):
        meta = _meta(name)
        for key in ("expected_class", "requires_repo_read", "language"):
            assert key in meta, f"{name}: meta.json has no {key}"
        assert isinstance(meta["requires_repo_read"], bool), name


def test_each_defect_class_is_planted_once() -> None:
    planted = sorted(
        meta
        for meta in (_meta(name)["expected_class"] for name in EXPECTED_DIRS)
        if meta is not None
    )
    assert planted == ["C1", "C2", "C3", "C4", "C5"], planted


def test_there_are_at_least_two_clean_controls() -> None:
    clean = [
        name for name in EXPECTED_DIRS if _meta(name)["expected_class"] is None
    ]
    assert len(clean) >= 2, f"clean controls: {sorted(clean)}"


def test_at_least_three_languages_are_covered() -> None:
    languages = {_meta(name)["language"] for name in EXPECTED_DIRS}
    assert len(languages) >= 3, sorted(languages)


def test_at_least_one_fixture_requires_reading_the_repo() -> None:
    assert any(_meta(name)["requires_repo_read"] for name in EXPECTED_DIRS)


def test_pair_shares_a_byte_identical_diff() -> None:
    first, second = (
        (FIXTURES / name / "change.diff").read_bytes() for name in PAIR
    )
    assert first == second, "the AC-7 pair must share one diff, byte for byte"


def test_pair_diff_never_uses_the_word_close() -> None:
    """A reviewer grepping the diff for `close` must learn nothing from it."""
    text = (FIXTURES / PAIR[0] / "change.diff").read_text(encoding="utf-8")
    assert "close" not in text.lower()


def test_clean_half_of_the_pair_has_no_close_token_anywhere() -> None:
    """The release is spelled `exec 3<&-`, so grep cannot find it."""
    text = _tree_text(FIXTURES / "c2-closed-elsewhere" / "repo").lower()
    assert "close" not in text, "the clean half's tree must contain no `close`"


def test_release_site_exists_in_the_clean_half_and_not_the_other() -> None:
    meta = _meta("c2-closed-elsewhere")
    site = meta["release_site"]
    released = FIXTURES / "c2-closed-elsewhere" / "repo" / site["file"]
    assert released.is_file(), site["file"]
    assert site["symbol"] in released.read_text(encoding="utf-8")
    absent = _tree_text(FIXTURES / "c2-unclosed-resource" / "repo")
    assert site["symbol"] not in absent, (
        f"{site['symbol']} must not exist in the unreleased half — it is what "
        "separates the two"
    )


def test_every_diff_applies_to_its_own_repo() -> None:
    """The r6 approval condition: the invariant is checked, not described."""
    for name in sorted(EXPECTED_DIRS):
        fixture = FIXTURES / name
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(fixture / "repo", tmp, dirs_exist_ok=True)
            subprocess.run(
                ["git", "init", "-q"], cwd=tmp, check=True, capture_output=True
            )
            proc = subprocess.run(
                ["git", "apply", "--check", str(fixture / "change.diff")],
                cwd=tmp,
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, (
                f"{name}: change.diff does not apply to its own repo/: "
                f"{proc.stderr}"
            )


def test_no_fixture_python_file_looks_like_a_test_module() -> None:
    """pytest collects `tests/` wholesale; a fixture must not join the suite."""
    for path in FIXTURES.rglob("*.py"):
        assert not path.name.startswith("test_"), path
        assert not path.name.endswith("_test.py"), path
