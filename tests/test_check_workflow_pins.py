"""Tests for scripts/check-workflow-pins.py.

The canary asserts the workflow pinning discipline (issue #1171): SHA-pinned
``uses:`` refs, no floating ``*-latest`` runner labels, and exact-version
inline tool installs. Each evasion the canary has to survive gets its own
fixture:

  - the real tree agrees with the canary,
  - a tag ref and a ref-less ``uses:`` are drift,
  - a floating ``runs-on:`` alias is drift — including the case-variant form
    (``Ubuntu-Latest``): GitHub runner labels are case-insensitive, so a
    case-sensitive match was an evasion,
  - the matrix bypass: ``runs-on: ${{ matrix.os }}`` over a literal
    ``os: [ubuntu-latest]`` used to pass clean; matrix literals (flow
    sequence, block list item, and ``include:`` entry) are now checked,
  - the block-scalar false positive: ``uses:``/``runs-on:`` strings inside a
    ``run: |`` body are prose, not workflow keys, and must not FAIL,
  - ``docker://image@sha256:<digest>`` is the immutable pin form for
    container actions and must pass; a docker tag ref must not,
  - the inline tool pins that motivated the PR: an unpinned
    ``npm install -g markdownlint-cli2`` or ``pip install ... ruff`` is
    drift — including inside a ``run: |`` body, the one place the
    block-scalar exemption must NOT cover,
  - the shapes a line-oriented scan could not see, each of which read as clean
    while selecting a floating runner or an unpinned tool: a block-scalar or
    block-list ``runs-on``, a flow-mapping ``matrix``, an expression wrapping a
    literal, a backslash-continued or ``pip3`` install, a pin that lives only
    in a comment, and an incomplete version (``@0.23``, ``ruff==0.15``),
  - the false positive that came with them: a matrix dimension ``runs-on``
    never references is test data, not a runner,
  - what cannot be resolved is drift, not a pass: an unverifiable runner
    expression, a matrix (or ``include:``) candidate that is itself an
    expression, and a workflow that does not parse,
  - zero scanned values is drift, not a silent pass,
  - main() exits 0 on a clean tree and 1 on drift.

Fixtures are standalone minimal workflows written to a temp tree — unlike the
sibling-gates tests there is no prose surface to anchor against, so each case
pins the exact line shape it exercises.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# The checker imports PyYAML at module scope, and this module executes it at
# import time — so without the guard a machine lacking PyYAML fails during
# pytest *collection*, taking the whole suite down instead of skipping here.
pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check-workflow-pins.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_workflow_pins", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pins = _load()

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
DIGEST = "f" * 64

# One fixture exercising every allowed shape at once: SHA-pinned uses with a
# tag comment, pinned runs-on, prose uses:/runs-on: inside a run: | body,
# pinned inline installs, a digest-pinned docker action, and a matrix whose
# literals are all pinned behind a ${{ matrix.os }} indirection.
CLEAN = f"""\
name: fixture
on: push
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@{SHA} # v7.0.1
      - uses: ./.github/actions/local-one
      - run: |
          echo "uses: actions/checkout@v4 is quoted prose, not a workflow key"
          echo "runs-on: ubuntu-latest likewise"
      - run: npm install -g markdownlint-cli2@0.23.2
      - run: python3 -m pip install pytest "ruff==0.15.8"
  container:
    runs-on: ubuntu-24.04
    steps:
      - uses: docker://alpine@sha256:{DIGEST}
  fanout:
    strategy:
      matrix:
        os: [ubuntu-24.04, macos-14]
        include:
          - os: windows-2022
            python: "3.12"
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - uses: actions/checkout@{SHA} # v7.0.1
"""


def _dir(tmp_path: Path, content: str = CLEAN, name: str = "wf.yml") -> Path:
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / name).write_text(content, encoding="utf-8")
    return d


def test_real_tree_holds():
    drifts, checked = pins.check()
    assert drifts == []
    assert checked > 0


def test_clean_fixture_passes(tmp_path):
    """Every case below starts from this passing shape, so a failure is the edit."""
    drifts, checked = pins.check(_dir(tmp_path))
    assert drifts == [], drifts
    assert checked > 0


def test_tag_ref_is_drift(tmp_path):
    content = CLEAN.replace(f"actions/checkout@{SHA} # v7.0.1", "actions/checkout@v7", 1)
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any(
        "wf.yml:" in d and "actions/checkout@v7" in d and "40-char commit SHA" in d
        for d in drifts
    ), drifts


def test_refless_uses_is_drift(tmp_path):
    content = CLEAN.replace(f"actions/checkout@{SHA} # v7.0.1", "actions/checkout", 1)
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("carries no @ref" in d for d in drifts), drifts


def test_floating_runs_on_is_drift(tmp_path):
    content = CLEAN.replace("runs-on: ubuntu-24.04", "runs-on: ubuntu-latest", 1)
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any(
        "runs-on 'ubuntu-latest' floats" in d and "wf.yml:5" in d for d in drifts
    ), drifts


def test_case_variant_floating_alias_is_drift(tmp_path):
    """GitHub runner labels are case-insensitive; the match must be too."""
    content = CLEAN.replace("runs-on: ubuntu-24.04", "runs-on: Ubuntu-Latest", 1)
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("runs-on 'Ubuntu-Latest' floats" in d for d in drifts), drifts


def test_matrix_flow_sequence_literal_is_drift(tmp_path):
    """The bypass: ${{ matrix.os }} over a floating literal used to pass clean."""
    content = CLEAN.replace(
        "os: [ubuntu-24.04, macos-14]", "os: [ubuntu-24.04, macos-latest]"
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("matrix.os value 'macos-latest' floats" in d for d in drifts), drifts


def test_matrix_block_list_item_is_drift(tmp_path):
    content = CLEAN.replace(
        "os: [ubuntu-24.04, macos-14]",
        "os:\n          - ubuntu-24.04\n          - ubuntu-latest",
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("matrix.os value 'ubuntu-latest' floats" in d for d in drifts), drifts


def test_matrix_include_entry_is_drift(tmp_path):
    content = CLEAN.replace("- os: windows-2022", "- os: windows-latest")
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("matrix.os value 'windows-latest' floats" in d for d in drifts), drifts


def test_matrix_expression_runs_on_alone_is_not_flagged(tmp_path):
    """CLEAN's fanout job keeps ${{ matrix.os }}; only literals are judged."""
    drifts, _ = pins.check(_dir(tmp_path))
    assert not any("matrix.os" in d for d in drifts), drifts


