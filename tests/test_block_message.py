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


def test_pr_body_evidence_checklist_enumerates_all_tokens() -> None:
    """Checklist must name BOTH pr-body gate tokens (praxis #824) plus the
    format rules and related-gate pointers, so one deny teaches the full
    enumeration."""
    out = bm.pr_body_evidence_checklist()
    assert "Caller chain verified:" in out
    assert "Pre-commit verified:" in out
    assert "column 0" in out
    assert "fenced code blocks" in out
    assert "[skip-codex-review]" in out
    assert "Falsified:" in out
    # Trailing newline — callers concatenate before the cascade hint.
    assert out.endswith("\n")


def test_pr_body_gates_append_checklist() -> None:
    """Both pr-body gates must import the shared checklist and append it to
    their deny message."""
    for name in (
        "block-pr-without-caller-evidence",
        "block-pr-without-precommit-evidence",
    ):
        src = (PREFLIGHT_DIR / name / "impl.py").read_text(encoding="utf-8")
        assert "pr_body_evidence_checklist" in src, (
            f"{name} must append pr_body_evidence_checklist() to its deny message"
        )


# ---------------------------------------------------------------------------
# 1b. Verb-keyed gate checklists (issue #873)
# ---------------------------------------------------------------------------

ADVISORY_DIR = REPO_ROOT / "hooks" / "advisory-nudge"


def test_verb_gate_checklist_unknown_verb_is_empty() -> None:
    """An unregistered verb yields "" so a caller cannot append a stray blank
    block to its deny message."""
    assert bm.verb_gate_checklist("git bisect") == ""
    assert bm.verb_gate_checklist("") == ""


def test_pr_body_checklist_is_the_create_verb_entry() -> None:
    """The #824 helper is an alias, not a second copy that can drift."""
    assert bm.pr_body_evidence_checklist() == bm.verb_gate_checklist("gh pr create")


def test_merge_verb_checklist_names_every_merge_gate() -> None:
    """One deny on `gh pr merge` must teach all four gates on that verb —
    discovering them one block at a time is the #873 defect."""
    out = bm.verb_gate_checklist("gh pr merge")
    for gate in (
        "momentum-rule-retrieval-gate",
        "pre-merge-approval-gate",
        "side-effect-scan",
        "gh-merge-worktree-precondition",
        "commit-title-length-check",
        "pipefail-advisory",
        "session-intent",
        "skill-gate-commands",
    ):
        assert gate in out, f"merge checklist must name {gate}"
    # Satisfaction forms, verified against each hook's own source.
    assert "PRAXIS_MOMENTUM_MERGE_ADVISORY=1" in out
    assert "# briefing-surfaced:" in out
    assert "# side-effect:ack" in out
    assert "--delete-branch" in out
    assert out.endswith("\n")


def test_merge_checklist_splits_unconditional_from_conditional() -> None:
    """A conditional gate listed as unconditional sends the reader looking for a
    requirement that does not apply to their command — the mirror of the
    under-enumeration this checklist exists to prevent (CodeRabbit, PR #931).

    Only three gates fire on every `gh pr merge`; the rest are flag-gated.
    """
    out = bm.verb_gate_checklist("gh pr merge")
    head, _, tail = out.partition("Conditional — fire only in the stated situation:")
    assert tail, "merge checklist must carry a Conditional section"
    for always in ("momentum-rule-retrieval-gate", "pre-merge-approval-gate", "side-effect-scan"):
        assert always in head, f"{always} fires on every merge — belongs above the split"
    for flag_gated in (
        "gh-merge-worktree-precondition",   # requires -d / --delete-branch
        "commit-title-length-check",        # requires -s / --squash
        "pipefail-advisory",                # requires a pipe
        "session-intent",                   # requires a read-only-intent session
        "skill-gate-commands",              # requires PRAXIS_SKILL_GATED_COMMANDS
    ):
        assert flag_gated in tail, f"{flag_gated} is flag-gated — belongs below the split"


