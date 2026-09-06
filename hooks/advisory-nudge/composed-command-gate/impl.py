#!/usr/bin/env python3
"""PreToolUse advisory: gate composed `$` command lines in published evidence.

Issue #1117. Every existing gate looks at the *output* pasted into an
external-write body, or at the identifiers around it. None asks whether the
`$` command line sitting above that output was ever actually executed. The
dangerous shape is the one where the output is genuine and only the command
line was tidied afterwards to fit — a probe run three or four times, with the
version you *meant* to run written above the output of a different run.

Two adjacent hooks deliberately do not cover this axis:

  - `source-citation-probe-gate` strips fenced blocks in preprocessing — the
    `$` line's home is outside its scan by design (its spec says so).
  - `external-write-falsify-check`'s author-exempt Check 2 does read code
    blocks, but hunts *identifiers* (CLI flags, labels, `schema.table`) and
    clears on a 3-entry allowlist of verification traces.

This hook extracts `$ `-prefixed lines from the body's fenced blocks and
compares them against Bash `tool_input.command` values in the recent
transcript. Two tiers:

  T1 (non-shell): the line is not a shell command at all — `$ safe_tokenize('x')`,
      function-call syntax where a binary name belongs. No transcript needed;
      an unexecutable line could not have been transcribed from anything.
  T2 (unmatched): a shell-shaped line whose head binary + token overlap finds
      no counterpart in the transcript.

Precision is the whole ballgame here: an advisory that fires on honest bodies
gets ignored, and then the hook is worse than absent. So the design errs low —
several documented escape hatches clear a line before T2 is ever evaluated
(placeholder / env-var substitution, `[transcribed]`, absent transcript), and
the overlap threshold clears generously.

Exits 0 by default — advisory, not block. Set
`PRAXIS_COMPOSED_COMMAND_STRICT=1` (literal "1" only) for a hard block.

Body extraction is shared with `source-citation-probe-gate` and
`external-write-falsify-check` via `_lib/_external_write_body.py`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    REJECTION_PHRASE,
    TRANSCRIPT_SCAN_LINES,
    TranscriptReadError,
    tail_lines,
)
from _external_write_body import (  # type: ignore[import-not-found]  # noqa: E402
    extract_gh_body as _extract_gh_body,
    extract_mcp_body as _extract_mcp_body,
    is_gh_external_write as _is_gh_external_write,
    is_mcp_external_write as _is_mcp_external_write,
)


# ---------------------------------------------------------------------------
# Fenced-block extraction
# ---------------------------------------------------------------------------

# A fence opens on 3+ backticks or 3+ tildes at line start (up to 3 leading
# spaces, CommonMark). It closes on the same character with at least the
# opening run length and no trailing content. A shorter run inside a longer
# fence is therefore content, which is what makes nested fences work.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

# The prompt line. The space after `$` is load-bearing: it separates a shell
# prompt from `$VAR` / `$(...)` expansions that start a line inside output.
_PROMPT_RE = re.compile(r"^\s*\$ +(\S.*)$")

_TRANSCRIBED_TOKEN = "[transcribed]"


def _fenced_lines(body: str) -> list[str]:
    """Return the lines living inside paired fenced blocks.

    An unclosed fence contributes nothing — a body still mid-composition is
    not evidence anyone can act on, and scanning it would fire on drafts.
    """
    collected: list[str] = []
    pending: list[str] = []
    fence: str | None = None
    opted_out = False
    for line in body.splitlines():
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                opted_out = _TRANSCRIBED_TOKEN in m.group(2)
                pending = []
            continue
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
            if not opted_out:
                collected.extend(pending)
            fence = None
            pending = []
            continue
        pending.append(line)
    return collected


def _continues(text: str) -> tuple[bool, str]:
    """Split a line into (is-continuation, body without the escaping backslash).

    Only an odd run of trailing backslashes continues the line; an even run is
    literal backslashes and the command ends there. Collapsing an even run
    welds the next `$` line onto this one, and the welded line is then judged
    on the first command's head — so a composed line hiding behind a preceding
    transcribed one would never be examined.
    """
    stripped = text.rstrip()
    run = len(stripped) - len(stripped.rstrip("\\"))
    if run % 2 == 0:
        return False, stripped
    return True, stripped[:-1].strip()


def _prompt_commands(lines: list[str]) -> list[str]:
    """Pull `$ `-prefixed commands out of block lines, joining continuations."""
    commands: list[str] = []
    buffer: str | None = None
    for line in lines:
        if buffer is not None:
            more, body = _continues(line.strip())
            buffer += " " + body
            if not more:
                commands.append(buffer)
                buffer = None
            continue
        m = _PROMPT_RE.match(line)
        if not m:
            continue
        text = m.group(1).strip()
        if _TRANSCRIBED_TOKEN in text:
            continue
        more, body = _continues(text)
        if more:
            buffer = body
        else:
            commands.append(body)
    if buffer is not None:
        commands.append(buffer)
    return commands


# ---------------------------------------------------------------------------
# Tiering and normalization
# ---------------------------------------------------------------------------

# `safe_tokenize('...')` — function-call syntax where a binary name belongs.
# Nothing shaped like this can have been run at a shell, so no transcript
# lookup is needed to know it was composed.
_NON_SHELL_HEAD_RE = re.compile(r"^[A-Za-z_][\w.]*\(")

# A line carrying an unexpanded variable or an angle-bracket placeholder is a
# *documented* legitimate mismatch: the redaction rule tells authors to move a
# secret into an env var and rerun, or substitute a placeholder and say so.
# Firing on those would punish the honest path.
_SUBSTITUTION_RE = re.compile(r"\$\{?[A-Za-z_]\w*\}?|<[A-Za-z_][\w .-]*>")

_ENV_PREFIX_RE = re.compile(r"^(?:env\s+)?(?:[A-Za-z_]\w*=\S*\s+)+|^env\s+")

# Keep the separators so a segment knows what preceded it: only `||`'s right
# side is skipped as provenance (see `_segments`).
_SEGMENT_SPLIT_RE = re.compile(r"(&&|\|\||[|;\n])")

# Only an ODD run of backslashes continues a Bash line. `foo \\` + newline is a
# literal backslash followed by a real separator, and collapsing it welds two
# commands into one — enough to hide a `gh pr comment` from the tokenizer's
# command-start walk entirely. The lookbehind anchors the run's start; the
# captured pairs survive, only the final `\`+newline becomes a space.
_LINE_CONTINUATION_RE = re.compile(r"(?<!\\)((?:\\\\)*)\\\n")


def _join_continuations(command: str) -> str:
    return _LINE_CONTINUATION_RE.sub(r"\1 ", command)
_QUOTE_CHARS = str.maketrans("", "", "'\"")

# Clearing needs a head-binary match plus this much operand overlap. The value
# is a deliberate under-fire: a pasted line is routinely a subset or superset
# of what ran (a `| head` added, a path shortened), and every one of those is
# honest. It has to take more than one differing operand to fire.
_OVERLAP_THRESHOLD = 0.6


def _segments(command: str, executed_only: bool = False) -> list[tuple[str, frozenset[str]]]:
    """Split a command into per-segment `(head, operands)` pairs.

    A transcript command is routinely a compound — `cd /repo && grep ...`, a
    newline-separated `cd` then the real work, `env FOO=1 grep ...`, a pipe
    into `head`. Normalizing the whole string leaves `cd` or `env` as the head
    and the `grep` that actually ran never matches anything, so a *genuinely*
    transcribed line reads as composed. Segmenting is what keeps that from
    being the hook's dominant output.

    Operands exclude the head and every `-flag`: those are the tokens two
    unrelated invocations of the same binary share, so counting them makes a
    swapped search term look like a match. What discriminates one `grep` run
    from another is what it was pointed at.
    """
    out: list[tuple[str, frozenset[str]]] = []
    parts = _SEGMENT_SPLIT_RE.split(_join_continuations(command))
    # split() with a capturing group yields [seg, sep, seg, sep, ...].
    preceding_sep = ""
    for index, raw in enumerate(parts):
        if index % 2:
            preceding_sep = raw
            continue
        # `A || B` runs B only when A failed, so B is the one branch a
        # transcript cannot vouch for — `true || grep ...` records a `grep`
        # that never ran. `&&` stays in: `cd /repo && grep ...` is the shape
        # the segmenting exists to match, and treating it as unexecuted would
        # bring back the false positives it was added to remove.
        if executed_only and preceding_sep == "||":
            continue
        text = _ENV_PREFIX_RE.sub("", raw.strip())
        tokens = [t for t in text.translate(_QUOTE_CHARS).split() if t]
        if not tokens:
            continue
        operands = frozenset(t for t in tokens[1:] if not t.startswith("-"))
        out.append((tokens[0].rsplit("/", 1)[-1], operands))
    return out


def _primary(command: str) -> tuple[str, frozenset[str]] | None:
    """The segment a published line is *about* — the first that is not a `cd`.

    `$ cd /repo && grep ...` is a claim about the grep; matching on the `cd`
    would clear it against any transcript command that also changed directory.
    """
    for head, operands in _segments(command):
        if head != "cd":
            return head, operands
    return None


# A tool_result carrying one of these was never executed: the harness or a
# PreToolUse hook refused the call, or the user declined it. A command that
# ran and merely exited non-zero carries its own stderr instead, so none of
# these markers can strip a legitimate probe out of the provenance set.
_NEVER_RAN_MARKERS = (
    "<tool_use_error>",
    "PreToolUse:",
    REJECTION_PHRASE,
)


def _transcript_commands(transcript_path: str) -> list[str] | None:
    """Executed Bash commands from the last N JSONL lines; None when unreadable.

    A missing or unreadable transcript and a genuinely empty one are
    different answers: only the last is "this session ran nothing"; the
    others are "no oracle", and treating them as an empty provenance set
    turns every published line into an advisory. `tail_lines(strict=True)`
    keeps them apart in one open — it raises for the first and returns `[]`
    for the last — where the former probe-then-read left a window in which
    a file that passed the probe was gone by the read (issue #1279).

    Calls whose `tool_result` says they never ran — hook-blocked, denied,
    user-rejected — are dropped. Admitting them would let a command the
    author *attempted* and never executed clear the very line this gate
    exists to catch.
    """
    if not transcript_path:
        return None
    try:
        lines = tail_lines(transcript_path, TRANSCRIPT_SCAN_LINES, strict=True)
    except TranscriptReadError:
        return None

    by_id: dict[str, str] = {}
    order: list[str] = []
    blocked: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message") or {}
        if not isinstance(msg, dict):
            continue
        for block in (msg.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                inp = block.get("input") or {}
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                # A tool_use with no id cannot be correlated to a result, so
                # it gets a key nothing will ever mark blocked — dropping it
                # instead would silently shrink the provenance set.
                use_id = block.get("id")
                if not isinstance(use_id, str):
                    use_id = f"anon-{len(order)}"
                if isinstance(cmd, str) and cmd.strip():
                    by_id[use_id] = cmd
                    order.append(use_id)
            elif block.get("type") == "tool_result" and block.get("is_error") is True:
                use_id = block.get("tool_use_id")
                if isinstance(use_id, str) and _never_ran(block.get("content")):
                    blocked.add(use_id)
    return [by_id[i] for i in order if i not in blocked]


def _never_ran(content) -> bool:
    """True when a tool_result's text carries a refusal/block marker."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    else:
        return False
    return any(marker in text for marker in _NEVER_RAN_MARKERS)


def _is_transcribed(published: str, ran: list[tuple[str, frozenset[str]]]) -> bool:
    primary = _primary(published)
    if primary is None:
        return True
    head, operands = primary
    for ran_head, ran_operands in ran:
        if head != ran_head:
            continue
        # A bare `$ pwd` has nothing to discriminate on — the head match is
        # all the evidence available, and it is enough.
        if not operands:
            return True
        if len(operands & ran_operands) / len(operands) >= _OVERLAP_THRESHOLD:
            return True
    return False


def _findings(body: str, transcript_path: str) -> list[tuple[str, str]]:
    """Return (tier, command) pairs worth an advisory."""
    published = _prompt_commands(_fenced_lines(body))
    if not published:
        return []

    non_shell = [c for c in published if _NON_SHELL_HEAD_RE.match(c)]
    findings = [("non-shell", c) for c in non_shell]

    # Arm B needs a transcript. Without one the comparison has no oracle at
    # all, and an advisory would carry no information — stay silent.
    commands = _transcript_commands(transcript_path)
    if commands is None:
        return findings

    ran = [seg for c in commands for seg in _segments(c, executed_only=True)]
    for cmd in published:
        if cmd in non_shell or _SUBSTITUTION_RE.search(cmd):
            continue
        if not _is_transcribed(cmd, ran):
            findings.append(("unmatched", cmd))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

ADVISORY_MESSAGE = (
    "REMINDER (External-Surface Write / Composed Command Line): the body's "
    "fenced blocks carry `$` command lines with no counterpart in this "
    "session's Bash calls ({samples}).\n"
    "Every `$` block is a transcription, never a composition — copy the "
    "command line from the invocation that produced the output you pasted. "
    "Output being genuine does not make the line above it genuine: the "
    "dangerous case is a probe run several times where the pasted line is "
    "the version you meant to run.\n"
    "If the mismatch is legitimate — you moved a literal into an env var and "
    "reran, or substituted a placeholder and said so — that shape already "
    "clears; otherwise rerun and paste what actually ran, or mark the line "
    "`[transcribed]` after checking it against the call it came from.\n"
    "Set PRAXIS_COMPOSED_COMMAND_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)


@fail_open
def main() -> int:
    payload = read_payload()
    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        return 0
    transcript_path = payload.get("transcript_path", "") or ""

    all_bodies: list[str] = []
    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if not isinstance(command, str) or not command.strip():
            return 0
        tokens = safe_tokenize(_join_continuations(command))
        if not tokens:
            return 0
        for argv in iter_command_starts(tokens):
            if _is_gh_external_write(argv):
                candidate = _extract_gh_body(argv)
                if candidate is not None:
                    all_bodies.append(candidate)
    elif _is_mcp_external_write(tool_name):
        mcp_body = _extract_mcp_body(tool_input)
        if mcp_body:
            all_bodies.append(mcp_body)
    else:
        return 0

    if not all_bodies:
        return 0

    findings = _findings("\n".join(all_bodies), transcript_path)
    if not findings:
        return 0

    seen = list(dict.fromkeys(f"{tier}: {cmd[:70]}" for tier, cmd in findings))
    sys.stderr.write(ADVISORY_MESSAGE.format(samples=" | ".join(seen[:3])))
    if os.environ.get("PRAXIS_COMPOSED_COMMAND_STRICT") == "1":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
