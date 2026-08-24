"""Tokenizer coverage for the two merge-gate false positives (issue #985).

Both families reached every `gh pr merge` gate without merging anything: a
heredoc body was tokenized line-by-line as if its prose were commands, and a
`--help` invocation matched the same argv shape as the real thing. The cases
below are the enumerated input surface for the two helpers that close them —
`strip_heredoc_bodies` and `is_help_invocation` — including the must-still-fire
variants that keep either fix from becoming a bypass.

Written as Python rather than appended to `tests/test_hook_utils.sh` because
every case is itself a shell string: expressing them as bash literals means
escaping the very quoting the parser is being tested on.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks" / "_lib"))

from _hook_utils import (  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    is_help_invocation,
    safe_tokenize,
    strip_heredoc_bodies,
)

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
    # bash closes a heredoc only on an exact delimiter line — `EOF  ` is body,
    # so everything after it is body too (CodeRabbit, verified against bash).
    ("terminator with trailing spaces does not close the body",
     f"cat <<EOF\nbody\nEOF  \n{MERGE} 985 --squash"),
    ("tilde delimiter", f"cat <<~EOF\n{MERGE} 985\n~EOF"),
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
])
def test_real_merge_still_reaches_the_gates(name: str, command: str) -> None:
    assert _has_merge(command), name


def test_command_without_heredoc_is_returned_unchanged() -> None:
    command = f"{MERGE} 985 --squash && echo done"
    assert strip_heredoc_bodies(command) == command


def test_heredoc_lookalike_inside_a_multiline_quote_is_not_an_opener() -> None:
    """Issue #1091: a `<<WORD` on a later physical line *inside* an open
    multi-line quoted string was misread as a heredoc opener because the scan
    reset quote state per physical line — so `strip_heredoc_bodies` blanked the
    closing-quote line and the chained command riding after it. Carrying quote
    state across physical lines (mirroring the #972/#987 `_logical_lines` fold)
    keeps the `<<EOF` as the string data it is, and the `&& gh pr merge`
    survives. Before the fix `safe_tokenize` of this payload returned ``[]``."""
    command = f'git commit -m "notes:\nsee <<EOF for details\n" && {MERGE} 9'
    # strip_heredoc_bodies must leave the whole command untouched — no line is
    # a heredoc body.
    assert strip_heredoc_bodies(command) == command
    assert _has_merge(command)
    assert safe_tokenize(command) == [
        "git", "commit", "-m", "notes:\nsee <<EOF for details\n",
        "&&", "gh", "pr", "merge", "9",
    ]


def test_heredoc_lookalike_in_a_comment_opens_nothing() -> None:
    """`# <<EOF` is a comment; bash reads no operator past it, so the next line
    must stay visible (CodeRabbit, verified against bash)."""
    assert _has_merge(f'echo hi # <<EOF\n{MERGE} 985 --squash')


def test_only_body_lines_are_blanked() -> None:
    """Line positions stay comparable, and the terminator survives — hooks
    running their own heredoc scan on top of this one wait for that line."""
    command = f"cat <<EOF\n{MERGE}\nEOF\necho after"
    assert strip_heredoc_bodies(command).split("\n") == [
        "cat <<EOF", "", "EOF", "echo after",
    ]


# ---------------------------------------------------------------------------
# `--help` prints usage and does nothing.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["--help"],
    ["-h"],
    ["985", "--help"],
    ["--squash", "-h"],
    ["-h", "985"],
])
def test_help_invocation_detected(argv: list[str]) -> None:
    assert is_help_invocation(argv, GH_MERGE_VALUE_FLAGS)


@pytest.mark.parametrize("argv", [
    ["985", "--squash"],
    ["985", "--subject", "-h"],          # -h is the subject text
    ["985", "--body", "--help"],         # --help is the body text
    ["985", "--subject=-h"],             # inline value, not a flag position
    ["--", "-h"],                        # everything after `--` is positional
])
def test_not_a_help_invocation(argv: list[str]) -> None:
    assert not is_help_invocation(argv, GH_MERGE_VALUE_FLAGS)