def test_ask_checklist_splits_unconditional_from_conditional() -> None:
    """Same split on the ask side. `block-manufactured-action-menu` is advisory
    by default and blocks only under PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT, so
    listing it among the always-satisfy gates makes readers rewrite menus that
    were never blocked (Codex round 2, PR #931)."""
    out = bm.verb_gate_checklist("AskUserQuestion")
    head, _, tail = out.partition("Conditional — fire only in the stated situation:")
    assert tail, "AskUserQuestion checklist must carry a Conditional section"
    for always in ("output-block-falsify-advisory", "block-ask-end-option"):
        assert always in head, f"{always} blocks by default — belongs above the split"
    for gated in (
        "block-manufactured-action-menu",     # strict-mode only
        "pr-state-refetch-gate",              # merge-intent question + strict
        "merge-menu-review-options-advisory",  # merge menu + strict
    ):
        assert gated in tail, f"{gated} is conditional — belongs below the split"


def test_ask_verb_checklist_names_every_ask_gate() -> None:
    out = bm.verb_gate_checklist("AskUserQuestion")
    for gate in (
        "output-block-falsify-advisory",
        "block-ask-end-option",
        "block-manufactured-action-menu",
        "pr-state-refetch-gate",
        "merge-menu-review-options-advisory",
        "pre-output-falsification-gate",
        "memory-hint",
    ):
        assert gate in out, f"AskUserQuestion checklist must name {gate}"
    assert "PRAXIS_ASK_END_ADVISORY=1" in out
    assert "column 0" in out
    assert out.endswith("\n")


def test_ask_end_optout_is_not_described_as_per_call() -> None:
    """`block-ask-end-option` reads PRAXIS_ASK_END_ADVISORY from its own process
    env (impl.py: `os.environ.get`), so an inline `VAR=1 <cmd>` prefix never
    reaches it. Calling the opt-out per-call sends the reader into a retry loop
    — the exact cost this checklist exists to remove."""
    out = bm.verb_gate_checklist("AskUserQuestion")
    assert "Opt out per call" not in out
    assert "SESSION environment" in out


def test_ask_and_merge_checklists_match_the_hook_registry() -> None:
    """The checklists must not drift below what `hooks/manifest.json` registers.

    Codex review of PR #931 caught this exact gap: the first draft named 3 of
    the 7 PreToolUse hooks on AskUserQuestion. A checklist that under-enumerates
    reproduces the very defect #873 fixes, and reads as authoritative while
    doing it.
    """
    import json

    manifest = json.loads(
        (REPO_ROOT / "hooks" / "manifest.json").read_text(encoding="utf-8")
    )
    registered = {
        h["name"]
        for h in manifest["hooks"]
        if h.get("event") == "PreToolUse" and "AskUserQuestion" in (h.get("matcher") or "")
    }
    out = bm.verb_gate_checklist("AskUserQuestion")
    missing = sorted(name for name in registered if name not in out)
    assert not missing, f"AskUserQuestion checklist omits registered hooks: {missing}"


def test_verb_checklists_do_not_start_a_column_0_falsified_line() -> None:
    """Self-application guard (#873).

    The AskUserQuestion checklist names the `Falsified:` token, and this text
    is emitted into the transcript on every block. `output-block-falsify-
    advisory` accepts the token only at column 0, so an unindented occurrence
    here would let the hook's own message satisfy the very predicate it is
    asking the author to satisfy.
    """
    for verb in ("gh pr create", "gh pr merge", "AskUserQuestion"):
        for line in bm.verb_gate_checklist(verb).splitlines():
            assert not line.startswith("Falsified:"), (
                f"{verb} checklist emits a column-0 'Falsified:' line"
            )


def test_verb_wired_hooks_append_their_checklist() -> None:
    """Each hook named by #873 must append its verb's checklist."""
    for name, verb in (
        ("momentum-rule-retrieval-gate", "gh pr merge"),
        ("output-block-falsify-advisory", "AskUserQuestion"),
    ):
        src = (ADVISORY_DIR / name / "impl.py").read_text(encoding="utf-8")
        assert f'verb_gate_checklist("{verb}")' in src, (
            f"{name} must append verb_gate_checklist(\"{verb}\") to its message"
        )


# ---------------------------------------------------------------------------
# 1c. Host-scoped checklist rows (issue #1245)
#
# The `gh pr create` checklist points at `block-commit-without-codex-review`,
# which carries `hosts: ["claude"]`. The two hooks that print the checklist
# carry `hosts: ["claude","codex"]`, so on codex that row asked for a review
# pass no installed gate requires and named a bypass that does not exist there
# — the same defect #1154 fixed one surface over.
# ---------------------------------------------------------------------------

CLAUDE_ONLY_ROW = "block-commit-without-codex-review"


