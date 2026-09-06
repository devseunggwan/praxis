"""Tests for scripts/check-skill-arg-substitution.py.

Claude Code rewrites a SKILL.md body's positional-parameter references with the
skill invocation's arguments, so a snippet that is correct on disk reaches the
model corrupted. These tests cover:

  - the real tree passes (no SKILL.md carries such a reference),
  - the input surface: which strings count as a reference and which do not,
  - a planted reference is reported with its file, line, and remedy,
  - scope: only ``skills/*/SKILL.md`` is scanned, never the shell scripts that
    sit beside it and legitimately take positional parameters,
  - main() exits 0 on a clean tree and 1 on drift,
  - a regression pin on the line issue #1259 was opened for, and on the
    ``$0`` snippets that only became visible once the matcher was widened.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check-skill-arg-substitution.py"


def _load():
    spec = importlib.util.spec_from_file_location("skill_arg_substitution", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sub = _load()


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A tracked temp tree — the checker reads `git ls-files`, so an untracked
    file would be skipped and a detection test would pass for the wrong reason."""
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_real_tree_holds():
    assert sub.check() == []


def test_real_tree_scan_set_is_not_empty():
    # Without this, `check() == []` above would also pass on a checker that
    # silently scanned nothing — the negative-oracle failure mode.
    docs = sub.skill_docs()
    assert len(docs) >= 10
    assert all(d.name == "SKILL.md" and d.parts[0] == "skills" for d in docs)


# Each case is one variant of the input surface the matcher classifies.
def test_input_surface_classification():
    cases = {
        # references — the forms the loader rewrites
        "awk '{print $1}'": True,
        "cut -f$2": True,
        "echo $9": True,
        "cost is $5 returned verbatim": True,
        # ``$0`` resolves to the first argument: the loader's ``\d+`` matches a
        # zero and its argument array is zero-indexed. Verified against the
        # expression in the installed runtime, quoted in the checker docstring.
        '"$(dirname "$0")/cmux-resume-sessions" "$@"': True,
        "echo $0": True,
        # multi-digit — substituted once that many arguments are passed, and
        # caught here through the leading ``$1``
        "echo $10": True,
        # not references
        '"$(dirname "${0}")/cmux-resume-sessions" "$@"': False,
        "${HOME} and ${1}": False,
        "a $ sign, then 1 on its own": False,
        "awk -v f=1 '{print $f}'": False,
        "$NF and $VAR": False,
        "$@ and $* and $#": False,
        "a line ending in a bare $": False,
    }
    for text, expected in cases.items():
        assert bool(sub._HIT_RE.search(text)) is expected, text


def test_zero_reference_is_reported(tmp_path, monkeypatch):
    """The widening this test pins: ``$0`` used to slip through.

    Both polarities in one tree, so a matcher that fires on everything fails
    here rather than passing the positive half by accident.
    """
    repo = _git_repo(
        tmp_path,
        {
            "skills/zero/SKILL.md": 'run\n\nbash "$(dirname "$0")/inner"\n',
            "skills/clean/SKILL.md": 'run\n\nbash "$(dirname "${0}")/inner" "$@"\n',
        },
    )
    monkeypatch.setattr(sub, "REPO", repo)
    hits = sub.check()
    assert len(hits) == 1, hits
    assert hits[0].startswith("skills/zero/SKILL.md:3")
    assert "$0" in hits[0]


def test_planted_reference_is_reported(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path, {"skills/demo/SKILL.md": "run\n\nawk '{print $1}'\n"})
    monkeypatch.setattr(sub, "REPO", repo)
    hits = sub.check()
    assert len(hits) == 1
    assert hits[0].startswith("skills/demo/SKILL.md:3")
    assert "$1" in hits[0]


