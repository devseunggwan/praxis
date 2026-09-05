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

Three further groups cover the PR #1142 review findings:

  - **host scoping.** Four of the eight gated hooks carry `hosts: ["claude"]`,
    so the sibling set the runtime actually installs is 8 on `claude` and 4 on
    `codex` / `cursor`. A host-blind derivation pinned `claude`'s
    number on every platform. The per-row `Hosts` cell and the per-host table
    are both checked, in both directions, against every platform that emits a
    `hooks` output.
  - **field shape.** `"gates": "not-git-commit"` (a bare string) made
    `GATE not in gates` a substring test, so `branch-name-check` derived as a
    commit gate. A non-list `gates` — or `hosts` — is now a loud drift.
  - **the second count.** "Four of the seven siblings are the checklist …" was
    hand-copied on two surfaces and pinned by nothing; it is now derived from
    the `← <hook>` rows of `verify-commit-flag-override`'s own
    `GIT_COMMIT_GATE_CHECKLIST`.

Fixtures are built by copying the real surfaces into a temp tree and mutating
one of them, so no case can pass because the fixture drifted away from the
production prose: every anchor string is asserted present before it is replaced.
"""

from __future__ import annotations

import importlib.util
import json
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
    "commit-message-paren-check",
    "commit-title-format-check",
    "commit-title-length-check",
    "pre-commit-staged-file-enumeration",
    "verify-commit-flag-override",
]

_SURFACE_FILES = (gates.MANIFEST, gates.SPEC, gates.IMPL, gates.TEST, gates.CHECKLIST)

# Hosts that install hooks today, and the sibling count each one actually gets
# once `hosts` whitelists are applied. Spelled out for the same reason EXPECTED
# is: a whitelist edit has to come through this file too.
EXPECTED_PER_HOST = {"claude": 8, "codex": 4, "cursor": 4}

# A host with no `hooks` output in `manifests/platforms/` — every shipped
# platform installs hooks today, so this is a synthetic name.
NON_HOOK_PLATFORM = "skills-only-host"

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

    `manifests/platforms/` is copied whole: the host list the per-host table is
    checked against is read from there, not hard-coded in the checker.
    """
    for rel in _SURFACE_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REPO / rel, dest)
    shutil.copytree(_REPO / gates.PLATFORMS, tmp_path / gates.PLATFORMS)
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
                "| `block-rename-sweep-survivors` | `claude` | a rename sweep "
                "with surviving occurrences |\n",
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
                "| `verify-commit-flag-override` |",
                "| `pipefail-advisory` | not a commit gate |\n"
                "| `verify-commit-flag-override` |",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "pipefail-advisory" in d and "carries no gates" in d for d in drifts
    ), drifts


def test_name_missing_from_impl_docstring_is_drift(tmp_path):
    repo = _tree(tmp_path, {gates.IMPL: ("block-rename-sweep-survivors,", "")})
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
    assert sum("prose says 8 siblings, manifest derives 7" in d for d in drifts) >= 3


def test_stale_count_word_alone_is_drift(tmp_path):
    """The exact #1123 failure mode: names edited, a count word left behind."""
    repo = _tree(tmp_path, {gates.SPEC: ("Eight sibling", "Six sibling")})
    drifts = gates.check(repo)
    assert any("prose says 6 siblings, manifest derives 8" in d for d in drifts), drifts


def test_count_word_in_the_shell_test_comment_is_checked(tmp_path):
    """That comment splits `the seven` / `# sibling gates` across two lines —
    the normalizer has to rejoin them or this surface is never really read."""
    repo = _tree(
        tmp_path,
        {
            gates.TEST: (
                "at ASK: the eight\n# sibling gates",
                "at ASK: the five\n# sibling gates",
            )
        },
    )
    drifts = gates.check(repo)
    assert any("prose says 5 siblings, manifest derives 8" in d for d in drifts), drifts


def test_unreadable_table_is_drift_not_a_silent_pass(tmp_path):
    repo = _tree(tmp_path, {gates.SPEC: ("| Sibling hook |", "| Hook |")})
    drifts = gates.check(repo)
    assert any("enumeration not found" in d for d in drifts), drifts


