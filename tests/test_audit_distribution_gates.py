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


def test_gate4_internal_owner_passes(tmp_path: Path) -> None:
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "upstream_feedback",
            "backing_repo: devseunggwan/praxis")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- gate_4_verdict: PASS" in r.stdout


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
    r = run(tmp_path, draft_of(
        row(1, "tool", "cli", "memory, issue",
            SCHEMA_A + "<br>backing_repo: devseunggwan/praxis"),
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
    r = run(tmp_path, draft_of(
        row(1, "workflow", "—", "issue", "backing_repo: devseunggwan/praxis")),
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
    assert "conservative fallback" in r.stdout
    assert "- gate_4_verdict: WARN" in r.stdout


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
