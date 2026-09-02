"""Tests for hooks/_lib/_memory_dir.py — shared memory-dir resolver (#823).

Coverage:
  - slugify_project_path: per-character non-alphanumeric substitution
    (Claude Code project-slug rule — a `.` in a path segment must slug to `-`)
  - resolve_memory_dir fallback: the #823 core regression — a cwd containing
    a `.` resolves to the per-character slug directory
  - PRAXIS_MEMORY_DIR env override (existing dir wins, missing dir → None)
  - fallback dir absent → None
  - both consumer hooks (memory-hint, momentum-rule-retrieval-gate) import
    the shared resolver — identity check blocks dual-SoT reintroduction
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import _memory_dir  # noqa: E402

FAKE_CWD = "/Users/jane.doe/projects/praxis"
FAKE_SLUG = "-Users-jane-doe-projects-praxis"


def _load_impl(name: str, rel_path: str):
    """Load a hook impl.py by file path (hook dirs contain `-`)."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# slugify_project_path
# --------------------------------------------------------------------------- #

def test_slugify_per_character_substitution():
    assert _memory_dir.slugify_project_path(FAKE_CWD) == FAKE_SLUG


def test_slugify_adjacent_specials_not_collapsed():
    # "/." is two characters → two dashes, never collapsed into one.
    assert (
        _memory_dir.slugify_project_path("/Users/jane.doe/.claude")
        == "-Users-jane-doe--claude"
    )


# --------------------------------------------------------------------------- #
# resolve_memory_dir
# --------------------------------------------------------------------------- #

def _fallback_fixture(tmp_path, monkeypatch):
    """Point HOME/cwd at a fake tree whose cwd contains a `.` (bug trigger)."""
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: FAKE_CWD)
    memory = tmp_path / ".claude" / "projects" / FAKE_SLUG / "memory"
    memory.mkdir(parents=True)
    return memory


def test_fallback_resolves_cwd_with_dot(tmp_path, monkeypatch):
    # Core #823 regression: `.` in the cwd must slug to `-` so the fallback
    # path resolves; the drifted replace("/", "-") copy returned None here.
    memory = _fallback_fixture(tmp_path, monkeypatch)
    assert _memory_dir.resolve_memory_dir() == str(memory)


def test_fallback_dir_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: FAKE_CWD)
    assert _memory_dir.resolve_memory_dir() is None


def test_env_override_existing_dir(tmp_path, monkeypatch):
    env_dir = tmp_path / "custom-memory"
    env_dir.mkdir()
    monkeypatch.setenv("PRAXIS_MEMORY_DIR", str(env_dir))
    assert _memory_dir.resolve_memory_dir() == str(env_dir)


def test_env_override_missing_dir_returns_none(tmp_path, monkeypatch):
    # Env var is authoritative when set: no fallback attempt on a missing dir.
    monkeypatch.setenv("PRAXIS_MEMORY_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: FAKE_CWD)
    (tmp_path / ".claude" / "projects" / FAKE_SLUG / "memory").mkdir(parents=True)
    assert _memory_dir.resolve_memory_dir() is None


# --------------------------------------------------------------------------- #
# CLAUDE_CONFIG_DIR relocation (#853)
# --------------------------------------------------------------------------- #

def test_claude_config_dir_relocates_fallback(tmp_path, monkeypatch):
    # #853 core regression: when CLAUDE_CONFIG_DIR points off ~/.claude, the
    # fallback must read from THAT root, not the hardcoded ~/.claude default
    # (which is left empty here to prove the relocated path is what resolves).
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    relocated = tmp_path / "relocated-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(relocated))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: FAKE_CWD)
    memory = relocated / "projects" / FAKE_SLUG / "memory"
    memory.mkdir(parents=True)
    assert _memory_dir.resolve_memory_dir() == str(memory)


def test_claude_config_dir_unset_falls_back_to_home_claude(tmp_path, monkeypatch):
    # No CLAUDE_CONFIG_DIR → default remains ~/.claude (back-compat).
    memory = _fallback_fixture(tmp_path, monkeypatch)
    assert _memory_dir.resolve_memory_dir() == str(memory)


# --------------------------------------------------------------------------- #
# linked-worktree fallback (#824)
# --------------------------------------------------------------------------- #

WORKTREE_CWD = "/Users/jane.doe/projects/praxis-issue-824"


# The resolver spawns through the shared budget-aware runner (issue #1178), so
# these stub `run_git` rather than `subprocess.run`. That runner already folds
# every failure — missing binary, nonzero exit, timeout, no runway left in the
# dispatch-group budget — into a single `None`, which is why the error cases
# below stub a return value instead of raising.
def _fake_git(stdout):
    return lambda *a, **k: stdout


def test_worktree_cwd_resolves_to_main_project_memory(tmp_path, monkeypatch):
    # Core #824 regression: from a linked-worktree cwd the cwd-slug dir does
    # not exist, but the main worktree (parent of --git-common-dir) does.
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: WORKTREE_CWD)
    monkeypatch.setattr(
        _memory_dir, "run_git", _fake_git(f"{FAKE_CWD}/.git\n")
    )
    memory = tmp_path / ".claude" / "projects" / FAKE_SLUG / "memory"
    memory.mkdir(parents=True)
    assert _memory_dir.resolve_memory_dir() == str(memory)


