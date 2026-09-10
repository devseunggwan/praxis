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

Scope: this module reports the channels a body *can* emit, reading the body's
code rather than its prose — a comment naming `systemMessage` is not a channel.
It deliberately does not infer, per branch, which exit code accompanies a
stderr write —
that needs flow analysis whose unresolved cases outnumbered its resolved ones
when measured, and a hand-rolled analyzer whose answer cannot be trusted is
worse than a narrower one that can. Reachability of a stderr write therefore
reads off the role contract above, in the doc, rather than off this table.
"""
from __future__ import annotations

import io
import re
import tokenize
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

# Literal prefilter, one tuple per channel.
#
# The patterns above are alternations over word boundaries and quote classes,
# and running six of them across every body scans the corpus six times: 2.1 MB
# of hook source became 12.6 MB of regex input, which measured at 321 ms of the
# matrix build's 358 ms. A body that cannot contain a channel is the common
# case, and `str.__contains__` settles that far faster than a regex can.
#
# Soundness requirement, checked by the parity test: EVERY alternative of a
# channel's pattern must contain at least one of its literals, so a body the
# prefilter rejects is one the pattern could not have matched either. The
# prefilter only skips work — it never decides a channel on its own.
CHANNEL_LITERALS: dict[str, tuple[str, ...]] = {
    "decision": ("emit_decision", "emit_ask", "emit_deny", "format_decision",
                 "permissionDecision"),
    "context": ("additional_context", "additionalContext"),
    "rewrite": ("updated_input", "updatedInput"),
    # A bare "block" passes 95 of 101 bodies for 12 real hits — the word is
    # everywhere in this repo's prose. Every alternative of the pattern ends
    # the word against a quote, so the quote comes into the literal.
    "stop-block": ("stop_block", 'block"', "block'"),
    "system-msg": ("stop_advisory", "systemMessage"),
    "stderr": ("stderr", "emit_block", ">&2"),
}


def strip_comments(source: str, suffix: str) -> str:
    """Return `source` with its comments removed.

    A comment cannot emit anything, so matching raw text reads prose as a
    channel: one shipped hook was classified `system-msg` on the strength of a
    sentence that merely named `systemMessage`. Because the matrix and the
    drift gate both consume this classification, regenerating preserved the
    wrong value rather than correcting it.

    String literals stay. They are where the hand-rolled payloads live
    (`{"additionalContext": ...}`), so removing them would trade this false
    positive for a false negative in the harder-to-notice direction.

    Python goes through `tokenize`, which is the language's own answer to
    where a comment ends. Only the comment's own span is blanked rather than
    round-tripping through `untokenize`: the two agree on every shipped body
    and `untokenize` measured at 120 ms against 65 ms for the same 98 bodies.
    A body that does not tokenize is returned unchanged — classifying it from
    raw text is the answer this function gave before the guard existed, and
    silently reporting no channels would be worse.
    """
    if suffix == ".py":
        try:
            spans = [
                tok
                for tok in tokenize.generate_tokens(io.StringIO(source).readline)
                if tok.type == tokenize.COMMENT
            ]
        except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
            return source
        if not spans:
            return source
        lines = source.splitlines(keepends=True)
        for tok in spans:
            row = tok.start[0] - 1
            lines[row] = lines[row][: tok.start[1]] + "\n"
        return "".join(lines)
    return _strip_sh_comments(source)


def _strip_sh_comments(source: str) -> str:
    """Drop whole-line `#` comments from a shell body.

    Deliberately only the unambiguous subset: a trailing `#` may sit inside a
    string, a `${#var}` expansion, or a pattern, and deciding that needs a
    shell parser whose corner cases outnumber what it would buy here. Three
    hook bodies are shell, and each was read to confirm no channel of theirs
    hides behind a trailing comment.
    """
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )


def channels_for_source(source: str, suffix: str = ".py") -> list[str]:
    """Return the channel tokens `source` can emit, in `CHANNEL_ORDER`."""
    code = strip_comments(source, suffix)
    return [
        name
        for name in CHANNEL_ORDER
        if any(lit in code for lit in CHANNEL_LITERALS[name])
        and CHANNEL_PATTERNS[name].search(code)
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
    seen: set[Path] = set()
    for entry in entries:
        path = hook_body_path(repo_root, entry)
        if path in seen or not path.exists():
            continue
        seen.add(path)
        found.update(channels_for_source(path.read_text(), path.suffix))
    return [name for name in CHANNEL_ORDER if name in found]


def render_channels(channels: list[str]) -> str:
    """Render the channel list as a matrix cell."""
    return ", ".join(channels) if channels else NO_CHANNEL