def test_missing_count_claim_is_drift_not_a_silent_pass(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.TEST: (
                "# eight sibling commit hooks that gate this argv",
                "# the commit hooks that gate this argv",
            )
        },
    )
    # The file carries two count claims; the surface only stops being checked
    # once both are gone, so the second is removed here too.
    path = repo / gates.TEST
    text = path.read_text(encoding="utf-8")
    assert "at ASK: the eight\n# sibling gates" in text
    path.write_text(
        text.replace("at ASK: the eight\n# sibling gates", "at ASK: the\n# gates"),
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
        gates, "REPO", _tree(tmp_path, {gates.SPEC: ("Eight sibling", "Six sibling")})
    )
    assert gates.main() == 1


# ---------------------------------------------------------------------------
# PR #1142 finding 1: the derivation, and every prose surface, must be host-aware
# ---------------------------------------------------------------------------


def test_hook_installing_hosts_are_read_from_the_platform_manifests():
    hosts, drifts = gates.hook_hosts(_REPO)
    assert drifts == []
    assert hosts == sorted(EXPECTED_PER_HOST)
    assert NON_HOOK_PLATFORM not in hosts, (
        f"{NON_HOOK_PLATFORM} declares no 'hooks' output, so it never runs "
        "side-effect-scan and must not be a host the demotion is checked on"
    )


def test_derivation_is_host_scoped():
    """The finding: `hosts: ["claude"]` thins the sibling set on every other host."""
    canonical, drifts = gates.derive(_REPO)
    assert drifts == []
    assert len(canonical) == len(EXPECTED)
    for host, count in EXPECTED_PER_HOST.items():
        derived, host_drifts = gates.derive(_REPO, host)
        assert host_drifts == [], host
        assert len(derived) == count, (host, derived)
        assert set(derived) <= set(EXPECTED), (host, derived)
    assert set(gates.derive(_REPO, "codex")[0]) < set(gates.derive(_REPO, "claude")[0])


def test_host_table_stale_count_is_drift(tmp_path):
    """A host row that still claims claude's coverage — the shipped defect."""
    repo = _tree(tmp_path, {gates.SPEC: ("| `codex` | 4 | 2 |", "| `codex` | 8 | 4 |")})
    drifts = gates.check(repo)
    assert any(
        "'codex' row says 8 sibling commit gates, manifest derives 4" in d
        for d in drifts
    ), drifts


def test_host_table_stale_checklist_count_is_drift(tmp_path):
    repo = _tree(
        tmp_path, {gates.SPEC: ("| `cursor` | 4 | 2 |", "| `cursor` | 4 | 4 |")}
    )
    drifts = gates.check(repo)
    assert any(
        "'cursor' row says 4 of them are in the deny checklist" in d for d in drifts
    ), drifts


def test_missing_host_row_is_drift(tmp_path):
    repo = _tree(tmp_path, {gates.SPEC: ("| `cursor` | 4 | 2 |\n", "")})
    drifts = gates.check(repo)
    assert any("'cursor' installs hooks but has no row" in d for d in drifts), drifts


