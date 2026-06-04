"""Differential + contract tests for the hoisted option-text collector (B3, #599).

`hooks/_lib/ask_option_text.py` is the single source of truth for
`collect_option_texts`, previously duplicated byte-for-byte (modulo one
docstring line) in two advisory-nudge hooks:
  - output-block-falsify-advisory  (T2 confidence-anchoring scan)
  - pre-output-falsification-gate  (Lane A evaluative-marker scan)

This suite locks:

  1. **Contract** — `collect_option_texts()` flattens option label+description,
     skips non-dict entries and non-string values, preserves order.

  2. **Single source** — both hooks import the SAME function object from _lib,
     so the collector can no longer drift across the two gates.

  3. **Behavior preservation (differential)** — invoking the real hooks via
     stdin payloads still yields identical decisions after the extraction:
       - (Recommended) label + NO `Falsified:` line  → output-block emits
         `permissionDecision: deny` (T1 hard gate, issue #393).
       - same payload WITH a `Falsified:` line in the question body → silent pass.
       - malformed `questions` (non-list) → both hooks exit 0 (fail-open).

The AskUserQuestion payload shape is mirrored from the working oracle baseline
`tests/hooks/advisory-nudge/test_output_block_falsify_advisory.sh`
(`make_ask_payload`), NOT from the hook under test, per the self-mocking guard.

Run: python3 -m pytest tests/test_ask_option_text.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "hooks" / "_lib"
NUDGE_DIR = REPO_ROOT / "hooks" / "advisory-nudge"
OUTPUT_BLOCK = NUDGE_DIR / "output-block-falsify-advisory" / "impl.py"
PRE_OUTPUT = NUDGE_DIR / "pre-output-falsification-gate" / "impl.py"

sys.path.insert(0, str(LIB_DIR))


def _load(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Load the canonical module first so the two impl modules' `from
# ask_option_text import collect_option_texts` resolves to this exact object.
aot = _load("ask_option_text", LIB_DIR / "ask_option_text.py")
collect_option_texts = aot.collect_option_texts


# ---------------------------------------------------------------------------
# 1. Contract
# ---------------------------------------------------------------------------

def test_collects_label_then_description_in_order():
    options = [
        {"label": "A", "description": "desc-a"},
        {"label": "B", "description": "desc-b"},
    ]
    assert collect_option_texts(options) == ["A", "desc-a", "B", "desc-b"]


def test_label_only_and_description_only():
    options = [{"label": "only-label"}, {"description": "only-desc"}]
    assert collect_option_texts(options) == ["only-label", "only-desc"]


def test_non_dict_entries_skipped():
    options = ["not-a-dict", 42, None, {"label": "kept"}]
    assert collect_option_texts(options) == ["kept"]


def test_non_string_label_and_description_skipped():
    options = [{"label": 123, "description": ["x"]}, {"label": "ok", "description": 9}]
    assert collect_option_texts(options) == ["ok"]


def test_empty_options_returns_empty():
    assert collect_option_texts([]) == []


# ---------------------------------------------------------------------------
# 2. Single source of truth — both gates import the SAME object
# ---------------------------------------------------------------------------

def test_both_hooks_share_one_collector_object():
    ob = _load("impl_output_block", OUTPUT_BLOCK)
    po = _load("impl_pre_output", PRE_OUTPUT)
    assert ob.collect_option_texts is collect_option_texts
    assert po.collect_option_texts is collect_option_texts


# ---------------------------------------------------------------------------
# 3. Behavior preservation — real hook invocation via stdin
# ---------------------------------------------------------------------------

def _make_ask_payload(option_labels: list[str], question: str = "What should we do?") -> str:
    """Mirror of the oracle baseline make_ask_payload (option labels only)."""
    payload = {
        "session_id": "test-session",
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [
                {
                    "question": question,
                    "options": [{"label": label} for label in option_labels],
                }
            ]
        },
        "cwd": "/tmp",
    }
    return json.dumps(payload)


def _run_hook(hook_path: Path, payload: str) -> subprocess.CompletedProcess:
    # Clear the falsification-gate bypass so an inherited env var cannot mask a
    # Lane-A advisory; everything else inherits.
    env = {**os.environ}
    env.pop("PRAXIS_FALSIFY_GATE_BYPASS", None)
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def _decision(stdout: str) -> str | None:
    if not stdout.strip():
        return None
    obj = json.loads(stdout)
    return obj.get("hookSpecificOutput", {}).get("permissionDecision")


def test_output_block_recommended_without_falsified_denies():
    payload = _make_ask_payload(["Option A (Recommended)", "Option B"])
    proc = _run_hook(OUTPUT_BLOCK, payload)
    assert proc.returncode == 0
    assert _decision(proc.stdout) == "deny"


def test_output_block_recommended_with_falsified_line_silent():
    payload = _make_ask_payload(
        ["Option A (Recommended)", "Option B"],
        question="What should we do?\nFalsified: ran the disconfirming test — no divergence.",
    )
    proc = _run_hook(OUTPUT_BLOCK, payload)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # silent pass


def test_malformed_questions_non_list_both_hooks_exit_zero():
    bad_payload = json.dumps({
        "session_id": "test-session",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": "not-a-list"},
        "cwd": "/tmp",
    })
    for hook in (OUTPUT_BLOCK, PRE_OUTPUT):
        proc = _run_hook(hook, bad_payload)
        assert proc.returncode == 0, f"{hook.parent.name} should fail-open on malformed questions"


# ---------------------------------------------------------------------------
# 4. Differential coverage of the extracted function's REAL consumers
# ---------------------------------------------------------------------------
# The (Recommended)-label tests above fire the output-block T1 marker path,
# which short-circuits BEFORE the `elif _has_confidence_anchoring(
# collect_option_texts(options))` branch — so they do not route through the
# extracted collector on the output-block side. The two cases below drive each
# hook's actual collector consumer: output-block T2 (confidence-anchoring) and
# pre-output Lane A (evaluative marker + negative transcript).


def test_output_block_t2_anchoring_routes_through_collector():
    """KO confidence-anchoring '안전한' (no literal (Recommended), no Falsified:)
    → T1 misses, the `elif` evaluates collect_option_texts(options) and T2 emits
    'ask'. This is the output-block path that actually exercises the collector.
    Anchoring token mirrored from the oracle T2 baseline ('가장 안전한')."""
    payload = _make_ask_payload(["가장 안전한 1회 변경", "Faster but more scope"])
    proc = _run_hook(OUTPUT_BLOCK, payload)
    assert proc.returncode == 0
    assert _decision(proc.stdout) == "ask"


def _write_transcript(tmp_path, tool_result_text: str) -> Path:
    """3-line JSONL ending in a user tool_result block carrying the given text.
    Mirrors the oracle baseline `build_transcript_tool_result`."""
    p = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "do the work"}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "running"}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "abc123", "content": tool_result_text}
        ]}},
    ]
    p.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return p


def test_pre_output_lane_a_positive_routes_through_collector(tmp_path):
    """Lane A fires: (Recommended) evaluative marker (A1) + negative transcript
    evidence (A2) + NO falsify phrase (A3 false) → stderr advisory, exit 0. The
    option texts are gathered via collect_option_texts inside _lane_a. Payload
    shape + negative-evidence text mirrored from the oracle baseline."""
    transcript = _write_transcript(tmp_path, "query returned 0 rows and a timeout error")
    payload = json.dumps({
        "session_id": "test-session-A",
        "transcript_path": str(transcript),
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [
                {
                    "question": "What should we do?",
                    "header": "Next",
                    "multiSelect": False,
                    "options": [
                        {"label": "Option A (Recommended)", "description": "the safer path"},
                        {"label": "Option B"},
                    ],
                }
            ]
        },
        "cwd": "/tmp",
    })
    proc = _run_hook(PRE_OUTPUT, payload)
    assert proc.returncode == 0
    assert "falsification-gate" in proc.stderr  # Lane A advisory fired
