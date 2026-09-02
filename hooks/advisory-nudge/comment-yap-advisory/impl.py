#!/usr/bin/env python3
"""PreToolUse(Edit|Write) advisory: unanchored comment sprawl ("yap").

Issue #1141. `yap` names a specific shape of AI slop — a comment that says a
great deal and informs about nothing. A reasoning model fills context with
detail to predict better; the defect is that the monologue leaks out of the
model and into the generated code as comments. Named shapes: a 30-line comment
above a simple type, a 200-line explanation crammed above one function, 50
lines at the top of a file that explain nothing identifiable.

Comments have no compiler. A wrong type fails to run; a wrong comment runs
fine forever, so nothing in the toolchain — tests, types, lint, CI — ever
corrects yap. The only thing that catches it is a human reading the diff.

## Why length alone is not the signal

This repo is its own counterexample: `secret-print-redaction-advisory/impl.py`
opens with a 68-line module docstring, and it is one of the most useful things
in the file. A hook that fires on it would be pruned within one audit cycle.

What separates it from yap is not length but *anchoring*. A good long comment
cites issue numbers, names files and identifiers, carries tables, examples and
measured figures. Yap narrates. Anchor density is structural, cheap to count,
and lets the hook stay out of the taste judgment entirely — it reports the
shape and hands the five questions back to the author.

## Detection — 2-signal AND gate

Volume alone never fires; a run must also be unanchored.

  * **D1 disproportionate preamble** — a comment run >= `PRAXIS_YAP_PREAMBLE_LINES`
    attached (no blank line) to a code span of <= 3 lines, with < 2 anchors.
  * **D2 unanchored sprawl** — a comment run >= `PRAXIS_YAP_SPRAWL_LINES` whose
    anchor count is below 1 per 10 comment lines.

The file-header case needs no third detector: it is D2 evaluated at offset 0.

## Silent by construction

Unknown file extension (comment syntax is resolved from the extension, never
guessed — this is also what excludes `.md`/`.txt`/`.rst`, where every line is
prose and the hook would self-trigger on its own spec), license/SPDX headers,
generated-file markers, and runs that are majority code-shaped (commented-out
code is a real problem but a different one). A marker must be the first
non-whitespace token on its line, so trailing comments never form a run — yap
is block-shaped, and the rule sidesteps string-literal false positives without
a lexer.

## Cost

Runs on every Write and Edit, so the budget is a design constraint: fast
reject before any line work, a hard byte cap, one forward pass with no
whole-text regex, and a `str.find` line generator instead of `splitlines()` so
auxiliary memory is O(longest line) rather than O(file). Run state is counters
plus one bounded snippet; the run's text is never accumulated.

Advisory only — writes to stderr, exits 0. Never blocks.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterator, NamedTuple, Optional, Sequence

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "comment-yap-advisory"
_ROLE = "advisory-nudge"

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_DEFAULT_PREAMBLE_LINES = 12
_DEFAULT_SPRAWL_LINES = 25
_DEFAULT_MAX_BYTES = 256 * 1024

# D1's "small subject": the code span a preamble is attached to. A type alias
# or a one-line constant gives 1-2; a real function body's first paragraph
# runs past this.
_MAX_SUBJECT_SPAN = 3

# D1 is satisfied only by a near-total absence of anchors; D2 uses a ratio.
_D1_MAX_ANCHORS = 2
_D2_ANCHORS_PER_LINES = 10

# A run whose lines are majority code-shaped is commented-out code, not yap.
_CODE_SHAPE_SHARE = 0.6

# Bounded work: run length saturates, and anchor counting stops once no
# reachable run length could still fire D2.
_RUN_LINE_CAP = 4096
_ANCHOR_COUNT_CAP = _RUN_LINE_CAP // _D2_ANCHORS_PER_LINES + 1

_ADVISORY_LIMIT = 3
_SNIPPET_CHARS = 100


def _int_env(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back on any garbage."""
    try:
        value = int(os.environ[name])
    except (KeyError, ValueError):
        return default
    return value if value > 0 else default


# ---------------------------------------------------------------------------
# Comment syntax, resolved from the file extension
# ---------------------------------------------------------------------------


