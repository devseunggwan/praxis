"""`_lib/_memory_frontmatter.py` against the runtime it answers for (#1426).

The point of this file is not that the helper returns the right strings. It is
that three readers cannot disagree about whether a memory is dark:

  - `memory-hint` — the runtime that drops the entry,
  - `check-memory-frontmatter.py` — the after-the-fact lint,
  - `memory-distillation-fields-gate` — the write-time gate.

Issue #1094 is the incident: two of them had drifted, and the check written to
find a dark memory could not see one. So each fixture below is run through the
helper AND through the real `memory-hint` parser, and the two must agree.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks" / "_lib"))

import _memory_frontmatter as MF  # type: ignore[import-not-found]  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


memory_hint = _load(REPO / "hooks" / "advisory-nudge" / "memory-hint" / "impl.py",
                    "memory_hint_impl")
def _block(metadata: str) -> str:
    """A frontmatter block in the shape entries actually have on disk: the
    taxonomy fields nested two spaces under `metadata:`. The lint requires
    that nesting; the runtime and the helper are indifferent to it, and this
    file is where all three are compared, so the strictest shape is used."""
    body = "\n".join(f"  {line}" for line in metadata.splitlines())
    return f"name: m\ndescription: d\nmetadata:\n{body}"


def _doc(metadata: str) -> str:
    return f"---\n{_block(metadata)}\n---\n\nbody text\n"


# (name, metadata lines, expected shape or None). Every shape the corpus holds,
# every shape the runtime rejects, and the two acceptances that look like
# accidents and are not.
FIXTURES = [
    ("flat list", "hookable: true\nhookKeywords: [git, push]", None),
    ("flat list, inline comment", "hookable: true\nhookKeywords: [git] # why", None),
    ("flat list, quoted items", 'hookable: true\nhookKeywords: ["git", \'push\']', None),
    # The runtime's `\s*` spans a newline, so a bracket list on the next line
    # IS indexed. Pinned because narrowing it would be a silent behaviour
    # change to memory-hint wearing the shape of a refactor.
    ("bracket on the next line", "hookable: true\nhookKeywords:\n  [git, push]", None),
    ("hookable true, quoted", 'hookable: "true"\nhookKeywords: [git]', None),
    ("hookable yes", "hookable: yes\nhookKeywords: [git]", None),
    # The inline-comment strip is what #1094 was about: without it this reads
    # non-truthy and the entry stops being checked at all.
    ("hookable true, inline comment", "hookable: true # on\nhookKeywords: [git]", None),

    ("block list", "hookable: true\nhookKeywords:\n  - git\n  - push", MF.SHAPE_BLOCK_LIST),
    ("block list, one item", "hookable: true\nhookKeywords:\n  - git", MF.SHAPE_BLOCK_LIST),
    ("scalar", "hookable: true\nhookKeywords: git", MF.SHAPE_SCALAR),
    ("unclosed bracket", "hookable: true\nhookKeywords: [git, push", MF.SHAPE_UNCLOSED),
    ("empty list", "hookable: true\nhookKeywords: []", MF.SHAPE_EMPTY),
    ("list of empties", "hookable: true\nhookKeywords: [ , ]", MF.SHAPE_EMPTY),
    ("key absent", "hookable: true\ntype: feedback", MF.SHAPE_ABSENT),
    # The key is there and nothing follows it in the block, so KEYWORDS_VALUE_RE
    # — whose `\s*` spans newlines — finds no value anywhere. The runtime drops
    # the entry exactly as for `absent`, but a caller keyed on "is the key
    # there" answers the opposite, which is why this shape has its own name:
    # named `absent`, it satisfied the lint's missing-key check and had no
    # message under its own shape, so a dark memory passed clean.
    ("key present, no value", "hookable: true\nhookKeywords:", MF.SHAPE_VALUELESS),

    # `hookable` not truthy: nothing indexes the entry, so no shape can hide
    # it from anything and the gate must stay silent whatever the keywords are.
    ("hookable false, block list", "hookable: false\nhookKeywords:\n  - git", None),
    ("hookable false, scalar", "hookable: false\nhookKeywords: git", None),
    ("hookable false, absent", "hookable: false\ntype: feedback", None),
    ("hookable absent", "type: feedback", None),
]


def test_shape_matches_the_expected_name():
    for name, fm, expected in FIXTURES:
        assert MF.dark_memory_shape(_block(fm)) == expected, name


def test_every_shape_name_has_a_reason():
    for shape in (MF.SHAPE_ABSENT, MF.SHAPE_BLOCK_LIST, MF.SHAPE_SCALAR,
                  MF.SHAPE_UNCLOSED, MF.SHAPE_EMPTY, MF.SHAPE_VALUELESS):
        assert shape in MF.SHAPE_REASON


def test_helper_and_runtime_agree_on_every_fixture():
    """The oracle. `dark_memory_shape` is not None exactly when `memory-hint`
    drops the entry — no fixture may be dark to one reader and fine to the
    other, in either direction."""
    for name, fm, _expected in FIXTURES:
        indexed = memory_hint.parse_frontmatter(_doc(fm)) is not None
        dark = MF.dark_memory_shape(_block(fm)) is not None
        # `hookable: false` is the one case where both say "not indexed" for
        # different reasons: the runtime drops it because it was never meant
        # to be indexed, and the helper answers None because that is correct.
        if not MF.hookable_is_truthy(_block(fm)):
            assert not indexed, name
            assert not dark, name
            continue
        assert indexed is not dark, name
