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
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Trigger: gh issue|pr create|comment
#
# Structural tokenization, not regex (DESIGN.md "Design mechanisms shared by
# all hooks"). A regex over raw command text cannot tell a real invocation
# from the same words inside a quoted string, and it cannot tell which body
# flag belongs to which invocation when several are chained.
# ---------------------------------------------------------------------------

_GH_OBJECTS = ("issue", "pr")
_GH_VERBS = ("create", "comment")

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
#
# `PASS`/`FAIL` stay case-sensitive: lowercasing them would match `bypass`,
# `password`, and `passed`. `verified` is the opposite case — an English
# verification sentence usually starts it, so only that alternative is
# case-insensitive.
_VERDICT_RE = re.compile(
    r"(?<![A-Za-z])(?:PASS|FAIL)(?![A-Za-z])"
    r"|(?<![A-Za-z])(?i:verified)(?![A-Za-z])"
    r"|검증\s*(?:완료|됨|함)",
)
# Korean particles attach directly to the unit — `48건을`, `91초로` — so a
# blanket Hangul right-guard rejects the natural phrasing. Naming the
# particles keeps `2920msg`-style false positives out (the guard against
# Latin letters is unchanged) while admitting the inflected forms.
_KO_PARTICLE = r"을|를|이|가|은|는|로|으로|에|에서|와|과|의|도|만|보다|까지|부터"
_UNIT_TAIL = rf"(?![A-Za-z])(?!(?:(?!{_KO_PARTICLE})[가-힣]))"

