"""Tests for scripts/check-changelog-coverage.py (issue #1228).

The guard exists because release-please skipped three commits inside green runs.
Its fixtures are therefore the real subjects from those runs, in both polarities:

  - FAIL side — the three commits release-please logged as unparseable
    (b328852b/#1210, 4b0df391/#1212, 2d86ff6c/#1232) must be reported when the
    changelog has no entry for them;
  - PASS side — the commits it parsed fine (ed44c51/#1198, 5fdff21/#1205,
    3d6a72f/#1207) must stay silent when their entries are present. Without this
    side, "the guard caught it" and "the guard always fails" look identical.

The rest covers the input surface: which types require an entry, which of the
two match forms (sha, the entry's own `([#N](` link) count, and the shapes that must not be mistaken for
coverage.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check-changelog-coverage.py"


def _load():
    spec = importlib.util.spec_from_file_location("changelog_coverage", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cov = _load()

# Verbatim from `git log --format='%H %s'` on main.
FAIL_COMMITS = [
    "b328852b86dd7667193b71a02dd691e705631d22 refactor(codex-review-wrap): split the body into references/ and fit the description budget (#1210)",
    "4b0df3910e18be2b1906c4590b17af422a205a55 fix(hooks): repair red main after the argv normalization, and state the anchor `gh` prerequisite (#1212)",
    "2d86ff6c7f389e4555f46907f50d10b09c3a607c refactor(hooks): extract shared _lib helpers (#1232)",
]
OK_COMMITS = [
    "ed44c51a0000000000000000000000000000dead perf(hooks): normalize matcher spellings and add Edit/Write dispatch groups (#1198)",
    "5fdff21a0000000000000000000000000000dead fix(hooks): route scope-confirm logs and gh-label cache into documented roots (#1205)",
    "3d6a72fa0000000000000000000000000000dead perf(hooks): shell-append fire records and bound the silent-pass scan (#1207)",
]

TYPES = {"feat", "fix", "perf", "refactor", "docs", "ci"}


def _entry(pr: str) -> str:
    """One changelog line in release-please's shape, for PR number `pr`."""
    return (
        f"* **hooks:** something ([#{pr}](https://github.com/o/r/issues/{pr})) "
        f"([abc1234](https://github.com/o/r/commit/abc1234))"
    )


# --- the two polarities the issue asks for ----------------------------------


def test_unparsed_commits_are_reported():
    commits = cov.parse_commits("\n".join(FAIL_COMMITS + OK_COMMITS))
    changelog = "\n".join(_entry(pr) for pr in ("1198", "1205", "1207"))
    missing = cov.find_missing(commits, changelog, TYPES)
    assert [c.pr for c in missing] == ["1210", "1212", "1232"]


def test_parsed_commits_are_silent():
    commits = cov.parse_commits("\n".join(OK_COMMITS))
    changelog = "\n".join(_entry(pr) for pr in ("1198", "1205", "1207"))
    assert cov.find_missing(commits, changelog, TYPES) == []


# --- what requires an entry --------------------------------------------------


def test_required_types_reads_the_repo_config():
    types = cov.required_types(_REPO / "release-please-config.json")
    # hidden: true in the config — release-please omits these by design.
    assert {"chore", "test", "style"}.isdisjoint(types)
    assert {"feat", "fix", "perf", "refactor", "docs", "ci"} <= types


def test_hidden_and_unlisted_types_are_not_required():
    commits = cov.parse_commits(
        "aaa1 chore: remove OpenCode platform support (#1226)\n"
        "aaa2 test(hooks): verify dispatch budget (#1217)\n"
        "aaa3 build(deps): bump something (#1)\n"
        "aaa4 Merge branch 'main' into topic\n"
    )
    assert cov.find_missing(commits, "", TYPES) == []


