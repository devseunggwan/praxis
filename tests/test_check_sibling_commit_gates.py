"""Tests for scripts/check-sibling-commit-gates.py.

The sibling `git commit` gate list is the premise behind side-effect-scan's
ADVISE tier, and it was hand-copied into three prose surfaces until issue #1127
gave it a machine-readable source (`gates: ["git-commit"]` in the manifest).
These tests cover:

  - the real tree agrees with the manifest,
  - the derivation itself: which manifest entries contribute a name,
  - the set difference in BOTH directions — a name dropped from a surface, and a
    name left on a surface the manifest no longer carries (the second is the
    shape PR #1123 shipped),
  - the count words, which are what actually drifted,
  - extraction failure as drift, not a silent pass — a surface reworded out of
    the shape the checker reads must fail loudly rather than verify nothing,
  - main() exits 0 on a clean tree and 1 on drift.

Fixtures are built by copying the four real surfaces into a temp tree and
mutating one of them, so no case can pass because the fixture drifted away from
the production prose: every anchor string is asserted present before it is
replaced.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check-sibling-commit-gates.py"


def _load():
    spec = importlib.util.spec_from_file_location("sibling_commit_gates", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gates = _load()

# The list this repo curates today. Spelled out here on purpose: this file is a
# third, intentional copy, so renaming a hook across the manifest and every
# prose surface at once still has to come through here.
EXPECTED = [
    "block-commit-without-codex-review",
    "block-rename-sweep-survivors",
    "commit-decomposition-advisory",
    "commit-title-format-check",
    "commit-title-length-check",
    "pre-commit-staged-file-enumeration",
    "verify-commit-flag-override",
]

_SURFACE_FILES = (gates.MANIFEST, gates.SPEC, gates.IMPL, gates.TEST)

# The manifest block the fixtures edit, quoted verbatim so a reformat of the
# manifest breaks the fixture loudly instead of silently no-opping.
_RENAME_SWEEP_ENTRY = (
    '      "name": "block-rename-sweep-survivors",\n'
    '      "role": "preflight-gate",\n'
    '      "event": "PreToolUse",\n'
    '      "matcher": "Bash",\n'
)
_RENAME_SWEEP_GATES = '      "gates": [\n        "git-commit"\n      ],\n'


def _tree(tmp_path: Path, edits: dict[str, tuple[str, str]] | None = None) -> Path:
    """A copy of the real surfaces under tmp_path, with optional replacements.

    `edits` maps a repo-relative path to an (old, new) pair; `old` must be
    present. Hook directories are created empty — the derivation only asserts
    the directory exists, it never reads it.
    """
    for rel in _SURFACE_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REPO / rel, dest)
    for name in EXPECTED:
        for role in ("preflight-gate", "advisory-nudge"):
            if (_REPO / "hooks" / role / name).is_dir():
                (tmp_path / "hooks" / role / name).mkdir(parents=True, exist_ok=True)
    for rel, (old, new) in (edits or {}).items():
        path = tmp_path / rel
        text = path.read_text(encoding="utf-8")
        assert old in text, f"fixture anchor not found in {rel}: {old!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return tmp_path


def test_real_tree_holds():
    assert gates.check(_REPO) == []


def test_derivation_matches_the_curated_list():
    derived, drifts = gates.derive(_REPO)
    assert drifts == []
    assert derived == EXPECTED


def test_unedited_fixture_tree_is_clean(tmp_path):
    """Every case below starts from a passing copy, so a failure is the edit."""
    assert gates.check(_tree(tmp_path)) == []


def test_name_missing_from_spec_table_is_drift(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.SPEC: (
                "   | `block-rename-sweep-survivors` | a rename sweep with "
                "surviving occurrences |\n",
                "",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "block-rename-sweep-survivors" in d and "missing from the enumeration" in d
        for d in drifts
    ), drifts


def test_spurious_name_in_spec_table_is_drift(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.SPEC: (
                "   | `verify-commit-flag-override` |",
                "   | `pipefail-advisory` | not a commit gate |\n"
                "   | `verify-commit-flag-override` |",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "pipefail-advisory" in d and "carries no gates" in d for d in drifts
    ), drifts


def test_name_missing_from_impl_docstring_is_drift(tmp_path):
    repo = _tree(tmp_path, {gates.IMPL: ("block-rename-sweep-survivors, ", "")})
    drifts = gates.check(repo)
    assert any(
        "impl.py" in d
        and "block-rename-sweep-survivors" in d
        and "missing from the enumeration" in d
        for d in drifts
    ), drifts


def test_spurious_name_in_impl_docstring_is_drift(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.IMPL: (
                "verify-commit-flag-override.",
                "verify-commit-flag-override, block-sciomc-finding-commit.",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "block-sciomc-finding-commit" in d and "carries no gates" in d
        for d in drifts
    ), drifts


def test_retired_hook_left_on_the_surfaces_is_drift(tmp_path):
    """The PR #1123 shape: the manifest entry loses the label, prose keeps it."""
    repo = _tree(
        tmp_path,
        {gates.MANIFEST: (_RENAME_SWEEP_ENTRY + _RENAME_SWEEP_GATES, _RENAME_SWEEP_ENTRY)},
    )
    drifts = gates.check(repo)
    assert any(
        "block-rename-sweep-survivors" in d and "carries no gates" in d
        for d in drifts
    ), drifts
    # …and every count word now disagrees, on all three surfaces.
    assert sum("prose says 7 siblings, manifest derives 6" in d for d in drifts) >= 3