class _Syntax(NamedTuple):
    line_markers: tuple[str, ...]
    block: Optional[tuple[str, str]]
    docstrings: tuple[str, ...]


_HASH = _Syntax(("#",), None, ())
_HASH_DOC = _Syntax(("#",), None, ('"""', "'''"))
_SLASH = _Syntax(("//",), ("/*", "*/"), ())
_BLOCK_ONLY = _Syntax((), ("/*", "*/"), ())
_DASH = _Syntax(("--",), None, ())

# Extensions absent from this map return 0 before any scanning. That is the
# blast-radius bound, and it is what keeps prose files (`.md`, `.rst`, ...)
# out: every line there is "comment", so scanning them would fire on any
# document — including this hook's own spec.md.
_SYNTAX_BY_EXT: dict[str, _Syntax] = {}
for _ext in ("py", "pyi"):
    _SYNTAX_BY_EXT[_ext] = _HASH_DOC
for _ext in ("sh", "bash", "zsh", "rb", "pl", "pm", "yaml", "yml", "toml",
             "tf", "tfvars", "nix", "jl", "r", "ex", "exs"):
    _SYNTAX_BY_EXT[_ext] = _HASH
for _ext in ("c", "h", "cc", "cpp", "cxx", "hpp", "hh", "java", "go", "rs",
             "js", "jsx", "mjs", "cjs", "ts", "tsx", "swift", "kt", "kts",
             "scala", "php", "cs", "dart", "m", "mm", "proto", "groovy",
             "gradle", "scss", "less"):
    _SYNTAX_BY_EXT[_ext] = _SLASH
_SYNTAX_BY_EXT["css"] = _BLOCK_ONLY
for _ext in ("sql", "lua", "hs", "elm"):
    _SYNTAX_BY_EXT[_ext] = _DASH


def _syntax_for(file_path: str) -> Optional[_Syntax]:
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return _SYNTAX_BY_EXT.get(ext)


# ---------------------------------------------------------------------------
# Anchors — the discriminator between a long comment and a yap
# ---------------------------------------------------------------------------

# Each alternative is something a reader can check against the world: an
# issue, a URL, a named symbol or path, an executable example, a structured
# API section, or a measured figure. Narration matches none of them.
_ANCHOR_RE = re.compile(
    r"#\d+"
    r"|https?://"
    r"|`[^`]+`"
    r"|```"
    r"|>>>"
    r"|\b[\w.-]+/[\w./-]*\.\w+\b"
    r"|\b[\w.-]+(?:/[\w.-]+){2,}/?"
    # A quoted literal names a concrete value the reader can grep for. Only
    # paired double quotes (straight or typographic) count — an apostrophe
    # pair spans ordinary prose too readily to mean anything.
    r"|\"[^\"]{2,}\""
    r"|“[^”]{2,}”"
    # A flag or a placeholder names a concrete interface, which is why an
    # enumeration of command forms is documentation rather than narration.
    r"|--[A-Za-z][\w-]+"
    r"|<[A-Za-z_][\w-]*>"
    r"|\b(?:Args|Arguments|Returns|Raises|Yields|Attributes|Example|Examples)\s*:"
    r"|@(?:param|returns?|throws|type|see)\b"
    r"|:(?:param|returns?|rtype|raises)\b"
    r"|\b\d+(?:\.\d+)?\+?\s*(?:%|ms|kb|mb|gb|kib|mib|x|lines?|bytes?|rounds?|times?)\b",
    re.IGNORECASE,
)


def _is_anchored(content: str) -> bool:
    """True if the comment line carries something checkable.

    A table row counts and is tested separately — a `|`-delimited row is
    structure that no alternation above would catch cheaply.
    """
    return content.count("|") >= 2 or _ANCHOR_RE.search(content) is not None


# Commented-out code, anchored at the start of the comment's content so prose
# that merely mentions `=` or a call does not qualify.
_CODE_SHAPE_RE = re.compile(
    r"[;{}]\s*$"
    r"|^(?:def|class|import|from|const|let|var|function|return|if|for|while|"
    r"else|elif|switch|case|try|catch|except|package|public|private|@)\b"
    r"|^\w[\w.\[\]]*\s*=\s*\S"
    r"|^\w[\w.]*\s*\("
)

