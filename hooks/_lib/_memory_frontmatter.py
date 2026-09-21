#!/usr/bin/env python3
"""Whether the hint index can read a memory's `hookKeywords` (issue #1426).

A memory with `hookable: true` whose `hookKeywords:` the hint parser cannot
read is not rejected anywhere — it is silently absent from the index, while
looking well-formed on disk. Measured over 742 local entries in 23 memory
directories: 70 carry `hookable: true`, 66 are indexed, and 4 (5.7%) are
dark — 3 in multi-line block-list form, 1 with no `hookKeywords:` key at all.

Three readers need the same answer and, before this module, each had its own
copy of it:

  - `hooks/advisory-nudge/memory-hint/impl.py` — the runtime that drops the
    entry,
  - `scripts/check-memory-frontmatter.py` — the after-the-fact lint,
  - `hooks/preflight-gate/memory-distillation-fields-gate/impl.py` — the
    write-time gate (#1426).

Issue #1094 is the incident that names the cost of copies: the lint had omitted
the inline-comment strip, so `hookable: true # note` read as non-truthy there
and truthy at runtime, and a dark memory went unflagged by the very check
written to find it. So the runtime imports this module rather than mirroring
it — the helper is not a mirror of the acceptance rule, it *is* the rule.

ACCEPTANCE IS UNCHANGED FROM THE PRE-#1426 RUNTIME, deliberately, including one
shape that looks like an accident and is not: `KEYWORDS_VALUE_RE`'s `\\s*` spans
a newline, so

    hookKeywords:
      [git, push]

is read as a flat list and indexed. Narrowing that would make this module a
behaviour change to `memory-hint` wearing the shape of a refactor. The
horizontal-only probe below exists to *name* a shape for an error message, and
never to decide one.
"""
from __future__ import annotations

import re

TRUTHY_VALUES = {"true", "yes"}

FRONTMATTER_FENCE = re.compile(r"^---\s*$", re.MULTILINE)
HOOKABLE_RE = re.compile(r"^\s*hookable\s*:\s*(.+)$", re.MULTILINE)
# `\s*` after the colon spans newlines — see the module docstring.
KEYWORDS_VALUE_RE = re.compile(r"^\s*hookKeywords\s*:\s*(.+)$", re.MULTILINE)
# Same key, horizontal whitespace only (`[^\S\n]`), so the value cannot come
# from the next line. Used to tell a block list from a scalar for the message.
KEYWORDS_SAME_LINE_RE = re.compile(r"^[^\S\n]*hookKeywords[^\S\n]*:(.*)$", re.MULTILINE)
# A YAML inline comment is a `#` preceded by whitespace (YAML 1.2).
INLINE_COMMENT_RE = re.compile(r"\s+#.*$")

# Every way a `hookable: true` entry fails to reach the index, and the phrase
# each one is named by. `absent` is the shape a predicate keyed on "the file
# has a hookKeywords key" cannot see, which is how six entries stayed dark.
SHAPE_ABSENT = "absent"
SHAPE_BLOCK_LIST = "block-list"
SHAPE_SCALAR = "scalar"
SHAPE_UNCLOSED = "unclosed"
SHAPE_EMPTY = "empty"
# A key with nothing after the colon and nothing after it in the block. The
# runtime drops it exactly like `absent`, but a reader keyed on "the file has
# a hookKeywords key" sees the key and skips its own absent check — so naming
# it `absent` left the one shape that satisfies both checks reported by
# neither (#1426 follow-up).
SHAPE_VALUELESS = "valueless"

SHAPE_REASON = {
    SHAPE_ABSENT: "`hookable: true` with no `hookKeywords:` key at all",
    SHAPE_VALUELESS: "`hookKeywords:` present with no value at all",
    SHAPE_BLOCK_LIST: "`hookKeywords:` in multi-line `- item` block form",
    SHAPE_SCALAR: "`hookKeywords:` in scalar form, not `[a, b]`",
    SHAPE_UNCLOSED: "`hookKeywords:` opens with `[` and never closes it",
    SHAPE_EMPTY: "`hookKeywords:` is an empty list",
}


def strip_inline_comment(value: str) -> str:
    return INLINE_COMMENT_RE.sub("", value)


def frontmatter_block(raw: str) -> str | None:
    """The text between the first two `---` fences, or None when there is none."""
    matches = list(FRONTMATTER_FENCE.finditer(raw))
    if len(matches) < 2:
        return None
    return raw[matches[0].end() : matches[1].start()]


def hookable_is_truthy(block: str) -> bool:
    """Whether the runtime will treat this entry as one to index.

    The inline-comment strip is load-bearing rather than cosmetic: without it
    `hookable: true # note` reads non-truthy, and an entry the runtime does
    index stops being checked (issue #1094).
    """
    match = HOOKABLE_RE.search(block)
    if not match:
        return False
    value = strip_inline_comment(match.group(1)).strip().lower().strip("\"'")
    return value in TRUTHY_VALUES


def parse_keywords(block: str) -> list[str] | None:
    """The keywords the index would hold, or None when it would drop the entry.

    `None` here is exactly `memory-hint`'s own drop condition, so a caller
    testing `is None` is asking the runtime's question, not a likeness of it.
    """
    match = KEYWORDS_VALUE_RE.search(block)
    if not match:
        return None
    raw = match.group(1).strip()
    if not raw.startswith("["):
        return None
    close = raw.find("]")
    if close == -1:
        return None
    inner = raw[1:close].strip()
    if not inner:
        return None
    keywords = [item.strip().strip("\"'") for item in inner.split(",")]
    keywords = [k for k in keywords if k]
    return keywords or None


def hookkeywords_shape(block: str) -> str | None:
    """Name the shape that keeps this entry out of the index, or None.

    None means the index will read it. A name is one of the `SHAPE_*` values,
    and it is chosen only after `parse_keywords` has already decided — naming
    never overrides the verdict.
    """
    if parse_keywords(block) is not None:
        return None
    match = KEYWORDS_VALUE_RE.search(block)
    if not match:
        # No value anywhere after the colon — not even on a following line,
        # since `KEYWORDS_VALUE_RE`'s `\s*` spans newlines. The key is still
        # there, so the two cases are different to a caller and identical to
        # the runtime, which drops the entry either way.
        same_line = KEYWORDS_SAME_LINE_RE.search(block)
        if same_line is not None:
            return SHAPE_VALUELESS
        return SHAPE_ABSENT
    raw = match.group(1).strip()
    if not raw.startswith("["):
        same_line = KEYWORDS_SAME_LINE_RE.search(block)
        if same_line is not None and not same_line.group(1).strip():
            return SHAPE_BLOCK_LIST
        return SHAPE_SCALAR
    if "]" not in raw:
        return SHAPE_UNCLOSED
    return SHAPE_EMPTY


def dark_memory_shape(block: str) -> str | None:
    """The shape a `hookable: true` entry is dark in, or None when it is fine.

    A `hookable: false` entry answers None whatever its keywords look like:
    nothing indexes it, so no shape can hide it from anything.
    """
    if not hookable_is_truthy(block):
        return None
    return hookkeywords_shape(block)
