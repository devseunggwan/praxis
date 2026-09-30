#!/usr/bin/env python3
"""PreToolUse advisory: probe-gate code-defect claims on their call site.

Issue #906. A bug report asserting that identified code is DEFECTIVE makes a
claim whose truth depends on how that code is *reached* — the caller decides
what state holds when the callee runs. Reading the callee proves what the
code does; it does not prove the outcome is wrong. Publishing the second
claim on the first claim's evidence is this hook's error class.

Motivating case (2026-07-31). An issue asserted that an account-deletion DAG
dropped data still reachable by other project members, citing the guard
function verbatim and attaching three SQL result sets. The caller revoked
every co-member's access in the same transaction *before* triggering the DAG,
so the asserted state never existed at deletion time. The issue was already
distributed to a downstream repo when a single `grep -rn <dag_name>` collapsed
it. The attached SQL was real — and measured a steady state that only mattered
if the claim was already true.

The sibling gates cannot see this shape:

  * `external-write-falsify-check` Check 1 scans for hypothesis hedging. A
    confidently-worded false claim carries none and passes by construction
    (the same blind spot Check 5 documents for over-claiming).
  * `source-citation-probe-gate` asks whether the CITED file was read. Here it
    was read, and read correctly — the unread file was its caller. That gate
    clears on exactly the evidence this one must reject.

Firing requires two axes to co-occur, with no clearing arm present:

  A. defect assertion — a `fix(...)` title or bug label on the call, or a
     defect token in the body prose
  B. code identification — a source path or backtick symbol on the same line
     as a body-level axis-A token; a call-level signal also puts the title and
     the body's first paragraph in scope

Axis A alone is an ordinary bug report; the conjunction isolates the class.

Cleared by either arm:

  Arm A (in-body): a filled-in `Caller-probe: <command> → <output>` line,
      or the repository's `Caller chain verified:` line naming a search tool.
      Scaffold placeholders and empty post-arrow evidence do not count
      (anti-bypass ported from output-block-falsify-advisory PR #796).
  Arm B (transcript): a search-tool call — Bash `grep`/`rg`, or the `Grep`
      tool — whose pattern carries a cited symbol or path stem. A `Read` of
      the cited file deliberately does NOT clear: that is precisely the
      evidence the motivating case had.

Exits 0 by default — advisory, not block. Set `PRAXIS_CALLER_PROBE_STRICT=1`
(literal "1" only) to convert into a hard block (exit 2).
"""
from __future__ import annotations

import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _external_write_body import (  # type: ignore[import-not-found]  # noqa: E402
    extract_gh_body,
    is_gh_external_write,
)
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import iter_transcript  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
)

# The whole session is read. This gate's error class is
# long-investigation-specific: the call-site search that clears Arm B happens
# early, while the defect claim is published late — in #1538's sample one
# clearing search sat more than 4,000 JSONL lines before the write (#1542).
# Only tool_use lines can clear, so every other line is rejected before
# `json.loads` (see `iter_transcript`).
_PROBE_NEEDLE = '"tool_use"'


# ---------------------------------------------------------------------------
# Axis A — defect assertion
# ---------------------------------------------------------------------------

# Call-level signals: a conventional-commit fix title, or a bug label.
_FIX_TITLE_RE = re.compile(r"^fix\(", re.IGNORECASE)
_BUG_LABEL_RE = re.compile(r"\bbug\b", re.IGNORECASE)
_TITLE_FLAGS = frozenset({"-t", "--title"})
_LABEL_FLAGS = frozenset({"-l", "--label"})

# Body-level defect tokens. Deliberately narrow: each asserts a WRONG OUTCOME,
# not merely a described behavior. Broad words ("silently", "wrong", "issue")
# are excluded — they fire on ordinary prose and would swamp the signal.
_DEFECT_TOKENS_EN = (
    "fails to",
    "does not check",
    "never checks",
    "does not account",
    "incorrectly",
    "is a defect",
    "is a bug",
    "breaks when",
    "data loss",
)
_DEFECT_TOKENS_KO = (
    "결함",
    "버그",
    "잘못",
    "누락",
    "무시하",
    "보지 않",
    "놓치",
    "소실",
)


# ---------------------------------------------------------------------------
# Axis B — code identification
# ---------------------------------------------------------------------------

_SOURCE_PATH_RE = re.compile(
    r"(?<![\w/])([\w./-]*[\w-]\.(?:py|ts|tsx|js|jsx|go|rb|java|kt|rs|sql|sh))\b"
)
# Backtick symbols: snake_case / camelCase / dotted, >= 4 chars. Short spans
# (`id`, `dev`) are prose noise.
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_SYMBOL_RE = re.compile(r"^[A-Za-z_][\w.]{3,}$")

