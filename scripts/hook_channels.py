#!/usr/bin/env python3
"""Derive, per hook, which output channels its body can emit (issue #1265).

A hook that fires and a hook that does not exist are indistinguishable to the
actor unless the hook's output travels a channel the actor can read. The
channels are not interchangeable, and which one a hook uses is a property of
its body — not of its manifest registration — so this module reads the body.

The channel semantics below are transcribed from the emitters that own them,
not inferred:

- `hooks/_lib/_hook_io.py` — `permissionDecision` reason "shown to the agent";
  a Stop `{"decision": "block", "reason": ...}` whose reason "is fed to the
  model so it can self-correct"; a Stop `{"systemMessage": ...}` that is
  "shown to the user in the transcript; does NOT block the stop and is NOT fed
  to the model"; and, on `updatedInput`, "a rewrite with no additionalContext
  is invisible to everyone downstream".
- `hooks/_lib/_dispatch.py` — `additionalContext` is "the ONE exit-0
  PreToolUse channel that actually reaches the model (stderr does not; see
  `_hook_io.py`)".
- `docs/adr/0001-hook-layout.md` — "`advisory-nudge/` — PreToolUse stderr
  nudges (never block)", i.e. that role's stderr rides an exit-0 path, which
  `_hook_io.py` records as reaching "only the debug log".

Scope: this module reports the channels a body *can* emit. It deliberately
does not infer, per branch, which exit code accompanies a stderr write —
that needs flow analysis whose unresolved cases outnumbered its resolved ones
when measured, and a hand-rolled analyzer whose answer cannot be trusted is
worse than a narrower one that can. Reachability of a stderr write therefore
reads off the role contract above, in the doc, rather than off this table.
"""
from __future__ import annotations

import re
from pathlib import Path

# Channel tokens, in the order they are rendered. The order is fixed so the
# generated cell is stable across runs: a set's iteration order is not.
CHANNEL_ORDER = (
    "decision",
    "context",
    "rewrite",
    "stop-block",
    "system-msg",
    "stderr",
)

# One regex per channel, matched against the hook body's source text.
#
# Each pattern names both the shared `_lib` emitter and the raw field, because
# a hook may hand-roll the payload instead of calling the helper — several
# predate the extraction, and `builtin-task-postuse` is documented in
# `_hook_io.py` as a deliberate non-caller. Matching only the helper would
# report those hooks as having no channel at all.
CHANNEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "decision": re.compile(
        r"\bemit_decision\s*\(|\bemit_ask\s*\(|\bemit_deny\s*\(|"
        r"\bformat_decision\s*\(|[\"']permissionDecision[\"']"
    ),
    "context": re.compile(
        r"\bemit_additional_context\s*\(|[\"']additionalContext[\"']"
    ),
    "rewrite": re.compile(
        r"\bemit_updated_input\s*\(|\bformat_updated_input\s*\(|"
        r"[\"']updatedInput[\"']"
    ),
    "stop-block": re.compile(
        r"\bemit_stop_block\s*\(|\bformat_stop_block\s*\(|"
        r"[\"']decision[\"']\s*:\s*[\"']block[\"']|decision:\s*\"block\""
    ),
    "system-msg": re.compile(
        r"\bemit_stop_advisory\s*\(|\bformat_stop_advisory\s*\(|systemMessage"
    ),
    # `emit_block` writes the standard block message to stderr, per its own
    # docstring, so it is a stderr channel rather than a channel of its own.
    "stderr": re.compile(
        r"sys\.stderr\.write|file=sys\.stderr|\bemit_block\s*\(|>&2"
    ),
}

#: Rendered when a hook emits nothing — a ledger-only recorder, by design.
NO_CHANNEL = "-"


def channels_for_source(source: str) -> list[str]:
    """Return the channel tokens `source` can emit, in `CHANNEL_ORDER`."""
    return [
        name
        for name in CHANNEL_ORDER
        if CHANNEL_PATTERNS[name].search(source)
    ]


def hook_body_path(repo_root: Path, entry: dict) -> Path:
    """Path to a manifest entry's body.

    `body` is optional in the manifest schema and defaults to `impl.py`; the
    six shell hooks declare `impl.sh` explicitly.
    """
    body = entry.get("body") or "impl.py"
    return repo_root / "hooks" / entry["role"] / entry["name"] / body


def channels_for_hook(repo_root: Path, entries: list[dict]) -> list[str]:
    """Channels for one hook, unioned across its registrations.

    A hook registered on several events shares one body, so the union is over
    identical sources in the normal case. It is still a union rather than a
    first-entry read, because a multi-event hook may ship one body per event.
    """
    found: set[str] = set()
    for entry in entries:
        path = hook_body_path(repo_root, entry)
        if path.exists():
            found.update(channels_for_source(path.read_text()))
    return [name for name in CHANNEL_ORDER if name in found]


def render_channels(channels: list[str]) -> str:
    """Render the channel list as a matrix cell."""
    return ", ".join(channels) if channels else NO_CHANNEL
