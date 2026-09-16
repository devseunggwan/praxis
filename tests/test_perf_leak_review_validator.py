"""Schema and closed-list enforcement for the perf-leak-review envelope (#1429).

The reviewer is an LLM, so the envelope is the only place the contract can be
held. These cases pin the two properties the rest of the design rests on: a
class outside C1..C5 is rejected rather than repaired (AC-2, AC-6), and an empty
`findings` list is accepted, because that is what the clean controls produce
(AC-5).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "skills" / "perf-leak-review" / "validate_findings.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("validate_findings", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_FINDING: dict[str, Any] = {
    "class": "C2",
    "file": "lib/source.sh",
    "line": 7,
    "evidence": 'exec 3<"$1"',
    "confidence": "med",
    "grade": "candidate",
}
VALID_ENVELOPE: dict[str, Any] = {
    "repo_root": "/tmp/prepared",
    "findings": [VALID_FINDING],
}


def _with(**overrides: Any) -> dict[str, Any]:
    envelope = copy.deepcopy(VALID_ENVELOPE)
    envelope["findings"][0].update(overrides)
    return envelope


def _without(key: str) -> dict[str, Any]:
    envelope = copy.deepcopy(VALID_ENVELOPE)
    del envelope["findings"][0][key]
    return envelope


def test_validator_is_executable() -> None:
    assert VALIDATOR.is_file()


def test_accepts_a_well_formed_envelope() -> None:
    assert _module().validate(VALID_ENVELOPE) == []


def test_accepts_an_empty_findings_list() -> None:
    """A clean diff yields `[]`; rejecting it would fail every clean control."""
    assert _module().validate({"repo_root": "/tmp/p", "findings": []}) == []


def test_rejects_a_class_outside_the_closed_list() -> None:
    violations = _module().validate(_with(**{"class": "C6"}))
    assert violations, "C6 must be rejected — the list is closed"
    assert any("outside the closed list" in v for v in violations)


def test_rejects_confidence_outside_the_enum() -> None:
    violations = _module().validate(_with(confidence="very high"))
    assert any(".confidence" in v for v in violations)


def test_rejects_a_missing_key() -> None:
    for key in ("class", "file", "line", "evidence", "confidence", "grade"):
        violations = _module().validate(_without(key))
        assert any(f".{key}: missing" in v for v in violations), key


def test_rejects_an_unknown_key() -> None:
    """The schema is closed at the finding level too."""
    violations = _module().validate(_with(severity="high"))
    assert any("unexpected key" in v for v in violations)


def test_rejects_an_absolute_file_path() -> None:
    violations = _module().validate(_with(file="/tmp/prepared/lib/source.sh"))
    assert any("is absolute" in v for v in violations)


def test_rejects_a_grade_other_than_candidate() -> None:
    violations = _module().validate(_with(grade="defect"))
    assert any(".grade" in v for v in violations)


def test_rejects_a_boolean_line_number() -> None:
    """`True` is an int subclass and would otherwise pass as line 1."""
    violations = _module().validate(_with(line=True))
    assert any(".line" in v for v in violations)


def test_cli_exits_one_on_an_out_of_list_class() -> None:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        input=json.dumps(_with(**{"class": "C9"})),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "outside the closed list" in proc.stderr


def test_cli_exits_zero_on_a_valid_envelope() -> None:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        input=json.dumps(VALID_ENVELOPE),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "envelope OK" in proc.stdout


def test_cli_exits_one_on_malformed_json() -> None:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        input="not json",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "not valid JSON" in proc.stderr