_URL_RE = re.compile(r"\w+://\S+")

# A commit SHA names a revision, not code whose caller decides anything; in
# #1538's sample a SHA was the only "blamed code" in two fires (#1542).
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _path_stem(path: str) -> str:
    """`analytics_backend/app/x/controller.py` → `controller`."""
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _flag_values(argv: list[str]) -> list[tuple[str, str]]:
    """(flag, value) for `--flag value` and `--flag=value` tokens."""
    out: list[tuple[str, str]] = []
    for i, tok in enumerate(argv):
        key, val = (tok.partition("=")[0], tok.partition("=")[2]) if "=" in tok else (tok, None)
        if val is None and i + 1 < len(argv):
            val = argv[i + 1]
        if val is not None:
            out.append((key, val))
    return out


def _axis_a_call_level(argv: list[str]) -> bool:
    """True if the gh call itself declares a defect (fix title / bug label)."""
    for key, val in _flag_values(argv):
        if key in _TITLE_FLAGS and _FIX_TITLE_RE.search(val.strip()):
            return True
        if key in _LABEL_FLAGS and _BUG_LABEL_RE.search(val):
            return True
    return False


def _title(argv: list[str]) -> str:
    return next((val for key, val in _flag_values(argv) if key in _TITLE_FLAGS), "")


def _defect_line_indices(lines: list[str]) -> list[int]:
    """Indices of body lines carrying a defect token."""
    hits: list[int] = []
    for i, raw in enumerate(lines):
        low = raw.lower()
        if any(tok in low for tok in _DEFECT_TOKENS_EN) or any(
            tok in raw for tok in _DEFECT_TOKENS_KO
        ):
            hits.append(i)
    return hits


def _cited_code(line: str) -> list[tuple[str, str]]:
    """Return (sample, clear_key) pairs for code cited on one line."""
    out: list[tuple[str, str]] = []
    stripped = _URL_RE.sub("", line)
    for m in _SOURCE_PATH_RE.finditer(stripped):
        path = m.group(1)
        out.append((path, _path_stem(path)))
    for span in _INLINE_CODE_RE.findall(stripped):
        span = span.strip()
        if _SYMBOL_RE.match(span) and not _COMMIT_SHA_RE.match(span):
            out.append((span, span.rsplit(".", 1)[-1]))
    return out


def _first_paragraph(lines: list[str]) -> list[str]:
    """Lines up to the first blank line after the body's first non-blank one."""
    out: list[str] = []
    for line in lines:
        if not line.strip():
            if out:
                break
            continue
        out.append(line)
    return out


def _detect(body: str, title: str, call_level_defect: bool) -> list[tuple[str, str]]:
    """Return (sample, clear_key) for code cited inside a defect assertion.

    A body-level token blames only code on its own line: within a ±3-line
    window, an evidence heading, a known-limits note or a retraction carrying
    `결함` blamed every path near it (#1542). A call-level signal (`fix(`
    title, bug label) is about the change as a whole, so it adds the title and
    the body's first paragraph — where the defect is stated — not the whole
    body, where it made every path anywhere count as blamed.
    """
    lines = body.splitlines()
    scoped = [lines[i] for i in _defect_line_indices(lines)]
    if call_level_defect:
        scoped = [title, *_first_paragraph(lines), *scoped]

    found: list[tuple[str, str]] = []
    for line in scoped:
        found.extend(_cited_code(line))
    # de-dup on clear_key, first sample wins
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for sample, key in found:
        if key in seen:
            continue
        seen.add(key)
        uniq.append((sample, key))
    return uniq


# ---------------------------------------------------------------------------
# Arm A — in-body caller probe
# ---------------------------------------------------------------------------

_PROBE_PREFIXES = ("Caller-probe:", "반증 프로브:")
# `block-pr-without-caller-evidence` makes every PR body carry this line; one
# that names a search tool is the same evidence as a Caller-probe line (#1542).
# `Caller chain verified: N/A — docs-only change` names none and clears nothing.
_CALLER_CHAIN_RE = re.compile(r"^Caller chain verified:.*\b(?:grep|rg|ag|ack)\b")
_PROBE_PLACEHOLDER_TOKENS = ("<command>", "<observed>", "<...>", "<output>")
_PROBE_ARROW_RE = re.compile(r"→|->")


