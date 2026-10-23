"""tests/test_wrapper_stdout_contract.py — Step 4 stdio contract (#1054).

The delegated worker must be an ordinary Claude Code session: a live TTY on
**both** stdin and stdout. Piping either one is what broke fire-and-forget.

- stdin piped (`cat file | claude`) closes the worker's fd 0 the moment `cat`
  exits. A permission prompt then has no channel to be answered on, and the
  worker prints "Awaiting your confirmation" and exits 0 — measured, not
  inferred. `cmux send` cannot reach it either, which is why Step 5b could not
  work against a Step 5a workspace.
- stdout piped (`| tee`) block-buffers the worker's output, so the cmux pane
  stays empty for the whole run and the operator sees nothing.

This file pins the *executable* half: it extracts the `claude)` branch verbatim
out of SKILL.md, instantiates the {placeholders}, and runs it under a real PTY
(what a cmux workspace hands the wrapper) with a stub `claude` on PATH that
reports isatty() on both descriptors. Grepping the prose cannot catch a pipe
that is written but does not actually take effect.

`test_stub_reports_no_when_piped` is the control: a stub that answered YES
unconditionally would make the assertions below pass for no reason.

Run:  python3 -m pytest tests/test_wrapper_stdout_contract.py
"""

import os
import pathlib
import pty
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "cmux-delegate" / "SKILL.md"

# The prompt carries every character class "Why Wrapper Script?" names as the
# reason an inline literal is excluded. If a future edit reintroduces a shell
# round-trip over the prompt text, these come back mangled.
HOSTILE_PROMPT = 'cost $5 {brace} `tick` "quote"'


def _extract_branch(name):
    """Pull one `case` branch out of the Step 4 fence, verbatim."""
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(r"^  %s\)\n(.*?)^    ;;\n" % name, text, re.S | re.M)
    assert m, f"could not locate the {name}) branch in {SKILL}"
    return m.group(1)


def _extract_preamble():
    """The Step 4 wrapper up to the provider `case`, verbatim.

    It sits outside every branch, so `_extract_branch` alone never runs it: the
    `unset` of an inherited `PRAXIS_UNATTENDED`, the prompt guards, and
    `SYS_PROMPT` (#1510). Without it the inherited-env tests below would pass
    with the `unset` deleted, and the claude branch would pass an empty system
    prompt.
    """
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(
        r'^### Step 4:.*?^```bash\n(.*?)^case "\{provider\}" in$', text, re.S | re.M
    )
    assert m, f"could not locate the Step 4 wrapper fence in {SKILL}"
    assert "\nunset PRAXIS_UNATTENDED" in m.group(1), "Step 4 no longer unsets PRAXIS_UNATTENDED"
    return m.group(1).replace("#!/bin/bash\n", "", 1)


def _stub_bin(tmp_path):
    """A `claude` that reports isatty() on both fds and echoes its argv prompt."""
    d = tmp_path / "bin"
    d.mkdir()
    stub = d / "claude"
    stub.write_text(
        "#!/bin/bash\n"
        'if [ -t 0 ]; then echo "stdin-is-tty=YES"; else echo "stdin-is-tty=NO"; fi\n'
        'if [ -t 1 ]; then echo "stdout-is-tty=YES"; else echo "stdout-is-tty=NO"; fi\n'
        'echo "prompt=[${!#}]"\n'
        'echo "unattended=[${PRAXIS_UNATTENDED:-unset}]"\n'
        'n=0; prev=""\n'
        'for a in "$@"; do\n'
        '  [ "$prev" = "--append-system-prompt" ] && echo "sysprompt=[$a]"\n'
        '  [ "$a" = "--append-system-prompt" ] && n=$((n + 1))\n'
        '  prev="$a"\n'
        'done\n'
        'echo "append-count=$n"\n'
    )
    stub.chmod(0o755)
    return d


# Cleared by the Step 4 preamble.
WRAPPER_VARS: tuple[str, ...] = ("PRAXIS_UNATTENDED",)


def _run_under_pty(script, extra_path, inherit=None):
    """Run `script` with a real TTY on both fds, as a cmux workspace would.

    The caller's own WRAPPER_VARS are dropped, so a developer shell (or a
    delegated worker running this suite) cannot decide the outcome. `inherit` sets
    them on purpose for the test that checks the wrapper clears them.
    """
    chunks = []

    def _read(fd):
        data = os.read(fd, 4096)
        chunks.append(data)
        return data

    overrides = dict.fromkeys(WRAPPER_VARS)
    overrides.update(inherit or {})
    overrides["PATH"] = f"{extra_path}:{os.environ['PATH']}"
    saved = {name: os.environ.get(name) for name in overrides}

    def _apply(values):
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # pty.spawn execs with the inherited environment, so the stub has to be on
    # os.environ — passing an env dict to spawn() does nothing.
    _apply(overrides)
    try:
        pty.spawn(["/bin/bash", str(script)], _read)
    finally:
        _apply(saved)
    return b"".join(chunks).decode(errors="replace")