def test_uses_prose_inside_run_block_is_not_flagged(tmp_path):
    """The false positive: a run: | body quoting workflow YAML is prose."""
    drifts, _ = pins.check(_dir(tmp_path))
    assert drifts == [], drifts
    # …and the exemption is really the block scalar, not luck: the same text
    # promoted to a real key line must FAIL.
    content = CLEAN.replace(
        '      - run: |\n'
        '          echo "uses: actions/checkout@v4 is quoted prose, not a workflow key"\n'
        '          echo "runs-on: ubuntu-latest likewise"\n',
        "      - uses: actions/checkout@v4\n",
    )
    drifts, _ = pins.check(_dir(tmp_path / "promoted", content))
    assert any("actions/checkout@v4" in d for d in drifts), drifts


def test_docker_digest_pin_passes_and_tag_fails(tmp_path):
    drifts, _ = pins.check(_dir(tmp_path))
    assert not any("docker" in d for d in drifts), drifts
    content = CLEAN.replace(
        f"docker://alpine@sha256:{DIGEST}", "docker://alpine:3.18"
    )
    drifts, _ = pins.check(_dir(tmp_path / "tagged", content))
    assert any(
        "docker action 'docker://alpine:3.18'" in d and "sha256" in d for d in drifts
    ), drifts


