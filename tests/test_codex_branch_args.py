"""The wrapper passes the resolved model and effort to the worker CLI.

codex: `codex exec -m … -c model_reasoning_effort=…` (#1483).
claude: `claude --model … --effort …`, the effort only when named (#1499).
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "cmux-delegate" / "SKILL.md"


def _branch(name: str) -> str:
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(r"^  %s\)\n(.*?)^    ;;\n" % name, text, re.S | re.M)
    assert m, f"could not locate the {name}) branch in {SKILL}"
    return m.group(1)


def _codex_branch() -> str:
    return _branch("codex")


def _render(branch: str, values: dict[str, str]) -> str:
    """Apply the skill's `{name:+text}` and `{name}` substitutions."""
    def conditional(m: re.Match[str]) -> str:
        return m.group(2) if values[m.group(1)] else ""

    # The conditional's text holds `{name}` itself, so resolve the outer form first.
    out = re.sub(r"\{(\w+):\+((?:[^{}]|\{\w+\})*)\}", conditional, branch)
    return re.sub(r"\{(\w+)\}", lambda m: values[m.group(1)], out)


def _run(tmp_path: pathlib.Path, sub_model: str, effort: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "codex"
    stub.write_text('#!/bin/bash\ncat >/dev/null\nprintf "%s\\n" "$@"\n')
    stub.chmod(0o755)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("task")
    body = _render(_codex_branch(), {"sub_model": sub_model, "effort": effort})
    script = tmp_path / "wrapper.sh"
    script.write_text("#!/bin/bash\n" + body.replace("$PROMPT_FILE", str(prompt)))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    result = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, env=env, check=True)
    return result.stdout.split()


@pytest.mark.parametrize(
    ("sub_model", "effort", "expected"),
    [
        ("gpt-5.6-terra", "medium", ["exec", "-m", "gpt-5.6-terra", "-c", "model_reasoning_effort=medium"]),
        ("gpt-5.6-sol", "xhigh", ["exec", "-m", "gpt-5.6-sol", "-c", "model_reasoning_effort=xhigh"]),
        ("some-other-model", "", ["exec", "-m", "some-other-model"]),
    ],
)
def test_codex_branch_argv(tmp_path: pathlib.Path, sub_model: str, effort: str, expected: list[str]) -> None:
    assert _run(tmp_path, sub_model, effort) == expected


def test_stub_sees_a_changed_argv(tmp_path: pathlib.Path) -> None:
    """Control: the stub reports what it was given, so a dropped flag would show."""
    assert _run(tmp_path, "gpt-5.6-luna", "") != _run(tmp_path / "b", "gpt-5.6-luna", "low")


def test_codex_values_are_validated_before_the_wrapper() -> None:
    """Step 1 must reject shell metacharacters; the wrapper interpolates unquoted.

    The rendered branch runs what it is given: `_run(..., "xhigh; touch f")`
    creates `f`. So the only guard is the rule in the skill's Step 1, which is
    prose the agent follows and cannot be executed here.
    """
    step1 = SKILL.read_text(encoding="utf-8")
    assert "if sub_model does not match /^[A-Za-z0-9._-]+$/" in step1
    assert "if effort and effort does not match /^[A-Za-z0-9._-]+$/" in step1


# --- claude (#1499) ---------------------------------------------------------


def _run_claude(tmp_path: pathlib.Path, sub_model: str, effort: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "claude"
    stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    stub.chmod(0o755)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("task")
    body = _render(_branch("claude"), {"sub_model": sub_model, "effort": effort, "claude_env": "", "budget_flag": ""})
    script = tmp_path / "wrapper.sh"
    script.write_text("#!/bin/bash\n" + body.replace("$PROMPT_FILE", str(prompt)))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, env=env, check=True, stdin=subprocess.DEVNULL
    )
    return result.stdout.split()


def _claude_efforts() -> list[str]:
    """The valid set, read from Step 1 rather than restated here."""
    m = re.search(r"^CLAUDE_EFFORTS = (\[.*\])$", SKILL.read_text(encoding="utf-8"), re.M)
    assert m, "Step 1 no longer declares CLAUDE_EFFORTS"
    return list(ast.literal_eval(m.group(1)))