def test_scope_excludes_shell_scripts_beside_the_skill(tmp_path, monkeypatch):
    # `claude-recover` and friends live under skills/ and are executed by the
    # shell, never loaded as model context, so their positional parameters are
    # correct. Flagging them would make the check unusable.
    repo = _git_repo(
        tmp_path,
        {
            "skills/demo/SKILL.md": "see the wrapper\n",
            "skills/demo/demo-cli": 'bash "$(dirname "$0")/inner" "$1" "$2"\n',
            "docs/notes.md": "awk '{print $1}'\n",
        },
    )
    monkeypatch.setattr(sub, "REPO", repo)
    assert sub.check() == []


def test_untracked_skill_is_not_scanned(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path, {"skills/demo/SKILL.md": "clean\n"})
    (repo / "skills" / "scratch").mkdir()
    (repo / "skills" / "scratch" / "SKILL.md").write_text("awk '{print $1}'\n")
    monkeypatch.setattr(sub, "REPO", repo)
    assert sub.check() == []


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    assert sub.main() == 0
    assert "check OK" in capsys.readouterr().out

    repo = _git_repo(tmp_path, {"skills/demo/SKILL.md": "echo $3\n"})
    monkeypatch.setattr(sub, "REPO", repo)
    assert sub.main() == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert sub.REMEDY in out


def test_cmux_delegate_step_5b_regression():
    """The line issue #1259 was opened for.

    Pins all three halves of the fix: the field number reaches awk through
    `-v` (so the loader has no digit to rewrite), the selected workspace's
    `* ` marker is stripped (its first field is the marker, not the ref), and
    the guard rejects a non-empty non-ref that `[ -z ... ]` waved through.
    """
    body = (_REPO / "skills" / "cmux-delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert "awk -v f=1 '{print $f}'" in body
    assert "awk '{print $1}'" not in body
    assert "sed 's/^\\* //'" in body
    assert '[ "${TARGET#workspace:}" = "$TARGET" ]' in body


def test_cmux_wrapper_snippets_resolve_via_plugin_root():
    """The four wrapper snippets the widening exposed.

    They once read ``dirname "${0}"`` — braced so the loader's ``\\$(\\d+)``
    could not rewrite it — but inside a skill body ``$0`` is the shell, not
    the SKILL.md, so the path never resolved (#1290). They now resolve through
    ``CLAUDE_PLUGIN_ROOT`` like every other helper-invoking skill; this pins
    that no ``$0`` form comes back in either spelling.
    """
    for rel in (
        "skills/cmux-resume-sessions/SKILL.md",
        "skills/cmux-save-sessions/SKILL.md",
        "skills/cmux-session-manager/SKILL.md",
    ):
        body = (_REPO / rel).read_text(encoding="utf-8")
        assert 'dirname "$0"' not in body, rel
        assert 'dirname "${0}"' not in body, rel
        assert "${CLAUDE_PLUGIN_ROOT:?" in body, rel


def test_recover_skill_snippets_resolve_via_skill_dir():
    """The two recover skills address their bundled tool via ``CLAUDE_SKILL_DIR``.

    They never had the ``$0`` form — they told the model to ``ln -sf`` the tool
    into ``~/.local/bin`` and call it by bare name, which a plugin install never
    sets up (#1333). Each tool lives inside its own skill directory, so the
    documented ``${CLAUDE_SKILL_DIR}`` placeholder (substituted inline in skill
    content, cwd-independent) is the exact fit. The bash ``${VAR:?...}`` guard
    the cmux skills use is deliberately absent here: skill-content placeholders
    are not promised as environment variables, so ``:?`` could fire on a
    substituted-away name with nothing to fall back on.
    """
    for rel, tool in (
        ("skills/recover-sessions/SKILL.md", "claude-recover"),
        ("skills/cmux-recover-sessions/SKILL.md", "cmux-recover-sessions"),
    ):
        body = (_REPO / rel).read_text(encoding="utf-8")
        assert "${CLAUDE_SKILL_DIR}/" + tool in body, rel
        assert 'dirname "$0"' not in body, rel
        assert "ln -sf" not in body, rel
        assert "symlinked to `~/.local/bin/`" not in body, rel
        assert "${CLAUDE_PLUGIN_ROOT:?" not in body, rel
