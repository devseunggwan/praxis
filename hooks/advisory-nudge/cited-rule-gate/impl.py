#!/usr/bin/env python3
"""PreToolUse advisory: a mutating call names the rule section that governs it.

Issue #1487. The rule set opens with "before any action, name the section that
governs it; if you cannot, re-read it first", and nothing enforces it. In the
session behind the issue no assistant text carried a citation line until the
user pointed it out, and every violation in that stretch was caught by the
user. `momentum-rule-retrieval-gate` covers merges only and the other gates
check the content of specific calls, so nothing checks that retrieval happened
at all before an ordinary mutation.

Detection: the call is mutating per the shared `_mutating_call` classifier, and
the assistant text written since the previous tool call carries no citation
line. A citation line starts with a configured prefix (`Rule:` by default)
followed by a section name. When a rule file is readable, the name must equal
one of its headings, so the line cannot be filler; a heading's trailing inline
code spans (`[E2]`-style tokens) are not part of its name.

"Since the previous tool call" is the text between the last user record before
the current assistant message and the current tool_use. Tool results of the
current message's own tool_uses do not close the window, so one citation
written before a parallel batch covers every call in it.

Modes: advisory by default (stderr + `additionalContext`, exit 0);
`PRAXIS_CITED_RULE_STRICT=1` asks for confirmation instead;
`PRAXIS_HOOK_BYPASS_CITED_RULE=1` is silent.

Configuration: `PRAXIS_CITED_RULE_PREFIXES` is a comma-separated list of
citation prefixes (per locale); `PRAXIS_CITED_RULE_FILES` is an
`os.pathsep`-separated list of rule files whose headings are accepted, and
replaces the default (`~/.claude/CLAUDE.md`, then `CLAUDE.md` and `AGENTS.md`
in the session cwd).

Fail-open contract: malformed stdin, an unreadable transcript, a tool_use id
absent from the transcript tail, or any uncaught exception exits 0 silently.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import (  # type: ignore[import-not-found]  # noqa: E402
    emit_additional_context,
    emit_decision,
)
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _mutating_call import is_mutating_call  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    TRANSCRIPT_SCAN_LINES,
    TranscriptReadError,
    tail_lines,
)

HOOK_NAME = "cited-rule-gate"
STRICT_ENV = "PRAXIS_CITED_RULE_STRICT"
BYPASS_ENV = "PRAXIS_HOOK_BYPASS_CITED_RULE"
PREFIXES_ENV = "PRAXIS_CITED_RULE_PREFIXES"
RULE_FILES_ENV = "PRAXIS_CITED_RULE_FILES"
DEFAULT_PREFIXES = ("Rule:",)

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_TRAILING_CODE_RE = re.compile(r"\s*`[^`]*`\s*$")
# Markdown a citation line may be wrapped in: list bullets, quotes, emphasis.
_LINE_DECOR = " \t-*+>_"
_EMPHASIS = " \t*_"
_QUOTES = " \t`'\""
# A line may cite several sections; a heading can itself hold a comma, so the
# whole remainder is tried before any split.
_NAME_SPLIT_RE = re.compile(r"\s*[·;|]\s*")


def _prefixes() -> tuple[str, ...]:
    raw = os.environ.get(PREFIXES_ENV, "")
    items = tuple(p.strip() for p in raw.split(",") if p.strip())
    return items or DEFAULT_PREFIXES


def _rule_files(cwd: str) -> list[str]:
    raw = os.environ.get(RULE_FILES_ENV)
    if raw is not None:
        return [p for p in raw.split(os.pathsep) if p]
    files = [os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md")]
    if cwd:
        files += [os.path.join(cwd, "CLAUDE.md"), os.path.join(cwd, "AGENTS.md")]
    return files


def _normalize(name: str) -> str:
    return " ".join(name.split())


def _strip_trailing_code(name: str) -> str:
    while True:
        stripped = _TRAILING_CODE_RE.sub("", name)
        if stripped == name:
            return _normalize(name)
        name = stripped


def heading_names(text: str) -> set[str]:
    """Every heading of a markdown file, with and without trailing code spans."""
    names: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        full = _normalize(m.group(1))
        names.add(full)
        bare = _strip_trailing_code(full)
        if bare:
            names.add(bare)
    return names


def load_headings(paths: list[str]) -> set[str] | None:
    """Headings across the readable rule files; None when none was readable."""
    found: set[str] | None = None
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        found = (found or set()) | heading_names(text)
    return found


def cited_names(text: str, prefixes: tuple[str, ...]) -> list[str]:
    """The remainder of every citation line in `text`."""
    names: list[str] = []
    for line in text.splitlines():
        body = line.strip().lstrip(_LINE_DECOR)
        for prefix in prefixes:
            if body.startswith(prefix):
                rest = body[len(prefix):].strip(_EMPHASIS)
                if rest:
                    names.append(rest)
                break
    return names


def _candidates(name: str) -> list[str]:
    parts = [name, *_NAME_SPLIT_RE.split(name), *name.split(",")]
    out: list[str] = []
    for part in parts:
        part = _normalize(part)
        out += [part, _strip_trailing_code(part).strip(_QUOTES)]
    return [c for c in out if c]


def has_valid_citation(text: str, prefixes: tuple[str, ...],
                       headings: set[str] | None) -> bool:
    """A citation line naming a real heading, or any citation when no rule
    file could be read to check it against."""
    for name in cited_names(text, prefixes):
        if headings is None:
            return True
        if any(c in headings for c in _candidates(name)):
            return True
    return False


def _content_blocks(ev: dict) -> list[dict]:
    msg = ev.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _role(ev: dict) -> str:
    msg = ev.get("message")
    return str(msg.get("role") or "") if isinstance(msg, dict) else ""


def window_text(events: list[dict], tool_use_id: str) -> str | None:
    """Assistant text written since the previous tool call, or None when the
    current tool_use is not in `events`."""
    main = [ev for ev in events if not ev.get("isSidechain")]
    current = None
    for i in range(len(main) - 1, -1, -1):
        if _role(main[i]) != "assistant":
            continue
        if any(b.get("type") == "tool_use" and b.get("id") == tool_use_id
               for b in _content_blocks(main[i])):
            current = i
            break
    if current is None:
        return None
    msg_id = (main[current].get("message") or {}).get("id")

    def same_message(ev: dict) -> bool:
        return bool(msg_id) and (ev.get("message") or {}).get("id") == msg_id

    batch_ids = {tool_use_id} | {
        str(b.get("id") or "") for ev in main if same_message(ev)
        for b in _content_blocks(ev) if b.get("type") == "tool_use"}
    texts: list[str] = []
    for i in range(current, -1, -1):
        ev = main[i]
        role = _role(ev)
        blocks = _content_blocks(ev)
        if role == "assistant":
            for b in reversed(blocks):
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    texts.append(b["text"])
                elif (b.get("type") == "tool_use"
                      and str(b.get("id") or "") not in batch_ids):
                    return "\n".join(reversed(texts))
        elif role == "user":
            own_results = blocks and all(
                b.get("type") == "tool_result" and b.get("tool_use_id") in batch_ids
                for b in blocks)
            if not own_results:
                break
    return "\n".join(reversed(texts))


def load_events(transcript_path: str) -> list[dict] | None:
    try:
        lines = tail_lines(transcript_path, TRANSCRIPT_SCAN_LINES, strict=True)
    except TranscriptReadError:
        return None
    events: list[dict] = []
    for line in lines:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def advisory_text(tool_name: str, prefixes: tuple[str, ...],
                  checked: bool, strict: bool) -> str:
    grade = "ASK" if strict else "ADVISORY"
    prefix = prefixes[0]
    source = ("a heading in the loaded rule file" if checked
              else "a section name (no rule file was readable to check it)")
    return (
        f"[{HOOK_NAME}] {grade}: {tool_name} is a mutating call, and the text "
        f"since the previous tool call carries no citation line.\n"
        f"  Before the call, write a line `{prefix} <section>` naming "
        f"{source} that governs it. If you cannot name it, re-read the rule "
        f"file first.\n"
        f"  Opt-out: {BYPASS_ENV}=1\n"
        f"  Reference: hooks/advisory-nudge/{HOOK_NAME}/spec.md"
    )


@fail_open
def main() -> int:
    if os.environ.get(BYPASS_ENV, "").strip():
        return 0
    payload = read_payload()
    if payload is None:
        return 0
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict) or not is_mutating_call(tool_name, tool_input):
        return 0
    tool_use_id = str(payload.get("tool_use_id") or "")
    transcript_path = str(payload.get("transcript_path") or "")
    if not tool_use_id or not transcript_path:
        return 0
    events = load_events(transcript_path)
    if events is None:
        return 0
    text = window_text(events, tool_use_id)
    if text is None:
        return 0

    prefixes = _prefixes()
    headings = load_headings(_rule_files(str(payload.get("cwd") or "")))
    if has_valid_citation(text, prefixes, headings):
        return 0

    strict = os.environ.get(STRICT_ENV, "").strip() == "1"
    message = advisory_text(tool_name, prefixes, headings is not None, strict)
    sys.stderr.write(message + "\n")
    if strict:
        emit_decision("ask", message)
    else:
        emit_additional_context(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
