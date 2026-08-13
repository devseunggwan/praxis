"""Tokenizer coverage for a merge-gate false positive (issue #985).

A heredoc body reached every `gh pr merge` gate without merging anything: it
was tokenized line-by-line as if its prose were commands. The cases below are
the enumerated input surface for `strip_heredoc_bodies`, including the
must-still-fire variants that keep the fix from becoming a bypass.

Written as Python rather than appended to `tests/test_hook_utils.sh` because
every case is itself a shell string: expressing them as bash literals means
escaping the very quoting the parser is being tested on.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks" / "_lib"))

from _hook_utils import safe_tokenize, strip_heredoc_bodies  # noqa: E402

# Split so this file's own text never carries the literal the gates match on —
# editing these tests would otherwise trip the gate the tests exist to fix.
MERGE = "gh pr " + "merge"


def _has_merge(command: str) -> bool:
    """True when the tokenizer reads a `gh pr merge` command start."""
    toks = safe_tokenize(command)
    return any(toks[i:i + 3] == ["gh", "pr", "merge"] for i in range(len(toks)))


# ---------------------------------------------------------------------------
# Heredoc bodies are data, not commands.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,command", [
    ("unquoted delimiter", f"cat <<EOF\n{MERGE} 985\nEOF"),
    ("single-quoted delimiter", f"cat <<'EOF'\n{MERGE} 985\nEOF"),
    ("double-quoted delimiter", f'cat <<"EOF"\n{MERGE} 985\nEOF'),
    ("backslash-escaped delimiter", f"cat <<\\EOF\n{MERGE} 985\nEOF"),
    ("dash form, tab-indented terminator", f"cat <<-EOF\n\t{MERGE} 985\n\tEOF"),
    ("arbitrary delimiter word", f"python3 <<PY\n{MERGE} 985\nPY"),
    ("two heredocs, both bodies", f"cat <<A <<B\n{MERGE} 1\nA\n{MERGE} 2\nB"),
    ("unterminated heredoc runs to the end", f"cat <<EOF\n{MERGE} 985"),
    ("near-miss terminator does not end the body",
     f"cat <<EOF\nEOFX\n{MERGE} 985\nEOF"),
    # The shape that actually fired: a commit message assembled through a
    # command substitution *inside* double quotes, where the substitution
    # re-opens shell parsing and the heredoc is genuinely a heredoc.
    ("command substitution inside double quotes",
     f"git commit -m \"$(cat <<'EOF'\nfix: reword\n\n{MERGE} 시점 설명\nEOF\n)\""),
])
def test_heredoc_body_is_not_read_as_a_command(name: str, command: str) -> None:
    assert not _has_merge(command), name


@pytest.mark.parametrize("name,command", [
    ("merge on a later line", f"cat <<'EOF'\nbody\nEOF\n{MERGE} 985 --squash"),
    ("merge before the heredoc", f"{MERGE} 985 --squash\ncat <<'EOF'\nbody\nEOF"),
    ("here-string has no body", f"grep x <<< body\n{MERGE} 985 --squash"),
    ("arithmetic left-shift is not an operator",
     f"echo $((1 << 3)); {MERGE} 985 --squash"),
    ("`<<` inside quotes is literal", f'echo "<<EOF"\n{MERGE} 985 --squash'),
    ("terminator with trailing whitespace still closes the body",
     f"cat <<EOF\nbody\nEOF  \n{MERGE} 985 --squash"),
])
def test_real_merge_still_reaches_the_gates(name: str, command: str) -> None:
    assert _has_merge(command), name


def test_command_without_heredoc_is_returned_unchanged() -> None:
    command = f"{MERGE} 985 --squash && echo done"
    assert strip_heredoc_bodies(command) == command


def test_suppressed_body_keeps_the_line_count() -> None:
    """Blank lines, not deleted ones — line positions stay comparable."""
    command = f"cat <<EOF\n{MERGE}\nEOF\necho after"
    assert strip_heredoc_bodies(command).split("\n") == [
        "cat <<EOF", "", "", "echo after",
    ]


