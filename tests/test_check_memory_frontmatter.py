"""Tests for scripts/check-memory-frontmatter.py (issue #942).

Covers the three shapes the script's docstring commits to:
  - taxonomy fields at the top level (not nested under `metadata:`) fail
  - a field duplicated across top-level + `metadata:` (or nested twice) fails
  - `hookKeywords:` / `hookEvents:` in multi-line YAML-block or scalar form
    fail — this is the functional-bug case, not just a style drift
  - a normalized entry passes
  - directory resolution honors `PRAXIS_MEMORY_DIR` and skips (rather than
    erroring) when the directory is absent, matching resolve_memory_dir()'s
    own contract
"""
from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_memory_frontmatter", REPO_ROOT / "scripts" / "check-memory-frontmatter.py"
)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


# ---------------------------------------------------------------------------
# check_file — pure frontmatter checker
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_normalized_entry_is_clean(tmp_path):
    p = _write(
        tmp_path,
        "feedback_good.md",
        """---
name: good
description: test
metadata:
  node_type: memory
  type: feedback
  hookable: true
  hookKeywords: [foo, bar]
  originSessionId: abc-123
---
body
""",
    )
    assert check.check_file(p) == []


def test_top_level_taxonomy_field_flagged(tmp_path):
    p = _write(
        tmp_path,
        "feedback_bad.md",
        """---
name: bad
description: test
type: feedback
originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert any("`type`" in e and "top level" in e for e in errors), errors
    assert any("`originSessionId`" in e and "top level" in e for e in errors), errors


def test_duplicate_field_top_level_and_metadata_flagged(tmp_path):
    p = _write(
        tmp_path,
        "feedback_bad_dup.md",
        """---
name: bad-dup
description: test
type: feedback
metadata:
  type: feedback
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert any("`type`" in e and "2 times" in e for e in errors), errors


def test_hookkeywords_block_form_flagged_as_functional_bug(tmp_path):
    p = _write(
        tmp_path,
        "feedback_bad_blockform.md",
        """---
name: bad-blockform
description: test
metadata:
  type: feedback
  hookable: true
  hookKeywords:
    - foo
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert any("multi-line YAML-block" in e for e in errors), errors


def test_hookkeywords_scalar_form_flagged(tmp_path):
    p = _write(
        tmp_path,
        "feedback_bad_scalar.md",
        """---
name: bad-scalar
description: test
metadata:
  type: feedback
  hookable: true
  hookKeywords: kubectl
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert any("scalar form" in e for e in errors), errors


def test_missing_frontmatter_fence_flagged(tmp_path):
    p = _write(tmp_path, "feedback_no_fence.md", "just prose, no frontmatter\n")
    errors = check.check_file(p)
    assert any("frontmatter" in e for e in errors), errors


def test_momentum_field_is_not_taxonomy(tmp_path):
    # `momentum:` is a real top-level field in several existing entries and
    # is deliberately out of scope — it must never be flagged.
    p = _write(
        tmp_path,
        "feedback_momentum.md",
        """---
name: has-momentum
description: test
metadata:
  type: feedback
  originSessionId: abc-123
momentum: [merge]
---
body
""",
    )
    assert check.check_file(p) == []


def test_missing_node_type_is_not_flagged(tmp_path):
    # node_type has no consumer (grep-verified, issue #942) — its absence is
    # optional, not drift. Two real entries in the corpus lack it entirely.
    p = _write(
        tmp_path,
        "feedback_no_node_type.md",
        """---
name: no-node-type
description: test
metadata:
  type: feedback
  originSessionId: abc-123
---
body
""",
    )
    assert check.check_file(p) == []


# ---------------------------------------------------------------------------
# main() — directory resolution + N/A contract
# ---------------------------------------------------------------------------

def test_main_skips_when_directory_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MEMORY_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.delenv("PRAXIS_TESTS_STRICT", raising=False)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = check.main()
    assert rc == 0
    assert "N/A" in out.getvalue()


def test_main_skip_is_failure_under_strict(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MEMORY_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("PRAXIS_TESTS_STRICT", "1")
    out = io.StringIO()
    with redirect_stdout(out):
        rc = check.main()
    assert rc == 1
    assert "N/A" in out.getvalue()


def test_main_fails_on_drifted_entry(tmp_path, monkeypatch):
    _write(
        tmp_path,
        "feedback_bad.md",
        """---
name: bad
description: test
type: feedback
originSessionId: abc-123
---
body
""",
    )
    monkeypatch.setenv("PRAXIS_MEMORY_DIR", str(tmp_path))
    monkeypatch.delenv("PRAXIS_TESTS_STRICT", raising=False)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = check.main()
    assert rc == 1
    assert "FAIL: feedback_bad.md" in out.getvalue()


def test_main_ignores_memory_md_index_file(tmp_path, monkeypatch):
    # MEMORY.md is the index, not a memory entry — it has no frontmatter
    # contract and must never be linted.
    _write(tmp_path, "MEMORY.md", "- [foo](foo.md) — bar\n")
    monkeypatch.setenv("PRAXIS_MEMORY_DIR", str(tmp_path))
    monkeypatch.delenv("PRAXIS_TESTS_STRICT", raising=False)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = check.main()
    assert rc == 0
    assert "N/A" in out.getvalue()  # no *.md entries besides MEMORY.md
