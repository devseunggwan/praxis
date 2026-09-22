"""The codex wrapper branch passes the resolved model and effort to `codex exec` (#1483)."""
from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "cmux-delegate" / "SKILL.md"


def _codex_branch() -> str:
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(r"^  codex\)\n(.*?)^    ;;\n", text, re.S | re.M)
    assert m, f"could not locate the codex) branch in {SKILL}"
    return m.group(1)


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
