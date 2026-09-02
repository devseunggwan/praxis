"""Tests for hooks/_lib/_git.py — shared git subprocess helpers (#1178).

Coverage:
  - origin_slug URL matrix against a real temp repo: github ssh (scp-like),
    github https, `.git` suffix, trailing slash, ssh:// form; gitlab and
    self-hosted origins REJECTED (host-anchored regex — the deliberate
    gh-label-verify tightening); no remote at all
  - run_git: success stdout passthrough, nonzero exit -> None,
    rc==0-with-empty-stdout stays distinguishable from failure, bad cwd,
    embedded NUL argv
  - repo_root: inside repo, outside repo
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from _git import origin_slug, repo_root, run_git  # noqa: E402


@pytest.fixture()
def temp_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _set_origin(repo: Path, url: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", url], check=True
    )


# ---------------------------------------------------------------------------
# origin_slug URL matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo/", "owner/repo"),
        ("ssh://git@github.com/owner/repo", "owner/repo"),
        ("git@github.com:my-org/my.repo.git", "my-org/my.repo"),
        # Host-anchored: non-GitHub origins are REJECTED (issue #1178 — the
        # pre-consolidation gh-label-verify regex wrongly matched these).
        ("git@gitlab.com:owner/repo.git", None),
        ("https://gitlab.com/owner/repo.git", None),
        ("https://git.example.com/owner/repo.git", None),
        # A host whose name merely ENDS in github.com is a different host.
        # These are the shapes an un-anchored search() accepts, and the slug
        # would then be handed to `gh --repo` (CodeRabbit, PR #1232).
        ("https://notgithub.com/evil/repo", None),
        ("https://github.company.com/owner/repo", None),
        ("https://github.com.attacker.net/evil/repo", None),
        # …and a github.com that is not the host at all.
        ("https://evil.example/x?u=github.com/owner/repo", None),
        # Userinfo before the host stays accepted.
        ("https://user@github.com/owner/repo", "owner/repo"),
    ],
)
def test_origin_slug_matrix(temp_repo, url, expected):
    _set_origin(temp_repo, url)
    assert origin_slug(cwd=str(temp_repo)) == expected


def test_origin_slug_no_remote(temp_repo):
    assert origin_slug(cwd=str(temp_repo)) is None


def test_origin_slug_outside_repo(tmp_path):
    bare = tmp_path / "notrepo"
    bare.mkdir()
    assert origin_slug(cwd=str(bare)) is None


# ---------------------------------------------------------------------------
# run_git
# ---------------------------------------------------------------------------


def test_run_git_success_returns_stdout(temp_repo):
    out = run_git(["rev-parse", "--show-toplevel"], cwd=str(temp_repo))
    assert out is not None
    assert Path(out.strip()).resolve() == temp_repo.resolve()


def test_run_git_nonzero_exit_returns_none(temp_repo):
    assert run_git(["rev-parse", "--verify", "no-such-ref"], cwd=str(temp_repo)) is None


def test_run_git_empty_stdout_is_not_none(temp_repo):
    # rc==0 with empty stdout must stay distinguishable from failure —
    # callers like `check-ignore -q` branch on `is not None`.
    out = run_git(["status", "--porcelain"], cwd=str(temp_repo))
    assert out == ""


def test_run_git_bad_cwd_returns_none(tmp_path):
    assert run_git(["status"], cwd=str(tmp_path / "missing")) is None


def test_run_git_embedded_nul_returns_none(temp_repo):
    assert run_git(["log", "bad\x00arg"], cwd=str(temp_repo)) is None


# ---------------------------------------------------------------------------
# repo_root
# ---------------------------------------------------------------------------


def test_repo_root_inside(temp_repo):
    root = repo_root(cwd=str(temp_repo))
    assert root is not None
    assert Path(root).resolve() == temp_repo.resolve()


def test_repo_root_outside(tmp_path):
    bare = tmp_path / "plain"
    bare.mkdir()
    assert repo_root(cwd=str(bare)) is None