def test_stale_count_word_alone_is_drift(tmp_path):
    """The exact #1123 failure mode: names edited, a count word left behind."""
    repo = _tree(tmp_path, {gates.SPEC: ("Seven sibling", "Six sibling")})
    drifts = gates.check(repo)
    assert any("prose says 6 siblings, manifest derives 7" in d for d in drifts), drifts


def test_count_word_in_the_shell_test_comment_is_checked(tmp_path):
    """That comment splits `the seven` / `# sibling gates` across two lines —
    the normalizer has to rejoin them or this surface is never really read."""
    repo = _tree(
        tmp_path,
        {
            gates.TEST: (
                "at ASK: the seven\n# sibling gates",
                "at ASK: the five\n# sibling gates",
            )
        },
    )
    drifts = gates.check(repo)
    assert any("prose says 5 siblings, manifest derives 7" in d for d in drifts), drifts


def test_unreadable_table_is_drift_not_a_silent_pass(tmp_path):
    repo = _tree(tmp_path, {gates.SPEC: ("| Sibling hook |", "| Hook |")})
    drifts = gates.check(repo)
    assert any("enumeration not found" in d for d in drifts), drifts


def test_missing_count_claim_is_drift_not_a_silent_pass(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.TEST: (
                "# by seven sibling commit hooks); git-push stays ASK",
                "# by the commit gates); git-push stays ASK",
            )
        },
    )
    # The file carries two count claims; the surface only stops being checked
    # once both are gone, so the second is removed here too.
    path = repo / gates.TEST
    text = path.read_text(encoding="utf-8")
    assert "at ASK: the seven\n# sibling gates" in text
    path.write_text(
        text.replace("at ASK: the seven\n# sibling gates", "at ASK: the\n# gates"),
        encoding="utf-8",
    )
    drifts = gates.check(repo)
    assert any("no '<n> sibling' count claim found" in d for d in drifts), drifts


def test_gate_label_on_a_non_bash_entry_is_drift(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.MANIFEST: (
                '      "name": "commit-title-length-check",\n'
                '      "role": "preflight-gate",\n'
                '      "event": "PreToolUse",\n',
                '      "name": "commit-title-length-check",\n'
                '      "role": "preflight-gate",\n'
                '      "event": "PostToolUse",\n',
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "commit-title-length-check" in d and "PreToolUse(Bash)" in d for d in drifts
    ), drifts


def test_gate_label_on_a_hook_absent_from_disk_is_drift(tmp_path):
    repo = _tree(tmp_path)
    shutil.rmtree(repo / "hooks" / "preflight-gate" / "block-rename-sweep-survivors")
    drifts = gates.check(repo)
    assert any("is not on disk" in d for d in drifts), drifts


def test_missing_manifest_is_drift(tmp_path):
    repo = _tree(tmp_path)
    (repo / gates.MANIFEST).unlink()
    assert any("manifest missing on disk" in d for d in gates.check(repo)), "no drift"


def test_malformed_manifest_is_drift(tmp_path):
    repo = _tree(tmp_path)
    (repo / gates.MANIFEST).write_text("{not json", encoding="utf-8")
    assert any("not valid JSON" in d for d in gates.check(repo)), "no drift"


def test_main_exit_codes(monkeypatch, tmp_path):
    assert gates.main() == 0
    monkeypatch.setattr(
        gates, "REPO", _tree(tmp_path, {gates.SPEC: ("Seven sibling", "Six sibling")})
    )
    assert gates.main() == 1
