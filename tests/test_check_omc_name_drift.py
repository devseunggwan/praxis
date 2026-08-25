"""Tests for scripts/check-omc-name-drift.py.

omc retires workflow names without aliases, so a praxis reference to one stops
resolving the moment the plugin updates. These tests cover:

  - the real tree passes (nothing references a retired name),
  - the input surface: which strings count as a reference and which do not,
  - a planted reference is reported with its file, line, and replacement,
  - the script and this test are exempt, so neither reports its own fixtures,
  - main() exits 0 on a clean tree and 1 on drift.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check-omc-name-drift.py"


def _load():
    spec = importlib.util.spec_from_file_location("omc_name_drift", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drift = _load()


# The temp trees below hold neither exempt file, so the real repo-relative paths
# would never match anyway; naming them keeps the fixtures reading like the
# production set rather than like an empty exemption.
_EXEMPT_IN_TMP = frozenset(
    {
        Path("scripts/check-omc-name-drift.py"),
        Path("tests/test_check_omc_name_drift.py"),
    }
)


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A tracked temp tree — the checker reads `git ls-files`, so an untracked
    file would be skipped and a detection test would pass for the wrong reason."""
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_real_tree_holds():
    assert drift.check() == []


def test_snapshot_table_is_well_formed():
    assert len(drift.RETIRED_NAMES) == 17
    for name, replacement in drift.RETIRED_NAMES.items():
        assert name and not name.startswith("oh-my-claudecode:")
        assert replacement, f"{name} has no stated replacement"


# Each case is one variant of the input surface the matcher classifies.
def test_input_surface_classification():
    cases = {
        # references — the prefixed forms that actually stop resolving
        'Skill(skill="oh-my-claudecode:ultrawork")': True,
        "/oh-my-claudecode:deep-dive": True,
        "`oh-my-claudecode:sciomc`": True,
        "Agent(subagent_type='oh-my-claudecode:merge-readiness')": True,
        "oh-my-claudecode:setup": True,
        # not references
        "a praxis ultrawork session hallucinated a merge": False,
        "oh-my-claudecode:research": False,
        "oh-my-claudecode:omc-setup": False,
        "oh-my-claudecode:setup-wizard": False,
    }
    for text, expected in cases.items():
        assert bool(drift._HIT_RE.search(text)) is expected, text


def test_planted_reference_is_reported(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path, {"docs/routing.md": "use `oh-my-claudecode:ultraqa` here\n"})
    monkeypatch.setattr(drift, "REPO", repo)
    monkeypatch.setattr(drift, "_EXEMPT", _EXEMPT_IN_TMP)
    hits = drift.check()
    assert len(hits) == 1
    assert hits[0].startswith("docs/routing.md:1")
    assert "ultraqa" in hits[0] and "verify" in hits[0]


def test_replacements_match_the_migration_table():
    # The replacement string is recovery guidance, so it has to name the whole
    # Replacement column. omc's table pairs ccg with `ask` AND `team`; the
    # "run ask codex / ask antigravity" half lives in its Notes column, and
    # quoting only that half drops `team` from the advice.
    assert drift.RETIRED_NAMES["ccg"] == (
        "ask + team (run ask codex and ask antigravity, then synthesize)"
    )
    assert drift.RETIRED_NAMES["ultrapilot"] == "team"
    assert drift.RETIRED_NAMES["swarm"] == "team"
    assert drift.RETIRED_NAMES["learner"] == "remember"
    assert drift.RETIRED_NAMES["writer-memory"] == "remember"


def test_live_names_do_not_drift(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path, {"a.md": "`oh-my-claudecode:research` `oh-my-claudecode:team`\n"})
    monkeypatch.setattr(drift, "REPO", repo)
    monkeypatch.setattr(drift, "_EXEMPT", _EXEMPT_IN_TMP)
    assert drift.check() == []


def test_exempt_files_are_skipped(tmp_path, monkeypatch):
    # Both spell retired names by definition — the script as its denylist, this
    # test as its fixtures — so scanning either would fail every clean run.
    # This one bites only once the test file is tracked, which is the state the
    # checker sees in CI but not while the file is still untracked locally.
    repo = _git_repo(
        tmp_path,
        {
            "scripts/check-omc-name-drift.py": "oh-my-claudecode:sciomc\n",
            "tests/test_check_omc_name_drift.py": "oh-my-claudecode:ultrawork\n",
        },
    )
    monkeypatch.setattr(drift, "REPO", repo)
    assert drift.check() == []


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    assert drift.main() == 0
    assert "none referenced" in capsys.readouterr().out

    repo = _git_repo(tmp_path, {"b.md": "oh-my-claudecode:swarm\n"})
    monkeypatch.setattr(drift, "REPO", repo)
    monkeypatch.setattr(drift, "_EXEMPT", _EXEMPT_IN_TMP)
    assert drift.main() == 1
    assert "FAILED" in capsys.readouterr().out