def test_worktree_cwd_honors_relocated_config_dir(tmp_path, monkeypatch):
    # The two #853 fixes above only exercise the cwd-slug branch, and the #824
    # test above only exercises the ~/.claude default — so the crossing of the
    # two (relocated config root reached via the main-worktree slug) was the
    # one path no test covered, even though it is the shape this repo actually
    # runs in. ~/.claude is left empty so only the relocated root can answer.
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    relocated = tmp_path / "relocated-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(relocated))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: WORKTREE_CWD)
    monkeypatch.setattr(
        _memory_dir, "run_git", _fake_git(f"{FAKE_CWD}/.git\n")
    )
    memory = relocated / "projects" / FAKE_SLUG / "memory"
    memory.mkdir(parents=True)
    assert _memory_dir.resolve_memory_dir() == str(memory)


def test_worktree_fallback_git_absent_returns_none(tmp_path, monkeypatch):
    # Fail-safe: any git error (binary absent, not a repo) → current behavior.
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: WORKTREE_CWD)

    monkeypatch.setattr(_memory_dir, "run_git", _fake_git(None))
    (tmp_path / ".claude" / "projects" / FAKE_SLUG / "memory").mkdir(parents=True)
    assert _memory_dir.resolve_memory_dir() is None


def test_worktree_fallback_git_error_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: WORKTREE_CWD)
    monkeypatch.setattr(_memory_dir, "run_git", _fake_git(None))
    (tmp_path / ".claude" / "projects" / FAKE_SLUG / "memory").mkdir(parents=True)
    assert _memory_dir.resolve_memory_dir() is None


def test_main_worktree_same_as_cwd_skips_retry(tmp_path, monkeypatch):
    # From the main worktree, --git-common-dir abspath()s back to cwd/.git —
    # the `!= cwd` guard must skip the redundant retry and return None when
    # the (identical) slug dir is absent.
    monkeypatch.delenv("PRAXIS_MEMORY_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_memory_dir.os, "getcwd", lambda: FAKE_CWD)
    monkeypatch.setattr(
        _memory_dir, "run_git", _fake_git(f"{FAKE_CWD}/.git\n")
    )
    assert _memory_dir.resolve_memory_dir() is None


# --------------------------------------------------------------------------- #
# consumer hooks share the single resolver (dual-SoT guard)
# --------------------------------------------------------------------------- #

def test_memory_hint_uses_shared_resolver(tmp_path, monkeypatch):
    memory = _fallback_fixture(tmp_path, monkeypatch)
    impl = _load_impl(
        "memory_hint_impl", "hooks/advisory-nudge/memory-hint/impl.py"
    )
    assert impl.resolve_memory_dir is _memory_dir.resolve_memory_dir
    assert impl.resolve_memory_dir() == str(memory)


def test_momentum_gate_uses_shared_resolver(tmp_path, monkeypatch):
    memory = _fallback_fixture(tmp_path, monkeypatch)
    impl = _load_impl(
        "momentum_gate_impl",
        "hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py",
    )
    assert impl.resolve_memory_dir is _memory_dir.resolve_memory_dir
    assert impl.resolve_memory_dir() == str(memory)
