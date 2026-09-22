"""Tests for skills/cmux-delegate/jev-route.py (#1481).

The decision table is exercised through `route` with the client's
`ask_samples` replaced; the client's own HTTP path is covered by
tests/hooks/_lib/test_jev.py. Each sample uses the live noul answer shape
(`{"type": "noul", "noul": <float>}`). The CLI contract (one JSON line, exit 0)
is checked by running the script as a subprocess, the surface the skill uses.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "cmux-delegate" / "jev-route.py"

_spec = importlib.util.spec_from_file_location("jev_route", SCRIPT)
assert _spec and _spec.loader
jev_route = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev_route)


def sample(deep: float, corpus: float, code: float, trivial: float) -> dict:
    values = {"deep": deep, "corpus": corpus, "code": code, "trivial": trivial}
    return {k: {"type": "noul", "noul": v} for k, v in values.items()}


@pytest.fixture()
def answers(monkeypatch):
    box: list[dict] = []
    monkeypatch.setattr(jev_route._jev, "ask_samples", lambda state, questions, n: box[:n])
    return box


@pytest.mark.parametrize("nouls,expected", [
    ((0.9, 0.9, 0.9, 0.9), ("claude", "opus")),
    ((0.1, 0.8, 0.9, 0.1), ("gemini", "")),
    ((0.1, 0.1, 0.8, 0.9), ("codex", "")),
    ((0.1, 0.1, 0.1, 0.8), ("claude", "haiku")),
    ((0.1, 0.1, 0.1, 0.1), ("claude", "sonnet")),
])
def test_decision_order(answers, nouls, expected):
    answers.extend([sample(*nouls)] * 3)
    got = jev_route.route("task")
    assert got["source"] == "jev"
    assert (got["provider"], got["tier"]) == expected


def test_mean_of_samples_decides(answers):
    # 0.9, 0.9, 0.0 averages to 0.6: above the band, so deep wins.
    answers.extend([sample(0.9, 0, 0, 0), sample(0.9, 0, 0, 0), sample(0.0, 0, 0, 0)])
    assert jev_route.route("task")["tier"] == "opus"


def test_ambiguous_consulted_question_falls_back(answers):
    answers.extend([sample(0.1, 0.55, 0.9, 0.1)] * 3)
    got = jev_route.route("task")
    assert got == {"source": "fallback", "reason": "corpus ambiguous",
                   "nouls": {"deep": 0.1, "corpus": 0.55, "code": 0.9, "trivial": 0.1}}


def test_ambiguity_after_decision_is_ignored(answers):
    answers.extend([sample(0.9, 0.5, 0.5, 0.5)] * 3)
    assert jev_route.route("task")["source"] == "jev"


def test_band_edge_is_outside_the_band(answers):
    answers.extend([sample(0.6, 0, 0, 0)] * 3)
    assert jev_route.route("task")["tier"] == "opus"


def test_mean_just_inside_band_is_not_rounded_out(answers):
    answers.extend([sample(0.5996, 0, 0, 0)] * 3)
    assert jev_route.route("task")["reason"] == "deep ambiguous"


def test_missing_samples_fall_back(answers):
    answers.extend([sample(0.9, 0, 0, 0)] * 2)
    assert jev_route.route("task") == {"source": "fallback", "reason": "2/3 samples returned"}


def test_unexpected_shape_falls_back(answers):
    answers.extend([{"deep": {"type": "choice", "choice": "yes"}}] * 3)
    assert jev_route.route("task") == {"source": "fallback", "reason": "unexpected answer shape"}


def run_cli(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, env={**os.environ, **env}, timeout=30)


def test_cli_kill_switch_prints_fallback_and_exits_zero():
    proc = run_cli("design the retry policy", PRAXIS_SKIP_JEV_ROUTING="1")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"source": "fallback", "reason": "0/3 samples returned"}


def test_cli_empty_task_prints_fallback():
    proc = run_cli()
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"source": "fallback", "reason": "empty task"}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 2.0])
def test_out_of_range_noul_falls_back(answers, bad):
    answers.extend([sample(bad, 0, 0, 0)] * 3)
    assert jev_route.route("task") == {"source": "fallback", "reason": "noul out of range"}
