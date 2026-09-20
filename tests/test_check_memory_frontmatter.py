"""Tests for scripts/check-memory-frontmatter.py (issue #942).

Covers the three shapes the script's docstring commits to:
  - taxonomy fields at the top level (not nested under `metadata:`) fail
  - a field duplicated across top-level + `metadata:` (or nested twice) fails
  - `hookKeywords:` / `hookEvents:` in multi-line YAML-block or scalar form
    fail — this is the functional-bug case, not just a style drift
  - `hookKeywords:` opened with `[` but never closed, and `hookable: true`
    carrying a YAML inline comment with no hookKeywords, both fail — the lint
    was diverging from the runtime parser on these two shapes (issue #1094)
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


def test_hookable_inline_comment_with_no_keywords_flagged(tmp_path):
    # issue #1094: `hookable: true # enabled` carries a YAML inline comment.
    # impl.py:108-111 runs _strip_inline_comment before the truthiness test,
    # so the runtime reads `true` (truthy) and — with no hookKeywords — drops
    # the memory (impl.py:117-119 returns None). The pre-#1094 lint did NOT
    # strip the inline comment, so its truthiness check read `true # enabled`
    # as non-truthy and skipped the hookable-but-no-keywords check entirely,
    # passing a memory the runtime silently drops.
    p = _write(
        tmp_path,
        "feedback_bad_hookable_comment.md",
        """---
name: bad-hookable-comment
description: test
metadata:
  type: feedback
  hookable: true # enabled
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


def test_hookkeywords_unclosed_bracket_flagged(tmp_path):
    # issue #1094: `hookKeywords: [kubectl, helm` opens a `[` but never closes
    # it. impl.py:127-129 returns None the moment `find("]") == -1`, dropping
    # the whole memory from the hint index. The pre-#1094 lint took
    # `value[1:]` as the inner list, saw non-empty content, and reported clean
    # — passing a memory the runtime silently drops (the exact silent-dark
    # class this lint exists to catch).
    p = _write(
        tmp_path,
        "feedback_bad_unclosed_keywords.md",
        """---
name: bad-unclosed-keywords
description: test
metadata:
  type: feedback
  hookable: true
  hookKeywords: [kubectl, helm
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert any("hookKeywords" in e and "no closing `]`" in e for e in errors), errors


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


def test_hookkeywords_bracket_on_the_next_line_is_not_flagged(tmp_path):
    # Regression (issue #1426). The lint's check was line-based: it asked
    # whether the value on the `hookKeywords:` line itself was a bracket list.
    # memory-hint's own regex puts `\s*` after the colon and `\s` spans a
    # newline, so this shape IS indexed — and the lint reported it as
    # "silently drops the entire memory", asserting a runtime behaviour that
    # was not true. Same class as #1094 in the opposite direction: not a dark
    # memory left unflagged, but a live one flagged as dark.
    p = _write(
        tmp_path,
        "feedback_next_line_bracket.md",
        """---
name: next-line-bracket
description: test
metadata:
  type: feedback
  hookable: true
  hookKeywords:
    [foo, bar]
  originSessionId: abc-123
---
body
""",
    )
    errors = check.check_file(p)
    assert not [e for e in errors if "hookKeywords" in e], errors


def test_hookkeywords_verdict_comes_from_the_shared_predicate(tmp_path):
    """Every dark shape still reported, and the wording still names the
    consequence. The verdict is `_lib/_memory_frontmatter`'s; only the message
    is this script's."""
    shapes = {
        "block form": ("  hookKeywords:\n    - foo\n", "multi-line YAML-block"),
        "scalar": ("  hookKeywords: foo\n", "scalar form"),
        "unclosed": ("  hookKeywords: [foo, bar\n", "no closing `]`"),
        "empty": ("  hookKeywords: []\n", "empty list"),
    }
    for name, (line, needle) in shapes.items():
        p = _write(
            tmp_path,
            f"feedback_shape_{name.replace(' ', '_')}.md",
            "---\nname: s\ndescription: test\nmetadata:\n  type: feedback\n"
            f"  hookable: true\n{line}  originSessionId: abc-123\n---\nbody\n",
        )
        errors = check.check_file(p)
        assert any(needle in e for e in errors), f"{name}: {errors}"


def test_hookkeywords_present_but_valueless_is_reported(tmp_path):
    """A bare `hookKeywords:` as the last frontmatter key used to pass clean.

    It fell between two checks: the missing-key check saw the key present, and
    the shape check got `absent` back from the helper, which has no message
    here. The runtime drops the memory from the hint index all the same, so
    the entry was dark and nothing said so. Controls below pin the three
    neighbouring shapes, which were always reported (or always clean).
    """
    def _entry(name: str, tail: str) -> list[str]:
        p = _write(
            tmp_path,
            f"feedback_{name}.md",
            "---\nname: s\ndescription: test\nmetadata:\n  type: feedback\n"
            f"  originSessionId: abc-123\n  hookable: true\n{tail}---\nbody\n",
        )
        return check.check_file(p)

    valueless = _entry("valueless", "  hookKeywords:\n")
    assert any("no value at all" in e for e in valueless), valueless

    # Control 1 — key absent entirely: its own check reports it.
    missing = _entry("missing", "")
    assert any("`hookKeywords` is missing" in e for e in missing), missing

    # Control 2 — scalar value: the shape check reports it.
    scalar = _entry("scalar", "  hookKeywords: git push\n")
    assert any("scalar form" in e for e in scalar), scalar

    # Control 3 — the good form the runtime indexes: no hookKeywords error.
    good = _entry("good", "  hookKeywords: [git, push]\n")
    assert not [e for e in good if "hookKeywords" in e], good