# Legally required or machine-emitted headers. Structurally unanchored, and
# neither is anybody's monologue.
_BOILERPLATE_RE = re.compile(
    r"SPDX-License-Identifier"
    r"|\bCopyright\b"
    r"|Licensed under"
    r"|@generated\b"
    r"|Code generated by"
    r"|DO NOT EDIT",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Line iteration
# ---------------------------------------------------------------------------


def _iter_lines(text: str) -> Iterator[str]:
    """Yield lines without materializing a list.

    `splitlines()` on the cap-sized payload would hold every line at once;
    here exactly one slice is live, so auxiliary memory is O(longest line).
    """
    start = 0
    length = len(text)
    while start <= length:
        nl = text.find("\n", start)
        if nl == -1:
            yield text[start:]
            return
        yield text[start:nl]
        start = nl + 1


# ---------------------------------------------------------------------------
# Run accumulation
# ---------------------------------------------------------------------------


class _Finding(NamedTuple):
    detector: str
    line: int
    run_lines: int
    anchors: int
    snippet: str


class _Run:
    """Counters for the comment run being accumulated. Never holds its text."""

    __slots__ = ("start", "docstring", "lines", "anchors", "code_shaped",
                 "boilerplate", "snippet")

    def __init__(self, start: int, docstring: bool = False) -> None:
        self.start = start
        self.docstring = docstring
        self.lines = 0
        self.anchors = 0
        self.code_shaped = 0
        self.boilerplate = False
        self.snippet = ""

    def add(self, content: str) -> None:
        if self.lines >= _RUN_LINE_CAP:
            # Every counter freezes together. Advancing `code_shaped` past a
            # frozen `lines` would drive the share above 1.0 and suppress the
            # run on arithmetic rather than on what it contains.
            return
        self.lines += 1
        if not content:
            # A bare `#` or ` *` separator: no content to anchor or shape.
            return
        if not self.snippet:
            self.snippet = content[:_SNIPPET_CHARS]
        if not self.boilerplate and _BOILERPLATE_RE.search(content):
            self.boilerplate = True
        if self.anchors < _ANCHOR_COUNT_CAP and _is_anchored(content):
            self.anchors += 1
        if _CODE_SHAPE_RE.search(content):
            self.code_shaped += 1

    @property
    def suppressed(self) -> bool:
        """True when the run is not prose the author wrote to be read."""
        return self.boilerplate or self.code_shaped >= self.lines * _CODE_SHAPE_SHARE


class _Opened(NamedTuple):
    delim: str
    is_block: bool


def _classify(
    stripped: str, syntax: _Syntax
) -> tuple[bool, str, Optional[_Opened]]:
    """Classify one non-blank line outside any open block or string.

    Returns `(is_comment, content, opened)`. `opened` names the delimiter that
    will close a multi-line comment the line started, or None when the comment
    ends on this line. `is_block` distinguishes a C-style `/* */` body — whose
    continuation lines carry a decorative leading `*` worth stripping — from a
    docstring, where a leading `*` is the author's own text.
    """
    for marker in syntax.line_markers:
        if stripped.startswith(marker):
            return True, stripped[len(marker):].strip(), None

    # A triple-quote only opens a docstring when it starts the line. A
    # continuation of `x = """...` is handled by the string tracking in
    # `_scan`, which runs before this and never reaches here.
    for delim in syntax.docstrings:
        if stripped.startswith(delim):
            rest = stripped[len(delim):]
            if delim in rest:
                return True, rest.split(delim, 1)[0].strip(), None
            return True, rest.strip(), _Opened(delim, False)

    if syntax.block is not None:
        opener, closer = syntax.block
        if stripped.startswith(opener):
            rest = stripped[len(opener):]
            if closer in rest:
                return True, rest.split(closer, 1)[0].strip(), None
            return True, rest.strip(), _Opened(closer, True)

    return False, "", None


def _string_opened(stripped: str, delims: Sequence[str]) -> Optional[str]:
    """Return the delimiter a code line leaves open, if any.

    A multi-line string assignment closes on a bare delimiter line, which
    `_classify` would otherwise read as a docstring opener and then score the
    following code as one enormous comment run. Getting this wrong is not a
    missed finding but a fabricated one, so it is tracked rather than ignored.

    Occurrences nested inside the other quote style are skipped — a table of
    delimiters is a literal, not an opener, and counting it desynchronizes the
    scan for the rest of the file. The opener's own line shape is not
    constrained: a trailing backslash continuation is as common as a bare one.
    """
    for delim in delims:
        other = "'" if delim[0] == '"' else '"'
        if _unquoted_count(stripped, delim, other) % 2:
            return delim
    return None


def _unquoted_count(line: str, delim: str, other: str) -> int:
    """Count `delim` in `line`, skipping occurrences wrapped in `other`."""
    count = 0
    width = len(delim)
    index = line.find(delim)
    while index != -1:
        before = line[index - 1] if index else ""
        after = line[index + width: index + width + 1]
        if not (before == other and after == other):
            count += 1
        index = line.find(delim, index + width)
    return count


def _scan(text: str, syntax: _Syntax, preamble_lines: int, sprawl_lines: int) -> list[_Finding]:
    """Single forward pass. Emits at most `_ADVISORY_LIMIT` findings."""
    findings: list[_Finding] = []
    run: Optional[_Run] = None
    # A run that ended on a code line, with the code span measured so far.
    pending: Optional[tuple[_Run, int]] = None
    open_comment: Optional[_Opened] = None
    open_string: Optional[str] = None
    # True when the last code line ended mid-expression.
    prev_operand = False

    def close_pending() -> None:
        nonlocal pending
        if pending is None:
            return
        candidate, span = pending
        pending = None
        if (
            candidate.lines >= preamble_lines
            # A docstring documents its enclosing function, not the two
            # statements that happen to precede the first blank line, so the
            # span measured here says nothing about proportion. Docstrings
            # remain covered by D2.
            and not candidate.docstring
            and span <= _MAX_SUBJECT_SPAN
            and candidate.anchors < _D1_MAX_ANCHORS
            and not candidate.suppressed
            and len(findings) < _ADVISORY_LIMIT
        ):
            findings.append(
                _Finding("disproportionate preamble", candidate.start,
                         candidate.lines, candidate.anchors, candidate.snippet)
            )

    def close_run(next_is_code: bool) -> None:
        nonlocal run, pending
        if run is None:
            return
        candidate, run = run, None
        if (
            candidate.lines >= sprawl_lines
            and candidate.anchors * _D2_ANCHORS_PER_LINES < candidate.lines
            and not candidate.suppressed
            and len(findings) < _ADVISORY_LIMIT
        ):
            findings.append(
                _Finding("unanchored sprawl", candidate.start,
                         candidate.lines, candidate.anchors, candidate.snippet)
            )
        elif next_is_code:
            # Only a run that did not already fire is worth measuring for D1.
            pending = (candidate, 1)

    def count_code_line() -> None:
        """A code line ends any run, then extends the D1 subject span."""
        nonlocal pending
        if run is not None:
            close_run(next_is_code=True)
            return
        if pending is None:
            return
        candidate, span = pending
        pending = (candidate, span + 1)
        if span + 1 > _MAX_SUBJECT_SPAN:
            close_pending()

    for lineno, raw in enumerate(_iter_lines(text), 1):
        stripped = raw.strip()

        # Inside a multi-line string: neither comment nor a run boundary the
        # author wrote. Scored as code so it can close a run and fill a span.
        if open_string is not None:
            if open_string in stripped:
                open_string = None
            # The string's own body never leaves an expression open for the
            # line after it; a stale flag here would read the next docstring
            # as an operand.
            prev_operand = False
            count_code_line()
            continue

        if open_comment is not None:
            content = stripped
            if open_comment.delim in stripped:
                content = stripped.split(open_comment.delim, 1)[0]
                if open_comment.is_block:
                    content = content.lstrip("*")
                open_comment = None
            elif open_comment.is_block:
                content = content.lstrip("*")
            if run is None:
                run = _Run(lineno)
            run.add(content.strip())
            continue

        if not stripped:
            close_run(next_is_code=False)
            close_pending()
            continue

        # A docstring is a statement. When the previous code line ends mid-
        # expression the triple quote is an operand — test fixture data, a
        # `write_text(` argument — so it opens a string, never a comment run.
        operand = bool(syntax.docstrings) and prev_operand and stripped.startswith(syntax.docstrings)
        is_comment, content, opened = (False, "", None) if operand else _classify(stripped, syntax)

        if is_comment:
            close_pending()
            if run is None:
                run = _Run(lineno, docstring=stripped.startswith(syntax.docstrings))
            run.add(content)
            open_comment = opened
            continue

        open_string = _string_opened(stripped, syntax.docstrings)
        prev_operand = stripped.endswith(("(", "[", ",", "=", "+"))
        count_code_line()

        if len(findings) >= _ADVISORY_LIMIT:
            return findings

    close_run(next_is_code=False)
    close_pending()
    return findings


# ---------------------------------------------------------------------------
# Advisory text
# ---------------------------------------------------------------------------


def _advisory_text(file_path: str, findings: Sequence[_Finding]) -> str:
    lines = [
        f"[comment-yap-advisory] unanchored comment sprawl in {file_path}",
        "",
    ]
    for finding in findings:
        anchor_word = "anchor" if finding.anchors == 1 else "anchors"
        lines.append(
            f"  Detected: {finding.detector} — {finding.run_lines} comment lines, "
            f"{finding.anchors} {anchor_word} (line {finding.line})"
        )
        if finding.snippet:
            lines.append(f"    {finding.snippet}")
    lines += [
        "",
        "  Long is not the problem; unanchored is. A comment that earns its",
        "  length cites something checkable — an issue, a path, an identifier,",
        "  an example, a measured figure. Re-ask, then keep what survives:",
        "",
        "    1. What value does this comment actually provide?",
        "    2. What is not already clear from the code itself?",
        "    3. What does a future reader not need?",
        "    4. Is the explanation next to the code it explains?",
        "    5. Is a comment needed here at all?",
        "",
        "  Advisory only — nothing is blocked. Reference: issue #1141",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0
    tool_name = payload.get("tool_name") or ""
    if tool_name not in ("Write", "Edit"):
        return 0
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    file_path = tool_input.get("file_path") or ""
    if not isinstance(file_path, str) or not file_path:
        return 0
    syntax = _syntax_for(file_path)
    if syntax is None:
        return 0

    text = tool_input.get("content" if tool_name == "Write" else "new_string") or ""
    if not isinstance(text, str) or not text.strip():
        return 0
    if len(text) > _int_env("PRAXIS_YAP_MAX_BYTES", _DEFAULT_MAX_BYTES):
        # A payload this size is vendored or generated; a partial scan would
        # be a verdict about a fragment.
        return 0

    findings = _scan(
        text,
        syntax,
        _int_env("PRAXIS_YAP_PREAMBLE_LINES", _DEFAULT_PREAMBLE_LINES),
        _int_env("PRAXIS_YAP_SPRAWL_LINES", _DEFAULT_SPRAWL_LINES),
    )
    if findings:
        sys.stderr.write(_advisory_text(file_path, findings) + "\n")
        _record_advise(payload.get("session_id"), tool_name)
    return 0


def _record_advise(session_id: object, tool: str) -> None:
    """Record the advisory as a RICH `advise` fire (issue #740 single-event).

    This hook signals its verdict on stderr while exiting 0, so `@fail_open`'s
    coarse path — which reads only the exit code — records every engagement as
    `pass`. Left there, the hook joins the coarse-only set the prune audit's
    advisory-never-escalated axis excludes, and no ledger query could answer
    whether it had ever advised at all. A hook that cannot be measured cannot
    be scored, and scoring is what decides whether it stays.

    Only the advising path records: a silent pass is exactly what the coarse
    record already says, so writing a second one would inflate the fire count
    without adding a decision.

    `record_session_fire` coerces a missing `session_id` to `""`, and
    `aggregate_fires` only adds non-empty ids to its per-session set, so an
    absent id costs per-session attribution and never the decision itself.
    """
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, _fire_ledger.DECISION_ADVISE,
        session_id if isinstance(session_id, str) else "", tool,
    ):
        # Gated on the rich append actually landing: suppressing after a
        # failed one would drop the engagement from both streams, which is
        # worse than the coarse mislabel it exists to remove.
        _fire_ledger.suppress_coarse_duplicate()


if __name__ == "__main__":
    sys.exit(main())
