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
  Arm B (transcript, whole session): a Read tool_use whose file_path
      basename matches the cited basename, a read-tool Bash command
      (grep/rg/sed/cat/head/tail/awk/nl) containing it, or that command's
      output naming `<basename>:<line>`. T2 clears on the called function
      name appearing in a Bash command, a Read path or a read-tool
      command's output; T3 clears on a pytest run / test-file basename.

Exits 0 by default — advisory, not block. Set
`PRAXIS_SOURCE_CITATION_STRICT=1` (literal "1" only) to convert into a
hard block (exit 2).

Body extraction (gh argv walk + MCP nested container/leaf walk) is shared
with external-write-falsify-check via `_lib/_external_write_body.py`
(extracted at the 3rd consumer per repo convention — issue #907).
"""
from __future__ import annotations

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
from _transcript import iter_transcript  # type: ignore[import-not-found]  # noqa: E402


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


# Only lines carrying a tool call or a tool result can clear a citation, so
# every other line is rejected before `json.loads` (see `iter_transcript`).
_PROBE_NEEDLES = ('"tool_use"', '"tool_result"')

# The line number a T1 sample cites: `impl.py:42` / `impl.py#L42`.
_CITED_LINE_RE = re.compile(r"(?::|#L)(\d{1,6})$")


def _output_line_re(sample: str, basename: str) -> re.Pattern[str] | None:
    """How a read command's output names the cited line: `grep -n` prints
    `<path>/<basename>:<line>:`, and a context line `<basename>-<line>-`."""
    m = _CITED_LINE_RE.search(sample)
    if not m:
        return None
    return re.compile(rf"(?<![\w.-]){re.escape(basename)}[:-]{m.group(1)}\b")


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def _clears_by_command(tier: str, clear_key: str, cmd: str) -> bool:
    if tier == "file:line":
        return bool(_READ_TOOL_RE.search(cmd)) and clear_key in cmd
    if tier == "call-syntax":
        return clear_key in cmd
    return bool(_TEST_PROBE_RE.search(cmd))


def _clears_by_read_path(tier: str, clear_key: str, path: str) -> bool:
    basename = path.rsplit("/", 1)[-1]
    if tier == "file:line":
        return basename == clear_key
    if tier == "call-syntax":
        return clear_key in path
    return basename.startswith("test_")


def _clears_by_output(tier: str, clear_key: str, line_re: re.Pattern[str] | None, text: str) -> bool:
    if tier == "file:line":
        return line_re is not None and bool(line_re.search(text))
    if tier == "call-syntax":
        return clear_key in text
    return False


def _probed_citations(transcript_path: str, citations: list[tuple[str, str, str]]) -> set[int]:
    """Indices of `citations` that a read-probe anywhere in the session covers.

    The whole session is scanned, not a tail window: a body is routinely
    written long after the investigation that read what it cites, and a
    400-line tail missed that read in 13 of 16 sampled fires (issue #1541).

    A read command's output counts too: `grep -rn <symbol> <dir>` reads the
    cited line although the file name appears only in what it printed. Only
    the result of a read-tool call is consulted, paired to it by id, so the
    output of an unrelated command clears nothing.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return set()
    pending = {
        i: (tier, key, _output_line_re(sample, key) if tier == "file:line" else None)
        for i, (tier, sample, key) in enumerate(citations)
    }
    read_call_ids: set[str] = set()

    for entry in iter_transcript(transcript_path, _PROBE_NEEDLES):
        msg = entry.get("message") or {}
        if not isinstance(msg, dict):
            continue
        for block in (msg.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and msg.get("role") == "assistant":
                inp = block.get("input") or {}
                if not isinstance(inp, dict):
                    continue
                if block.get("name") == "Bash":
                    cmd = inp.get("command", "")
                    if not isinstance(cmd, str) or not cmd.strip():
                        continue
                    if _READ_TOOL_RE.search(cmd) and isinstance(block.get("id"), str):
                        read_call_ids.add(block["id"])
                    for i, (tier, key, _) in list(pending.items()):
                        if _clears_by_command(tier, key, cmd):
                            del pending[i]
                elif block.get("name") == "Read":
                    fp = inp.get("file_path", "")
                    if not isinstance(fp, str) or not fp.strip():
                        continue
                    for i, (tier, key, _) in list(pending.items()):
                        if _clears_by_read_path(tier, key, fp):
                            del pending[i]
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in read_call_ids:
                text = _result_text(block.get("content"))
                for i, (tier, key, line_re) in list(pending.items()):
                    if text and _clears_by_output(tier, key, line_re, text):
                        del pending[i]
        if not pending:
            break
    return set(range(len(citations))) - set(pending)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

ADVISORY_MESSAGE = (
    "REMINDER (External-Surface Write / Source-Citation Probe): body cites "
    "source facts ({samples}) with no read-probe found in this session's "
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
    probed = _probed_citations(transcript_path, citations)
    unprobed = [
        (tier, sample)
        for i, (tier, sample, _) in enumerate(citations)
        if i not in probed
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
