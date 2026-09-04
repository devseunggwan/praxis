"""Differential + contract tests for the hoisted git-argv parser (B2, #594).

`hooks/_lib/git_commit_titles.py` is the single source of truth for the
commit-title extraction that commit-title-format-check and
commit-title-length-check previously duplicated byte-for-byte. The two copies
had already drifted: length-check carried a dead `tok.startswith("-") is not
False` quirk that format-check did not. This suite locks three guarantees:

  1. **Contract** — `extract_git_titles()` returns the correct title list for
     the union of every argv shape the two oracle suites exercise
     (-m / --message / -m= / --message= / -mvalue / -am / -F file / global
     -C / -c, first-message-only, -S cluster rejection, ...).

  2. **Dead-quirk equivalence** — the `=`-embedded branch (where the two copies
     differed) behaves identically under the adopted clean form. Because
     `str.startswith` always returns a bool, `b is not False` ≡ `b`; the
     embedded `-m=`/`--message=` path is asserted directly so a regression in
     that exact branch fails here.

  3. **Single source** — both hooks import the SAME function object from _lib,
     so the parser can no longer drift across the two gates.

  4. **Fail-open** — malformed / dangling / non-git argv returns a list and
     never raises (the hooks are fail-open by contract).

Run: python3 -m pytest tests/test_git_commit_titles.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "hooks" / "_lib"
GATE_DIR = REPO_ROOT / "hooks" / "preflight-gate"

# git_commit_titles.py inserts its own dir on sys.path to find _hook_utils, but
# inserting here too makes the import order-independent.
sys.path.insert(0, str(LIB_DIR))


def _load(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Load the canonical module first so the two impl modules' `from
# git_commit_titles import ...` resolves to this exact object.
gct = _load("git_commit_titles", LIB_DIR / "git_commit_titles.py")
extract_git_titles = gct.extract_git_titles


# ---------------------------------------------------------------------------
# 1. Contract — union of oracle-suite argv shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argv, expected",
    [
        # separate-token message forms
        (["git", "commit", "-m", "hello world"], ["hello world"]),
        (["git", "commit", "--message", "hello world"], ["hello world"]),
        # embedded '=' forms: only `--long=value` is split. Short `-m=hello`
        # is `=hello` to git (leading `=` kept), so the extractor keeps it too
        # via the attached-short-option branch (#1097).
        (["git", "commit", "-m=hello"], ["=hello"]),
        (["git", "commit", "--message=hello"], ["hello"]),
        # attached short-option form
        (["git", "commit", "-mhello"], ["hello"]),
        # combined short clusters terminating in -m
        (["git", "commit", "-am", "hello"], ["hello"]),
        (["git", "commit", "-vsm", "hello"], ["hello"]),
        # only the FIRST -m contributes the title; later -m are body
        (["git", "commit", "-m", "title", "-m", "body para"], ["title"]),
        # --amend before -m
        (["git", "commit", "--amend", "-m", "hi"], ["hi"]),
        # git global flags stripped before the subcommand
        (["git", "-C", "/some/path", "commit", "-m", "hi"], ["hi"]),
        (["git", "-c", "key=val", "commit", "-m", "hi"], ["hi"]),
        # -S<keyid> must NOT be misread as a message even though it ends in 'm'
        (["git", "commit", "-Smike@example.com"], []),
        # not a git-commit argv
        (["echo", "hi"], []),
        (["git", "status"], []),
        (["git", "commit", "--amend"], []),
        # a positional containing '=' that does not start with '-' is ignored
        (["git", "commit", "key=val", "-m", "hi"], ["hi"]),
    ],
)
def test_extract_contract(argv, expected):
    assert extract_git_titles(argv) == expected


# ---------------------------------------------------------------------------
# 2. Dead-quirk equivalence — embedded `=` branch
# ---------------------------------------------------------------------------

def test_embedded_message_branch_clean_form():
    """The embedded-`=` branch applies to `--long=value` only.

    Git splits on `=` for `--message=…` but NOT for the short `-m`: it takes
    the whole `=quirk path` (leading `=` kept) as the message. The short form
    therefore falls through to the attached-short-option branch (`-m<value>`),
    which preserves the `=` (#1097).
    """
    assert extract_git_titles(["git", "commit", "-m=quirk path"]) == ["=quirk path"]
    assert extract_git_titles(["git", "commit", "--message=quirk path"]) == ["quirk path"]
    # a token with '=' but not starting with '-' takes neither branch
    assert extract_git_titles(["git", "commit", "a=b"]) == []


def test_short_m_equals_keeps_leading_equals():
    """`git commit -m=fix: thing` — git's message is `=fix: thing`, so the
    extracted title must retain its leading `=` rather than being split into
    `fix: thing` by the `--long=value` branch (#1097)."""
    titles = extract_git_titles(["git", "commit", "-m=fix: thing"])
    assert titles == ["=fix: thing"]
    assert titles[0].startswith("=")


# ---------------------------------------------------------------------------
# 3. -F / --file forms (first line of the file is the title)
# ---------------------------------------------------------------------------

def test_file_message_forms(tmp_path):
    msg = tmp_path / "msg.txt"
    msg.write_text("feat: from file\n\nbody line\n", encoding="utf-8")
    assert extract_git_titles(["git", "commit", "-F", str(msg)]) == ["feat: from file"]
    assert extract_git_titles(["git", "commit", "--file", str(msg)]) == ["feat: from file"]
    # `=`-splitting is `--long=value` only. Git reads `-F=path` as a file
    # literally named `=path` (verified: `fatal: could not read log file
    # '=msg.txt'`), so the short form no longer resolves the real file — the
    # extractor yields no title rather than a wrong one (#1097).
    assert extract_git_titles(["git", "commit", f"-F={msg}"]) == []
    # The long form keeps its historical `=`-split behaviour.
    assert extract_git_titles(["git", "commit", f"--file={msg}"]) == ["feat: from file"]


def test_file_relative_resolves_against_dash_C(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    msg = sub / "rel.txt"
    msg.write_text("fix: relative file\n", encoding="utf-8")
    argv = ["git", "-C", str(sub), "commit", "-F", "rel.txt"]
    assert extract_git_titles(argv) == ["fix: relative file"]


def test_file_stdin_and_unreadable_are_silent(tmp_path):
    # `-F -` is stdin → silent (no title)
    assert extract_git_titles(["git", "commit", "-F", "-"]) == []
    # unreadable path → silent (no raise, no title)
    missing = tmp_path / "does-not-exist.txt"
    assert extract_git_titles(["git", "commit", "-F", str(missing)]) == []


# ---------------------------------------------------------------------------
# 4. Single source of truth — both gates import the SAME object
# ---------------------------------------------------------------------------

def test_both_hooks_share_one_parser_object():
    fmt = _load(
        "impl_format_check",
        GATE_DIR / "commit-title-format-check" / "impl.py",
    )
    length = _load(
        "impl_length_check",
        GATE_DIR / "commit-title-length-check" / "impl.py",
    )
    # Both modules' `extract_git_titles` must be the very object defined in _lib.
    assert fmt.extract_git_titles is extract_git_titles
    assert length.extract_git_titles is extract_git_titles


# ---------------------------------------------------------------------------
# 5. Fail-open — malformed argv returns a list, never raises
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["git"],
        ["git", "commit"],
        ["git", "commit", "-m"],        # dangling -m, no value
        ["git", "commit", "-F"],        # dangling -F, no value
        ["git", "commit", "--message"],  # dangling long flag
        ["git", "-C"],                  # dangling global flag
        ["git", "commit", "-"],         # lone dash
        ["git", "commit", "--"],        # end-of-options sentinel
    ],
)
def test_fail_open_returns_list(argv):
    result = extract_git_titles(argv)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 6. Substitution exception — quoting decides, not the leading characters
# ---------------------------------------------------------------------------
#
# Tokenization strips quoting, so `'$(x)'` and `"$(x)"` arrive here as the same
# string while the shell treats them oppositely. Deciding on the token alone
# suppressed both, which let a single-quoted literal title skip the format and
# length gates entirely — the two things this parser exists to feed.

def _titles_from(command: str) -> list[str]:
    """Titles as the gates see them: tokenize, then extract with the raw command."""
    from _hook_utils import safe_tokenize as _tok  # noqa: PLC0415

    return extract_git_titles(_tok(command), command)


@pytest.mark.parametrize(
    "command, expected",
    [
        # single quotes: the shell does NOT substitute, so this is a real title
        ("git commit -m '$(literal) title'", ["$(literal) title"]),
        ("git commit -m '`literal` title'", ["`literal` title"]),
        # double quotes: the shell DOES substitute — value unknowable, stay silent
        ('git commit -m "$(printf %s x)"', []),
        ('git commit -m "`printf %s x`"', []),
        # the standard heredoc commit form the exception was written for
        ("git commit -m \"$(cat <<'EOF'\nbody\nEOF\n)\"", []),
        # a substitution mid-title was always graded and still is
        ("git commit -m 'fix: handle $(foo)'", ["fix: handle $(foo)"]),
    ],
)
def test_quoting_decides_the_substitution_exception(command, expected):
    assert _titles_from(command) == expected


def _titles_from_all_segments(command: str) -> list[str]:
    """Titles as the gates see them when the command has SEVERAL segments.

    `_titles_from` above hands the whole token list to `extract_git_titles`
    once, which returns nothing as soon as argv[0] is not `git` — fine for a
    one-command string, useless for `echo …; git commit …`. The gates walk
    `iter_command_starts` (`commit-title-format-check/impl.py:249`), so the
    multi-segment cases below have to walk it too.
    """
    from _hook_utils import iter_command_starts as _starts  # noqa: PLC0415
    from _hook_utils import safe_tokenize as _tok  # noqa: PLC0415

    out: list[str] = []
    for argv in _starts(_tok(command)):
        out += extract_git_titles(argv, command)
    return out


@pytest.mark.parametrize(
    "command, expected",
    [
        # An unrelated substitution in ANOTHER segment must not reach the title.
        # The callers tokenize once and hand every segment the same raw command,
        # so an opener-based scan saw the `echo`'s `$(` and suppressed a title
        # the shell never touched (CodeRabbit, PR #1014). Before and after.
        ("echo $(date); git commit -m '$(literal) title'", ["$(literal) title"]),
        ("git commit -m '$(literal) title' && echo $(date)", ["$(literal) title"]),
        ("echo `date`; git commit -m '`literal` title'", ["`literal` title"]),
        ("git commit -m '`literal` title' && echo `date`", ["`literal` title"]),
        # The control that keeps the fix from becoming "always literal": an
        # unrelated segment does not rescue a title the shell really expands.
        ('echo $(date); git commit -m "$(printf %s x)"', []),
    ],
)
def test_another_segments_substitution_does_not_reach_the_title(command, expected):
    assert _titles_from_all_segments(command) == expected


@pytest.mark.parametrize(
    "command, expected",
    [
        # The reported case: the SAME text quoted both ways. Nothing in the
        # token stream says which run produced the title, so its value is
        # genuinely unknown and the gates must stay silent (issue #1036).
        # Before the fix the earlier single-quoted run made this read as a
        # literal and the format gate blocked a legitimate commit.
        ("""echo '$(x)'; git commit -m "$(x)\"""", []),
        ("""git commit -m "$(x)" && echo '$(x)'""", []),
        ("echo '`x`'; git commit -m \"`x`\"", []),
        # Positive control — a title that IS a single-quoted literal keeps
        # being graded. Without it the change above is indistinguishable from
        # disabling literal detection outright.
        ("""echo "$(y)"; git commit -m '$(x) title'""", ["$(x) title"]),
    ],
)
def test_same_text_quoted_both_ways_is_unknowable(command, expected):
    assert _titles_from_all_segments(command) == expected


def test_value_disqualification_also_silences_the_mirror_case():
    """Pins the cost of matching by value: here the title IS the literal and
    the double-quoted run is another segment's, yet the gates stay silent.
    Closing this needs source spans; silence is the fail-open direction, so the
    residual is recorded rather than fixed."""
    command = """git commit -m '$(x)' && echo "$(x)\""""
    assert _titles_from_all_segments(command) == []


def test_argv_only_callers_keep_the_conservative_behaviour():
    """Without the raw command there is nothing to disambiguate with, so the
    opener alone still suppresses — old callers must not start hard-blocking."""
    assert extract_git_titles(["git", "commit", "-m", "$(literal) title"]) == []
    assert extract_git_titles(["git", "commit", "-m", "$(literal) title"], None) == []


# ---------------------------------------------------------------------------
# 7. Value-disqualification ignores regions the shell never executes
# ---------------------------------------------------------------------------
#
# `_quoted_runs` used to scan the whole raw command, including a trailing
# comment and a heredoc body — text the shell parses as data, not code. A
# double-quoted run living in either region disqualified a genuine
# single-quoted literal title, silencing the gates on a title they should
# have graded (issue #1036 follow-up).

def test_comment_double_quote_does_not_disqualify_the_literal():
    # The `#` starts a real comment here (preceded by whitespace), so its
    # double-quoted run never executes and must not cancel the literal.
    command = """git commit -m '$(x)' # "$(x)\""""
    assert _titles_from(command) == ["$(x)"]


def test_comment_at_the_start_of_a_continuation_line():
    # A `#` that opens a fresh physical line is a boundary too, even though
    # `_starts_unquoted_comment`'s callers only ever see one line at a time.
    command = "echo hi\n# \"$(x)\"\ngit commit -m '$(x)'"
    assert _titles_from_all_segments(command) == ["$(x)"]


def test_heredoc_body_double_quote_does_not_disqualify_the_literal():
    # The double-quoted text lives inside a heredoc body (data written to a
    # file), not inside any executed shell segment.
    command = "cat <<'EOF' > /tmp/x\n\"$(x)\"\nEOF\ngit commit -m '$(x)'"
    assert _titles_from_all_segments(command) == ["$(x)"]


def test_mirror_case_residual_is_unaffected_by_the_region_exclusion():
    """The comment/heredoc fix must not touch the documented residual: a
    double-quoted run in a SIBLING executed segment still disqualifies the
    literal by value, because closing that needs source spans this PR does
    not add."""
    command = """git commit -m '$(x)' && echo "$(x)\""""
    assert _titles_from_all_segments(command) == []


# ---------------------------------------------------------------------------
# extract_git_message_texts — the whole message, not just the title (#1228)
# ---------------------------------------------------------------------------

extract_git_message_texts = gct.extract_git_message_texts


@pytest.mark.parametrize(
    "argv,expected",
    [
        # Successive -m paragraphs join the way git joins them.
        (["git", "commit", "-m", "fix: x", "-m", "body one", "-m", "body two"],
         ["fix: x\n\nbody one\n\nbody two"]),
        # A multi-line -m value keeps every line.
        (["git", "commit", "-m", "fix: x\n\ndetail"], ["fix: x\n\ndetail"]),
        # Attached and combined short forms contribute too.
        (["git", "commit", "-mfix: x", "-m", "body"], ["fix: x\n\nbody"]),
        (["git", "commit", "-am", "fix: x"], ["fix: x"]),
        (["git", "commit", "--message=fix: x"], ["fix: x"]),
        # Not a commit.
        (["git", "log", "-1"], []),
        (["echo", "-m", "x"], []),
        # No message flag at all.
        (["git", "commit", "--amend", "--no-edit"], []),
    ],
)
def test_message_texts_contract(argv, expected):
    assert extract_git_message_texts(argv) == expected


def test_message_texts_reads_the_whole_file(tmp_path):
    path = tmp_path / "msg.txt"
    path.write_text("fix: x\n\nline two\nline three\n", encoding="utf-8")
    argv = ["git", "commit", "-F", str(path)]
    assert extract_git_message_texts(argv) == ["fix: x\n\nline two\nline three\n"]
    # The title gate still sees only the first line of the same file.
    assert extract_git_titles(argv) == ["fix: x"]


def test_message_texts_drop_unresolvable_substitutions():
    command = 'git commit -m "$(cat <<\'EOF\'\nfix: x\nEOF\n)"'
    argv = ["git", "commit", "-m", "$(cat <<'EOF'"]
    assert extract_git_message_texts(argv, command) == []


def test_message_texts_keep_a_single_quoted_literal():
    command = "git commit -m '$(x) and more'"
    argv = ["git", "commit", "-m", "$(x) and more"]
    assert extract_git_message_texts(argv, command) == ["$(x) and more"]


def test_titles_and_texts_walk_the_same_argv():
    """The first -m is the title; every -m is body. One walk, two readings."""
    argv = ["git", "commit", "-m", "fix: x", "-m", "second"]
    assert extract_git_titles(argv) == ["fix: x"]
    assert extract_git_message_texts(argv) == ["fix: x\n\nsecond"]


# ---------------------------------------------------------------------------
# argv[0]: the git binary, bare or path-prefixed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "binary,parses",
    [
        # Named, so the gates built on this walk see the commit.
        ("git", True),
        ("/usr/bin/git", True),
        ("./git", True),
        ("$(git", True),
        # Not git — a name merely ending in the three letters is not the binary.
        ("gitk", False),
        ("mygit", False),
        ("legit", False),
        ("gh", False),
    ],
)
def test_path_prefixed_git_is_still_git(binary, parses):
    argv = [binary, "commit", "-m", "fix: x"]
    expected = ["fix: x"] if parses else []
    assert extract_git_titles(argv) == expected
    assert extract_git_message_texts(argv) == expected
