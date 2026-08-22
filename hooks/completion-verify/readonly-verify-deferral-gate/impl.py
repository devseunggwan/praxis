#!/usr/bin/env python3
"""Stop hook advisory: read-only verification offered instead of just run.

Issue #641. Recurring failure mode (Loaded≠Retrieved family): the assistant
*offers* to run a read-only verification ("should I check ...?", "진행할까요?")
and defers it to the user, instead of simply running the read and pasting the
result. A read-only command carries no mutation risk, so deferring it adds a
round-trip and trains the user to approve things that need no approval.

This is the inverse of completion-signal-gate (which catches claiming done
*without* evidence): here the assistant has the means to produce evidence and
declines to. Both live under the completion-verify role and fire on Stop.

Detection is a three-signal AND gate over the last assistant turn's text:

    read-only intent  ∧  deferral phrasing  ∧  ¬mutation carve-out

plus an already-ran suppressor: an offer sentence that *also* carries a
past-tense run cue ("I ran `X` — show the result?") describes a read that was
already executed, so it does not warn. The cue is keyed off the offer sentence
text, not the turn's tool history — a coarse "any read tool ran this turn"
check wrongly suppresses a *mixed* turn (ran read A, then offered read B): B was
not run and must still warn.

The hard design constraint is telling a read-only offer (should auto-run) from
a legitimate mutation-approval ask (should ask). False-warning a real mutation
gate is worse than a missed read-only warn — so the mutation carve-out biases
toward suppression.

Normalization (issue #641): all built-in signals are project-agnostic. Read
verbs (SELECT…FROM, `kubectl get/describe/logs`, `--dry-run`, …) are generic.
A project that wants to recognise its own read-only CLIs sets
PRAXIS_READONLY_VERIFY_SIGNALS to an extra regex (alternation) — no
project-specific token is hard-coded in this file.

Tier: advisory (stdout `{"systemMessage": ...}` JSON, exit 0 — issue #647 H3
standardized the completion-verify role on stdout JSON; the old stderr form
only reached the debug log). PRAXIS_READONLY_VERIFY_STRICT=1 escalates to
`{"decision": "block", "reason": ...}` (Stop is blocked and the reason is fed
to the model so it re-runs the read). PRAXIS_READONLY_VERIFY_BYPASS=1 silences
the hook entirely.

KNOWN LIMITATION (see spec.md): this is an output-scan proxy. It polices the
*articulation* of a deferral, not the behaviour — it misses a vague offer that
names no command, misses a silently-incomplete answer, and is evadable by
rewording. It is a nudge, not a guarantee.

Fail-open contract:
  - Malformed / missing stdin JSON → exit 0
  - Missing / unreadable / empty transcript → exit 0
  - Any uncaught exception → exit 0 (via @fail_open)
  - stop_hook_active=true → exit 0 (re-entry guard)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import (  # type: ignore[import-not-found]  # noqa: E402
    emit_stop_advisory,
    emit_stop_block,
)
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    extract_last_assistant_text,
    load_current_turn,
)

# ---------------------------------------------------------------------------
# Prefix / env
# ---------------------------------------------------------------------------

PREFIX = "[praxis:readonly-verify-deferral-gate]"

_BYPASS_ENV = "PRAXIS_READONLY_VERIFY_BYPASS"
_STRICT_ENV = "PRAXIS_READONLY_VERIFY_STRICT"
_SIGNALS_ENV = "PRAXIS_READONLY_VERIFY_SIGNALS"
_HOOK_NAME = "readonly-verify-deferral-gate"
_ROLE = "completion-verify"

# ---------------------------------------------------------------------------
# Signal A — read-only verification intent
# ---------------------------------------------------------------------------
#
# Generic, project-agnostic read-only patterns. Each is something an assistant
# would name when offering to *verify* state without changing it. A project
# adds its own read-only CLIs via PRAXIS_READONLY_VERIFY_SIGNALS (see below) —
# nothing project-specific is hard-coded here.

_READ_SIGNAL_PARTS: list[str] = [
    r"\bselect\b[^;]{0,200}\bfrom\b",          # SELECT ... FROM (read SQL)
    r"\bshow\s+(tables|catalogs|schemas|databases|columns)\b",  # SHOW <meta>
    r"\bdescribe\s+\w",                          # DESCRIBE <table>
    r"\bkubectl\s+(get|describe|logs|top)\b",   # kubectl read verbs
    r"\bgit\s+(status|log|diff|show|branch|remote|rev-parse)\b",  # git read
    r"\baws\s+[a-z0-9-]+\s+(get|describe|list)-",  # aws <svc> get-/describe-/list-
    r"\bgh\s+\w+\s+(view|list|status)\b",       # gh ... view/list/status
    r"--dry-run\b",                              # any --dry-run invocation
]

_READ_SIGNAL_RE = re.compile("(?i)(" + "|".join(_READ_SIGNAL_PARTS) + ")")


def _load_extra_signal() -> re.Pattern[str] | None:
    """Compile the optional project-supplied read-signal regex.

    PRAXIS_READONLY_VERIFY_SIGNALS holds a single regex (typically an
    alternation of a project's read-only CLI shapes). An empty value or an
    invalid pattern is ignored — never let a misconfigured env var break the
    hook (fail-open).
    """
    raw = os.environ.get(_SIGNALS_ENV, "").strip()
    if not raw:
        return None
    try:
        return re.compile("(?i)(" + raw + ")")
    except re.error:
        return None


def _has_read_signal(text: str, extra: re.Pattern[str] | None) -> bool:
    if _READ_SIGNAL_RE.search(text):
        return True
    if extra is not None and extra.search(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Signal B — deferral phrasing
# ---------------------------------------------------------------------------
#
# The phrasing that turns "I ran the read" into "do you want me to run it?".
# EN uses ASCII word-boundary-safe forms; Korean forms are plain substrings.

# Offer-stems, not verb whitelists: the verb slot after "should I" / "want me
# to" is usually the command itself ("want me to SHOW TABLES", "should I
# re-run"), so requiring a fixed verb causes false negatives. Precision is
# preserved by the three-signal AND (a read command must also be named and no
# mutation present), so a broad deferral stem is safe here.
_DEFERRAL_EN_RE = re.compile(
    r"(?i)("
    r"\bshould\s+i\b"
    r"|\bshall\s+i\b"
    r"|(do\s+you\s+)?\bwant\s+me\s+to\b"
    r"|\bwould\s+you\s+like\s+me\s+to\b"
    r"|\blet\s+me\s+know\s+if\s+you\b"
    r")"
)

# Korean deferral substrings (Hangul-safe). These are the question/offer forms
# that hand a read-only verification back to the user.
_DEFERRAL_KO_MARKERS: tuple[str, ...] = (
    "진행할까요",
    "진행 여부",
    "진행하면",
    "확인할까요",
    "확인해 드릴까요",
    "조회할까요",
    "조회해 드릴까요",
    "실행할까요",
    "검토할까요",
    "살펴볼까요",
    "확인해드릴까요",
    "원하시면",
    "필요하시면",
    "알려주세요",
    "알려 주세요",
    "말씀해 주세요",
    "말씀 주세요",
    "말씀해주세요",
)


def _has_deferral(text: str) -> bool:
    if _DEFERRAL_EN_RE.search(text):
        return True
    return any(marker in text for marker in _DEFERRAL_KO_MARKERS)


# ---------------------------------------------------------------------------
# Carve-out — mutation present → asking is legitimate, suppress
# ---------------------------------------------------------------------------
#
# When the deferred action is (or is mixed with) a mutation, asking first is
# CORRECT behaviour and must not be warned. Over-matching here only costs
# missed read-only warns, which this gate prefers over false-warning a real
# approval gate. SQL/HTTP write keywords are matched case-sensitively
# (uppercase) so prose words ("update the doc", "post a comment") do not
# suppress; command/verb forms are matched case-insensitively.

_MUTATION_CI_RE = re.compile(
    r"(?i)("
    r"\bgit\s+push\b"
    r"|\bgh\s+(pr|issue)\s+(merge|create|edit|close|comment)\b"
    r"|\bkubectl\s+(apply|delete|scale|patch|exec|edit|drain|rollout|create)\b"
    r"|\bgit\s+(commit|merge|rebase|reset|revert)\b"
    r"|\bcurl\b[^\n]*(-X\s*(POST|PUT|PATCH|DELETE)|--data\b|-d\s)"
    r"|\bterraform\s+(apply|destroy)\b"
    r")"
)

# SQL / HTTP write keywords — uppercase only (case-sensitive), so that ordinary
# prose ("we will update the schema doc") does not trip the carve-out.
_MUTATION_CS_RE = re.compile(
    r"\b(POST|PUT|PATCH|DELETE|INSERT|UPDATE|ALTER|DROP|TRUNCATE|MERGE|CREATE|GRANT|REVOKE)\b"
)

# Korean mutation markers (Hangul-safe).
_MUTATION_KO_MARKERS: tuple[str, ...] = (
    "머지",
    "배포",
    "삭제",
    "제거",
    "전송",
    "발송",
    "메시지를 보",
    "푸시",
    "커밋",
    "롤아웃",
)


def _has_mutation(text: str) -> bool:
    if _MUTATION_CI_RE.search(text):
        return True
    if _MUTATION_CS_RE.search(text):
        return True
    return any(marker in text for marker in _MUTATION_KO_MARKERS)


# Sentence boundaries. A genuine read-offer puts the command and the deferral
# in the SAME sentence ("should I run `SELECT …`?"). A declarative read report
# closing with a generic courtesy phrase splits them across sentences ("Ran
# `git status` — clean. Let me know if you need more.") — that is correct
# behaviour (the read already ran) and must NOT warn. Requiring read ∧ deferral
# to co-occur within one sentence removes that false-positive class without
# losing real offers (issue #641 review).
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")

# Already-ran cues: a past-tense run verb in the SAME sentence as the read+
# deferral means the read was executed and the deferral is about showing /
# continuing, not about running the read ("`SELECT …` 를 실행했고 결과를
# 확인할까요?" / "I ran `git status` — want me to break it down?"). EN uses
# ASCII word-boundary lookarounds so "run"/"running" (future/gerund) do NOT
# match "ran"; Korean past forms are plain substrings (확인할까요 ≠ 확인했).
_ALREADY_RAN_EN_RE = re.compile(
    r"(?i)(?<![a-z])(ran|executed|checked|fetched|queried|pulled|looked\s+up)(?![a-z])"
)
_ALREADY_RAN_KO_MARKERS: tuple[str, ...] = (
    "실행했",
    "실행한",
    "확인했",
    "확인한",
    "조회했",
    "조회한",
    "돌렸",
    "받았",
)


def _has_already_ran_cue(sentence: str) -> bool:
    if _ALREADY_RAN_EN_RE.search(sentence):
        return True
    return any(marker in sentence for marker in _ALREADY_RAN_KO_MARKERS)


def _is_readonly_offer(text: str, extra: re.Pattern[str] | None) -> bool:
    """(read-only ∧ deferral ∧ ¬already-ran) within ONE sentence, ∧ ¬mutation.

    The mutation carve-out is turn-global: any mutation present means asking is
    legitimate, so the whole turn is suppressed (bias toward suppression — a
    false-warned approval gate is worse than a missed read-only warn). The
    read+deferral pairing is per-sentence so a declarative read report followed
    by an unrelated courtesy close or a deferral about the *next* step does not
    false-warn. An offer sentence that itself carries a past-tense run cue
    describes an already-executed read and is skipped — but a sentence offering
    read B while a *different* read A ran earlier in the turn has no such cue and
    still warns (issue #641 codex review).
    """
    if _has_mutation(text):
        return False  # mutation present anywhere — asking is legitimate
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not (_has_read_signal(sentence, extra) and _has_deferral(sentence)):
            continue
        if _has_already_ran_cue(sentence):
            continue  # this read was already executed — not offered
        return True
    return False


# ---------------------------------------------------------------------------
# Advisory text
# ---------------------------------------------------------------------------

ADVISORY = (
    f"{PREFIX} a read-only verification was OFFERED instead of run: the last "
    "turn names a read-only command (SELECT…FROM, kubectl get/describe/logs, "
    "git status, --dry-run, …) and defers it to the user ('should I check?', "
    "'진행할까요?').\n"
    f"{PREFIX} Rule: CLAUDE.md 'Read-only calls auto-proceed' — a read carries "
    "no mutation risk, so just run it and paste the result. Do NOT hand it back "
    "as an option. Deferral is reserved for mutations (write/push/merge/delete/"
    "send/deploy).\n"
    f"{PREFIX} If the deferred action is actually a mutation, name it explicitly "
    "so the carve-out recognises it. Bypass: " + _BYPASS_ENV + "=1; "
    "strict (decision: block): " + _STRICT_ENV + "=1."
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("stop_hook_active", False):
        return 0  # avoid re-entrant loops

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    turn = load_current_turn(transcript_path)
    if not turn:
        return 0

    last_text = extract_last_assistant_text(turn)
    if not last_text:
        return 0

    extra = _load_extra_signal()

    if not _is_readonly_offer(last_text, extra):
        return 0

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(ADVISORY)
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(ADVISORY)
        decision = _fire_ledger.DECISION_ADVISE
    # Rich fire record (issue #847): Stop hooks signal block/advise via a stdout
    # decision while exiting 0, so @fail_open's coarse path records only "pass".
    # Record the real decision (one rich record per genuine emit; stop_hook_active
    # already guards re-entrant re-fires), keeping session attribution when present
    # and forgoing it otherwise. suppress_coarse_duplicate() drops the redundant
    # coarse "pass" so aggregate_fires() does not count one emit as fires=2.
    session_id = payload.get("session_id")
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, decision,
        session_id if isinstance(session_id, str) else "", "Stop",
    ):
        # Suppress the coarse fallback ONLY when the rich record actually
        # landed — else a failed rich append would drop the fire from both
        # streams (coderabbit finding on PR #855).
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