def test_unpinned_npm_markdownlint_is_drift(tmp_path):
    content = CLEAN.replace(
        "npm install -g markdownlint-cli2@0.23.2", "npm install -g markdownlint-cli2"
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any(
        "npm install of markdownlint-cli2 is unpinned" in d for d in drifts
    ), drifts


def test_unpinned_pip_ruff_is_drift(tmp_path):
    content = CLEAN.replace('pip install pytest "ruff==0.15.8"', "pip install pytest ruff")
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("pip install of ruff is unpinned" in d for d in drifts), drifts


def test_unpinned_install_inside_run_block_is_still_drift(tmp_path):
    """The one thing the block-scalar exemption must NOT cover: installs
    live inside run: | bodies, so the pin discipline follows them there."""
    content = CLEAN.replace(
        "      - run: npm install -g markdownlint-cli2@0.23.2\n",
        "      - run: |\n"
        "          set -e\n"
        "          npm install -g markdownlint-cli2\n",
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any(
        "npm install of markdownlint-cli2 is unpinned" in d for d in drifts
    ), drifts


def test_zero_scanned_lines_is_drift_not_a_silent_pass(tmp_path):
    empty = tmp_path / ".github" / "workflows"
    empty.mkdir(parents=True)
    drifts, _ = pins.check(empty)
    assert any("no uses:/runs-on: values found" in d for d in drifts), drifts


def test_main_exit_codes(monkeypatch, tmp_path):
    assert pins.main() == 0
    broken = CLEAN.replace("runs-on: ubuntu-24.04", "runs-on: ubuntu-latest", 1)
    monkeypatch.setattr(pins, "WORKFLOWS", _dir(tmp_path, broken))
    assert pins.main() == 1


# --- Shapes a line-oriented scan could not see -----------------------------
# Each of the next cases read as CLEAN under the regex scanner while selecting
# a floating runner or an unpinned tool, which is why the checker parses YAML.


def test_block_scalar_runs_on_is_drift(tmp_path):
    content = CLEAN.replace(
        "    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout",
        "    runs-on: >-\n      ubuntu-latest\n    steps:\n      - uses: actions/checkout",
        1,
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("runs-on 'ubuntu-latest' floats" in d for d in drifts), drifts


def test_block_list_runs_on_is_drift(tmp_path):
    content = CLEAN.replace(
        "    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout",
        "    runs-on:\n      - ubuntu-latest\n    steps:\n      - uses: actions/checkout",
        1,
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("runs-on 'ubuntu-latest' floats" in d for d in drifts), drifts


def test_flow_mapping_matrix_is_drift(tmp_path):
    content = CLEAN.replace(
        "      matrix:\n        os: [ubuntu-24.04, macos-14]\n"
        "        include:\n          - os: windows-2022\n"
        '            python: "3.12"\n',
        "      matrix: {os: [ubuntu-latest]}\n",
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("matrix.os value 'ubuntu-latest' floats" in d for d in drifts), drifts


def test_expression_wrapping_a_literal_is_resolved(tmp_path):
    """`${{ 'ubuntu-latest' }}` selects a floating runner and used to pass."""
    content = CLEAN.replace(
        "    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout",
        "    runs-on: ${{ 'ubuntu-latest' }}\n    steps:\n      - uses: actions/checkout",
        1,
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("runs-on 'ubuntu-latest' floats" in d for d in drifts), drifts


def test_unverifiable_runner_expression_fails_closed(tmp_path):
    content = CLEAN.replace(
        "    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout",
        "    runs-on: ${{ inputs.runner }}\n    steps:\n      - uses: actions/checkout",
        1,
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("cannot be verified" in d for d in drifts), drifts


def test_backslash_continued_install_is_examined(tmp_path):
    content = CLEAN.replace(
        '      - run: python3 -m pip install pytest "ruff==0.15.8"\n',
        "      - run: |\n          pip install \\\n            ruff\n",
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("pip install of ruff is unpinned" in d for d in drifts), drifts


def test_pip3_install_is_examined(tmp_path):
    content = CLEAN.replace(
        '      - run: python3 -m pip install pytest "ruff==0.15.8"\n',
        "      - run: pip3 install ruff\n",
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("pip install of ruff is unpinned" in d for d in drifts), drifts


def test_pin_inside_a_comment_is_not_a_pin(tmp_path):
    content = CLEAN.replace(
        '      - run: python3 -m pip install pytest "ruff==0.15.8"\n',
        "      - run: pip install ruff # ruff==0.15.8\n",
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("pip install of ruff is unpinned" in d for d in drifts), drifts


def test_incomplete_npm_version_is_not_a_pin(tmp_path):
    for spec in ("markdownlint-cli2@0", "markdownlint-cli2@0.23"):
        content = CLEAN.replace("markdownlint-cli2@0.23.2", spec)
        drifts, _ = pins.check(_dir(tmp_path / spec, content))
        assert any(
            "npm install of markdownlint-cli2 is unpinned" in d for d in drifts
        ), (spec, drifts)


def test_non_runner_matrix_dimension_is_not_flagged(tmp_path):
    """A matrix dimension runs-on never references is test data, not a runner."""
    content = CLEAN.replace(
        "        os: [ubuntu-24.04, macos-14]\n",
        "        os: [ubuntu-24.04, macos-14]\n        release: [ubuntu-latest]\n",
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert not any("ubuntu-latest" in d for d in drifts), drifts


def test_unparseable_workflow_is_drift(tmp_path):
    drifts, _ = pins.check(_dir(tmp_path, "jobs: [unclosed\n"))
    assert any("cannot verify" in d for d in drifts), drifts


def test_incomplete_pip_version_is_not_a_pin(tmp_path):
    """Same rule as npm: a two-part `ruff==0.15` is a range, not a pin."""
    for spec in ("ruff==0", "ruff==0.15"):
        content = CLEAN.replace("ruff==0.15.8", spec)
        drifts, _ = pins.check(_dir(tmp_path / spec.replace("=", "_"), content))
        assert any("pip install of ruff is unpinned" in d for d in drifts), (
            spec,
            drifts,
        )


def test_expression_valued_matrix_candidate_fails_closed(tmp_path):
    """`os: ["${{ inputs.runner }}"]` only moves the indirection one hop."""
    content = CLEAN.replace(
        "        os: [ubuntu-24.04, macos-14]\n",
        '        os: ["${{ inputs.runner }}"]\n',
        1,
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("matrix.os candidate" in d and "cannot be verified" in d for d in drifts), drifts


def test_expression_valued_include_candidate_fails_closed(tmp_path):
    content = CLEAN.replace(
        "          - os: windows-2022\n",
        '          - os: "${{ inputs.runner }}"\n',
        1,
    )
    drifts, _ = pins.check(_dir(tmp_path, content))
    assert any("matrix.os candidate" in d and "cannot be verified" in d for d in drifts), drifts