def test_host_row_for_a_platform_without_hooks_is_drift(tmp_path):
    """A host shipping no hooks.json: a row for it claims coverage it never gets."""
    repo = _tree(
        tmp_path,
        {
            gates.SPEC: (
                "| `cursor` | 4 | 2 |\n",
                f"| `cursor` | 4 | 2 |\n| `{NON_HOOK_PLATFORM}` | 4 | 2 |\n",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        f"row for host '{NON_HOOK_PLATFORM}'" in d and "installs hooks" in d
        for d in drifts
    ), drifts


def test_unreadable_host_table_is_drift_not_a_silent_pass(tmp_path):
    repo = _tree(
        tmp_path,
        {gates.SPEC: ("| Host | Sibling commit gates |", "| Platform | Gates |")},
    )
    drifts = gates.check(repo)
    assert any("table was not found" in d for d in drifts), drifts


def test_wrong_hosts_cell_in_the_sibling_table_is_drift(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.SPEC: (
                "| `commit-title-length-check` | all |",
                "| `commit-title-length-check` | `claude` |",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "commit-title-length-check Hosts cell says ['claude'], manifest declares "
        "['all']" in d
        for d in drifts
    ), drifts


def test_narrowing_a_hosts_whitelist_fails_both_host_surfaces(tmp_path):
    """The other direction: the manifest narrows, the prose keeps claude's numbers."""
    repo = _tree(
        tmp_path,
        {
            gates.MANIFEST: (
                '      "name": "verify-commit-flag-override",\n'
                '      "role": "preflight-gate",\n',
                '      "name": "verify-commit-flag-override",\n'
                '      "hosts": [\n        "claude"\n      ],\n'
                '      "role": "preflight-gate",\n',
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "verify-commit-flag-override Hosts cell says ['all'], manifest declares "
        "['claude']" in d
        for d in drifts
    ), drifts
    assert any(
        "'codex' row says 4 sibling commit gates, manifest derives 3" in d
        for d in drifts
    ), drifts


# ---------------------------------------------------------------------------
# PR #1142 finding 2: a non-list `gates` / `hosts` is a loud error, not a
# silently-degraded substring test
# ---------------------------------------------------------------------------

_BRANCH_NAME_CHECK_ENTRY = (
    '      "name": "branch-name-check",\n'
    '      "role": "preflight-gate",\n'
    '      "event": "PreToolUse",\n'
    '      "matcher": "Bash",\n'
)


def test_string_gates_value_is_a_loud_drift_not_a_substring_match(tmp_path):
    """`"git-commit" in "not-git-commit"` is True — the reported defect exactly."""
    assert gates.GATE in "not-git-commit", "the substring hazard this test pins"
    repo = _tree(
        tmp_path,
        {
            gates.MANIFEST: (
                _BRANCH_NAME_CHECK_ENTRY,
                _BRANCH_NAME_CHECK_ENTRY + '      "gates": "not-git-commit",\n',
            )
        },
    )
    derived, drifts = gates.derive(repo)
    assert "branch-name-check" not in derived, derived
    assert derived == EXPECTED
    assert any(
        "branch-name-check: 'gates' must be a JSON array of strings" in d
        and "substring test" in d
        for d in drifts
    ), drifts
    # …and the misleading "add it to the commit sibling table" message is gone.
    assert not any(
        "branch-name-check" in d and "missing from the enumeration" in d
        for d in gates.check(repo)
    ), gates.check(repo)


def test_string_hosts_value_is_a_loud_drift(tmp_path):
    """Same hazard on the field the host filter reads."""
    repo = _tree(
        tmp_path,
        {
            gates.MANIFEST: (
                '      "name": "commit-title-length-check",\n',
                '      "name": "commit-title-length-check",\n'
                '      "hosts": "claude",\n',
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "commit-title-length-check: 'hosts' must be a JSON array of strings" in d
        for d in drifts
    ), drifts


def test_a_well_formed_unrelated_gate_label_is_still_ignored(tmp_path):
    """The bidirectional half: the correct list shape must NOT trip the guard."""
    repo = _tree(
        tmp_path,
        {
            gates.MANIFEST: (
                _BRANCH_NAME_CHECK_ENTRY,
                _BRANCH_NAME_CHECK_ENTRY
                + '      "gates": [\n        "git-branch"\n      ],\n',
            )
        },
    )
    derived, drifts = gates.derive(repo)
    assert derived == EXPECTED
    assert drifts == []
    assert gates.check(repo) == []


# ---------------------------------------------------------------------------
# PR #1142 finding 3: the "<n> of the <m> siblings … the checklist" count
# ---------------------------------------------------------------------------


def test_checklist_membership_is_derived_from_the_hook_that_prints_it():
    names, drifts = gates.checklist_names(_REPO)
    assert drifts == []
    assert names == [
        "block-commit-without-codex-review",
        "commit-title-format-check",
        "commit-title-length-check",
        "pre-commit-staged-file-enumeration",
    ]
    assert set(names) <= set(EXPECTED)


def test_stale_checklist_count_word_is_drift(tmp_path):
    repo = _tree(tmp_path, {gates.SPEC: ("Four of the eight", "Eleven of the eight")})
    drifts = gates.check(repo)
    assert any(
        "prose says 11 of the siblings are in the deny checklist" in d for d in drifts
    ), drifts


def test_a_row_added_to_the_deny_checklist_breaks_the_stale_four(tmp_path):
    """The unpinned-count shape the review named.

    Growing `verify-commit-flag-override`'s printed checklist — whether by an
    eighth gate joining it or, as here, by an existing gate being added to it —
    used to leave both prose surfaces saying "Four" with this canary green.
    """
    repo = _tree(
        tmp_path,
        {
            gates.CHECKLIST: (
                "    Advisory only — never blocks.\n",
                "    Advisory only — never blocks.\n"
                "  Oversized single commit                   ← "
                "commit-decomposition-advisory\n"
                "    Advisory only — never blocks.\n",
            )
        },
    )
    drifts = gates.check(repo)
    for surface in ("spec.md", "impl.py"):
        assert any(
            surface in d
            and "prose says 4 of the siblings are in the deny checklist" in d
            for d in drifts
        ), (surface, drifts)
    # The per-host table's second column moves with it — but only where the
    # newly-listed hook actually ships. `commit-decomposition-advisory` is
    # claude-only, so `codex`'s "2" is still correct and must NOT be flagged.
    assert any(
        "'claude' row says 4 of them are in the deny checklist" in d for d in drifts
    ), drifts
    assert not any("'codex' row" in d for d in drifts), drifts


def test_checklist_naming_a_hook_that_is_not_a_commit_gate_is_drift(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.CHECKLIST: (
                "    Advisory only — never blocks.\n",
                "    Advisory only — never blocks.\n"
                "  Not a commit gate                         ← pipefail-advisory\n",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "pipefail-advisory is listed as a gate that also fires on `git commit`" in d
        for d in drifts
    ), drifts


def test_missing_checklist_claim_is_drift_not_a_silent_pass(tmp_path):
    repo = _tree(
        tmp_path,
        {
            gates.SPEC: (
                "Four of the eight siblings are the checklist",
                "Some of the siblings appear in the checklist",
            )
        },
    )
    drifts = gates.check(repo)
    assert any(
        "no '<n> of the <m> siblings … the checklist' claim found" in d for d in drifts
    ), drifts


def test_unreadable_deny_checklist_is_drift_not_a_silent_pass(tmp_path):
    repo = _tree(
        tmp_path,
        {gates.CHECKLIST: ("GIT_COMMIT_GATE_CHECKLIST = ", "COMMIT_GATE_NOTE = ")},
    )
    drifts = gates.check(repo)
    assert any("assignment not found" in d for d in drifts), drifts


def test_an_unfiltered_deny_checklist_is_drift(tmp_path):
    """The per-host checklist column is a claim about what the runtime prints.

    With the renderer gone the checklist prints all four rows on every host,
    which is the state this canary was green through before issue #1154.
    """
    repo = _tree(
        tmp_path,
        {gates.CHECKLIST: ("def render_gate_checklist(", "def _render_all(")},
    )
    drifts = gates.check(repo)
    assert any("printed unfiltered" in d for d in drifts), drifts


def test_a_defined_but_unwired_renderer_is_drift(tmp_path):
    """A renderer nothing calls satisfies a name search and prints every row.

    This is the shape a substring check cannot see: the `def` line is intact,
    so the surface reads as filtered while `main` emits the raw literal.
    """
    repo = _tree(
        tmp_path,
        {
            gates.CHECKLIST: (
                "+ render_gate_checklist(runtime_host())",
                "+ GIT_COMMIT_GATE_CHECKLIST",
            )
        },
    )
    drifts = gates.check(repo)
    assert any("never calls" in d for d in drifts), drifts
    assert any("references `GIT_COMMIT_GATE_CHECKLIST` directly" in d for d in drifts), drifts


def test_unparsable_checklist_source_is_drift_not_a_silent_pass(tmp_path):
    """A file the AST cannot read verifies nothing, so it must say so."""
    repo = _tree(
        tmp_path,
        {gates.CHECKLIST: ("def render_gate_checklist(", "def render_gate_checklist(((")},
    )
    drifts = gates.check(repo)
    assert any("could not be parsed" in d for d in drifts), drifts


def test_a_rewritten_manifest_is_re_read_not_served_stale(tmp_path):
    """The parse cache must key on the file's bytes, not only on its path.

    `_load_manifest` is cached because `derive()` runs once per
    hook-installing host and re-parsed the same 101-entry manifest a dozen
    times per run. That cache is only safe while a rewrite busts it — every
    fixture in this file rewrites the manifest under one path and re-runs the
    checker, so a path-only key would hand them the previous parse and the
    fixtures would silently stop testing anything.
    """
    tree = _tree(tmp_path)
    assert len(gates.derive(tree, "claude")[0]) == 8

    manifest_path = tree / gates.MANIFEST
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in data["hooks"]:
        if entry["name"] == "commit-title-length-check":
            entry.pop("gates", None)
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    names, _ = gates.derive(tree, "claude")
    assert len(names) == 7, "the rewrite was served from a stale parse"
    assert "commit-title-length-check" not in names
