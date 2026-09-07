#!/usr/bin/env python3
"""PreToolUse advisory: probe-gate source citations in external-write bodies.

Issue #830. Source-fact citations published to external surfaces (PR/issue
bodies and comments, Slack messages, Notion pages) are recall-prone: a
`file:line` reference, an exact call-syntax snippet, or a test-semantics
claim ("test_x asserts y") is frequently written from memory rather than
from a read-probe executed this session. Published wrong, these train
downstream readers (review bots, teammates) on fabricated specifics — the
highest-risk shape of the Information Accuracy rule's "checkmark without
citation" failure mode.

This hook detects three citation tiers in external-write bodies:

  T1. file:line references (`impl.py:42`, `hooks/foo/impl.py:42`,
      `impl.py#L42` anchor form)
  T2. exact call-syntax inside inline code spans (single backticks) —
      `foo(bar.baz)` — the weakest detector; fenced code blocks are
      deliberately excluded (they are external-write-falsify-check's
      author-exempt Check 2 territory)
  T3. test-semantics claims — `test_*` + assert/raise/expect within the
      same-sentence 80-char window

A citation is CLEARED (no advisory) when a probe basis is present:

  Arm A (in-body): `[verified]` token on the citation's own line, OR a
      valid `Probe: <command> → <output>` line anywhere in the body.
      Anti-bypass ported from output-block-falsify-advisory (PR #796):
      unfilled scaffold placeholders (`<command>` / `<observed>` / `<...>`
      / `<output>`) or empty evidence after the arrow do NOT count.
  Arm B (transcript, last 400 JSONL lines): a Read tool_use whose
      file_path basename matches the cited basename, or a read-tool Bash
      command (grep/rg/sed/cat/head/tail/awk/nl) containing it. T2 clears
      on the called function name appearing in a transcript Bash command
      or Read path; T3 clears on a pytest run / test-file basename.

Exits 0 by default — advisory, not block. Set
`PRAXIS_SOURCE_CITATION_STRICT=1` (literal "1" only) to convert into a
hard block (exit 2).

Body extraction (gh argv walk + MCP nested container/leaf walk) is shared
with external-write-falsify-check via `_lib/_external_write_body.py`
(extracted at the 3rd consumer per repo convention — issue #907).
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
from _transcript import TRANSCRIPT_SCAN_LINES, tail_lines  # type: ignore[import-not-found]  # noqa: E402


# Shared surface detection + body extraction now lives in
# `_lib/_external_write_body.py` (extracted at the 3rd consumer per repo
# convention — see that module's docstring). Aliased to the previous
# private names so call sites and tests stay unchanged.
from _external_write_body import (  # type: ignore[import-not-found]  # noqa: E402
    extract_gh_body as _extract_gh_body,
    is_gh_external_write as _is_gh_external_write,
)


# ---------------------------------------------------------------------------
# Citation detection
# ---------------------------------------------------------------------------

# URLs stripped BEFORE detection so `https://example.com:8080/x.py:1` shapes
# never reach the file:line regex.
_URL_RE = re.compile(r"\w+://\S+")

# Fenced code blocks are excluded from ALL tiers — code samples are
# external-write-falsify-check's author-exempt Check 2 territory. Paired
# fences only; an unclosed fence leaves its content scanned (accepted).
_FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)

# T1: file:line. Extension group must start with a letter (kills `v1.2:3`,
# `127.0.0.1:8080`, `1.5:1`); TLD denylist kills scheme-less `example.com:8080`.
_FILE_LINE_RE = re.compile(
    r"(?<![\w:/])[\w./-]*[\w-]\.([A-Za-z][A-Za-z0-9]{0,7}):\d{1,6}\b"
)
# T1 anchor form: impl.py#L42 (GitHub permalink fragment without URL scheme).
_FILE_ANCHOR_RE = re.compile(r"\b[\w./-]+\.([A-Za-z]\w{0,7})#L\d{1,6}\b")
_TLD_DENYLIST = frozenset({"com", "org", "net", "io", "co", "ai", "dev"})

# T2: call-syntax inside inline code spans. Requires a `.` or `[` inside the
# argument list so bare `foo()` / `foo(x)` prose mentions do not fire —
# weakest detector by design (see spec Known limits).
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_CALL_SYNTAX_RE = re.compile(r"\b([A-Za-z_]\w*)\([^()\n]*[.\[][^()\n]*\)")

# T3: test-semantics claim — test token + assert/raise/expect within the
# same-sentence 80-char window.
_TEST_SEMANTICS_RE = re.compile(
    r"\btest\w*\b[^.\n]{0,80}?\b(?:assert\w*|raises?|expect\w*)\b",
    re.IGNORECASE,
)

_VERIFIED_TOKEN = "[verified]"

# Anti-bypass (ported from output-block-falsify-advisory PR #796): a Probe:
# line still carrying scaffold placeholders, or with empty evidence after
# the arrow, does not count as probe basis.
_PROBE_PLACEHOLDER_TOKENS = ("<command>", "<observed>", "<...>", "<output>")
_PROBE_ARROW_RE = re.compile(r"→|->")


def _has_valid_probe_line(body: str) -> bool:
    """True if any body line is a filled-in `Probe: <cmd> → <output>` citation."""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("Probe:"):
            continue
        if any(tok in line for tok in _PROBE_PLACEHOLDER_TOKENS):
            continue
        m = _PROBE_ARROW_RE.search(line)
        if not m:
            continue
        if line[m.end():].strip():
            return True
    return False


def _citation_basename(match_text: str) -> str:
    """`hooks/foo/impl.py:42` / `impl.py#L42` → `impl.py`."""
    path_part = re.split(r"[:#]", match_text, maxsplit=1)[0]
    return path_part.rsplit("/", 1)[-1]


def _detect_citations(body: str) -> list[tuple[str, str, str]]:
    """Return uncleared-by-[verified] citations as (tier, sample, clear_key).

    tier ∈ {"file:line", "call-syntax", "test-semantics"}; clear_key is the
    token Arm B matches against the transcript (basename / function name /
    "" for test-semantics, which clears on any test probe).
    """
    citations: list[tuple[str, str, str]] = []
    body = _FENCED_BLOCK_RE.sub("", body)
    for raw_line in body.splitlines():
        line = _URL_RE.sub("", raw_line)
        if _VERIFIED_TOKEN in line:
            continue
        for m in _FILE_LINE_RE.finditer(line):
            if m.group(1).lower() in _TLD_DENYLIST:
                continue
            citations.append(("file:line", m.group(0), _citation_basename(m.group(0))))
        for m in _FILE_ANCHOR_RE.finditer(line):
            if m.group(1).lower() in _TLD_DENYLIST:
                continue
            citations.append(("file:line", m.group(0), _citation_basename(m.group(0))))
        for span in _INLINE_CODE_RE.findall(line):
            for m in _CALL_SYNTAX_RE.finditer(span):
                citations.append(("call-syntax", m.group(0), m.group(1)))
        for m in _TEST_SEMANTICS_RE.finditer(line):
            citations.append(("test-semantics", m.group(0), ""))
    return citations


# ---------------------------------------------------------------------------
# Transcript probe scan (Arm B)
# ---------------------------------------------------------------------------

_READ_TOOL_RE = re.compile(r"\b(?:grep|rg|sed|cat|head|tail|awk|nl)\b")
_TEST_PROBE_RE = re.compile(r"\bpytest\b|\btest_\w+\.\w+")


def _recent_probes(transcript_path: str) -> tuple[list[str], list[str]]:
    """Return (bash_commands, read_file_paths) from the last N JSONL lines.

    Extends external-write-falsify-check's `_recent_bash_commands` to also
    collect Read tool_use file_paths — a Read IS a read-probe for Arm B.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return [], []
    cmds: list[str] = []
    read_paths: list[str] = []
    for line in tail_lines(transcript_path, TRANSCRIPT_SCAN_LINES):
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
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for block in (msg.get("content") or []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") or {}
            if not isinstance(inp, dict):
                continue
            if block.get("name") == "Bash":
                cmd = inp.get("command", "")
                if isinstance(cmd, str) and cmd.strip():
                    cmds.append(cmd)
            elif block.get("name") == "Read":
                fp = inp.get("file_path", "")
                if isinstance(fp, str) and fp.strip():
                    read_paths.append(fp)
    return cmds, read_paths


def _is_probed(tier: str, clear_key: str, cmds: list[str], read_paths: list[str]) -> bool:
    """True if the transcript contains a read-probe covering this citation."""
    if tier == "file:line":
        basename = clear_key
        if any(fp.rsplit("/", 1)[-1] == basename for fp in read_paths):
            return True
        return any(_READ_TOOL_RE.search(cmd) and basename in cmd for cmd in cmds)
    if tier == "call-syntax":
        name = clear_key
        return any(name in cmd for cmd in cmds) or any(name in fp for fp in read_paths)
    # test-semantics: clears on any pytest run / test-file probe.
    if any(_TEST_PROBE_RE.search(cmd) for cmd in cmds):
        return True
    return any(fp.rsplit("/", 1)[-1].startswith("test_") for fp in read_paths)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

ADVISORY_MESSAGE = (
    "REMINDER (External-Surface Write / Source-Citation Probe): body cites "
    "source facts ({samples}) with no read-probe found in the recent "
    "transcript.\n"
    "file:line, exact call syntax, and test-semantics claims are "
    "recall-prone — re-read the cited site (Read / grep -n) before "
    "publishing, then cite inline (`Probe: <command> → <output>`) or append "
    "`[verified]` on the citation line after checking.\n"
    "This gate only sees external-write bodies — free conversational prose "
    "is NOT covered (known limit).\n"
    "Set PRAXIS_SOURCE_CITATION_STRICT=1 to convert this advisory into a "
    "hard block (exit 2).\n"
)


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed stdin

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
        command = command.replace("\\\n", " ")
        tokens = safe_tokenize(command)
        if not tokens:
            return 0
        for argv in iter_command_starts(tokens):
            if _is_gh_external_write(argv):
                candidate = _extract_gh_body(argv)
                if candidate is not None:
                    all_bodies.append(candidate)
    else:
        return 0

    if not all_bodies:
        return 0

    combined = "\n".join(all_bodies)

    citations = _detect_citations(combined)
    if not citations:
        return 0

    # Arm A: a filled-in Probe: line anywhere in the body clears everything.
    if _has_valid_probe_line(combined):
        return 0

    # Arm B: per-citation transcript probe scan.
    cmds, read_paths = _recent_probes(transcript_path)
    unprobed = [
        (tier, sample)
        for tier, sample, clear_key in citations
        if not _is_probed(tier, clear_key, cmds, read_paths)
    ]
    if not unprobed:
        return 0

    samples = ", ".join(list(dict.fromkeys(sample for _, sample in unprobed))[:3])
    sys.stderr.write(ADVISORY_MESSAGE.format(samples=samples))
    if os.environ.get("PRAXIS_SOURCE_CITATION_STRICT") == "1":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
