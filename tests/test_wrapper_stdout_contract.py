"""tests/test_wrapper_stdout_contract.py — Step 4 stdout contract (#981).

ARCHITECTURE.md's Provider CLI Spec puts a precondition on the `claude` row's
stdin column: `claude --help` skips the workspace trust dialog only "via -p, or
when stdout is not a TTY", that dialog reads stdin, and the row's command pipes
the prompt into stdin. Using the stdin column therefore obliges the caller to
supply one exemption. cmux-delegate Step 4 is the only caller in this repo.

This file pins the *executable* half: it extracts the `claude)` branch verbatim
out of SKILL.md, instantiates the {placeholders}, and runs it under a real PTY
(what a cmux workspace hands the wrapper) with a stub `claude` on PATH that
reports isatty(stdout). Grepping the prose cannot catch a redirect that is
written but does not actually take effect — e.g. a trailing `\\` dropped so the
pipe lands on the next command instead.

Both directions are pinned on purpose. `test_claude_branch_stdout_is_not_a_tty`
is the case that must now fire; `test_probe_detects_a_tty` is the control that
must stay silent, because a stub that reported NO unconditionally would make the
first test pass for no reason.

NOTE: this is a documented-contract drift, not a repaired failure. The owner's
4-run control on claude 2.1.232 showed the prompt is not actually lost. Nothing
here should be read as evidence that delegation was previously broken.

Run:  python3 -m pytest tests/test_wrapper_stdout_contract.py
"""

import os
import pathlib
import pty
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "cmux-delegate" / "SKILL.md"

# The prompt carries every character class "Why Wrapper Script?" names as the
# reason `-p` is excluded. If a future edit reintroduces a shell round-trip,
# these come back mangled and the assertion below fails.
HOSTILE_PROMPT = 'cost $5 {brace} `tick` "quote"\n'


def _extract_branch(name):
    """Pull one `case` branch out of the Step 4 fence, verbatim."""
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(r"^  %s\)\n(.*?)^    ;;\n" % name, text, re.S | re.M)
    assert m, f"could not locate the {name}) branch in {SKILL}"
    return m.group(1)


def _stub_bin(tmp_path):
    """A `claude` that reports isatty(stdout) and echoes the prompt it read."""
    d = tmp_path / "bin"
    d.mkdir()
    stub = d / "claude"
    stub.write_text(
        "#!/bin/bash\n"
        'if [ -t 1 ]; then echo "stdout-is-tty=YES"; else echo "stdout-is-tty=NO"; fi\n'
        'echo "prompt=[$(cat)]"\n'
    )
    stub.chmod(0o755)
    return d


def _run_under_pty(script, extra_path):
    """Run `script` with a real TTY on stdout, as a cmux workspace would."""
    chunks = []

    def _read(fd):
        data = os.read(fd, 4096)
        chunks.append(data)
        return data

    old = os.environ["PATH"]
    # pty.spawn execs with the inherited environment, so the stub has to be on
    # os.environ — passing an env dict to spawn() does nothing.
    os.environ["PATH"] = f"{extra_path}:{old}"
    try:
        pty.spawn(["/bin/bash", str(script)], _read)
    finally:
        os.environ["PATH"] = old
    return b"".join(chunks).decode(errors="replace")


def _instantiate(tmp_path, branch):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(HOSTILE_PROMPT)
    log = tmp_path / "wrapper.log"
    body = (
        branch.replace("{claude_env}", "")
        .replace("{sub_model}", "sonnet")
        .replace("{permission_mode}", "auto")
        # Empty is the common case (no --budget). It also proves the trailing
        # backslash before the pipe still parses when the flag expands to
        # nothing.
        .replace("{budget_flag}", "")
        .replace("$PROMPT_FILE", str(prompt))
        .replace("$WRAPPER_LOG", str(log))
    )
    script = tmp_path / "wrapper.sh"
    script.write_text("#!/bin/bash\n" + body)
    script.chmod(0o755)
    return script, log


def test_claude_branch_stdout_is_not_a_tty(tmp_path):
    """Must fire: Step 4 supplies the non-TTY exemption ARCHITECTURE.md names."""
    script, log = _instantiate(tmp_path, _extract_branch("claude"))
    out = _run_under_pty(script, _stub_bin(tmp_path))

    assert "stdout-is-tty=NO" in out, (
        "claude was handed a TTY on stdout — the trust dialog and the piped "
        "prompt are competing for stdin (ARCHITECTURE.md Provider CLI Spec)"
    )
    assert "stdout-is-tty=YES" not in out


def test_operator_still_sees_the_output(tmp_path):
    """The redirect must be the narrowest one: a full `>` empties the cmux pane.

    tee keeps the workspace TTY fed while making claude's own stdout a pipe.
    """
    script, log = _instantiate(tmp_path, _extract_branch("claude"))
    out = _run_under_pty(script, _stub_bin(tmp_path))

    assert "stdout-is-tty=NO" in out, "worker output vanished from the terminal"
    assert log.exists() and "stdout-is-tty=NO" in log.read_text()


def test_prompt_survives_the_pipeline(tmp_path):
    """The stdin path must stay shell-free — the reason `-p` is excluded."""
    script, _ = _instantiate(tmp_path, _extract_branch("claude"))
    out = _run_under_pty(script, _stub_bin(tmp_path))

    assert "prompt=[" + HOSTILE_PROMPT.strip() + "]" in out.replace("\r\n", "\n")


def test_probe_detects_a_tty(tmp_path):
    """Control that must stay silent: the stub reports YES when nothing is
    redirected. Without this, a stub wedged at NO would pass every test above."""
    prompt = tmp_path / "prompt.md"
    prompt.write_text(HOSTILE_PROMPT)
    script = tmp_path / "bare.sh"
    script.write_text(f'#!/bin/bash\ncat "{prompt}" | claude --model sonnet\n')
    script.chmod(0o755)

    out = _run_under_pty(script, _stub_bin(tmp_path))
    assert "stdout-is-tty=YES" in out
    assert "stdout-is-tty=NO" not in out


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_other_providers_are_not_redirected(provider):
    """Control that must stay silent: the obligation is on the claude row only.

    ARCHITECTURE.md attaches the precondition to that row alone, and gemini does
    not use the stdin column at all (`-p "$(cat …)"`). Blanket-teeing every
    branch would be cargo-culting this fix outward.
    """
    assert "tee" not in _extract_branch(provider)
