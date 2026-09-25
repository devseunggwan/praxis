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
        'echo "time-env=[${PRAXIS_TIME_START_EPOCH:-unset}/${PRAXIS_TIME_BUDGET_S:-unset}]"\n'
        'for a in "$@"; do [ "$a" = "--append-system-prompt" ] && echo "append-system-prompt=YES"; done\n'
    )
    stub.chmod(0o755)
    return d


def _run_under_pty(script, extra_path):
    """Run `script` with a real TTY on both fds, as a cmux workspace would."""
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


def _instantiate(tmp_path, branch, time_env="", time_sysprompt=""):
    prompt = tmp_path / "prompt.md"
    prompt.write_text(HOSTILE_PROMPT)
    body = (
        branch.replace("{claude_env}", "")
        # Empty is the default (no --time-budget, #1501); the same trailing
        # backslash argument as {budget_flag} below applies to both.
        .replace("{time_env}", time_env)
        .replace("{time_sysprompt}", time_sysprompt)
        .replace("{sub_model}", "sonnet")
        # Empty is the common case (no --budget). It also proves the trailing
        # backslash still parses when the flag expands to nothing.
        .replace("{budget_flag}", "")
        .replace("$PROMPT_FILE", str(prompt))
    )
    script = tmp_path / "wrapper.sh"
    script.write_text("#!/bin/bash\n" + body)
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


# --time-budget (#1501): the substitution texts Step 4 documents. Pinned here
# and checked against SKILL.md so the executable test and the prose cannot
# drift apart.
TIME_ENV_1200 = 'PRAXIS_TIME_START_EPOCH="$(date +%s)" PRAXIS_TIME_BUDGET_S={time_budget}'
TIME_SYSPROMPT = (
    '--append-system-prompt "Time matters here: do not spend time that can be '
    'avoided, and the earlier a correct result is obtained, the better."'
)


def test_time_substitutions_match_skill_text():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert "`" + TIME_ENV_1200 + "`" in text
    assert "`" + TIME_SYSPROMPT + "`" in text


def test_time_budget_absent_sets_nothing(wrapper_output):
    out = wrapper_output.replace("\r\n", "\n")
    assert "time-env=[unset/unset]" in out
    assert "append-system-prompt=YES" not in out


@pytest.mark.parametrize(
    "budget, sysprompt, expect_append",
    [("1200", "", False), ("0", TIME_SYSPROMPT, True)],
)
def test_time_budget_reaches_worker_env(tmp_path, budget, sysprompt, expect_append):
    """The env pair lands on the claude process, stdio stays on the TTY, and
    only elapsed-only mode (0) adds the system-prompt sentence."""
    script = _instantiate(
        tmp_path,
        _extract_branch("claude"),
        time_env=TIME_ENV_1200.replace("{time_budget}", budget),
        time_sysprompt=sysprompt,
    )
    out = _run_under_pty(script, _stub_bin(tmp_path)).replace("\r\n", "\n")
    m = re.search(r"time-env=\[(\d+)/(\d+)\]", out)
    assert m, out
    assert m.group(2) == budget
    assert int(m.group(1)) > 1_600_000_000  # a real epoch from `date +%s`
    assert ("append-system-prompt=YES" in out) is expect_append
    assert "stdin-is-tty=YES" in out and "stdout-is-tty=YES" in out
    assert "prompt=[" + HOSTILE_PROMPT + "]" in out
