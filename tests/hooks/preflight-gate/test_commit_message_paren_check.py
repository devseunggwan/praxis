"""Coverage for hooks/preflight-gate/commit-message-paren-check/impl.py.

The corpus cases are the real commits from this repository's history that
release-please's parser rejected (positives) and accepted (negative controls).
Without the controls a green run cannot distinguish "the gate caught it" from
"the gate always fires" — `2d558892` in particular carries depth-3 nested
parens mid-line and parses fine.

The messages are read from tests/fixtures/commit-message-paren-check/<sha>.txt,
verbatim copies of `git log -1 --format=%B <sha>` (see the README there), not
from the repository itself: a shallow clone has none of these commits and
`git log` fails with exit 128 (issue #1302). One extra case re-reads the live
history when it is present and asserts the copies have not drifted, so the
"real commits" property survives without coupling the suite to clone depth.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "hooks" / "preflight-gate" / "commit-message-paren-check" / "impl.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "commit-message-paren-check"

# Commits release-please logged as unparseable, with the line and shape its
# error string reported (issue #1228).
REJECTED = [
    ("2d86ff6c", 127, "nested"),
    ("b328852b", 33, "unclosed"),
    ("4b0df391", 9, "unclosed"),
    ("36f937f7", 156, "nested"),
    ("e399693e", 18, "unclosed"),
    ("4d83c916", 189, "nested"),
    ("54128d0c", 17, "unclosed"),
    # 9ea4785a is this gate's OWN merge commit. GitHub composed its squash
    # body from the PR's commit messages, so a line-initial pseudo-scope
    # written before the gate shipped reached main by a path no hook sees —
    # the case spec.md used to call impossible. The parser reported
    # `unexpected token '(' at 75:10`.
    ("9ea4785a", 75, "nested"),
]

# Commits the same parser accepted, over the same range.
ACCEPTED = ["ed44c51", "5fdff21", "3d6a72f", "2d558892"]


ALL_SHAS = [sha for sha, _, _ in REJECTED] + ACCEPTED


def _message(sha: str) -> str:
    # Decoded from bytes so nothing translates newlines: the text is exactly
    # what `%B` wrote, trailing newline included.
    return (FIXTURES / f"{sha}.txt").read_bytes().decode("utf-8")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
    )


def _live_history_available() -> bool:
    return all(_git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
               for sha in ALL_SHAS)


def _run(command: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    proc = subprocess.run(
        ["python3", str(HOOK)], input=payload, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PRAXIS_FIRE_TELEMETRY_DISABLE": "1", **(env or {})},
    )
    return proc.returncode, proc.stdout, proc.stderr


def _commit_via_file(tmp_path: Path, message: str, name: str = "msg.txt") -> str:
    path = tmp_path / name
    path.write_text(message, encoding="utf-8")
    return f"git commit -F {path}"


# ---------------------------------------------------------------------------
# The rule, unit level
# ---------------------------------------------------------------------------

def _load_impl():
    import importlib.util

    spec = importlib.util.spec_from_file_location("commit_message_paren_check", HOOK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IMPL = _load_impl()


@pytest.mark.parametrize(
    "line,expected",
    [
        # Clause 4 — the scope does not close cleanly.
        ("`(a(b))`", "nested"),
        ("`(a b", "unclosed"),
        ("word(a(b))", "nested"),
        ("fix(a(b)): x", "nested"),
        ("1(a(b))", "nested"),
        ('"(a b', "unclosed"),
        ("```(a(b))", "nested"),
        # Clause 4 — it does close.
        ("`(ab)`", None),
        ("f(x) g(y", None),
        ("f()", None),
        ("type(scope):(a(b))", None),
        # Clause 1 — no `(`, or `(` at column 1.
        ("plain text", None),
        ("(a(b))", None),
        ("((a)", None),
        # Clause 2 — whitespace in the prefix.
        (" `(a(b))`", None),
        ("\t`(a(b))`", None),
        ("- `(a b", None),
        ("x `(a(b))`", None),
        ("Co-Authored-By: X (a(b))", None),
        # Clause 3 — the header separator was already consumed.
        ("!(a(b))", None),
        ("fix!(a(b)): x", None),
        (":a(c(d))", None),
        ("a:b(c(d))", None),
    ],
)
def test_rule_matches_the_measured_parser_verdict(line, expected):
    hits = IMPL.offending_lines(f"fix(x): subject\n\n{line}")
    if expected is None:
        assert hits == []
    else:
        assert [k for _, k, _ in hits] == [expected]


# ---------------------------------------------------------------------------
# Corpus — real commits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sha,lineno,kind", REJECTED)
def test_real_rejected_commits_block(tmp_path, sha, lineno, kind):
    rc, out, err = _run(_commit_via_file(tmp_path, _message(sha)))
    assert rc == 2, err
    assert out == ""
    assert f"line {lineno}: [{kind}]" in err


@pytest.mark.parametrize("sha", ACCEPTED)
def test_real_accepted_commits_pass(tmp_path, sha):
    rc, out, err = _run(_commit_via_file(tmp_path, _message(sha)))
    assert (rc, out, err) == (0, "", "")


@pytest.mark.skipif(
    not _live_history_available(),
    reason="not every corpus commit is reachable here (shallow clone or "
           "missing history) — the fixture copies are graded above regardless",
)
@pytest.mark.parametrize("sha", ALL_SHAS)
def test_fixture_matches_the_live_commit(sha):
    """The fixture is only evidence while it is byte-identical to the commit
    it claims to be. Compared whenever the history is present; skipped, not
    failed, when it is not, so clone depth never decides the verdict."""
    live = _git("log", "-1", "--format=%B", sha)
    assert live.returncode == 0, live.stderr
    assert _message(sha) == live.stdout


# ---------------------------------------------------------------------------
# Message sources
# ---------------------------------------------------------------------------

BAD_BODY = "fix: x\n\n`(a(b))` note"
GOOD_BODY = "fix: x\n\n `(a(b))` note"


@pytest.mark.parametrize(
    "command,rc",
    [
        ("git commit -m 'fix: x' -m '`(a(b))` note'", 2),
        ("git commit -m 'fix: x' -m ' `(a(b))` note'", 0),
        ("git commit -m 'fix(a(b)): x'", 2),
        ("git commit -m \"$(cat <<'EOF'\n" + BAD_BODY + "\nEOF\n)\"", 2),
        ("git commit -m \"$(cat <<'EOF'\n" + GOOD_BODY + "\nEOF\n)\"", 0),
        ("git commit -F - <<'EOF'\n" + BAD_BODY + "\nEOF", 2),
        # Not a commit — the heredoc must not be graded.
        ("cat > /tmp/notes <<'EOF'\n" + BAD_BODY + "\nEOF", 0),
        ("echo '`(a(b))`'", 0),
        ("git log -1 --format=%B", 0),
        # Unresolvable message value — silent, not a guess.
        ("git commit -m \"$MSG\"", 0),
        # The binary may be path-prefixed; a lookalike name is not git.
        ("/usr/bin/git commit -m 'word(a(b))'", 2),
        ("env FOO=1 /usr/bin/git commit -m 'word(a(b))'", 2),
        ("gitk commit -m 'word(a(b))'", 0),
        # A commit inside an ACTIVE substitution is still a commit…
        ("MSG=$(git commit -m 'fix: x' -m 'word(a(b))')", 2),
        ("MSG=$(git commit -m 'fix: x' -m 'word (a(b))')", 0),
        ("echo \"$(git commit -m 'word(a(b))')\"", 2),
        # …and one the shell never expands is text, not a command.
        ("echo '$(git commit -m \"word(a(b))\")'", 0),
    ],
)
def test_message_sources(command, rc):
    assert _run(command)[0] == rc


# A heredoc that belongs to some other command in the same chain.
NOTES = "cat > /tmp/notes <<'NOTE'\n" + BAD_BODY + "\nNOTE\n"


@pytest.mark.parametrize(
    "command,rc",
    [
        # A readable `-m` subject no longer ends the search: the body arrives
        # from the heredoc the SECOND `-m` opens, and it is still the message.
        ("git commit -m 'fix: x' -m \"$(cat <<'EOF'\n" + BAD_BODY + "\nEOF\n)\"", 2),
        ("git commit -m 'fix: x' -m \"$(cat <<'EOF'\n" + GOOD_BODY + "\nEOF\n)\"", 0),
        # …and the other direction: a heredoc belonging to another command is
        # not the commit message, however malformed it is.
        (NOTES + "git commit -F - <<'EOF'\nfix: ok\nEOF", 0),
        (NOTES + "git commit -F - <<'EOF'\n" + BAD_BODY + "\nEOF", 2),
        (NOTES + "git commit -m \"$(cat <<'EOF'\nfix: ok\nEOF\n)\"", 0),
        # `-F -` with no heredoc at all: stdin comes from somewhere we cannot
        # read, so the unrelated body must not stand in for it.
        (NOTES + "git commit -F -", 0),
        # One delimiter word naming two bodies identifies neither — silent.
        (
            "cat > /tmp/notes <<'EOF'\n" + BAD_BODY + "\nEOF\n"
            "git commit -F - <<'EOF'\nfix: ok\nEOF",
            0,
        ),
    ],
)
def test_heredoc_is_bound_to_the_source_that_names_it(command, rc):
    assert _run(command)[0] == rc


@pytest.mark.parametrize(
    "command,rc",
    [
        # `<<-` strips leading tabs from every body line, so the message the
        # parser sees starts at ``(` — see tests/test_heredoc_bodies.py for the
        # bash probe behind this expectation.
        ("git commit -m \"$(cat <<-'EOF'\n\tfix: x\n\n\t`(a(b))` note\n\tEOF\n)\"", 2),
        # Indentation that is NOT a stripped tab still exempts the line.
        ("git commit -m \"$(cat <<-'EOF'\n\tfix: x\n\n\t  `(a(b))` note\n\tEOF\n)\"", 0),
        # A plain `<<` keeps the tab, and the tab is real indentation.
        ("git commit -m \"$(cat <<'EOF'\nfix: x\n\n\t`(a(b))` note\nEOF\n)\"", 0),
    ],
)
def test_dash_heredoc_body_is_read_the_way_bash_writes_it(command, rc):
    assert _run(command)[0] == rc


def test_an_unquoted_delimiter_makes_the_same_body_a_substitution():
    """The delimiters above are quoted for a reason: unquote them and the body
    is no longer the message. Measured, with `git` replaced by a recorder:

        $ bash -c 'git commit -m "$(cat <<-EOF
                \tfix: x

                \t`(a(b))` note
                \tEOF
        )"'
        bash: command substitution: line 0: syntax error near unexpected token `b'
        {"argv": ["commit", "-m", "fix: x\\n\\n note"]}

    Bash runs the backtick run as a command, it fails, and what git receives
    carries neither the backticks nor the parens. Grading the source text
    instead blocked that commit — a false positive on a message the parser
    never sees (issue #1228 round 2)."""
    assert _run(
        "git commit -m \"$(cat <<-EOF\n\tfix: x\n\n\t`(a(b))` note\n\tEOF\n)\""
    ) == (0, "", "")


def test_file_path_relative_to_dash_C(tmp_path):
    (tmp_path / "msg.txt").write_text(BAD_BODY, encoding="utf-8")
    assert _run(f"git -C {tmp_path} commit -F msg.txt")[0] == 2


def test_unreadable_file_is_silent(tmp_path):
    assert _run(f"git commit -F {tmp_path}/absent.txt") == (0, "", "")


# ---------------------------------------------------------------------------
# Modes and fail-open
# ---------------------------------------------------------------------------

def test_strict_zero_is_advisory(tmp_path):
    rc, out, err = _run(
        _commit_via_file(tmp_path, BAD_BODY), env={"PRAXIS_COMMIT_PAREN_STRICT": "0"}
    )
    assert rc == 0
    assert out == ""
    assert "ADVISORY (STRICT=0)" in err


def test_strict_one_is_the_default(tmp_path):
    assert _run(_commit_via_file(tmp_path, BAD_BODY),
                env={"PRAXIS_COMMIT_PAREN_STRICT": "1"})[0] == 2


def test_non_bash_payload_is_silent():
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}})
    proc = subprocess.run(["python3", str(HOOK)], input=payload,
                          capture_output=True, text=True)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


def test_malformed_stdin_is_silent():
    proc = subprocess.run(["python3", str(HOOK)], input="not json",
                          capture_output=True, text=True)
    assert proc.returncode == 0


def test_main_is_wrapped_by_fail_open():
    assert getattr(IMPL.main, "__wrapped__", None) is not None


@pytest.mark.parametrize(
    "command,rc",
    [
        # A substitution opening on a later line is text the shell replaces, so
        # grading it would block on a message nobody can predict.
        ('git commit -m "fix: x\n\n$(git log --oneline"', 0),
        # The same line with no substitution is real message text.
        ('git commit -m "fix: x\n\nword(a(b))"', 2),
        # A substitution the shell resolves MID-line is the same case: bash
        # delivers `foo(x)`, which the parser accepts. Grading the source read
        # the inner `(` as a nested paren and blocked a valid commit.
        ('git commit -m "fix: x" -m "body\nfoo($(printf x))"', 0),
        # Blanking is per line, so a clean line beside it still gets graded.
        ('git commit -m "fix: x" -m "foo($(printf x))\nword(a(b))"', 2),
    ],
)
def test_a_later_line_substitution_does_not_reach_the_detector(command, rc):
    assert _run(command)[0] == rc


def test_the_reported_line_number_survives_a_blanked_line():
    """Blanking rather than dropping is what keeps this number honest."""
    rc, _out, err = _run('git commit -m "fix: x\n\n$(date)\n\nword(a(b))"')
    assert rc == 2
    assert "line 5: [nested]" in err


def test_an_unclosed_substitution_is_not_a_commit_at_all():
    """Bash never finishes parsing this command, so git is never invoked and
    there is no message to grade. Measured:

        $ bash -c 'git commit -m "fix: x

        $(x

        word(a(b))"'
        bash: -c: line 4: unexpected EOF while looking for matching `"'
        bash: -c: line 5: syntax error: unexpected end of file

    The old reading blanked only the `$(x` line and kept `word(a(b))` as
    message text, so the gate blocked a command that could never run
    (CodeRabbit, issue #1228 round 2)."""
    assert _run('git commit -m "fix: x\n\n$(x\n\nword(a(b))"') == (0, "", "")


# ---------------------------------------------------------------------------
# Round-2 surface (issue #1228)
#
# Every expectation below was measured before it was asserted: the command was
# run under bash with `git` replaced by a recorder that prints the argv and the
# message it was actually handed, and the expected verdict is
# `offending_lines` applied to THAT text rather than to the source. Half of
# these are false-positive guards — cases where the source text reads as
# malformed but the message git receives does not, so the gate must stay
# silent. They are marked `FP guard`, and they are the ones protecting valid
# commits from a blocking gate.
# ---------------------------------------------------------------------------

BAD = "fix: x\n\nword(a(b))"
OK = "fix: x\n\nplain body"


@pytest.mark.parametrize(
    "command,rc",
    [
        # --- attached and clustered value-taking short options --------------
        # `-F-` and `-Ffile` are one token; the scanner read only the
        # detached `-F <value>` form, so neither was a message source.
        (f"git commit -F- <<EOF\n{BAD}\nEOF", 2),
        (f"git commit -F- <<EOF\n{OK}\nEOF", 0),
        (f"git commit -aF - <<EOF\n{BAD}\nEOF", 2),
        (f"git commit -aF - <<EOF\n{OK}\nEOF", 0),
        # `-am"…"`: the cluster ends at `m`, which takes the REST of the
        # cluster as its value. The old rule required the token to end in
        # `m`, so an attached value yielded no source at all.
        (f'git commit -am"{BAD}"', 2),
        (f'git commit -am"{OK}"', 0),
        (f'git commit -vsm"{BAD}"', 2),
        (f"git commit -am '{BAD}'", 2),
        # A cluster whose leading chars are not no-value flags is still
        # refused: `-S` takes an attached key id, so this is not a message.
        ("git commit -Smword -m 'fix: x'", 0),
        # --- `--` ends option scanning (FP guard) ---------------------------
        # A pathspec literally named `-m` is a path, not a message flag.
        ("git commit -m 'fix: x' -- '-m' 'word(a(b))'", 0),
        # …while a real second `-m` before the `--` is still message text.
        (f"git commit -m '{BAD}' -- src", 2),
    ],
)
def test_round_two_option_surface(command, rc):
    assert _run(command)[0] == rc


@pytest.mark.parametrize(
    "command,rc",
    [
        # --- heredoc delimiter spellings feeding `-F -` (stdin) -------------
        # The operator and the delimiter are separate tokens when a space
        # sits between them, which found no delimiter at all and let a
        # malformed message through unseen.
        (f"git commit -F - << EOF\n{BAD}\nEOF", 2),
        (f"git commit -F - << EOF\n{OK}\nEOF", 0),
        (f'git commit -F - <<"EOF"\n{BAD}\nEOF', 2),
        ("git commit -F - <<- 'EOF'\n\tfix: x\n\n\tword(a(b))\n\tEOF", 2),
        # --- which heredoc actually feeds the reader (FP guard) -------------
        # Redirections apply left to right, so stdin comes from the LAST one
        # and the body of `A` is opened and discarded. Grading it blocked a
        # commit whose real message is clean.
        ("git commit -m \"$(cat <<'A' <<'B'\nword(a(b))\nA\n" + OK + "\nB\n)\"", 0),
        ("git commit -m \"$(cat <<'A' <<'B'\nclean\nA\n" + BAD + "\nB\n)\"", 2),
        (f"git commit -F - <<A <<B\nclean\nA\n{BAD}\nB", 2),
        (f"git commit -F - <<A <<B\nword(a(b))\nA\n{OK}\nB", 0),
        # --- backticks re-enter shell parsing inside double quotes ----------
        # `<<EOF` inside the backtick run really does open a heredoc; reading
        # it as string data let a malformed message pass silently.
        ("git commit -m \"`cat <<'EOF'\n" + BAD + "\nEOF\n`\"", 2),
        ("git commit -m \"`cat <<'EOF'\n" + OK + "\nEOF\n`\"", 0),
        # --- bash line continuation -----------------------------------------
        # A backslash-newline is removed, not turned into a separator, so the
        # message really is `word(a(b))`. Splicing to a space produced
        # `word (a(b))` — the one shape this gate always passes.
        ('git commit -m "fix: x" -m "word\\\n(a(b))"', 2),
        ('git commit -m "fix: x" -m "word\\\nplain"', 0),
        # FP guard: bash does NOT splice inside single quotes, so the paren
        # really does start its own line and the message is well formed.
        ("git commit -m 'fix: x' -m 'word\\\n(a(b))'", 0),
        # The same distinction inside a heredoc body, which the reader
        # splices only when the delimiter is unquoted.
        ("git commit -F - <<EOF\nfix: x\n\nword\\\n(a(b))\nEOF", 2),
        ("git commit -F - <<'EOF'\nfix: x\n\nword\\\n(a(b))\nEOF", 0),
        # --- expansion inside an unquoted heredoc body (FP guard) -----------
        # `word($(printf x))` is delivered as `word(x)`, which parses.
        ("git commit -m \"$(cat <<EOF\nfix: x\n\nword($(printf x))\nEOF\n)\"", 0),
        # Quote the delimiter and the same text is literal — and malformed.
        ("git commit -m \"$(cat <<'EOF'\nfix: x\n\nword($(printf x))\nEOF\n)\"", 2),
    ],
)
def test_round_two_heredoc_surface(command, rc):
    assert _run(command)[0] == rc


def test_attached_file_flag_reads_the_file(tmp_path):
    """`-Fmsg.txt` is one token; only the detached form was ever a source."""
    (tmp_path / "bad.txt").write_text(BAD_BODY, encoding="utf-8")
    (tmp_path / "ok.txt").write_text(GOOD_BODY, encoding="utf-8")
    assert _run(f"git commit -F{tmp_path}/bad.txt")[0] == 2
    assert _run(f"git commit -F{tmp_path}/ok.txt")[0] == 0
    # …and the `-C` working directory still resolves its relative path.
    assert _run(f"git -C {tmp_path} commit -Fbad.txt")[0] == 2
    assert _run(f"git -C {tmp_path} commit -Fok.txt")[0] == 0
    # …and a path-prefixed git binary is still git.
    assert _run(f"/usr/bin/git commit -F{tmp_path}/bad.txt")[0] == 2


def test_a_literal_dollar_paren_in_a_double_quoted_message_is_not_a_block():
    """FP guard. Escaped in the source, so the shell substitutes nothing and
    git receives the text verbatim — and `$(foo)` closes, so it parses."""
    assert _run('git commit -m "fix: x" -m "note about \\$(foo) here"') == (0, "", "")