def _has_valid_caller_probe(body: str) -> bool:
    """True if a body line is a filled-in `Caller-probe: <cmd> → <output>`.

    Both halves must be present. An arrow with nothing before it
    (`Caller-probe: → observed caller`) names no command, so it asserts a
    probe without citing one — the shape the gate exists to reject.
    """
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if any(tok in line for tok in _PROBE_PLACEHOLDER_TOKENS):
            continue
        if _CALLER_CHAIN_RE.match(line):
            return True
        prefix = next((p for p in _PROBE_PREFIXES if line.startswith(p)), None)
        if prefix is None:
            continue
        m = _PROBE_ARROW_RE.search(line)
        if not m:
            continue
        if not line[len(prefix):m.start()].strip():
            continue
        if line[m.end():].strip():
            return True
    return False


# ---------------------------------------------------------------------------
# Arm B — transcript search-tool probe
# ---------------------------------------------------------------------------

# Only cross-file SEARCH clears. Read/cat/sed/head open one known file, which
# is the evidence the motivating case already had.
_SEARCH_TOOL_RE = re.compile(r"\b(?:grep|rg|ag|ack)\b")


def _search_probes(transcript_path: str) -> list[str]:
    """Search-tool haystacks from the whole session.

    Collects Bash grep/rg command strings and `Grep` tool patterns. Read tool
    calls are deliberately NOT collected — see module docstring.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return []

    probes: list[str] = []
    for entry in iter_transcript(transcript_path, _PROBE_NEEDLE):
        msg = entry.get("message") or {}
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for block in (msg.get("content") or []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") or {}
            if not isinstance(inp, dict):
                continue
            name = block.get("name")
            if name == "Bash":
                cmd = inp.get("command", "")
                if isinstance(cmd, str) and _SEARCH_TOOL_RE.search(cmd):
                    probes.append(cmd)
            elif name == "Grep":
                pattern = inp.get("pattern", "")
                if isinstance(pattern, str) and pattern.strip():
                    probes.append(pattern)
    return probes


def _is_caller_probed(clear_key: str, probes: list[str]) -> bool:
    return any(clear_key in p for p in probes)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

ADVISORY_MESSAGE = (
    "REMINDER (External-Surface Write / Caller Probe): body asserts a code "
    "defect citing {samples} with no call-site search found in this "
    "session's transcript.\n"
    "Reading the cited code proves what it DOES; whether that outcome is "
    "wrong depends on the caller, which decides the state the code runs "
    "under. Attached query output is not a substitute — evidence gathered on "
    "the assumption the claim is true cannot disconfirm it.\n"
    "Run one search for the call site (grep -rn <symbol>) and either cite it "
    "inline (`Caller-probe: <command> → <output>`) or drop the defect "
    "framing.\n"
    "A Read of the cited file does NOT clear this gate — that is the exact "
    "evidence the motivating case already had (issue #906).\n"
    "Set PRAXIS_CALLER_PROBE_STRICT=1 to convert this advisory into a hard "
    "block (exit 2).\n"
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

    # One Bash command can carry several external writes. Each keeps its own
    # axis-A state and its own body — a `fix(` title on one write must not
    # make a sibling comment's incidental file mention read as a defect
    # citation, and the +-3-line window must not span a write boundary.
    writes: list[tuple[str, str, bool]] = []

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if not isinstance(command, str) or not command.strip():
            return 0
        command = command.replace("\\\n", " ")
        tokens = safe_tokenize(command)
        if not tokens:
            return 0
        for argv in iter_command_starts(tokens):
            if not is_gh_external_write(argv):
                continue
            candidate = extract_gh_body(argv)
            if candidate is not None:
                writes.append((candidate, _title(argv), _axis_a_call_level(argv)))
    else:
        return 0

    if not writes:
        return 0

    cited: list[tuple[str, str]] = []
    for body, title, call_level_defect in writes:
        # Arm A is per-write: a probe line in one body says nothing about a
        # claim published by another.
        if _has_valid_caller_probe(body):
            continue
        cited.extend(_detect(body, title, call_level_defect))

    if not cited:
        return 0

    # Arm B: per-citation transcript search scan.
    probes = _search_probes(transcript_path)
    unprobed = [sample for sample, key in cited if not _is_caller_probed(key, probes)]
    if not unprobed:
        return 0

    samples = ", ".join(list(dict.fromkeys(unprobed))[:3])
    sys.stderr.write(ADVISORY_MESSAGE.format(samples=samples))
    if os.environ.get("PRAXIS_CALLER_PROBE_STRICT") == "1":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
