"""A lone shell grouping token must not be mistaken for the command (#1193).

`( gh pr merge 1 )` — with a space after the paren — tokenizes `(` as the
COMMAND and leaves `gh` a POSITIONAL. Every gate keying on `argv[0]` therefore
went silent on it. Measured on the pre-fix tree: `pre-merge-approval-gate`
dropped from ask to silent, and `block-pr-without-caller-evidence` and
`block-pr-without-precommit-evidence` from exit 2 to silent.

`(gh …` with no space was never affected — the binary helpers strip the prefix
off a token that still carries the name — and neither was `{ …; }`, because
the tokenizer does not type `{` as a COMMAND. Both are pinned here so a future
change cannot "fix" one shape by breaking those.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks" / "_lib"))

from _hook_utils import (  # noqa: E402
    filter_argv,
    strip_prefix,
    tokenize_with_roles,
)


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["(", "gh", "pr", "merge"], ["gh", "pr", "merge"]),
        (["(", "(", "gh", "pr"], ["gh", "pr"]),
        (["${", "gh", "pr"], ["gh", "pr"]),
        # Already-normalizing shapes must not move.
        (["(gh", "pr"], ["(gh", "pr"]),
        (["gh", "pr"], ["gh", "pr"]),
        (["env", "X=1", "gh", "pr"], ["gh", "pr"]),
        # Not a grouping token: a real command keeps its place.
        (["echo", "("], ["echo", "("]),
    ],
)
def test_strip_prefix_peels_only_pure_grouping_tokens(argv, expected):
    assert strip_prefix(argv) == expected


@pytest.mark.parametrize(
    "command,head",
    [
        ("( gh pr merge 1 )", "gh"),
        ("( ( gh pr merge 1 ) )", "gh"),
        ("{ gh pr merge 1 ; }", "gh"),
        ("(gh pr merge 1)", "(gh"),
        ("gh pr merge 1", "gh"),
        ("echo hi", "echo"),
    ],
)
def test_filter_argv_head_is_the_real_command(command, head):
    seg = tokenize_with_roles(command, {})[0]
    argv = filter_argv(seg)
    assert argv and argv[0].text == head


def test_a_quoted_group_is_still_not_a_command():
    """The control: normalizing the real form must not start matching prose.

    `echo "( gh pr merge 1 )"` is one argument to `echo`, not an invocation,
    and a fix that made it look like one would turn every mention of a command
    into a firing.
    """
    seg = tokenize_with_roles('echo "( gh pr merge 1 )"', {})[0]
    argv = filter_argv(seg)
    assert argv[0].text == "echo"
    assert all(tok.text != "gh" for tok in argv)
