"""Tests for scripts/check-hook-token-invariants.py (issue #712).

The canary pins load-bearing hook tokens to both the hook source that enforces
them (via ``scan_pattern``, the regex fragment) and the spec that documents
them (via ``token``). These tests cover:

  - the real tree passes (every pinned token is present on both sides),
  - a token reworded on either side is detected,
  - the partial-drift case: the enforcing regex line is reworded while a bare
    copy of the token survives in a comment — the gap a plain substring check
    would miss,
  - a missing file is reported rather than silently passing,
  - main() exits 0 with a verified count on a clean tree.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check-hook-token-invariants.py"


def _load():
    spec = importlib.util.spec_from_file_location("hook_token_canary", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


canary = _load()

# A sentinel that does NOT contain any pinned token/pattern as a substring —
# appending a suffix would leave the original present and miss the drift.
_SENTINEL = "<<REWORDED>>"


def _temp_repo(tmp_path: Path) -> Path:
    """Copy every scan/doc file referenced by INVARIANTS into a temp tree."""
    for inv in canary.INVARIANTS:
        for side in ("scan", "doc"):
            src = _REPO / inv[side]
            dst = tmp_path / inv[side]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
    return tmp_path


def test_real_tree_holds():
    # Regression guard + clean-state AC: every pinned invariant holds verbatim
    # on both sides in the actual repo.
    assert canary.check() == []


def test_invariant_table_is_well_formed():
    for inv in canary.INVARIANTS:
        assert set(inv) >= {"token", "scan_pattern", "scan", "doc"}
        assert isinstance(inv["token"], str) and inv["token"]
        assert isinstance(inv["scan_pattern"], str) and inv["scan_pattern"]
        assert inv["scan"].endswith(".py")
        assert inv["doc"].endswith(".md")


def test_reworded_doc_is_detected(tmp_path):
    repo = _temp_repo(tmp_path)
    inv = canary.INVARIANTS[0]
    target = repo / inv["doc"]
    target.write_text(target.read_text().replace(inv["token"], _SENTINEL))
    drifts = canary.check(repo=repo)
    assert any(inv["token"] in d and "doc side" in d for d in drifts)


def test_reworded_scan_pattern_is_detected(tmp_path):
    repo = _temp_repo(tmp_path)
    inv = canary.INVARIANTS[0]
    target = repo / inv["scan"]
    target.write_text(target.read_text().replace(inv["scan_pattern"], _SENTINEL))
    drifts = canary.check(repo=repo)
    assert any(inv["token"] in d and "scan side" in d for d in drifts)


def test_partial_scan_drift_is_detected(tmp_path):
    # The core strengthening: rewording ONLY the enforcing regex line must be
    # caught even when a bare copy of the token survives elsewhere (a comment,
    # docstring, deny message). A plain `token in text` check would pass here.
    # A synthetic scan/doc pair keeps this decoupled from any production hook
    # file's comment wording (so an unrelated comment cleanup can't break it).
    inv = {
        "token": "Pre-commit verified:",
        "scan_pattern": "^Pre-commit verified:",
        "scan": "hooks/example/impl.py",
        "doc": "hooks/example/spec.md",
    }
    scan_file = tmp_path / inv["scan"]
    doc_file = tmp_path / inv["doc"]
    scan_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    # A bare token copy (prose comment) alongside the enforcing regex line.
    scan_file.write_text(
        "# deny message mentions Pre-commit verified: in prose\n"
        'PATTERN = re.compile(r"^Pre-commit verified:[ \\t]*\\S")\n'
    )
    doc_file.write_text("Add a `Pre-commit verified:` line.\n")
    # Reword only the enforcing fragment; the bare comment copy remains.
    scan_file.write_text(scan_file.read_text().replace(inv["scan_pattern"], _SENTINEL))
    assert inv["token"] in scan_file.read_text()  # a bare copy still survives...
    drifts = canary.check(repo=tmp_path, invariants=[inv])  # ...yet drift is caught
    assert any(inv["token"] in d and "scan side" in d for d in drifts)


def test_missing_file_is_reported(tmp_path):
    custom = [
        {"token": "X-TOKEN", "scan_pattern": "X-TOKEN", "scan": "nope/a.py", "doc": "nope/b.md"}
    ]
    drifts = canary.check(repo=tmp_path, invariants=custom)
    assert len(drifts) == 2
    assert all("file missing on disk" in d for d in drifts)


def test_main_clean_exits_zero(capsys):
    rc = canary.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert f"{len(canary.INVARIANTS)} invariants verified" in out


def test_bypass_regex_pin_matches_runtime_regex():
    # The pinned bypass-family pattern must be the exact regex source both the
    # hook compiles and the spec documents — not a paraphrase.
    bypass = next(i for i in canary.INVARIANTS if "BYPASS" in i["token"])
    scan_text = (_REPO / bypass["scan"]).read_text()
    doc_text = (_REPO / bypass["doc"]).read_text()
    assert f're.compile(r"{bypass["scan_pattern"]}")' in scan_text
    assert bypass["token"] in doc_text