# Measurement units: sub-minute latency and throughput only. Minute- and
# hour-scale durations are excluded — those are overwhelmingly build/run
# times rather than sample-dependent claims, and including them fires on
# ordinary prose.
_MEASUREMENT_RE = re.compile(
    rf"(?:{_NUM})\s*(?:ms|sec|secs|s|초|qps|rps|req/s){_UNIT_TAIL}",
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


# Form C — a verdict-attached bare count whose run condition is unstated.
# `48건` / `12 rows` / `3 hits` says nothing until the reader knows what
# produced it. Same shape as Form B with a count in place of a unit-bearing
# measurement, and a different pass condition: any one of the three run-
# condition fields (command / collection scope / where it ran) silences it.
_COUNT_RE = re.compile(
    rf"(?:{_NUM})\s*(?:건|개|행|줄|rows?|hits?|matches?|files?|cases?|tests?|failures?|failed)"
    rf"{_UNIT_TAIL}",
    re.IGNORECASE,
)
# Field 1 — the command that produced it, cited rather than described.
_RUN_COMMAND_RE = re.compile(r"^\s*\$\s+\S+|`[^`\n]*(?:pytest|grep|rg|gh|git|find|wc)[^`\n]*`", re.MULTILINE)
# Field 2 — what it collected. Field 3 — where it ran.
_RUN_SCOPE_RE = re.compile(
    r"수집\s*범위|collection scope|PYTHONPATH|--maxfail|\bmaxfail\b|테스트\s*범위|대상\s*범위",
    re.IGNORECASE,
)
_RUN_LOCATION_RE = re.compile(
    r"로컬\s*재현|로컬\s*실행|CI\s*run\b|CI\s*조건|workflow\s*run|워크트리|실행\s*위치|ran on\b|ran in\b",
    re.IGNORECASE,
)


def _has_verdict_count(text: str) -> bool:
    counts = [m.span() for m in _COUNT_RE.finditer(text)]
    if not counts:
        return False
    for v in _VERDICT_RE.finditer(text):
        for c_start, c_end in counts:
            if v.start() - _VERDICT_WINDOW <= c_end and c_start <= v.end() + _VERDICT_WINDOW:
                return True
    return False


def _has_run_condition(text: str) -> bool:
    """Any one of command / collection scope / where it ran."""
    return bool(
        _RUN_COMMAND_RE.search(text)
        or _RUN_SCOPE_RE.search(text)
        or _RUN_LOCATION_RE.search(text)
    )


def _has_sample_dependent_claim(text: str) -> bool:
    return _has_summary_statistic(text) or _has_verdict_measurement(text)


# ---------------------------------------------------------------------------
# (2) Sample-size evidence — pass condition
# ---------------------------------------------------------------------------

# Sample sizes are written with thousands separators as readily as figures
# are — `n=1,000` must read as 1000, not as 1. The separators are stripped
# before the int conversion; `_SAMPLE_NUM` is deliberately NOT `_NUM`, since
# a sample size is a count and never carries a decimal part.
_SAMPLE_NUM = r"\d{1,3}(?:,\d{3})+|\d+"

# `n=15`, `n = 15`, `N=15`. Left-guarded so `min=15`, `column=15`, and
# `run=15` do not read as a sample size.
_N_EQUALS_RE = re.compile(rf"(?<![A-Za-z0-9_])[nN]\s*=\s*({_SAMPLE_NUM})")
# `15회 측정`, `15번 반복`, `15회 실행` — Korean count + repetition verb.
_KO_REPEAT_RE = re.compile(rf"({_SAMPLE_NUM})\s*(?:회|번)\s*(?:측정|반복|실행|시도|수행)")
# `표본 15`, `표본 크기 15`, `샘플 15`.
_KO_SAMPLE_RE = re.compile(
    rf"(?:표본|샘플)(?:\s*크기)?\s*(?:은|는|이|가)?\s*({_SAMPLE_NUM})"
)
# `15 runs`, `15 trials`, `15 samples`, `15 iterations`, `15 measurements`.
_EN_RUNS_RE = re.compile(
    rf"({_SAMPLE_NUM})\s*(?:runs?|trials?|samples?|iterations?|measurements?|reps?)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

_SAMPLE_SIZE_PATTERNS = (_N_EQUALS_RE, _KO_REPEAT_RE, _KO_SAMPLE_RE, _EN_RUNS_RE)


def _has_sample_size_at_least_two(text: str) -> bool:
    for pattern in _SAMPLE_SIZE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                if int(m.group(1).replace(",", "")) >= 2:
                    return True
            except (TypeError, ValueError, AttributeError):
                continue
    return False


# ---------------------------------------------------------------------------
# Body-text extraction (mirrors perf-multiplier-evidence-advisory)
# ---------------------------------------------------------------------------


def _is_gh_deliverable(argv: list[str]) -> bool:
    """True iff this argv slice is a `gh (issue|pr) (create|comment)` call.

    The object and verb are located by scanning past `gh`'s own global flags
    (`--repo owner/name`, `-R …`), so a flag sitting between them does not
    hide the invocation.
    """
    if not argv:
        return False
    head = argv[0].lstrip("(${")
    if not (head == "gh" or head.endswith("/gh")):
        return False

    words = [tok for tok in argv[1:] if not tok.startswith("-")]
    for i, word in enumerate(words):
        if word in _GH_OBJECTS:
            return any(w in _GH_VERBS for w in words[i + 1:i + 3])
    return False


def _body_of(argv: list[str]) -> str | None:
    """Return the body text THIS invocation would post, or None."""
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


def _extract_bodies(command: str) -> list[str]:
    """Every body a `gh` deliverable in this command would post.

    One command can chain several invocations; each carries its own body, and
    a sample size stated in one says nothing about a claim in another.
    """
    # `shlex.split` rather than the shared `safe_tokenize`: this hook's inputs
    # are GitHub bodies, and a verification anchor opens with `### Verification`.
    # `safe_tokenize` strips `#` to end-of-line as a shell comment before the
    # quote state is settled, which swallows the opening quote and shreds every
    # multi-line body — the primary path. `shlex.split` defaults to
    # `comments=False`, so `#` stays literal inside the quoted body.
    # Command-boundary splitting still uses the shared primitives below.
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    if not tokens:
        return []

    bodies = []
    for segment in iter_command_starts(tokens):
        argv = strip_prefix(segment)
        if not _is_gh_deliverable(argv):
            continue
        body = _body_of(argv)
        if body:
            bodies.append(body)
    return bodies


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


def _count_advisory_text() -> str:
    return (
        "[n1-quantitative-claim-advisory] this deliverable states a count as a "
        "verified result with none of its three run-condition fields present "
        "(the command, what it collected, where it ran).\n"
        "  Rule (CLAUDE.md, Negative Claims State Their Scan Scope): a bare "
        "count is a claim with the polarity flipped — `<n> <unit> (<command>, "
        "<scope>, <where it ran>)`.\n"
        "  Cheapest unexecuted broadening probe: re-run the count with the "
        "invocation pasted verbatim, and state whether it ran locally or in "
        "CI — the same command under a different collection scope is a "
        "different number.\n"
        "  A count is not falsifiable without its run condition, so the "
        "reader cannot tell a wrong number from a differently-scoped one.\n"
        "  Opt-out: include `sample-size-noted` in the body."
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

    # Each chained invocation is scanned against its OWN body: a sample size
    # stated in one says nothing about a claim published by another.
    for body in _extract_bodies(command):
        if _OPT_OUT_MARKER in body:
            continue

        # Two claim families, each with its own pass condition. A body can
        # carry both; the first unsatisfied one is reported.
        if _has_sample_dependent_claim(body) and not _has_sample_size_at_least_two(body):
            sys.stderr.write(_advisory_text() + "\n")
            return 0

        if _has_verdict_count(body) and not _has_run_condition(body):
            sys.stderr.write(_count_advisory_text() + "\n")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
