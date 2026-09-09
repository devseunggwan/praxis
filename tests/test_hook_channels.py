"""Tests for the per-hook output-channel derivation (#1265).

The census exists because a hook that fires and a hook that does not exist
look the same to the actor unless the output travels a channel the actor can
read. What that makes fragile is not the classification of a hook that calls
the shared emitter — that is one grep — but three cases where a plausible
implementation quietly reports the wrong channel:

  * a hook that hand-rolls the payload instead of calling `_hook_io.py`.
    Several predate the extraction and `builtin-task-postuse` is documented
    there as a deliberate non-caller, so a helper-only matcher reports them as
    having no channel at all — which reads as "emits nothing", the one verdict
    that hides the failure this census is looking for.
  * `emit_block`, whose own docstring says it "writes the formatted string to
    stderr". Treating it as a channel of its own would put the repo's most
    common block path outside the stderr column.
  * a body registered on several events, where a first-entry read answers for
    one registration and silently drops the rest.

The real corpus is asserted too: the classification is only as good as its
agreement with the bodies actually shipped, and a regex that stops matching
degrades to "every hook emits nothing" without failing anything else.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from hook_channels import (  # noqa: E402
    CHANNEL_ORDER,
    NO_CHANNEL,
    channels_for_hook,
    channels_for_source,
    render_channels,
)


def _manifest() -> dict:
    return json.loads((REPO_ROOT / "hooks" / "manifest.json").read_text())


def _entries_by_name() -> dict[str, list[dict]]:
    by_name: dict[str, list[dict]] = {}
    for entry in _manifest()["hooks"]:
        by_name.setdefault(entry["name"], []).append(entry)
    return by_name


# --- the shared emitters ----------------------------------------------------


def test_each_shared_emitter_maps_to_its_channel() -> None:
    assert channels_for_source("emit_decision(a, b)") == ["decision"]
    assert channels_for_source("emit_additional_context(x)") == ["context"]
    assert channels_for_source("emit_updated_input(a, b)") == ["rewrite"]
    assert channels_for_source("emit_stop_block(r)") == ["stop-block"]
    assert channels_for_source("emit_stop_advisory(m)") == ["system-msg"]


def test_the_ask_and_deny_wrappers_are_decisions() -> None:
    assert channels_for_source("emit_ask(reason)") == ["decision"]
    assert channels_for_source("emit_deny(reason)") == ["decision"]


# --- the three cases a plausible implementation gets wrong -------------------


def test_a_hand_rolled_payload_is_not_reported_as_channel_less() -> None:
    """A body that dumps the field itself never calls the helper."""
    hand_rolled = (
        'json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", '
        '"permissionDecision": "deny"}}, sys.stdout)'
    )
    assert channels_for_source(hand_rolled) == ["decision"]
    assert channels_for_source('{"additionalContext": note}') == ["context"]
    assert channels_for_source('{"decision": "block", "reason": r}') == [
        "stop-block"
    ]


def test_emit_block_is_a_stderr_channel() -> None:
    """`emit_block` writes to stderr, per its docstring in block_message.py."""
    assert channels_for_source("emit_block(msg)") == ["stderr"]


def test_a_multi_event_hook_unions_every_registration(tmp_path: Path) -> None:
    """A first-entry read would answer for one body and drop the other."""
    for name, body, text in (
        ("pre.py", "pre.py", "emit_decision(a, b)"),
        ("post.py", "post.py", "emit_additional_context(x)"),
    ):
        target = tmp_path / "hooks" / "advisory-nudge" / "two-event" / body
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        del name
    entries = [
        {"name": "two-event", "role": "advisory-nudge", "body": "pre.py"},
        {"name": "two-event", "role": "advisory-nudge", "body": "post.py"},
    ]
    assert channels_for_hook(tmp_path, entries) == ["decision", "context"]


# --- shape ------------------------------------------------------------------


def test_channels_render_in_a_fixed_order() -> None:
    """A set's iteration order would make the generated cell unstable."""
    source = "emit_stop_advisory(m); emit_decision(a, b); sys.stderr.write(x)"
    assert channels_for_source(source) == ["decision", "system-msg", "stderr"]
    assert render_channels(channels_for_source(source)) == (
        "decision, system-msg, stderr"
    )


def test_a_body_that_emits_nothing_renders_as_the_placeholder() -> None:
    assert channels_for_source("ledger.record(row)") == []
    assert render_channels([]) == NO_CHANNEL


# --- the shipped corpus -----------------------------------------------------


def test_every_manifest_hook_has_a_readable_body() -> None:
    """A missing body would silently classify as emitting nothing."""
    missing = [
        entry["name"]
        for entry in _manifest()["hooks"]
        if not (
            REPO_ROOT
            / "hooks"
            / entry["role"]
            / entry["name"]
            / (entry.get("body") or "impl.py")
        ).exists()
    ]
    assert missing == []


def test_the_corpus_does_not_collapse_to_one_verdict() -> None:
    """A dead regex degrades to "everything emits nothing" and nothing else."""
    by_name = _entries_by_name()
    derived = {
        name: channels_for_hook(REPO_ROOT, entries)
        for name, entries in by_name.items()
    }
    channel_less = [name for name, ch in derived.items() if not ch]
    assert len(channel_less) < len(derived) // 4, (
        "most hooks classified as emitting nothing — the patterns are dead"
    )
    seen = {channel for channels in derived.values() for channel in channels}
    assert seen == set(CHANNEL_ORDER), (
        f"channels never observed in the corpus: {set(CHANNEL_ORDER) - seen}"
    )


def test_a_known_hook_of_each_channel_classifies_as_expected() -> None:
    """Anchors the derivation to bodies a reader can open and check."""
    by_name = _entries_by_name()
    expected = {
        # opt-in rewrite arm plus its deny fallback (#1399)
        "gh-flag-verify": ["decision", "rewrite"],
        # a Stop advisory with no block path: reaches the user, not the model
        "completion-signal-gate": ["system-msg"],
        # the exit-0 stderr advisory class this census was opened for
        "pytest-direct-exec-advisory": ["stderr"],
    }
    for name, channels in expected.items():
        assert channels_for_hook(REPO_ROOT, by_name[name]) == channels, name
