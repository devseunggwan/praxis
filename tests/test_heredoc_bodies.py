"""Coverage for `heredoc_bodies`, the counterpart of `strip_heredoc_bodies`.

The stripper blanks a heredoc body because it is data; this one returns it for
the same reason — `git commit -m "$(cat <<'EOF' … EOF)"` puts the commit
message there and nothing else in the command carries it, which is where
`commit-message-paren-check` reads it from.

The two must agree on where a body begins and ends: a case the stripper blanks
and this one does not return is a body one gate can see and the other cannot.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks" / "_lib"))

from _hook_utils import heredoc_bodies, strip_heredoc_bodies  # noqa: E402


@pytest.mark.parametrize(
    "command,expected",
    [
        ("echo hi", []),
        ("cat <<EOF\nbody\nEOF", ["body"]),
        ("cat <<'EOF'\nline one\nline two\nEOF", ["line one\nline two"]),
        # The terminator must match exactly — `EOF ` does not close one.
        ("cat <<EOF\nbody\nEOF \nstill body\nEOF", ["body\nEOF \nstill body"]),
        # `<<-` strips leading tabs on the terminator line only.
        ("cat <<-EOF\n\tbody\n\tEOF", ["\tbody"]),
        # Two heredocs on one line: bodies come back in source order.
        ("cat <<A <<B\nfirst\nA\nsecond\nB", ["first", "second"]),
        # Unterminated — the body runs to the end, mirroring the stripper.
        ("cat <<EOF\nbody\nmore", ["body\nmore"]),
        # `1 << 3` is a shift, not a heredoc.
        ("echo $((1 << 3))", []),
        # A `<<WORD` inside an open quoted string is string data (#1091).
        ('echo "notes\nsee <<EOF here\n" && echo done', []),
        # Empty body.
        ("cat <<EOF\nEOF", [""]),
    ],
)
def test_bodies(command, expected):
    assert heredoc_bodies(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nbody\nEOF",
        "cat <<'EOF'\nline one\nline two\nEOF",
        "cat <<-EOF\n\tbody\n\tEOF",
        "cat <<A <<B\nfirst\nA\nsecond\nB",
        "cat <<EOF\nbody\nmore",
        "echo $((1 << 3))",
        'echo "notes\nsee <<EOF here\n" && echo done',
    ],
)
def test_agrees_with_the_stripper_on_which_lines_are_body(command):
    """Every line the stripper blanks is a line this one returns, and no other.

    Compared as multisets of lines: the stripper keeps terminators and line
    count, so the blanked positions are exactly the body lines.
    """
    stripped = strip_heredoc_bodies(command).split("\n")
    original = command.split("\n")
    blanked = sorted(
        o for o, s in zip(original, stripped) if s == "" and o != ""
    )
    returned = sorted(
        line for body in heredoc_bodies(command) for line in body.split("\n") if line
    )
    assert blanked == returned