def _rows(text: str) -> list[str]:
    """The hook name of every `← <hook>` row in `text`, in order."""
    return re.findall(r"←\s*([a-z0-9][a-z0-9-]*)", text)


def test_create_checklist_keeps_every_row_on_claude() -> None:
    """claude installs all four gates, so nothing is dropped."""
    assert CLAUDE_ONLY_ROW in _rows(bm.pr_body_evidence_checklist("claude"))


def test_create_checklist_drops_the_claude_only_row_on_codex() -> None:
    """The defect: codex does not install the codex-review gate."""
    out = bm.pr_body_evidence_checklist("codex")
    assert CLAUDE_ONLY_ROW not in _rows(out)
    # The rows codex DOES install must survive — a filter that drops
    # everything would satisfy the assertion above on its own.
    assert "block-pr-without-caller-evidence" in _rows(out)
    assert "output-block-falsify-advisory" in _rows(out)


def test_dropped_row_takes_its_remedy_lines_with_it() -> None:
    """A row's indented prose is part of the row, not of the next one."""
    out = bm.pr_body_evidence_checklist("codex")
    assert "[skip-codex-review]" not in out
    assert "Falsified:" in out


def test_filter_keeps_section_headers_and_closing_prose() -> None:
    """A non-indented line after a blank one ends the row above it.

    Without that rule a dropped row swallows the next section's header, and
    the merge checklist's `Conditional — …` heading would vanish with it.
    """
    out = bm.verb_gate_checklist("gh pr merge", "codex")
    assert "Conditional — fire only in the stated situation:" in out
    assert "Approving one PR approves only that PR" in out
    assert "column 0" in bm.pr_body_evidence_checklist("codex")


def test_unknown_and_absent_hosts_keep_every_row() -> None:
    """Negative control. Dropping a row the host DOES install hides the next
    hard block, which is the worse of the two failures — so anything that is
    not a declared host id prints the full list."""
    full = _rows(bm.pr_body_evidence_checklist())
    assert CLAUDE_ONLY_ROW in full
    for host in (None, "not-a-host", ""):
        assert _rows(bm.pr_body_evidence_checklist(host)) == full


def test_filter_gate_rows_returns_empty_when_no_row_survives() -> None:
    """A header introducing an empty list is worse than no text at all."""
    assert bm.filter_gate_rows(bm.pr_body_evidence_checklist(), set()) == ""


def test_neutered_filter_would_reprint_the_claude_only_row() -> None:
    """Regression-injection control for the two assertions above.

    They only mean something if the filter is what removes the row. Replace
    `filter_gate_rows` with the identity and the codex output must carry the
    claude-only row again — so a future edit that unwires the filter fails the
    tests above instead of passing them silently.
    """
    original = bm.filter_gate_rows
    try:
        bm.filter_gate_rows = lambda text, installed: text  # type: ignore[assignment]
        assert CLAUDE_ONLY_ROW in _rows(bm.pr_body_evidence_checklist("codex"))
    finally:
        bm.filter_gate_rows = original
    assert CLAUDE_ONLY_ROW not in _rows(bm.pr_body_evidence_checklist("codex"))


def test_pr_body_gates_pass_the_runtime_host() -> None:
    """Both printers must resolve the host — an unfiltered call site puts the
    row back on every platform, and no output check can see that from here."""
    for name in (
        "block-pr-without-caller-evidence",
        "block-pr-without-precommit-evidence",
    ):
        src = (PREFLIGHT_DIR / name / "impl.py").read_text(encoding="utf-8")
        assert "pr_body_evidence_checklist(runtime_host())" in src, (
            f"{name} must pass runtime_host() to the checklist (issue #1245)"
        )


# ---------------------------------------------------------------------------
# 2. Adoption lint
# ---------------------------------------------------------------------------

# Preflight-gate hooks not yet migrated to the helper. The three hooks
# migrated in issue #439 are intentionally absent. Reserved-for-other-issue
# hooks (commit-title-length-check) stay listed.
# Migrating any of these later = removing it from this set.
# NOTE: the two block-pr-without-*-evidence gates import block_message for
# the shared checklist suffix (#824) but still hand-roll their main deny
# message — they stay listed until migrated to format_block/emit_block.
LEGACY_UNMIGRATED = {
    "block-ask-end-option",
    "block-commit-without-codex-review",
    "block-manufactured-action-menu",
    "block-pr-without-caller-evidence",
    "block-pr-without-precommit-evidence",
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