class Abort(Exception):
    pass


def _resolve_claude(model: str) -> tuple[str, str]:
    """Transcription of Step 1's claude path; the valid set comes from the skill."""
    if re.fullmatch(r"(fable|opus|sonnet|haiku)(?::.+)?", model):
        sub_model = model
    elif m := re.fullmatch(r"claude(?::(.+))?", model):
        sub_model = m.group(1) or ""
    else:
        sub_model = model
    effort = ""
    if m := re.fullmatch(r"(.*):([A-Za-z]+)", sub_model):
        sub_model, effort = m.group(1), m.group(2)
    if not re.fullmatch(r"[A-Za-z0-9._:\[\]-]*", sub_model):
        raise Abort(f"invalid claude model {sub_model!r}")
    if effort and effort not in _claude_efforts():
        raise Abort(f"invalid claude effort {effort!r}")
    return sub_model, effort


def test_claude_effort_set_is_exactly_the_cli_list() -> None:
    """`claude --help` (2.1.282): `--effort <level>` (low, medium, high, xhigh, max)."""
    assert _claude_efforts() == ["low", "medium", "high", "xhigh", "max"]


@pytest.mark.parametrize(
    ("model", "expected_argv"),
    [
        ("claude:opus:low", ["--model", "opus", "--effort", "low", "task"]),
        ("opus:low", ["--model", "opus", "--effort", "low", "task"]),
        ("claude:sonnet:max", ["--model", "sonnet", "--effort", "max", "task"]),
        # No effort named -> no --effort; the model's own default applies.
        ("claude:opus", ["--model", "opus", "task"]),
        ("opus", ["--model", "opus", "task"]),
        ("fable", ["--model", "fable", "task"]),
        # Bare `claude` is the CLI default model: no --model either.
        ("claude", ["task"]),
        ("claude::high", ["--effort", "high", "task"]),
        # A model ID that holds a colon is not mistaken for an effort.
        ("claude:us.anthropic.claude-opus-4-v1:0", ["--model", "us.anthropic.claude-opus-4-v1:0", "task"]),
        ("claude:us.anthropic.claude-opus-4-v1:0:low", ["--model", "us.anthropic.claude-opus-4-v1:0", "--effort", "low", "task"]),
    ],
)
def test_claude_branch_argv(tmp_path: pathlib.Path, model: str, expected_argv: list[str]) -> None:
    sub_model, effort = _resolve_claude(model)
    assert _run_claude(tmp_path, sub_model, effort) == expected_argv


@pytest.mark.parametrize("model", ["claude:opus:bogus", "opus:HIGH", "claude:opus:xHigh"])
def test_claude_invalid_effort_aborts(model: str) -> None:
    with pytest.raises(Abort, match="invalid claude effort"):
        _resolve_claude(model)


@pytest.mark.parametrize("model", ["claude:opus:low; touch x", "opus:$(id)", "claude:opus low"])
def test_claude_shell_metacharacters_abort(model: str) -> None:
    with pytest.raises(Abort, match="invalid claude model"):
        _resolve_claude(model)


def test_claude_invalid_effort_rule_is_in_step1() -> None:
    """The abort is prose the agent follows; pin it next to the codex one.

    `claude --effort bogus` only warns and runs at the default (rc 0), so the
    CLI would not stop a typo — this rule is the only guard.
    """
    step1 = SKILL.read_text(encoding="utf-8")
    assert "if effort and effort not in CLAUDE_EFFORTS: abort" in step1
    assert "if sub_model matches /^(.*):([A-Za-z]+)$/:" in step1
    assert 'if sub_model does not match /^[A-Za-z0-9._:\\[\\]-]*$/: abort "invalid claude model"' in step1
    assert "elif model matches /^(fable|opus|sonnet|haiku)(?::.+)?$/:" in step1


def test_claude_stub_sees_a_changed_argv(tmp_path: pathlib.Path) -> None:
    """Control: a dropped --effort would show."""
    assert _run_claude(tmp_path, "opus", "") != _run_claude(tmp_path / "b", "opus", "low")
