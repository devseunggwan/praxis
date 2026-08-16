"""Tokenizer coverage for newlines inside a quote (issues #972, #987).

`safe_tokenize` pre-split the command on `\\n` and gave every physical line its
own shlex pass. A quote opened on one line and closed on another therefore made
*both* of those lines raise ``ValueError: No closing quotation``, and the
fail-open ``except ValueError: continue`` arm dropped them:

- #972 — ``gh pr comment --body '…multi-line…'`` lost its ``gh`` argv[0], so
  every hook looking for a gh invocation went blind. Measured impact was zero
  verdict changes across all 45 consuming hooks, matching the issue's own
  admission of zero observed damage.
- #987 — when a real command rode on the quote-closing line
  (``git commit -m "…" && gh pr merge``) the whole command vanished, and the
  three merge gates the issue names (momentum-rule-retrieval-gate,
  pre-merge-approval-gate, side-effect-scan) all went silent.

#972's stated mechanism ("strips `#` before quote state is resolved") does not
hold — `safe_tokenize` sets ``commenters = ""``, so `#` is never stripped, and
the identical payload with every `#` removed tokenizes identically broken. Both
issues are one defect on one line, closed by `_logical_lines`.

The must-still-fire cases below are the ones that keep the fix from becoming a
bypass of its own: folding lines across an open quote without making the
scanner comment-aware reads the apostrophe in ``git status # don't do this`` as
an opening quote and swallows the genuine command on the next line.

Written as Python rather than appended to `tests/test_hook_utils.sh` for the
same reason as `test_heredoc_help_tokenizer.py` — every case is itself a shell
string, so bash literals mean escaping the very quoting under test. The same
cases are duplicated into that shell suite because it is the one that runs
without pytest installed.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks" / "_lib"))

from _hook_utils import safe_tokenize  # noqa: E402

# Split so this file's own text never carries the literal the gates match on —
# editing these tests would otherwise trip the gate the tests exist to fix.
MERGE = "gh pr " + "merge"


def _has_merge(command: str) -> bool:
    """True when the tokenizer reads a `gh pr merge` command start."""
    toks = safe_tokenize(command)
    return any(toks[i:i + 3] == ["gh", "pr", "merge"] for i in range(len(toks)))


# ---------------------------------------------------------------------------
# The two reported token streams.
# ---------------------------------------------------------------------------

def test_multiline_body_keeps_argv0() -> None:
    """#972's exact payload. Before the fix this returned
    ``[';', '|', '1', '|', 'x', '|', 'PASS(live)', '|', ';']`` — no `gh`."""
    command = "gh pr comment 949 --body '### Verification\n| 1 | x | PASS(live) |\n'"
    assert safe_tokenize(command) == [
        "gh", "pr", "comment", "949", "--body",
        "### Verification\n| 1 | x | PASS(live) |\n",
    ]


def test_heredoc_commit_then_merge_survives() -> None:
    """#987's exact payload. Before the fix this returned ``[';', 'EOF', ';']``.

    The `-m` value stays the opaque command substitution because
    `strip_heredoc_bodies` blanks the body first (issue #985) — the point of
    this case is that the `&&` and everything after it are visible again.
    """
    command = (
        "git commit -m \"$(cat <<'EOF'\nbody line\nEOF\n)\" && " + MERGE + " 9 --squash"
    )
    assert safe_tokenize(command) == [
        "git", "commit", "-m", "$(cat <<'EOF'\n\nEOF\n)",
        "&&", "gh", "pr", "merge", "9", "--squash",
    ]


@pytest.mark.parametrize("name,command", [
    # #987 framed this as heredoc-specific. It is not: any newline inside any
    # quote is sufficient, with no heredoc and no `#` anywhere in the payload.
    ("double quote", 'git commit -m "line one\nline two" && ' + MERGE + " 9 --squash"),
    ("single quote", "git commit -m 'line one\nline two' && " + MERGE + " 9 --squash"),
    ("body spanning three lines",
     'git commit -m "a\nb\nc" && ' + MERGE + " 9 --squash"),
    ("heredoc form", "git commit -m \"$(cat <<'EOF'\nbody\nEOF\n)\" && "
                     + MERGE + " 9 --squash"),
])
def test_command_after_a_multiline_quote_reaches_the_gates(name: str, command: str) -> None:
    assert _has_merge(command), name


@pytest.mark.parametrize("name,command", [
    ("double quote", 'git commit -m "line one\nline two"'),
    ("single quote", "git commit -m 'line one\nline two'"),
])
def test_newline_stays_inside_the_quoted_token(name: str, command: str) -> None:
    """The newline is data — bash hands it to the process inside the word, so
    it must not split `-m` from its value nor become a synthetic `;`."""
    assert safe_tokenize(command) == ["git", "commit", "-m", "line one\nline two"], name


def test_multiline_body_containing_a_hash() -> None:
    """#972 task 3 notes no hook exercises a `#`-bearing quoted body today.

    A `#` inside quotes is text, not a comment, so it must survive inside the
    token — this is the case whose absence let #972's mechanism go unfalsified.
    """
    command = 'gh pr create --title t --body "# Heading\nbody line"'
    assert safe_tokenize(command) == [
        "gh", "pr", "create", "--title", "t", "--body", "# Heading\nbody line",
    ]


# ---------------------------------------------------------------------------
# Must-still-fire: the fix must not become a bypass of its own.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,command", [
    # Folding lines across an open quote *without* the comment rule reads this
    # apostrophe as an opening quote, joins both lines into one unterminated
    # group, and drops the real merge — verified: the naive variant returns [].
    ("apostrophe in an unquoted comment",
     "git status # don't do this\n" + MERGE + " 9 --squash"),
    ("odd quote count in a comment",
     "echo hi  # it's fine\n" + MERGE + " 9 --squash"),
    ("comment at the start of a line",
     "# don't\n" + MERGE + " 9 --squash"),
    # A `#` inside quotes is not a comment, so the quote closes normally and
    # the next line is a separate command.
    ("hash inside quotes is not a comment",
     "echo '# not a comment'\n" + MERGE + " 9 --squash"),
    ("plain newline separator still separates",
     "git status\n" + MERGE + " 9 --squash"),
])
def test_merge_still_visible(name: str, command: str) -> None:
    assert _has_merge(command), name


# ---------------------------------------------------------------------------
# Must-not-fire and unchanged behavior.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,command", [
    # Quoted prose is data. Before the fix the second line was tokenized on its
    # own and read as a real invocation; now the whole string is one token.
    ("merge quoted inside multi-line prose",
     "echo 'first line\nrun " + MERGE + " later'"),
    ("heredoc body still blanked (issue #985)",
     "cat <<'EOF'\n" + MERGE + " 985\nEOF"),
])
def test_quoted_prose_is_not_a_command(name: str, command: str) -> None:
    assert not _has_merge(command), name


def test_ack_marker_still_tokenizes() -> None:
    """`commenters = ""` is untouched: comments are excluded from quote-state
    tracking only, never from the token stream, so side-effect-scan's
    `# side-effect:ack` opt-out marker still reaches its caller."""
    assert safe_tokenize("git commit -m x # side-effect:ack") == [
        "git", "commit", "-m", "x", "#", "side-effect:ack",
    ]


@pytest.mark.parametrize("name,command", [
    ("unterminated double quote", 'echo "never closed'),
    ("unterminated single quote", "echo 'never closed"),
])
def test_unterminated_quote_fails_open(name: str, command: str) -> None:
    """A group still open at the end of the command is handed to shlex
    unchanged, so it raises and the fail-open arm returns no tokens for it —
    a silent pass, never a crashed hook."""
    assert safe_tokenize(command) == [], name


@pytest.mark.parametrize("name,command,expected", [
    # Issue #510: a `\` before a newline is a continuation, not a separator.
    ("line continuation", "git \\\ncommit", ["git", "commit"]),
    ("line continuation with flags", "git commit \\\n-m x",
     ["git", "commit", "-m", "x"]),
    # A bare newline outside any quote is still a command separator.
    ("bare newline", "git status\ngit log", ["git", "status", ";", "git", "log"]),
])
def test_prior_newline_contracts_are_unchanged(
    name: str, command: str, expected: list[str]
) -> None:
    assert safe_tokenize(command) == expected, name
