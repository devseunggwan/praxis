"""Tests + lint for the standard block-message helper (issue #439).

Two concerns:

  1. Helper format contract — `format_block` / `emit_block` render the exact
     five-field standard format, and `bypass_env=None` omits the Bypass line.

  2. Adoption lint — every preflight-gate hook that emits a block/ask message
     must build that message via `hooks/_lib/block_message.py` rather than
     hand-rolling stderr text. A `LEGACY_UNMIGRATED` allowlist carves out the
     hooks not yet ported; the three hooks migrated in issue #439 are
     deliberately NOT on it, so dropping the helper from them — or adding a
     NEW hand-rolled block hook — fails this test.

Run: python3 -m pytest tests/test_block_message.py -q
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "hooks" / "_lib"
PREFLIGHT_DIR = REPO_ROOT / "hooks" / "preflight-gate"

sys.path.insert(0, str(LIB_DIR))

_spec = importlib.util.spec_from_file_location(
    "block_message", LIB_DIR / "block_message.py"
)
assert _spec is not None and _spec.loader is not None
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 1. Helper format contract
# ---------------------------------------------------------------------------

def test_format_block_full_five_fields() -> None:
    out = bm.format_block(
        rule_name="gh issue create dup-search",
        why="no prior search this session",
        correct_path="run gh search issues '<kw>' --repo <repo>",
        bypass_env="CLAUDE_HOOK_BYPASS_DUP_GATE",
        reference="CLAUDE.md → GitHub Issue Hygiene",
    )
    lines = out.split("\n")
    # Header is uppercased rule name + " blocked", then a blank line.
    assert lines[0] == "⚠️ GH ISSUE CREATE DUP-SEARCH blocked"
    assert lines[1] == ""
    assert lines[2].startswith("Why: ")
    assert lines[3].startswith("Correct path: ")
    assert lines[4].startswith("Bypass (if truly needed): CLAUDE_HOOK_BYPASS_DUP_GATE=1")
    assert lines[5].startswith("Reference: ")
    # No trailing newline — caller owns it.
    assert not out.endswith("\n")


def test_format_block_omits_bypass_line_when_none() -> None:
    out = bm.format_block(
        rule_name="gh pr merge approval",
        why="merge is irreversible",
        correct_path="confirm in the dialog",
        bypass_env=None,
        reference="CLAUDE.md → Pre-Merge Reporting",
    )
    assert "Bypass" not in out
    lines = out.split("\n")
    # Header, blank, Why, Correct path, Reference — exactly 5 lines.
    assert len(lines) == 5
    assert lines[-1].startswith("Reference: ")


def test_emit_block_writes_to_stream_with_trailing_newline() -> None:
    import io

    buf = io.StringIO()
    bm.emit_block(
        rule_name="x rule",
        why="y",
        correct_path="z",
        bypass_env=None,
        reference="r",
        stream=buf,
    )
    text = buf.getvalue()
    assert text.startswith("⚠️ X RULE blocked")
    assert text.endswith("\n")


# ---------------------------------------------------------------------------
# 2. Adoption lint
# ---------------------------------------------------------------------------

# Preflight-gate hooks not yet migrated to the helper. The three hooks
# migrated in issue #439 are intentionally absent. Reserved-for-other-issue
# hooks (block-sciomc-finding-commit / commit-title-length-check) stay listed.
# Migrating any of these later = removing it from this set.
LEGACY_UNMIGRATED = {
    "block-ask-end-option",
    "block-commit-without-codex-review",
    "block-manufactured-action-menu",
    "block-pr-without-caller-evidence",
    "block-pr-without-precommit-evidence",
    "block-sciomc-finding-commit",
    "commit-title-length-check",
    "cross-boundary-preflight",
    "gh-flag-verify",
    "gh-json-validator",
    "gh-label-verify",
    "pre-edit-protected-branch-guard",
    "pre-gh-pr-create-dedup-gate",
    "session-intent",
    "side-effect-scan",
    "verify-commit-flag-override",
}

# Hooks that emit a block message and so must use the helper (post-migration).
EXPECTED_MIGRATED = {
    "block-gh-state-all",
    "pre-merge-approval-gate",
    "block-gh-issue-create-without-dup-search",
}

# A hook "emits a block message" if it writes to stderr with a block marker,
# returns the PreToolUse blocking exit code, or surfaces an ask/deny decision.
_EMITS_BLOCK_RE = re.compile(
    r"stderr\.write|permissionDecision|return 2\b|emit_ask|emit_block",
)
_USES_HELPER_RE = re.compile(r"from block_message import|import block_message")


def _hook_impls() -> list[Path]:
    return sorted(PREFLIGHT_DIR.glob("*/impl.py"))


def test_migrated_hooks_use_helper() -> None:
    """The 3 issue-#439 hooks must import the standard block helper."""
    for name in sorted(EXPECTED_MIGRATED):
        impl = PREFLIGHT_DIR / name / "impl.py"
        assert impl.is_file(), f"missing {impl}"
        src = impl.read_text(encoding="utf-8")
        assert _USES_HELPER_RE.search(src), (
            f"{name} must build its block message via hooks/_lib/block_message.py"
        )
        assert name not in LEGACY_UNMIGRATED, (
            f"{name} is migrated — remove it from LEGACY_UNMIGRATED"
        )


def test_no_unaccounted_handrolled_block_hook() -> None:
    """Any preflight hook that emits a block must either use the helper or be
    explicitly listed as legacy. A NEW hand-rolled block hook fails here."""
    offenders: list[str] = []
    for impl in _hook_impls():
        name = impl.parent.name
        src = impl.read_text(encoding="utf-8")
        if not _EMITS_BLOCK_RE.search(src):
            continue  # not a blocking hook (e.g. pure advisory/ask-only)
        if _USES_HELPER_RE.search(src):
            continue  # compliant
        if name in LEGACY_UNMIGRATED:
            continue  # known not-yet-migrated
        offenders.append(name)
    assert not offenders, (
        "preflight-gate hook(s) emit a block message without using "
        "hooks/_lib/block_message.py and are not on the LEGACY_UNMIGRATED "
        f"allowlist: {offenders}. Use emit_block/format_block (see "
        "docs/hook/block-message-format.md), or — if intentionally legacy — "
        "add to LEGACY_UNMIGRATED."
    )


def test_legacy_allowlist_has_no_phantom_entries() -> None:
    """Every LEGACY_UNMIGRATED entry must name a real hook dir — prevents the
    allowlist from silently rotting as hooks are renamed/removed."""
    existing = {p.parent.name for p in _hook_impls()}
    phantom = sorted(LEGACY_UNMIGRATED - existing)
    assert not phantom, f"LEGACY_UNMIGRATED names non-existent hooks: {phantom}"


def test_legacy_and_migrated_are_disjoint() -> None:
    overlap = EXPECTED_MIGRATED & LEGACY_UNMIGRATED
    assert not overlap, f"hook listed as both migrated and legacy: {overlap}"
