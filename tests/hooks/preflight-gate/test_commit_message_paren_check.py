"""Coverage for hooks/preflight-gate/commit-message-paren-check/impl.py.

The corpus cases are the real commits from this repository's history that
release-please's parser rejected (positives) and accepted (negative controls).
Without the controls a green run cannot distinguish "the gate caught it" from
"the gate always fires" — `2d558892` in particular carries depth-3 nested
parens mid-line and parses fine.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "hooks" / "preflight-gate" / "commit-message-paren-check" / "impl.py"

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
]

# Commits the same parser accepted, over the same range.
ACCEPTED = ["ed44c51", "5fdff21", "3d6a72f", "2d558892"]


def _message(sha: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%B", sha],
        capture_output=True, text=True, check=True,
    ).stdout


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
    ],
)
def test_message_sources(command, rc):
    assert _run(command)[0] == rc


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
