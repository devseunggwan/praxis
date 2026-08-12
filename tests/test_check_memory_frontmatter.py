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


def test_hookable_true_missing_hookkeywords_flagged(tmp_path):
    # F1 (issue #942, codex-review pass after the original release): a field
    # that is entirely ABSENT from the frontmatter was previously invisible
    # to this checker — only present-but-malformed forms were caught. The
    # memory-hint parser (impl.py:117-119) drops the memory outright when
    # `hookable: true` and `hookKeywords:` has no match at all.
    p = _write(
        tmp_path,
        "feedback_bad_missing_keywords.md",
        """---
name: bad-missing-keywords
description: test
metadata:
  type: feedback
  hookable: true
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert any("hookable: true" in e and "hookKeywords" in e and "missing" in e for e in errors), errors


def test_hookable_false_missing_hookkeywords_not_flagged(tmp_path):
    # The F1 check is conditional on hookable being truthy — a non-hookable
    # memory has no hint-index behavior to protect, so omitting hookKeywords
    # entirely is normal, not drift.
    p = _write(
        tmp_path,
        "feedback_not_hookable.md",
        """---
name: not-hookable
description: test
metadata:
  type: feedback
  originSessionId: abc-123
---
body
""",
    )
    assert check.check_file(p) == []


def test_hookkeywords_empty_bracket_flagged(tmp_path):
    # F1: `hookKeywords: []` starts with `[` so the pre-F1 check accepted it
    # as clean bracket form, but the runtime parser (impl.py:130-139) reads
    # an empty inner list and returns None — same silent drop as the missing
    # case above.
    p = _write(
        tmp_path,
        "feedback_bad_empty_keywords.md",
        """---
name: bad-empty-keywords
description: test
metadata:
  type: feedback
  hookable: true
  hookKeywords: []
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert any("hookKeywords" in e and "empty list" in e for e in errors), errors


def test_hookevents_empty_bracket_not_flagged(tmp_path):
    # Asymmetric with hookKeywords (F1 scope note): an empty hookEvents list
    # falls back to the `[Bash]` default at runtime (impl.py:150-169) rather
    # than dropping the memory, so it is not a drift the way an empty
    # hookKeywords is.
    p = _write(
        tmp_path,
        "feedback_empty_events.md",
        """---
name: empty-events
description: test
metadata:
  type: feedback
  hookable: true
  hookKeywords: [foo]
  hookEvents: []
  originSessionId: abc-123
---
body
""",
    )
    assert check.check_file(p) == []


def test_hookevents_block_form_message_says_fallback_not_drop(tmp_path):
    # F2 (issue #942, same codex-review pass): the shared error message used
    # to claim "the entire memory is dropped" for both hookKeywords and
    # hookEvents malformed shapes. For hookEvents that is false — impl.py
    # falls back to the default [Bash] event and still indexes the memory.
    p = _write(
        tmp_path,
        "feedback_bad_events_blockform.md",
        """---
name: bad-events-blockform
description: test
metadata:
  type: feedback
  hookable: true
  hookKeywords: [foo]
  hookEvents:
    - Edit
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    matching = [e for e in errors if e.startswith("`hookEvents:`")]
    assert matching, errors
    assert "falls back to the default" in matching[0], matching
    assert "drops the entire memory" not in matching[0], matching


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
