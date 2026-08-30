"""Functional tests for skills/retrospect/audit-distribution-gates.py (#774).

Each case invokes the script as a subprocess — the same surface the retrospect
skill uses — against a synthetic Stage 3 draft.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "retrospect" / "audit-distribution-gates.py"
)

HEADER = (
    "| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap "
    "| Repeat? | Proposed Actions (1~2) | Rationale | Priority |"
)
SEP = "|---|---|---|---|---|---|---|---|---|---|"

SCHEMA_A = (
    "not issue: no repeat<br>not claude_md_draft: too narrow<br>"
    "not skill_idea: no workflow<br>not hook_code: no enforcement need<br>"
    "not upstream_feedback: internal only"
)


def row(num: int, category: str, layer: str, actions: str, rationale: str,
        root_cause: str = "plain cause", pattern: str = "p",
        repeat: str = "false") -> str:
    return (f"| {num} | {category} | {layer} | {pattern} | {root_cause} "
            f"| rule | {repeat} | {actions} | {rationale} | P2 |")


def run(tmp_path: Path, draft: str, *extra_args: str,
        env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    draft_file = tmp_path / "draft.md"
    draft_file.write_text(draft, encoding="utf-8")
    env = dict(os.environ, PRAXIS_OWN_ORGS="devseunggwan")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--draft", str(draft_file), *extra_args],
        capture_output=True, text=True, env=env,
    )


def draft_of(*rows: str, extra: str = "") -> str:
    return "\n".join(["## Retrospect Report", "", HEADER, SEP, *rows, "", extra])


MS_BLOCK = (
    "<!-- memory_scan finding #1:\n  scanned: true\n"
    "  candidates_reviewed: memory/foo.md\n  repeat: false\n"
    "  repeat_count: 0\n-->"
)


def test_clean_memory_only_schema_a(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A), extra=MS_BLOCK))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- memory: 1" in r.stdout
    assert "- gate_2_verdict: PASS" in r.stdout
    assert "- gate_5_verdict: PASS" in r.stdout


def test_schema_b_passes(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", "not-others: tool,workflow"),
        extra=MS_BLOCK))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_2_verdict: PASS" in r.stdout


def test_schema_neither_fails_gate2(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", "generic one-liner"),
        extra=MS_BLOCK))
    assert r.returncode == 1
    assert "matches neither schema A" in r.stdout
    assert "- gate_2_verdict: FAIL" in r.stdout


def test_gate1_tool_memory_only_blocks(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "tool", "cli", "memory", SCHEMA_A), extra=MS_BLOCK))
    assert r.returncode == 1
    assert "memory only" in r.stdout
    assert "- gate_1_verdict: FAIL" in r.stdout


def test_degenerate_memory_memory_is_memory_only(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "tool", "cli", "memory, memory", SCHEMA_A), extra=MS_BLOCK))
    assert r.returncode == 1
    assert "memory only" in r.stdout


def test_behavioral_label_falsify_keyword_requires_justify(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A,
            root_cause="hook missing for the --state flag"),
        extra=MS_BLOCK))
    assert r.returncode == 1
    assert "behavioral-label-justify" in r.stdout


def test_behavioral_label_justify_line_passes(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory",
            SCHEMA_A + "<br>behavioral-label-justify: keyword is incidental",
            root_cause="hook missing for the --state flag"),
        extra=MS_BLOCK))
    assert r.returncode == 0, r.stdout + r.stderr


def test_tool_without_tool_layer_fails(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "tool", "—", "issue", "backing_repo: devseunggwan/praxis")))
    assert r.returncode == 1
    assert "Tool Layer" in r.stdout


def test_routed_action_missing_backing_repo(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(row(1, "workflow", "—", "issue", "no repo here")))
    assert r.returncode == 1
    assert "backing_repo" in r.stdout


def test_gate4_own_org_public_is_escalated(tmp_path: Path) -> None:
    # #993: own-org membership no longer exempts a public backing repo — this
    # exact draft returned `gate_4_verdict: PASS` + exit 0 before the change.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: devseunggwan/praxis<br>repo_visibility: public")))
    assert r.returncode == 1
    assert "own-org membership no longer exempts a public repo" in r.stdout
    assert "literal warning prefix" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_own_org_undeclared_visibility_is_escalated(tmp_path: Path) -> None:
    # #993: a missing `repo_visibility:` line must not widen the gate — the
    # undeclared repo is treated as public and an advisory says so.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: devseunggwan/praxis")))
    assert r.returncode == 1
    assert "conservative fallback treats the backing repo as public" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_own_org_public_with_warning_prefix_passes(tmp_path: Path) -> None:
    # #993: escalated but compliant — WARN verdict, no violation, exit 0.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: devseunggwan/praxis<br>repo_visibility: public<br>"
            "⚠ EXTERNAL: per-action approval required at Stage 4")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_own_org_private_is_not_escalated(tmp_path: Path) -> None:
    # #993 opposite direction: the exemption survives only for a repo that is
    # own-org AND declared private/internal. This case must stay silent.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: devseunggwan/internal-notes<br>"
            "repo_visibility: private")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: PASS" in r.stdout
    assert "gate-4:" not in r.stdout


def test_gate4_own_org_internal_is_not_escalated(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: devseunggwan/internal-notes<br>"
            "repo_visibility: internal")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: PASS" in r.stdout


def test_gate4_external_private_stays_escalated(tmp_path: Path) -> None:
    # #993 guard: the visibility exemption must not de-escalate a third-party
    # repo — `private` on a non-own-org owner is still a cross-boundary write.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: someoneelse/repo<br>repo_visibility: private")))
    assert r.returncode == 1
    assert "outside the own-org allowlist" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_external_owner_needs_warning_prefix(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: someoneelse/repo")))
    assert r.returncode == 1
    assert "literal warning prefix" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_external_with_warning_prefix_warns_but_passes(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: someoneelse/repo<br>"
            "⚠ EXTERNAL: per-action approval required at Stage 4")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_issue_only_public_own_org_is_escalated(tmp_path: Path) -> None:
    # #1038: Gate-4 used to select only `upstream_feedback` rows, so an
    # `issue`-only row to a public own-org repo fell through as NA. Same
    # repo/visibility as test_gate4_own_org_public_is_escalated above, but
    # with `issue` as the sole action.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "issue",
            "backing_repo: devseunggwan/praxis<br>repo_visibility: public")))
    assert r.returncode == 1
    assert "own-org membership no longer exempts a public repo" in r.stdout
    assert "literal warning prefix" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_issue_only_public_with_warning_prefix_passes(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "issue",
            "backing_repo: devseunggwan/praxis<br>repo_visibility: public<br>"
            "⚠ EXTERNAL: per-action approval required at Stage 4")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_issue_only_private_is_not_escalated(tmp_path: Path) -> None:
    # Negative control: an `issue` row to an own-org private repo stays PASS,
    # same as the equivalent upstream_feedback case.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "issue",
            "backing_repo: devseunggwan/internal-notes<br>"
            "repo_visibility: private")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: PASS" in r.stdout
    assert "gate-4:" not in r.stdout


def test_gate4_na_only_when_no_routed_action_at_all(tmp_path: Path) -> None:
    # #1038: NA must mean "no upstream_feedback or issue finding exists",
    # not "only issue rows were present". A memory-only draft still gets NA.
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A), extra=MS_BLOCK))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: NA" in r.stdout


def test_gate5_missing_block_fails(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(row(1, "behavioral", "—", "memory", SCHEMA_A)))
    assert r.returncode == 1
    assert "memory_scan" in r.stdout
    assert "- gate_5_verdict: FAIL" in r.stdout


def test_gate5_incomplete_block_fails(tmp_path: Path) -> None:
    incomplete = ("<!-- memory_scan finding #1:\n  scanned: true\n"
                  "  repeat: false\n-->")
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A), extra=incomplete))
    assert r.returncode == 1
    assert "incomplete" in r.stdout


def test_compound_without_gate3_flag_fails(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "tool", "cli", "memory, issue",
            SCHEMA_A + "<br>backing_repo: devseunggwan/praxis"),
        extra=MS_BLOCK))
    assert r.returncode == 1
    assert "--gate3 not supplied" in r.stdout


def test_compound_with_gate3_pass(tmp_path: Path) -> None:
    # #1038: gate-4 now audits `issue` rows too, so this fixture must declare
    # repo_visibility to stay isolated from gate-4 and test gate-3 alone.
    r = run(tmp_path, draft_of(
        row(1, "tool", "cli", "memory, issue",
            SCHEMA_A + "<br>backing_repo: devseunggwan/praxis<br>"
            "repo_visibility: private"),
        extra=MS_BLOCK), "--gate3", "PASS")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_3_verdict: PASS" in r.stdout


def test_zero_findings_all_na(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of())
    assert r.returncode == 0, r.stdout + r.stderr
    for g in range(1, 7):
        assert f"- gate_{g}_verdict: NA" in r.stdout


def test_no_table_is_error(tmp_path: Path) -> None:
    r = run(tmp_path, "just prose, no table")
    assert r.returncode == 1
    assert "no unified findings table" in r.stdout


def test_short_row_schema_violation(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of("| 1 | behavioral | memory |"))
    assert r.returncode == 1
    assert "expected 10" in r.stdout


def test_escaped_pipe_in_rationale(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory",
            SCHEMA_A.replace("no repeat", "a \\| b")), extra=MS_BLOCK))
    assert r.returncode == 0, r.stdout + r.stderr


def test_invalid_action_token(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(row(1, "behavioral", "—", "banana", "x")))
    assert r.returncode == 1
    assert "invalid Proposed Actions" in r.stdout


def test_reinforce_memory_added_to_count(tmp_path: Path) -> None:
    # #1038: gate-4 now audits `issue` rows too, so this fixture must declare
    # repo_visibility to stay isolated from gate-4 and test the reinforce
    # count alone.
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "issue",
            "backing_repo: devseunggwan/praxis<br>repo_visibility: private")),
        "--reinforce-memory", "2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- memory: 2" in r.stdout


def test_behavioral_only_safeguard_advisory(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A,
            pattern="kubectl timeout while waiting"), extra=MS_BLOCK))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "behavioral-only safeguard" in r.stdout


def test_word_boundary_no_false_positive(tmp_path: Path) -> None:
    # `respect`/`expected`/`high` must not hit `spec`/`gh` (code-reviewer #774).
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A,
            root_cause="agent did not respect the expected high-level intent"),
        extra=MS_BLOCK))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "behavioral-only safeguard" not in r.stdout


def test_gate3_na_claim_with_compound_is_violation(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "tool", "cli", "memory, issue",
            SCHEMA_A + "<br>backing_repo: devseunggwan/praxis"),
        extra=MS_BLOCK), "--gate3", "NA")
    assert r.returncode == 1
    assert "NA claimed" in r.stdout
    assert "- gate_3_verdict: FAIL" in r.stdout


def test_gate4_conservative_fallback_all_external(tmp_path: Path) -> None:
    # No PRAXIS_OWN_ORGS and no gh on PATH -> every owner is external.
    draft_file = tmp_path / "draft.md"
    draft_file.write_text(draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: devseunggwan/praxis")), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PRAXIS_OWN_ORGS"}
    env["PATH"] = str(tmp_path)  # empty dir: gh resolution fails
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--draft", str(draft_file)],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 1
    assert "allowlist unresolved" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_fallback_stays_warn_when_no_backing_repo_parses(
    tmp_path: Path,
) -> None:
    """Unresolved allowlist AND an unparseable backing_repo — still WARN.

    The two conditions overlap in exactly one place: the row `continue`s before
    `any_external` is ever set, so the verdict is decided entirely by the
    fallback flag. Reading PASS here would label an unread gate as a clean one.
    The exit code is 1 either way (the missing backing_repo is its own
    violation), which is why only the label distinguishes the two versions.
    """
    draft_file = tmp_path / "draft.md"
    draft_file.write_text(draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "no backing repo declared here")), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PRAXIS_OWN_ORGS"}
    env["PATH"] = str(tmp_path)  # empty dir: gh resolution fails
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--draft", str(draft_file)],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 1
    assert "allowlist unresolved" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


def test_gate4_resolved_allowlist_with_no_backing_repo_is_pass(
    tmp_path: Path,
) -> None:
    """The negative control for the case above — without the fallback the same
    unparseable row leaves the gate at PASS, so the WARN there comes from the
    unresolved allowlist and not from the missing backing_repo."""
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "no backing repo declared here")))
    assert r.returncode == 1  # missing backing_repo is still a violation
    assert "allowlist unresolved" not in r.stdout
    assert "- gate_4_verdict: PASS" in r.stdout


def test_signals_file_feeds_safeguard(tmp_path: Path) -> None:
    signals = tmp_path / "signals.txt"
    signals.write_text("permission denied while calling kubectl",
                       encoding="utf-8")
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A), extra=MS_BLOCK),
        "--signals", str(signals))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "behavioral-only safeguard" in r.stdout


def test_more_than_two_actions_violation(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "issue, hook_code, skill_idea",
            "backing_repo: devseunggwan/praxis")))
    assert r.returncode == 1
    assert "max 2" in r.stdout


def test_invalid_category_token(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(row(1, "vibes", "—", "memory", SCHEMA_A),
                               extra=MS_BLOCK))
    assert r.returncode == 1
    assert "invalid category[]" in r.stdout


def test_extra_cells_unescaped_pipe_rejected(tmp_path: Path) -> None:
    # codex #774 F1: an unescaped '|' adds an 11th cell — must not pass.
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A,
            pattern="a | b unescaped"), extra=MS_BLOCK))
    assert r.returncode == 1
    assert "expected 10" in r.stdout


def test_nontool_row_invalid_tool_layer_rejected(tmp_path: Path) -> None:
    # codex #774 F2: Tool Layer enum applies to every row, not only tool rows.
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "banana", "memory", SCHEMA_A), extra=MS_BLOCK))
    assert r.returncode == 1
    assert "mcp|cli|builtin|skill|—" in r.stdout


def test_schema_a_duplicate_lines_fail(tmp_path: Path) -> None:
    # codex #774 F3: five duplicate 'not issue:' lines must not pass Schema A.
    dup = "<br>".join(["not issue: reason"] * 5)
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", dup), extra=MS_BLOCK))
    assert r.returncode == 1
    assert "distinct" in r.stdout
    assert "- gate_2_verdict: FAIL" in r.stdout


def test_gate6_flag_embedded_in_card(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory",
            SCHEMA_A + "<br>oracle_match: true"), extra=MS_BLOCK),
        "--gate6", "PASS")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_6_verdict: PASS" in r.stdout


def test_gate3_fail_is_violation(tmp_path: Path) -> None:
    # CodeRabbit #775: explicit FAIL must not exit 0 with a clean audit.
    r = run(tmp_path, draft_of(
        row(1, "tool", "cli", "memory, issue",
            SCHEMA_A + "<br>backing_repo: devseunggwan/praxis"),
        extra=MS_BLOCK), "--gate3", "FAIL")
    assert r.returncode == 1
    assert "gate-3 verdict is FAIL" in r.stdout
    assert "- gate_3_verdict: FAIL" in r.stdout


def test_gate6_fail_is_violation(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "behavioral", "—", "memory", SCHEMA_A), extra=MS_BLOCK),
        "--gate6", "FAIL")
    assert r.returncode == 1
    assert "gate-6 verdict is FAIL" in r.stdout
    assert "- gate_6_verdict: FAIL" in r.stdout