def _instantiate(tmp_path, branch, preamble=None):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(HOSTILE_PROMPT)
    # `{name:+text}` is conditional expansion (#1499): with no effort named the
    # `--effort` flag must vanish, not reach argv as literal braces.
    branch = re.sub(r"\{effort:\+(?:[^{}]|\{\w+\})*\}", "", branch)
    branch = re.sub(r"\{sub_model:\+((?:[^{}]|\{\w+\})*)\}", r"\1", branch)
    body = (
        branch.replace("{claude_env}", "")
        .replace("{sub_model}", "sonnet")
        # Empty is the common case (no --budget). It also proves the trailing
        # backslash still parses when the flag expands to nothing.
        .replace("{budget_flag}", "")
        .replace("$PROMPT_FILE", str(prompt))
    )
    script = tmp_path / "wrapper.sh"
    if preamble is None:
        preamble = (
            _extract_preamble()
            .replace("{prompt_file}", str(prompt))
            .replace("{script_file}", str(tmp_path / "wrapper-copy.sh"))
        )
    script.write_text("#!/bin/bash\n" + preamble + body)
    script.chmod(0o755)
    return script


@pytest.fixture()
def wrapper_output(tmp_path):
    script = _instantiate(tmp_path, _extract_branch("claude"))
    return _run_under_pty(script, _stub_bin(tmp_path))


def test_claude_branch_keeps_stdin_on_the_tty(wrapper_output):
    """The #1054 oracle: fd 0 must still be the terminal, not a spent pipe."""
    assert "stdin-is-tty=YES" in wrapper_output, (
        "the worker's stdin is a pipe — a permission prompt has no channel to "
        "be answered on and the worker will exit instead of waiting (#1054)"
    )
    assert "stdin-is-tty=NO" not in wrapper_output


def test_claude_branch_keeps_stdout_on_the_tty(wrapper_output):
    """A piped stdout block-buffers the run and empties the cmux pane."""
    assert "stdout-is-tty=YES" in wrapper_output
    assert "stdout-is-tty=NO" not in wrapper_output


def test_prompt_survives_argv(wrapper_output):
    """argv must stay shell-free — the reason an inline literal is excluded."""
    assert "prompt=[" + HOSTILE_PROMPT + "]" in wrapper_output.replace("\r\n", "\n")


def test_stub_reports_no_when_piped(tmp_path):
    """Control: the stub must be able to answer NO, or every assertion above
    would pass against a stub wedged at YES."""
    script = _instantiate(tmp_path, _extract_branch("claude"))
    env = dict(os.environ, PATH=f"{_stub_bin(tmp_path)}:{os.environ['PATH']}")
    out = subprocess.run(
        ["/bin/bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    ).stdout

    assert "stdin-is-tty=NO" in out
    assert "stdout-is-tty=NO" in out


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_other_providers_are_not_redirected(provider):
    """Control that must stay silent: no branch pipes the worker's stdout.

    gemini already takes the prompt via `-p "$(cat …)"`, and `codex exec` is
    non-interactive by design, so neither carries the claude row's obligation.
    """
    assert "tee" not in _extract_branch(provider)


NOTE_START = "Text inside <pasted_content> tags was copied into this prompt"


def test_system_prompt_note_reaches_worker_once(wrapper_output):
    """The pasted-content note lands on the claude process through exactly one
    `--append-system-prompt`: the CLI keeps only the last one, so a second
    flag would drop the note (#1510)."""
    out = wrapper_output.replace("\r\n", "\n")
    assert "append-count=1" in out
    assert NOTE_START in out
    assert "stdin-is-tty=YES" in out and "stdout-is-tty=YES" in out
    assert "prompt=[" + HOSTILE_PROMPT + "]" in out


# PRAXIS_UNATTENDED (#1512): the marker that puts early-stop-advisory in block
# mode. A new-session claude worker is an unattended run; nothing else is.


def test_claude_worker_is_marked_unattended(wrapper_output):
    assert "unattended=[1]" in wrapper_output.replace("\r\n", "\n")


def test_inherited_unattended_marker_is_replaced_not_passed(tmp_path):
    """A value the delegating session carried (here a non-`1` one) must not
    reach the worker; the launch line sets its own."""
    script = _instantiate(tmp_path, _extract_branch("claude"))
    out = _run_under_pty(script, _stub_bin(tmp_path), inherit={"PRAXIS_UNATTENDED": "0"})
    assert "unattended=[1]" in out.replace("\r\n", "\n"), out


def _env_stub_bin(tmp_path, name):
    d = tmp_path / "bin"
    d.mkdir()
    stub = d / name
    stub.write_text('#!/bin/bash\necho "unattended=[${PRAXIS_UNATTENDED:-unset}]"\n')
    stub.chmod(0o755)
    return d


@pytest.mark.parametrize("provider", ["codex", "gemini"])
@pytest.mark.parametrize("preamble, expected", [(None, "unset"), ("", "1")])
def test_other_providers_are_not_marked_unattended(tmp_path, provider, preamble, expected):
    """codex and gemini workers are not marked, and an inherited marker is
    cleared. The empty-preamble case is the control: without the `unset` the
    same inheritance reaches the stub."""
    script = _instantiate(tmp_path, _extract_branch(provider), preamble=preamble)
    env = dict(os.environ, PATH=f"{_env_stub_bin(tmp_path, provider)}:{os.environ['PATH']}")
    env["PRAXIS_UNATTENDED"] = "1"
    out = subprocess.run(
        ["/bin/bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    ).stdout
    assert f"unattended=[{expected}]" in out, out