def test_breaking_and_scopeless_subjects_still_require_an_entry():
    commits = cov.parse_commits("aaa1 feat!: drop the legacy path (#9)\naaa2 fix: typo (#10)")
    assert [c.pr for c in cov.find_missing(commits, "", TYPES)] == ["9", "10"]


# --- what counts as coverage -------------------------------------------------


def test_full_sha_alone_covers_a_commit_with_no_pr_reference():
    sha = "0a9d31833659b114c7123782aa67c0265c2d08c7"
    commits = cov.parse_commits(f"{sha} ci: guard the workflow pinning discipline")
    assert cov.find_missing(commits, f"([0a9d318](https://o/r/commit/{sha}))", TYPES) == []
    assert len(cov.find_missing(commits, "unrelated text", TYPES)) == 1


def test_a_longer_number_does_not_cover_a_shorter_one():
    commits = cov.parse_commits("aaa1 fix(hooks): something (#123)")
    assert len(cov.find_missing(commits, _entry("1234"), TYPES)) == 1


def test_a_closes_reference_in_another_entry_is_not_coverage():
    """release-please appends `closes [#N](...)` to an unrelated entry — three
    sit in the pending v7.14.0 release. Only the entry's own `([#N](` link
    counts, or a dropped #1213 would read as covered by #1223's entry."""
    commits = cov.parse_commits("aaa1 fix(hooks): something (#1213)")
    changelog = (
        "* **hooks:** pr-anchor-existence-gate records no fire events "
        "([#1223](https://o/r/issues/1223)) ([a86f695](https://o/r/commit/a86f695)), "
        "closes [#1213](https://o/r/issues/1213)"
    )
    assert len(cov.find_missing(commits, changelog, TYPES)) == 1


def test_short_sha_alone_does_not_count_as_coverage():
    """release-please prints a short sha as link text and the full one in the
    href; matching the short form would let a coincidental 7-hex string pass."""
    commits = cov.parse_commits("0a9d31833659b114c7123782aa67c0265c2d08c7 ci: something")
    assert len(cov.find_missing(commits, "([0a9d318](https://o/r/commit/0a9d318))", TYPES)) == 1


# --- CLI ---------------------------------------------------------------------


def _run(tmp_path, commits: list[str], changelog: str):
    commits_file = tmp_path / "commits.txt"
    commits_file.write_text("\n".join(commits) + "\n", encoding="utf-8")
    changelog_file = tmp_path / "changelog.md"
    changelog_file.write_text(changelog, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--commits-file",
            str(commits_file),
            "--changelog-file",
            str(changelog_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_exits_1_and_names_the_dropped_commits(tmp_path):
    result = _run(tmp_path, FAIL_COMMITS + OK_COMMITS, "\n".join(_entry(p) for p in ("1198", "1205", "1207")))
    assert result.returncode == 1
    assert "#1210" in result.stderr and "#1232" in result.stderr
    assert "#1198" not in result.stderr
    assert "commit could not be parsed" in result.stderr


def test_cli_exits_0_when_every_entry_is_present(tmp_path):
    result = _run(tmp_path, OK_COMMITS, "\n".join(_entry(p) for p in ("1198", "1205", "1207")))
    assert result.returncode == 0
    assert "changelog-coverage OK" in result.stdout


def test_cli_fails_open_on_an_unreadable_config(tmp_path):
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--config", str(tmp_path / "nope.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "warning" in result.stderr


def test_cli_fails_open_when_the_release_tag_is_absent(tmp_path):
    """A shallow clone or a pre-first-release repo must not block main."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@e",
         "commit", "-q", "--allow-empty", "-m", "feat: seed"],
        check=True,
    )
    (tmp_path / "release-please-config.json").write_text(
        json.dumps({"changelog-sections": [{"type": "feat", "section": "Added"}]}), encoding="utf-8"
    )
    (tmp_path / ".release-please-manifest.json").write_text(
        json.dumps({".": "9.9.9"}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "v9.9.9 not found" in result.stderr
