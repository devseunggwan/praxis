"""`--input` beside a `body=` field, in `_parse_api_patch` (issue #1265).

gh does not merge the two: the `--input` file is the request body and any
`-f`/`-F` field is appended to the query string instead. Verified on gh 2.98.0
against an unreachable host, so nothing reached GitHub:

    $ gh api --hostname invalid.invalid --verbose /repos/o/r/issues/1/comments \
        --input in.json -f body=FROM_FIELD
    > POST /api/v3/repos/o/r/issues/1/comments?body=FROM_FIELD HTTP/1.1
    {"body": "FROM_FILE"}

`gh api --help` says the same in prose: "from file specified by `--input`. Use
`-` to read from standard input. When passing the request body this way, any
parameters specified via field flags are added to the query" string.

The gate used to set `undecodable` from `--input` and then, in an independent
`if`, overwrite `body` with the field value — so the returned dict carried text
gh never posts. It did not reach the grader, because `_comment_posts` tests
`undecodable` before `_is_anchor`, so the live behaviour was already the
correct warn-and-skip. What the fix closes is the contract: any future consumer
reading `body` alongside `undecodable` would grade an unposted string.

The body is unknown, never `""` — an empty body reads downstream as "checked
and clean", which is the one thing it is not.

Run: python3 -m pytest tests/hooks/preflight-gate/test_anchor_api_patch_input.py -q
"""
from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPL = REPO_ROOT / "hooks" / "preflight-gate" / "anchor-comment-gate" / "impl.py"

sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))
_spec = importlib.util.spec_from_file_location("anchor_gate_input_impl", IMPL)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

ENDPOINT = "/repos/owner/repo/issues/comments/999"

INPUT_UNDECODABLE = [
    # `--input` alone — the shape the mixed forms below now agree with
    f"gh api --method PATCH {ENDPOINT} --input payload.json",
    # beside a raw field
    f"gh api --method PATCH {ENDPOINT} --input payload.json -f body=NEVER_POSTED",
    # beside an expanding field
    f"gh api --method PATCH {ENDPOINT} --input payload.json -F body=@anchor.md",
    # stdin as the request body
    f"gh api --method PATCH {ENDPOINT} --input - -f body=NEVER_POSTED",
    # `--input=` attached form (gh 2.98.0 accepts it; verified live)
    f"gh api --method PATCH {ENDPOINT} --input=payload.json -f body=NEVER_POSTED",
]


def _parse(command: str, cwd: str = "."):
    return gate._parse_api_patch(shlex.split(command), cwd, command)


@pytest.mark.parametrize("command", INPUT_UNDECODABLE)
def test_input_leaves_the_body_unknown(command):
    """No `body` key at all, so nothing downstream can grade the field."""
    parsed = _parse(command)
    assert parsed == {"undecodable": "--input 으로 전달된 JSON 본문"}


@pytest.mark.parametrize("command", INPUT_UNDECODABLE)
def test_input_is_still_reported_not_silently_skipped(command):
    """The other polarity: the call is still seen, and it warns rather than
    passing quietly — silence would read as "checked and clean"."""
    anchors, undecodable = gate._comment_posts(command, ".")
    assert anchors == []
    assert undecodable == ["--input 으로 전달된 JSON 본문"]


def test_input_does_not_read_an_existing_body_file(tmp_path):
    """The `@` file exists and holds a defective anchor. Pre-fix its contents
    became the graded body; the field is query-string, so it is not read."""
    anchor = tmp_path / "anchor.md"
    anchor.write_text("### Verification — `abc1234` (rev 2)\n\nno toggles\n", encoding="utf-8")
    command = (
        f"gh api --method PATCH {ENDPOINT} --input payload.json -F body=@{anchor}"
    )
    parsed = _parse(command)
    assert "body" not in parsed
    assert parsed["undecodable"] == "--input 으로 전달된 JSON 본문"


# ---------------------------------------------------------------------------
# No regression: without `--input`, the field really is the posted body
# ---------------------------------------------------------------------------

def test_raw_field_body_is_still_extracted():
    parsed = _parse(f"gh api --method PATCH {ENDPOINT} -f body=POSTED")
    assert parsed == {"body": "POSTED", "edit_last": False, "undecodable": None}


def test_field_body_file_is_still_read(tmp_path):
    anchor = tmp_path / "anchor.md"
    anchor.write_text("### Verification — `abc1234` (rev 2)\n", encoding="utf-8")
    parsed = _parse(f"gh api --method PATCH {ENDPOINT} -F body=@{anchor}")
    assert parsed["body"].startswith("### Verification")
    assert parsed["undecodable"] is None
