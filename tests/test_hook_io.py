"""Tests + lint for the standard decision-emit helper (issue #470).

Two concerns, mirroring tests/test_block_message.py (issue #439):

  1. Helper format contract — `format_decision` / `emit_decision` produce the
     exact byte-identical `permissionDecision` JSON shape every hook used to
     hand-roll (fixed key order + trailing newline).

  2. Adoption lint — any hook that emits a `permissionDecision` JSON must build
     it via `hooks/_lib/_hook_io.py` rather than hand-rolling the `json.dump`.
     After migration the hand-rolled `permissionDecision` literal is GONE from a
     compliant hook (it lives only in _hook_io), so the lint detects an
     *offender* as "still hand-rolls the literal AND does not import the helper
     AND is not on the LEGACY_UNMIGRATED allowlist", and separately asserts the
     13 migrated hooks DO import the helper. Adding a NEW hand-rolled
     permissionDecision hook fails this test.

`builtin-task-postuse` is intentionally out of scope: it emits a different
shape (`additionalContext` + top-level `"continue": True`) and is the only such
caller, so it carries no `permissionDecision` literal and is never flagged.

Run: python3 -m pytest tests/test_hook_io.py -q
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "hooks" / "_lib"
HOOKS_DIR = REPO_ROOT / "hooks"

sys.path.insert(0, str(LIB_DIR))

_spec = importlib.util.spec_from_file_location("_hook_io", LIB_DIR / "_hook_io.py")
assert _spec is not None and _spec.loader is not None
hio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hio)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 1. Helper format contract — byte-identical to the pre-extraction hand-roll
# ---------------------------------------------------------------------------

def test_format_decision_key_order_is_fixed() -> None:
    d = hio.format_decision("ask", "because reasons")
    assert list(d.keys()) == ["hookSpecificOutput"]
    inner = d["hookSpecificOutput"]
    # Order is load-bearing for byte-identical serialization.
    assert list(inner.keys()) == [
        "hookEventName",
        "permissionDecision",
        "permissionDecisionReason",
    ]
    assert inner["hookEventName"] == "PreToolUse"
    assert inner["permissionDecision"] == "ask"
    assert inner["permissionDecisionReason"] == "because reasons"


def test_emit_decision_is_byte_identical_to_handrolled() -> None:
    """The emitted bytes must equal exactly what every hook hand-rolled:
    json.dump(obj, stdout) + a trailing newline, default separators."""
    for decision in ("ask", "deny"):
        buf = io.StringIO()
        hio.emit_decision(decision, "msg text", stream=buf)
        got = buf.getvalue()

        expected_obj = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": "msg text",
            }
        }
        ref = io.StringIO()
        json.dump(expected_obj, ref)
        ref.write("\n")
        assert got == ref.getvalue()
        # And it round-trips to the same object.
        assert json.loads(got) == expected_obj
        assert got.endswith("\n")


def test_emit_decision_event_name_override() -> None:
    buf = io.StringIO()
    hio.emit_decision("deny", "x", event_name="UserPromptSubmit", stream=buf)
    obj = json.loads(buf.getvalue())
    assert obj["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_emit_decision_does_not_exit() -> None:
    """Helper never calls sys.exit — caller owns the exit code."""
    buf = io.StringIO()
    # If this raised SystemExit the test would error, not fail silently.
    hio.emit_decision("ask", "x", stream=buf)
    assert buf.getvalue()


# ---------------------------------------------------------------------------
# 2. Adoption lint
# ---------------------------------------------------------------------------

# The 13 hooks that hand-rolled a permissionDecision JSON, migrated in #470.
# Stored as bare dir names (unique across role dirs).
EXPECTED_MIGRATED = {
    "output-block-falsify-advisory",
    "path-probe-gate",
    "pre-edit-md-escape-advisory",
    "branch-name-check",
    "commit-title-length-check",
    "cross-boundary-preflight",
    "gh-flag-verify",
    "gh-json-validator",
    "pre-edit-protected-branch-guard",
    "pre-merge-approval-gate",
    "session-intent",
    "side-effect-scan",
    "verify-commit-flag-override",
}

# Hooks that emit a permissionDecision but are intentionally not yet migrated.
# Empty: issue #470 migrated every permissionDecision emitter in one pass.
LEGACY_UNMIGRATED: set[str] = set()

# Hand-rolled emit = the literal JSON key appears in source.
_HANDROLLS_RE = re.compile(r'"permissionDecision"')
# Compliant = imports the shared helper.
_USES_HELPER_RE = re.compile(r"from _hook_io import|import _hook_io")


def _hook_impls() -> list[Path]:
    return sorted(HOOKS_DIR.glob("*/*/impl.py"))


def test_migrated_hooks_import_helper() -> None:
    """Each #470 hook must import _hook_io and must NOT hand-roll the literal."""
    by_name = {p.parent.name: p for p in _hook_impls()}
    for name in sorted(EXPECTED_MIGRATED):
        impl = by_name.get(name)
        assert impl is not None and impl.is_file(), f"missing impl for {name}"
        src = impl.read_text(encoding="utf-8")
        assert _USES_HELPER_RE.search(src), (
            f"{name} must emit via hooks/_lib/_hook_io.py (from _hook_io import emit_decision)"
        )
        assert not _HANDROLLS_RE.search(src), (
            f"{name} still hand-rolls the permissionDecision literal — "
            "the JSON shape must live only in _hook_io.py"
        )
        assert name not in LEGACY_UNMIGRATED, (
            f"{name} is migrated — remove it from LEGACY_UNMIGRATED"
        )


def test_no_unaccounted_handrolled_decision_hook() -> None:
    """Any hook that still hand-rolls a permissionDecision JSON without the
    helper, and is not on the legacy allowlist, fails here."""
    offenders: list[str] = []
    for impl in _hook_impls():
        name = impl.parent.name
        src = impl.read_text(encoding="utf-8")
        if not _HANDROLLS_RE.search(src):
            continue  # no hand-rolled literal (compliant or non-emitter)
        if _USES_HELPER_RE.search(src):
            # Uses helper → compliant. A residual `permissionDecision` literal
            # here is only a comment/docstring, not executable emit code (the
            # JSON shape lives solely in _hook_io). Boundary: this lint does NOT
            # catch a hook that imports the helper AND ALSO hand-rolls the dump;
            # that regression is guarded by per-hook tests + code review.
            continue
        if name in LEGACY_UNMIGRATED:
            continue
        offenders.append(name)
    assert not offenders, (
        "hook(s) hand-roll a permissionDecision JSON without using "
        f"hooks/_lib/_hook_io.py and are not on LEGACY_UNMIGRATED: {offenders}. "
        "Use emit_decision (see hooks/_lib/_hook_io.py), or add to LEGACY_UNMIGRATED."
    )


def test_legacy_allowlist_has_no_phantom_entries() -> None:
    existing = {p.parent.name for p in _hook_impls()}
    phantom = sorted(LEGACY_UNMIGRATED - existing)
    assert not phantom, f"LEGACY_UNMIGRATED names non-existent hooks: {phantom}"


def test_expected_migrated_all_exist() -> None:
    existing = {p.parent.name for p in _hook_impls()}
    missing = sorted(EXPECTED_MIGRATED - existing)
    assert not missing, f"EXPECTED_MIGRATED names non-existent hooks: {missing}"


def test_legacy_and_migrated_are_disjoint() -> None:
    overlap = EXPECTED_MIGRATED & LEGACY_UNMIGRATED
    assert not overlap, f"hook listed as both migrated and legacy: {overlap}"
