#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: a sample statistic or a verdict-attached
measurement reaches a deliverable (`gh issue|pr create|comment`) with no
stated sample size above 1.

Why this exists (issue #949):

`feedback_exhaust_cheap_evidence_before_recommendation_surface` reached
recurrence 6 with `enforcement: none`, while the same rule also sat in
always-loaded `CLAUDE.md` (Falsification Gates -> "Exhaust cheap evidence
before surfacing"). Both retrieval layers were loaded; both failed six
times. The 2026-08-05 session produced three instances and the
self-completion rate was 0/3 — every one of them needed a user prompt.

Instance 1 is the one this hook covers: a PR anchor row marked
`PASS(실환경)` carrying a latency figure measured **once**. The user asked
"추가로 측정이 필요하지 않을까?", the measurement was repeated at n=15, and
the fuller result (p50 2,920ms -> 91ms, non-overlapping distributions) was
far stronger than the one the claim had stopped at.

The failure is not a wrong conclusion — each conclusion was defensible. It
is that the termination condition used was "my claim is defensible" rather
than "cheap evidence is exhausted". The untried remediation layer is
emission time, which is what this hook is.

Coverage ceiling (stated, not papered over): instances 2 and 3 of that
session were prose-completeness misses (enumeration breadth, not sample
size). Judging whether a prose report is "complete" is semantic and would
produce noise, so it is deliberately out of scope — those two remain
memory-only. One hook covers one of the three instances.

Detection — all three must hold, scanned over the SAME body text:

  1. A **sample-size-dependent claim**, in one of two forms:
       Form A — a statistical summary marker (`p50`/`p95`/`p99`, `median`,
         `중앙값`, `평균`, `avg`, `mean`, `백분위`) carrying a number. A
         percentile or a central tendency IS a sample statistic; asserting
         one is asserting a distribution.
       Form B — a verdict token (`PASS`, `FAIL`, `verified`, `검증`) within
         `_VERDICT_WINDOW` chars of a number + measurement unit. This is
         instance 1's exact shape: a verdict row carrying a timing figure.
  2. NO sample-size marker with a value >= 2 anywhere in the body
     (`n=15`, `15회 측정`, `15 runs`, `표본 15`, ...). An explicit `n=1`
     therefore does NOT pass — it is the case the hook exists for.
  3. NO `sample-size-noted` opt-out marker in the body.

Deliberately NOT triggered on (scope boundaries):

  - A bare `PASS` with no measurement near it. Every PR verification anchor
    carries `PASS(live)` rows; treating the token alone as a trigger would
    fire on every anchor comment ever posted. The issue names `PASS` in its
    trigger vocabulary; this narrows it to PASS-with-a-measurement, which is
    the shape that actually missed.
  - Multiplier notation (`3x`, `1.76배`) and percentage-with-direction.
    `perf-multiplier-evidence-advisory` already owns those tokens on this
    exact surface (PreToolUse(Bash), `gh issue|pr create|comment` body);
    duplicating them here would double-fire one body.
  - Prose completeness / enumeration breadth (instances 2 and 3 above).

Pass condition is presence-only: the hook does not verify that the stated
sample size actually backs the specific claim. That adequacy judgment is
out of scope, mirroring the sibling gates (`perf-multiplier-evidence-
advisory`'s timing artifact, `negative-existence-verdict-gate`'s
`Enumerated:` line — both presence, not adequacy).

Fail-open contract:
  - malformed JSON / non-Bash tool / empty command -> exit 0
  - no `gh issue|pr create|comment` invocation -> exit 0
  - `--body-file`/`-F` target unreadable -> exit 0 for that invocation
  - any uncaught exception -> exit 0 (`@fail_open`)

Advisory only -- writes to stderr, exits 0. Never blocks, no bypass token.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Trigger: gh issue|pr create|comment  (mirrors perf-multiplier-evidence-advisory)
# ---------------------------------------------------------------------------

_GH_DELIVERABLE_RE = re.compile(r"\bgh\s+(?:issue|pr)\s+(?:create|comment)\b")

_BODY_TEXT_FLAGS = ("--body", "-b")
_BODY_FILE_FLAGS = ("--body-file", "-F")

# ---------------------------------------------------------------------------
# Opt-out
# ---------------------------------------------------------------------------

# Mirrors count-assertion-verify's `# count-verified` marker convention.
_OPT_OUT_MARKER = "sample-size-noted"

# ---------------------------------------------------------------------------
# (1) Sample-size-dependent claim
# ---------------------------------------------------------------------------

# A number, optionally with thousands separators (`2,920`) and decimals.
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# Form A — statistical summary marker.
# `p50` / `P95` / `p99.9`: guarded on both sides so `top50`, `group50`, and
# `p50x` do not match. Exactly two leading digits — a one-digit `[P1]` is a
# Codex finding-severity tag, which appears in this repo's own PR bodies.
_PERCENTILE_RE = re.compile(r"(?<![A-Za-z0-9])[pP]\d{2}(?:\.\d)?(?![A-Za-z0-9])")
_SUMMARY_WORD_RE = re.compile(
    r"(?<![A-Za-z])(?:median|average|avg|mean)(?![A-Za-z])",
    re.IGNORECASE,
)
_SUMMARY_KO = ("중앙값", "백분위", "평균")

# Form B — verdict token near a measurement.
# `PASS`/`FAIL` are matched case-sensitively with ASCII-letter guards: `\b`
# is Unicode-aware, so `\bPASS\b` fails to match `PASS를` in mixed text, and
# a case-insensitive match would hit `bypass` / `password` / `passed`.
# A bare `검증` is ordinary Korean PR prose ("검증 앵커"); only the completed
# form is a verdict.
_VERDICT_RE = re.compile(
    r"(?<![A-Za-z])(?:PASS|FAIL)(?![A-Za-z])"
    r"|(?<![A-Za-z])verified(?![A-Za-z])"
    r"|검증\s*(?:완료|됨|함)",
)
# Measurement units: sub-minute latency and throughput only. Minute- and
# hour-scale durations are excluded — those are overwhelmingly build/run
# times rather than sample-dependent claims, and including them fires on
# ordinary prose. Right-guarded so `2920msg` and `12sample` are not read as
# measurements.
_MEASUREMENT_RE = re.compile(
    rf"(?:{_NUM})\s*(?:ms|sec|secs|s|초|qps|rps|req/s)(?![A-Za-z가-힣])",
)
_VERDICT_WINDOW = 80


def _has_summary_statistic(text: str) -> bool:
    """Form A — a statistical summary marker carrying a number."""
    for m in _PERCENTILE_RE.finditer(text):
        if _number_within(text, m.start(), m.end()):
            return True
    for m in _SUMMARY_WORD_RE.finditer(text):
        if _number_within(text, m.start(), m.end()):
            return True
    for word in _SUMMARY_KO:
        start = 0
        while True:
            idx = text.find(word, start)
            if idx < 0:
                break
            if _number_within(text, idx, idx + len(word)):
                return True
            start = idx + 1
    return False


def _number_within(text: str, start: int, end: int) -> bool:
    """A number near the marker, excluding the marker's own digits."""
    window = text[max(0, start - _VERDICT_WINDOW):start] + " " + text[end:end + _VERDICT_WINDOW]
    return bool(re.search(_NUM, window))


def _has_verdict_measurement(text: str) -> bool:
    """Form B — a verdict token within the window of a measurement."""
    measurements = [m.span() for m in _MEASUREMENT_RE.finditer(text)]
    if not measurements:
        return False
    for v in _VERDICT_RE.finditer(text):
        for m_start, m_end in measurements:
            if v.start() - _VERDICT_WINDOW <= m_end and m_start <= v.end() + _VERDICT_WINDOW:
                return True
    return False


def _has_sample_dependent_claim(text: str) -> bool:
    return _has_summary_statistic(text) or _has_verdict_measurement(text)


# ---------------------------------------------------------------------------
# (2) Sample-size evidence — pass condition
# ---------------------------------------------------------------------------

# `n=15`, `n = 15`, `N=15`. Left-guarded so `min=15`, `column=15`, and
# `run=15` do not read as a sample size.
_N_EQUALS_RE = re.compile(r"(?<![A-Za-z0-9_])[nN]\s*=\s*(\d+)")
# `15회 측정`, `15번 반복`, `15회 실행` — Korean count + repetition verb.
_KO_REPEAT_RE = re.compile(r"(\d+)\s*(?:회|번)\s*(?:측정|반복|실행|시도|수행)")
# `표본 15`, `표본 크기 15`, `샘플 15`.
_KO_SAMPLE_RE = re.compile(r"(?:표본|샘플)(?:\s*크기)?\s*(?:은|는|이|가)?\s*(\d+)")
# `15 runs`, `15 trials`, `15 samples`, `15 iterations`, `15 measurements`.
_EN_RUNS_RE = re.compile(
    r"(\d+)\s*(?:runs?|trials?|samples?|iterations?|measurements?|reps?)(?![A-Za-z])",
    re.IGNORECASE,
)

_SAMPLE_SIZE_PATTERNS = (_N_EQUALS_RE, _KO_REPEAT_RE, _KO_SAMPLE_RE, _EN_RUNS_RE)


def _has_sample_size_at_least_two(text: str) -> bool:
    for pattern in _SAMPLE_SIZE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                if int(m.group(1)) >= 2:
                    return True
            except (TypeError, ValueError):
                continue
    return False


# ---------------------------------------------------------------------------
# Body-text extraction (mirrors perf-multiplier-evidence-advisory)
# ---------------------------------------------------------------------------


def _extract_body_text(command: str) -> str | None:
    """Return the body text this `gh` invocation would post, or None."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None

    for i, tok in enumerate(argv):
        flag, sep, inline_value = tok.partition("=")
        if tok in _BODY_TEXT_FLAGS and i + 1 < len(argv):
            return argv[i + 1]
        if sep and flag in _BODY_TEXT_FLAGS:
            return inline_value
        if tok in _BODY_FILE_FLAGS and i + 1 < len(argv):
            return _read_body_file(argv[i + 1])
        if sep and flag in _BODY_FILE_FLAGS:
            return _read_body_file(inline_value)
    return None


def _read_body_file(path_text: str) -> str | None:
    try:
        p = Path(path_text).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Advisory text
# ---------------------------------------------------------------------------


def _advisory_text() -> str:
    return (
        "[n1-quantitative-claim-advisory] this deliverable states a "
        "sample-dependent quantitative claim (a percentile / central-tendency "
        "figure, or a PASS-attached measurement) with no sample size above 1 "
        "anywhere in the body.\n"
        "  Rule (CLAUDE.md, Falsification Gates): the termination condition is "
        "'cheap evidence is exhausted', not 'my claim is defensible'.\n"
        "  Cheapest unexecuted broadening probe: re-run the SAME measurement "
        "command N>=5 times and report the distribution, not the single "
        "reading — no new tooling, no new environment, same command.\n"
        "  Reference: issue #949 (recurrence 6, self-completion 0/3). At n=1 a "
        "claim read PASS; at n=15 the same measurement gave p50 2,920ms -> "
        "91ms with non-overlapping distributions — a far stronger result than "
        "the one the claim stopped at.\n"
        "  Opt-out: include `sample-size-noted` in the body once the sample "
        "size is deliberate and stated."
    )


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
    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "") or ""
    if not isinstance(command, str) or not command.strip():
        return 0

    if not _GH_DELIVERABLE_RE.search(command):
        return 0

    body = _extract_body_text(command)
    if not body:
        return 0

    if _OPT_OUT_MARKER in body:
        return 0

    if not _has_sample_dependent_claim(body):
        return 0

    if _has_sample_size_at_least_two(body):
        return 0

    sys.stderr.write(_advisory_text() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
