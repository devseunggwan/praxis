"""Tests for hooks/_lib/_external_write_body.py — `gh api` detection (#1265).

`is_gh_external_write` matched only the `gh <noun> <verb>` write pairs, so a
comment written through `gh api` was outside the detection surface of all five
consuming hooks at once. That path is not exotic: the verification anchor's own
convention makes a rev ≥2 anchor a `PATCH` against a comment id, which no
noun/verb form can issue.

The case list is the input-surface enumeration for `gh api`: method spelling
(`-X` / `--method` / `--method=` / attached `-XPOST` / lowercase), method before
or after the endpoint, endpoint with or without a leading slash, a full URL,
gh's `{owner}/{repo}` templating, each endpoint family, and the field dialects
(`-f` / `--raw-field` / `-F` / `--field` / `--input`).

The read cases are the load-bearing half. `gh api` is overwhelmingly a read
tool, so a detector that fired on every `gh api` would be indistinguishable
from a correct one when only the writes are checked.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from _external_write_body import (  # noqa: E402
    extract_gh_body,
    is_gh_external_write,
    parse_gh_api,
    split_gh_short_flags,
)

WRITES = [
    # the issue's own probe lines
    "gh api --method PATCH /repos/o/r/issues/comments/1 -F body=@x",
    "gh api /repos/o/r/pulls/9/comments --method POST -f body=x",
    # method spelling
    "gh api -X PATCH repos/o/r/issues/comments/1 -f body=hi",
    "gh api -XPATCH repos/o/r/issues/comments/1 -fbody=hi",
    "gh api --method=PATCH repos/o/r/issues/comments/1 --field body=@x",
    # `=`-attached shorthand: pflag reads `-X=PATCH` as the method PATCH
    "gh api -X=PATCH repos/o/r/issues/comments/1 -f body=hi",
    "gh api repos/o/r/issues/comments/1 -X=PATCH -f=body=hi",
    "gh api --method patch repos/o/r/issues/comments/1 -f body=hi",
    # method after the endpoint
    "gh api repos/o/r/issues/comments/1 --method PATCH -f body=hi",
    # endpoint spellings
    "gh api https://api.github.com/repos/o/r/issues/comments/1 -X PATCH -f body=hi",
    "gh api repos/{owner}/{repo}/issues/comments/1 -X PATCH -f body=hi",
    # every endpoint family
    "gh api repos/o/r/issues/12/comments -X POST -f body=hi",
    "gh api repos/o/r/pulls/12/reviews -X POST -f body=hi",
    "gh api repos/o/r/pulls/comments/99 -X PATCH -f body=hi",
    "gh api repos/o/r/pulls/12/comments -X PUT -f body=hi",
    # global flags and an env prefix in front of the subcommand
    "gh --hostname ghe.example api repos/o/r/issues/comments/1 -X PATCH -f body=hi",
    "GH_REPO=o/r gh api repos/o/r/issues/comments/1 -X PATCH -f body=hi",
    "gh api -H 'Accept: application/vnd.github+json' -X PATCH repos/o/r/issues/comments/1 -f body=hi",
    # an unreadable body is still a write — the body is unknown, not absent
    "gh api repos/o/r/issues/comments/1 -X PATCH --input payload.json",
    "gh api repos/o/r/issues/comments/1 -X PATCH -f body=@-",
]

READS = [
    "gh api repos/o/r/issues/comments/123 --jq .body",
    "gh api repos/o/r/issues/12/comments --paginate",
    "gh api -X GET repos/o/r/pulls/9/comments",
    "gh api -X=GET repos/o/r/pulls/9/comments -f a=b",
    "gh api --method get repos/o/r/issues/comments/1",
    "gh api repos/o/r/pulls/9/files",
    # a write, but not to a comment surface
    "gh api graphql -f query=xyz",
    "gh api --method POST graphql -f query=xyz",
    "gh api -X PATCH repos/o/r/issues/12 -f body=hi",
    "gh api -X POST repos/o/r/actions/workflows/x.yml/dispatches -f ref=main",
    "gh api -X DELETE repos/o/r/issues/comments/1",
]


@pytest.mark.parametrize("command", WRITES)
def test_gh_api_write_detected(command):
    assert is_gh_external_write(shlex.split(command)) is True


@pytest.mark.parametrize("command", READS)
def test_gh_api_read_not_detected(command):
    assert is_gh_external_write(shlex.split(command)) is False


def test_noun_verb_pairs_still_detected():
    """Positive control for the pre-existing surface: widening did not replace it."""
    assert is_gh_external_write(shlex.split("gh pr comment 1261 --body-file x"))
    assert is_gh_external_write(shlex.split("gh issue create -R o/r --title t --body b"))
    assert not is_gh_external_write(shlex.split("gh pr view 1 --json body"))


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------

def test_raw_field_body_is_literal():
    argv = shlex.split("gh api repos/o/r/issues/comments/1 -X PATCH -f body=hello")
    assert extract_gh_body(argv) == "hello"


def test_raw_field_does_not_expand_at_sign():
    """`-f/--raw-field` never expands `@`; only `-F/--field` does."""
    argv = shlex.split("gh api repos/o/r/issues/comments/1 -X PATCH -f body=@notafile")
    assert extract_gh_body(argv) == "@notafile"


def test_field_body_reads_the_file(tmp_path):
    body = tmp_path / "anchor.md"
    body.write_text("### Verification — `abc1234` (rev 2)\n", encoding="utf-8")
    argv = shlex.split(f"gh api repos/o/r/issues/comments/1 -X PATCH -F body=@{body}")
    assert extract_gh_body(argv).startswith("### Verification")


def test_field_body_unreadable_file_is_empty(tmp_path):
    argv = shlex.split(f"gh api repos/o/r/issues/comments/1 -X PATCH -F body=@{tmp_path}/missing.md")
    assert extract_gh_body(argv) == ""


def test_stdin_and_input_bodies_are_unknown():
    """Neither can be read from the command line, so both are None, not ''."""
    stdin = shlex.split("gh api repos/o/r/issues/comments/1 -X PATCH -F body=@-")
    payload = shlex.split("gh api repos/o/r/issues/comments/1 -X PATCH --input payload.json")
    assert extract_gh_body(stdin) is None
    assert extract_gh_body(payload) is None


def test_api_body_flag_not_read_as_body_file():
    """`-F` means `--body-file` to `gh pr comment` and `--field` to `gh api`.

    Reading an api call with the noun/verb flag set would open a file literally
    named `body=@anchor.md` and hand back an empty body.
    """
    argv = shlex.split("gh api repos/o/r/issues/comments/1 -X PATCH -F body=inline")
    assert extract_gh_body(argv) == "inline"


def test_non_body_fields_are_ignored():
    argv = shlex.split("gh api repos/o/r/pulls/12/reviews -X POST -f event=APPROVE")
    assert extract_gh_body(argv) is None


def test_last_body_field_wins():
    argv = shlex.split("gh api repos/o/r/issues/comments/1 -X PATCH -f body=first -f body=second")
    assert extract_gh_body(argv) == "second"


# ---------------------------------------------------------------------------
# parse_gh_api structure
# ---------------------------------------------------------------------------

def test_parse_returns_none_for_non_api_gh():
    assert parse_gh_api(shlex.split("gh pr comment 5 --body hi")) is None
    assert parse_gh_api(shlex.split("git log --oneline")) is None


def test_parse_defaults_method_to_get():
    call = parse_gh_api(shlex.split("gh api repos/o/r/issues/comments/1"))
    assert call.method == "GET"
    assert call.path == "repos/o/r/issues/comments/1"


def test_parse_records_the_field_flag_that_carried_the_body():
    call = parse_gh_api(shlex.split("gh api repos/o/r/issues/comments/1 -X PATCH --field body=@a.md"))
    assert (call.method, call.body_flag, call.body_raw) == ("PATCH", "--field", "@a.md")
    assert call.has_input is False


# ---------------------------------------------------------------------------
# Attached shorthand values (`-XPATCH` / `-X=PATCH`)
# ---------------------------------------------------------------------------

def test_split_keeps_bare_attached_value():
    assert split_gh_short_flags(["-XPATCH"]) == ["-X", "PATCH"]
    assert split_gh_short_flags(["-fbody=hi"]) == ["-f", "body=hi"]


def test_split_drops_the_pflag_equals_separator():
    """`gh api -X=PATCH` really sends PATCH (gh 2.98.0, verified live)."""
    assert split_gh_short_flags(["-X=PATCH"]) == ["-X", "PATCH"]
    assert split_gh_short_flags(["-f=body=hi"]) == ["-f", "body=hi"]


def test_split_leaves_a_valueless_flag_alone():
    """`-X=` has no value; emitting `''` would be read as the endpoint."""
    assert split_gh_short_flags(["-X=", "repos/o/r"]) == ["-X", "repos/o/r"]
    assert split_gh_short_flags(["-X", "PATCH"]) == ["-X", "PATCH"]
    assert split_gh_short_flags(["--method=PATCH"]) == ["--method=PATCH"]


def test_equals_attached_method_and_field_parse():
    call = parse_gh_api(shlex.split("gh api -X=PATCH repos/o/r/issues/comments/1 -f=body=hi"))
    assert (call.method, call.path, call.body_raw) == ("PATCH", "repos/o/r/issues/comments/1", "hi")
