#!/usr/bin/env python3
"""PreToolUse advisory: warn when a Write/Edit artifact embeds an *exclusion
directive* whose stated verification is not backed by a cited probe.

Background (praxis issue #807):
  A committed, pushed artifact carried the line

      DELIBERATELY EXCLUDED (verified via HMS -- do NOT add these)

  The `verified via HMS` claim was never actually run, and both exclusion
  grounds were false. The migration shipped incomplete, and the surviving
  `do NOT add these` directive would have justified deleting the source data
  on the false premise "migration complete".

  The damaging property is that the content is *self-authored*: it is not an
  external quote, so sibling-grep or an external falsifier cannot catch it,
  and the self-written `verified` marker actively suppresses a later reader's
  re-verification. This is the structural enforcement layer for the global
  behavioral-contract rule "Information Accuracy -> Layer 2 -> Author-exempt
  verification trap", which failed prompt-layer retrieval three generations
  running.

Error class (deductive, NOT induced from the single `DELIBERATELY EXCLUDED`
instance -- see issue #807 "커버리지 도출 원칙"):

  "Self-authored content that excludes a target from an artifact while
   asserting a verification it did not actually perform (no cited probe)."

  Firing requires BOTH axes to co-occur within a small block window, with NO
  probe citation nearby:
    Axis A -- exclusion directive: an imperative ("do NOT add") or an
      intentful declarative ("deliberately excluded / omitted / skipped")
      telling a future reader an item is purposely absent.
    Axis B -- verification claim: a self-asserted grounding ("verified via",
      "confirmed with", "확인함", "검증됨") that would block re-verification.

  Axis A alone is legitimate far too often (rule prohibitions, API-usage
  comments, "what was NOT verified" PR sections), so the conjunction with a
  concrete verification claim is what isolates the error class. The free-form
  "근거 서술" (grounding narration) branch named in the issue is deliberately
  narrowed to the explicit verification-claim tokens: an arbitrary rationale
  sentence is not regex-detectable without an unacceptable false-positive
  rate (issue T0 compromise -- existence of a claim token is the proxy).

Exculpating probe citation:
  A `Probe: <command> -> <one-line output>` citation (the codex-review-wrap
  Step 5g format) within the same block window means the exclusion is backed
  by a real observation -> pass. Per issue T0 this is an *existence* check
  only; whether the cited probe actually supports the exclusion is a semantic
  judgment a regex cannot make.

False-positive surfaces excluded (issue #807 table):
  - Normative-document paths (CLAUDE.md / AGENTS.md / SKILL.md / spec.md ...):
    prohibitions are their whole purpose, so the path is skipped wholesale.
  - Test / fixture paths: exclusion strings there are test data, not artifacts.
  - Fenced code blocks, inline-code spans, and blockquote lines are masked
    before scanning, so an *illustrative example* of such a directive inside a
    doc does not fire. (Threat-model boundary: a genuine directive deliberately
    wrapped in a fence is not caught -- documented in spec.md, mirroring the
    shell-parser diminishing-returns lesson.)
  - A negated claim ("not verified", "검증하지 않") is not a verification claim.

Surface scope:
  Write `content` and Edit `new_string` only -- the file-content path that was
  the actual cause. Bash heredoc / `gh ... --body` writes (issue T1) are a
  deferred follow-up: covering them would be the third occurrence of the
  gh-body/heredoc extractor and should trigger a shared-helper refactor
  (DRY rule-of-three) rather than a third local copy in this PR.

Severity:
  Advisory (exit 0) by default -- the detection target is natural language and
  the initial false-positive rate is unmeasured; a hard block is unsafe before
  that measurement (issue T1). PRAXIS_EXCLUSION_PROBE_STRICT=1 escalates to a
  hard deny (exit 2).

Bypass / tuning:
  PRAXIS_EXCLUSION_PROBE_SKIP=1    -- disable entirely for this session
  PRAXIS_EXCLUSION_PROBE_STRICT=1  -- escalate advisory to hard deny (exit 2)

Fail-open:
  Any infrastructure error (malformed stdin, unexpected exception) exits 0.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

TARGET_TOOLS = frozenset({"Write", "Edit"})

# Proximity window: an exclusion directive and its verification claim count as
# co-occurring when they fall within +/- BLOCK_RADIUS lines of each other
# (covers same-line and same-paragraph forms like the cause instance).
BLOCK_RADIUS = 3

# ---------------------------------------------------------------------------
# Path-based skips (normative documents + test fixtures)
# ---------------------------------------------------------------------------

# Normative documents: prohibition IS their content, not a factual exclusion
# claim about an artifact. Skipped wholesale per issue #807.
_NORMATIVE_BASENAMES = frozenset({
    "claude.md",
    "agents.md",
    "skill.md",
    "spec.md",
    "ethos.md",
    "gemini.md",
    ".cursorrules",
})

# Test / fixture paths: exclusion strings there are test data. This also keeps
# the hook's own test suite from self-tripping.
_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|fixtures?)(?:/|$)"
    r"|(?:^|/)test_[^/]*$"
    r"|(?:^|/)conftest\.py$"
    r"|_test\.[A-Za-z0-9]+$"
    r"|\.(?:test|spec)\.[A-Za-z0-9]+$"
)


def _is_skipped_path(file_path: str) -> bool:
    base = os.path.basename(file_path).lower()
    if base in _NORMATIVE_BASENAMES:
        return True
    return bool(_TEST_PATH_RE.search(file_path))


# ---------------------------------------------------------------------------
# Non-content masking (fenced code, inline code, blockquotes)
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")


def _mask_inline_code(line: str) -> str:
    """Mask inline-code spans, preserving length.

    Handles multi-backtick delimiters (CommonMark): a span opens on a run of N
    backticks and closes on the next run of exactly N backticks, so a stray
    single backtick inside a ``double`` span does not terminate it. A run with
    no matching closer is left untouched (not a code span).
    """
    out = list(line)
    i = 0
    n = len(line)
    while i < n:
        if line[i] != "`":
            i += 1
            continue
        run = i
        while run < n and line[run] == "`":
            run += 1
        fence_len = run - i
        # look for a closing run of exactly fence_len backticks
        j = run
        while j < n:
            if line[j] == "`":
                close = j
                while close < n and line[close] == "`":
                    close += 1
                if close - j == fence_len:
                    for k in range(i, close):
                        out[k] = " "
                    i = close
                    break
                j = close
            else:
                j += 1
        else:
            i = run  # unmatched opener — skip past it
    return "".join(out)


def _mask_noncontent(text: str) -> str:
    """Blank out code blocks / inline code / blockquotes, preserving line count.

    Masked regions become runs of spaces (newlines kept) so line numbers and
    block-proximity windows are unaffected -- an illustrative directive shown
    inside a fence or quote never fires, but its surrounding prose is unchanged.
    """
    out_lines: list[str] = []
    in_fence = False
    fence_marker = ""
    fence_len = 0
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if m:
            token = m.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = token[0]  # ` or ~
                fence_len = len(token)
                out_lines.append(" " * len(line))
                continue
            # A closing fence must use the same char AND be at least as long as
            # the opening run (CommonMark): a shorter inner run stays content.
            if token[0] == fence_marker and len(token) >= fence_len:
                in_fence = False
                out_lines.append(" " * len(line))
                continue
            out_lines.append(" " * len(line))
            continue
        if in_fence:
            out_lines.append(" " * len(line))
            continue
        if re.match(r"^\s*>", line):  # blockquote line
            out_lines.append(" " * len(line))
            continue
        # inline-code spans -> spaces (keep length so column math is stable)
        out_lines.append(_mask_inline_code(line))
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Axis A -- exclusion directive
# ---------------------------------------------------------------------------

_AXIS_A_PATTERNS = [
    # EN directive imperatives
    r"do(?:es)?\s+not\s+(?:add|include|re-?add|reintroduce|put\s+back)",
    r"don'?t\s+(?:add|include|re-?add|reintroduce|put\s+back)",
    r"must\s+not\s+(?:add|include|be\s+added|be\s+included)",
    r"never\s+(?:add|re-?add|include|reintroduce)",
    # EN intentful declaratives
    r"(?:deliberately|intentionally|purposely|knowingly)\s+"
    r"(?:excluded?|omitted?|omit|skipp?ed|left\s+out|removed?|dropped?)",
    r"(?:excluded?|omitted?|skipp?ed|left\s+out|removed?)\s+"
    r"(?:on\s+purpose|intentionally|deliberately|purposely)",
    # KO directive
    r"추가하지\s*(?:말|마)",
    r"포함하지\s*(?:말|않)",
    r"제외\s*(?:대상|함|했|시킴|시켰|하였|됨|한)",
    r"(?:의도적|일부러|고의)(?:으?로)?\s*(?:제외|누락|생략|뺐|뺌|빼|배제)",
    r"누락\s*(?:시킴|시켰|함|했)",
]
_AXIS_A_RE = re.compile("|".join(f"(?:{p})" for p in _AXIS_A_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Axis B -- verification claim (with negation guard)
# ---------------------------------------------------------------------------

_AXIS_B_PATTERNS = [
    r"(?:verified|confirmed|checked|validated|cross-?checked|tested)\s+"
    r"(?:via|with|by|against|using|through)",
    r"확인\s*(?:함|했|완료|됨|결과)",
    r"검증\s*(?:함|했|완료|됨)",
    r"대조\s*완료",
]
_AXIS_B_RE = re.compile("|".join(f"(?:{p})" for p in _AXIS_B_PATTERNS), re.IGNORECASE)

# A verification claim is void only when the negation governs the verification
# verb itself — the negation may be separated from the verb by auxiliaries /
# adverbs ("has not been verified via", "was never actually confirmed with"),
# but NOT by another content word. This is what distinguishes a negated claim
# from an exclusion directive that merely contains a negation word ("do NOT
# add X, confirmed via Y" — here "not" governs "add", and the claim is live).
_EN_VERB = r"(?:verified|confirmed|checked|validated|cross-?checked|tested)\s+(?:via|with|by|against|using|through)"
# negation + (allowed aux/adverb filler)* + verification verb
_NEG_CLAIM_EN_RE = re.compile(
    r"(?:\b(?:not|never|without|cannot)\b|n't)"
    r"(?:\s+(?:been|being|yet|ever|actually|fully|really|properly|"
    r"independently|directly|so\s+far|as\s+of\s+yet))*"
    r"\s+" + _EN_VERB,
    re.IGNORECASE,
)
# Korean: negation bound to the verification verb (검증되지 않 / 확인하지 못 /
# 아직 검증 / 대조 안 됨). The Axis-B forms are the affirmative 확인함·검증됨·
# 대조 완료, so a negated claim reads 검증/확인/대조 + 하지/되지 + 않/못, or a
# leading 아직/못/안 directly before the verb.
_NEG_CLAIM_KO_RE = re.compile(
    r"(?:검증|확인|대조)\s*(?:하지|되지|한|된)?\s*(?:않|못)"
    r"|아직\s*(?:검증|확인|대조)"
    r"|(?:검증|확인|대조)\s*(?:안\s*(?:함|했|됨|해))"
)

# ---------------------------------------------------------------------------
# Probe citation (exculpating)
# ---------------------------------------------------------------------------

_PROBE_RE = re.compile(r"\bProbe:\s*\S", re.IGNORECASE)


def _negated_spans(window: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rx in (_NEG_CLAIM_EN_RE, _NEG_CLAIM_KO_RE):
        spans.extend((m.start(), m.end()) for m in rx.finditer(window))
    return spans


def _has_live_claim(window: str) -> bool:
    negated = _negated_spans(window)
    for m in _AXIS_B_RE.finditer(window):
        # The claim is live unless it falls inside a negated-claim construct.
        if not any(lo <= m.start() and m.end() <= hi for lo, hi in negated):
            return True
    return False


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------

def _scan(content: str) -> list[tuple[str, str]]:
    """Return de-duplicated (exclusion_phrase, claim_phrase) findings."""
    masked = _mask_noncontent(content)
    lines = masked.split("\n")
    findings: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for i, line in enumerate(lines):
        a = _AXIS_A_RE.search(line)
        if not a:
            continue
        lo = max(0, i - BLOCK_RADIUS)
        hi = min(len(lines), i + BLOCK_RADIUS + 1)
        window = "\n".join(lines[lo:hi])
        if _PROBE_RE.search(window):
            continue  # cited probe near the directive -> substantiated
        if not _has_live_claim(window):
            continue  # exclusion directive alone -> legitimate
        b = _AXIS_B_RE.search(window)
        key = (a.group(0).strip().lower(), (b.group(0).strip().lower() if b else ""))
        if key in seen:
            continue
        seen.add(key)
        findings.append((a.group(0).strip(), b.group(0).strip() if b else ""))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_ADVISORY_TMPL = (
    "[exclusion-probe-gate] {n} exclusion directive(s) with an uncited "
    "verification claim detected in {path}:\n"
    "{items}"
    "A self-authored 'excluded + verified via X' claim with no cited probe "
    "suppresses a later reader's re-verification -- exactly the "
    "Author-exempt verification trap.\n"
    "  - Add a probe citation near the directive, in the Step 5g format:\n"
    "        Probe: <command> -> <one-line output>\n"
    "  - Or, if the exclusion is not actually probe-backed, remove the "
    "'verified' claim so the uncertainty stays visible.\n"
    "Set PRAXIS_EXCLUSION_PROBE_STRICT=1 to convert this advisory to a hard "
    "block (exit 2).\n"
)

_DENY_TMPL = (
    "[exclusion-probe-gate] Write blocked: {n} exclusion directive(s) with an "
    "uncited verification claim in {path}:\n"
    "{items}"
    "\n"
    "A self-authored exclusion directive that asserts verification "
    "('verified via ...') without a cited probe is the Author-exempt "
    "verification trap (issue #807): the 'verified' marker blocks the next "
    "reader from re-checking a claim that may be false.\n"
    "\n"
    "To unblock, near each directive either:\n"
    "  a) cite the probe that backs the exclusion, in the Step 5g format:\n"
    "       Probe: <command> -> <one-line output>\n"
    "  b) remove the verification claim so the exclusion reads as unverified, OR\n"
    "  c) set PRAXIS_EXCLUSION_PROBE_SKIP=1 to disable the gate for this session.\n"
)


def _format_items(findings: list[tuple[str, str]]) -> str:
    out = []
    for excl, claim in findings[:5]:
        if claim:
            out.append(f"  - directive '{excl}' + claim '{claim}'\n")
        else:
            out.append(f"  - directive '{excl}'\n")
    return "".join(out)


def _resolve_scan_text(tool_name: str, tool_input: dict, file_path: str) -> str:
    """Return the text to scan.

    Write: the new content directly.
    Edit: the *post-edit* full file, so a directive that already lives in the
      file and an Edit that only adds the verification claim still co-occur
      (the two axes must be checked against the resulting file, not the
      new_string fragment alone). If the current file cannot be read, fall
      back to scanning new_string in isolation (fail-open, never crash).
    """
    if tool_name == "Write":
        return tool_input.get("content") or ""

    new_string = tool_input.get("new_string") or ""
    old_string = tool_input.get("old_string") or ""
    try:
        with open(file_path, encoding="utf-8") as fh:
            current = fh.read()
    except (OSError, ValueError):
        return new_string  # new file or unreadable — scan the fragment only
    if not old_string:
        return current + "\n" + new_string
    if tool_input.get("replace_all"):
        return current.replace(old_string, new_string)
    return current.replace(old_string, new_string, 1)


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed stdin

    tool_name = payload.get("tool_name", "") or ""
    if tool_name not in TARGET_TOOLS:
        return 0

    if os.environ.get("PRAXIS_EXCLUSION_PROBE_SKIP", "").strip() == "1":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = (tool_input.get("file_path") or "").strip()
    if not file_path:
        return 0
    if _is_skipped_path(file_path):
        return 0

    content = _resolve_scan_text(tool_name, tool_input, file_path)
    if not content:
        return 0

    findings = _scan(content)
    if not findings:
        return 0

    items = _format_items(findings)
    strict = os.environ.get("PRAXIS_EXCLUSION_PROBE_STRICT", "").strip() == "1"
    if strict:
        emit_decision(
            "deny",
            _DENY_TMPL.format(n=len(findings), path=file_path, items=items),
        )
        return 2
    sys.stderr.write(
        _ADVISORY_TMPL.format(n=len(findings), path=file_path, items=items)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
