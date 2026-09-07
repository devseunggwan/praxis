#!/usr/bin/env python3
"""Stop hook advisory: completion-signal phrase without evidence-block check.

Issue #392 (advisory v1). Recurring failure mode (Loaded≠Retrieved family,
effective_repeat=6 in 2026-05-23 retrospect): assistant authors a completion-
signal phrase ("실질적 수정은 없습니다 ... 머지하셔도 무방합니다", "done", "all
set") without same-turn verification evidence, bypassing the evidence gate that
completion-verify.sh enforces only for narrow completion-claim patterns.

Also handles Event 2 — cross-plugin slash command surfacing: assistant outputs
a /command from a foreign plugin namespace while the cwd is a praxis repo.

Two detection rules:

  Rule 1 — completion-signal without evidence-block:
    Scan the last assistant turn for completion-signal tokens (EN/KR).
    If found AND the turn has no evidence-block indicators (Bash tool call,
    Read tool call, cited `$ ... → output` lines), emit the advisory.

  Rule 2 — plugin-context anchoring (Event 2):
    Scan the last assistant message text for /command patterns.
    Cross-check against the cwd's active plugin namespace (from
    .claude-plugin/marketplace.json or git remote slug).
    If mismatch detected, emit advisory.

Tier: advisory (stdout `{"systemMessage": ...}` JSON, exit 0 — issue #647 H3
standardized the completion-verify role on stdout JSON; the old stderr form
only reached the debug log). No block tier — this hook never blocks.
The systemMessage is shown to the user in the transcript only — it does NOT
enter the model's context. No blocking until tier promotion (follow-up issue).

Fail-open contract:
  - Malformed / missing stdin JSON → exit 0
  - Missing / unreadable transcript → exit 0
  - Empty transcript → exit 0
  - Any uncaught exception → exit 0
  - stop_hook_active=true → exit 0
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_stop_advisory  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    remaining_budget,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    has_tool_in_turn,
    load_stop_turn,
    stop_last_assistant_text,
)

# ---------------------------------------------------------------------------
# Prefix
# ---------------------------------------------------------------------------

PREFIX = "[praxis:completion-signal-gate]"

_HOOK_NAME = "completion-signal-gate"
# Upper bound on the origin-URL probe; the effective timeout is this clamped
# to the remaining member budget (see _get_cwd_git_slug).
_GIT_TIMEOUT_SEC = 3
_ROLE = "completion-verify"

# ---------------------------------------------------------------------------
# Rule 1 — completion-signal tokens
# ---------------------------------------------------------------------------

# English completion-signal patterns.
# ASCII word-boundary lookarounds (Python \b is Unicode-aware and misfires
# adjacent to Hangul — same strategy as output-block-falsify-advisory.py).
_COMPLETION_EN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?<![A-Za-z])no\s+fix(?:es)?\s+needed(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])ready\s+to\s+merge(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])all\s+set(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])done(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])complete(?![A-Za-z])", re.IGNORECASE),
]

# Korean completion-signal substrings (plain substring / regex, Hangul safe).
_COMPLETION_KO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"실질적\s*수정.*없"),
    re.compile(r"머지하셔도"),
    re.compile(r"완료\b"),
    re.compile(r"결함\s*없음"),
    re.compile(r"이상\s*없음"),
]

# Gate 1 variant: GO verdict phrases (issue #845) must not co-occur with
# unresolved-gap markers in the same assistant turn.
# Each entry carries the language of its predicate, because the negation guards
# are language-specific: the English one reads the window BEFORE the match, the
# Korean one the window AFTER. Running both on every match makes an unrelated
# `No`/`아직` on the other side of the sentence silence a real contradiction.
_GO_VERDICT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"보내도\s*됩니다", re.IGNORECASE), "ko"),
    (re.compile(r"보내도\s*좋습니다", re.IGNORECASE), "ko"),
    (re.compile(r"머지\s*가능", re.IGNORECASE), "ko"),
    (re.compile(r"approve\s*가능", re.IGNORECASE), "ko"),
    (re.compile(r"문제\s*없음", re.IGNORECASE), "ko"),
    (re.compile(r"ready\s+to\s+merge", re.IGNORECASE), "en"),
]

_GAP_MARKER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"⚠"),
    re.compile(r"미해소"),
    re.compile(r"갭"),
    re.compile(r"검증\s*증거\s*부재"),
    re.compile(r"not\s+verified", re.IGNORECASE),
    re.compile(r"unverified", re.IGNORECASE),
]

# Negation markers that flip a completion phrase into a not-yet-complete
# statement (negation/status-form rule, issue #515). EN: token immediately
# BEFORE the match ("not done"). Progressive "-ing" forms are already
# excluded by the word-boundary lookarounds ("completing" != "complete").
_NEGATION_WINDOW_EN = 24  # chars preceding the matched completion phrase
_NEGATION_MARKERS_EN = (
    "not ",
    "n't ",
    "no ",
    "never ",
    "without ",
    "yet to ",
    "isn't",
    "aren't",
    "won't",
    "can't",
    "cannot",
    "wasn't",
)

# KO: a negation form FOLLOWING the completion token ("완료되지 않", "완료 안 됨").
_NEGATION_WINDOW_KO = 12  # chars following the matched completion token
_NEGATION_MARKERS_KO = (
    "되지 않",
    "되지않",
    "지 않",
    "지않",
    "안 됨",
    "안됨",
    "안 됐",
    "안됐",
    "안 된",
    "안된",
    "못 ",
    "못함",
    "못했",
    "아직",
)


# GO verdicts are negated as predicates ("머지 가능한 것은 아닙니다"), a form the
# completion-verb markers above do not cover. Scoped to the GO path so the
# issue-#515 completion-signal behaviour is left untouched.
#
# `아직` and `못` are deliberately absent: they qualify a completion verb, but
# next to a GO verdict they usually belong to a *different* clause — in
# "ready to merge — 아직 실환경은 unverified" the `아직` describes the gap, and
# treating it as negation would silence exactly the contradiction Rule 1b exists
# to surface. Only forms that attach directly to the predicate are listed.
_GO_NEGATION_MARKERS_KO = (
    "되지 않",
    "되지않",
    "지 않",
    "지않",
    "안 됨",
    "안됨",
    "안 됐",
    "안됐",
    "안 된",
    "안된",
    "아닙",
    "아니다",
    "아니라",
    "아니야",
    "아님",
)


def _is_negated_en(text: str, start: int) -> bool:
    """True if an English completion match at `start` is under negation."""
    prefix = text[max(0, start - _NEGATION_WINDOW_EN):start].lower()
    return any(neg in prefix for neg in _NEGATION_MARKERS_EN)


def _is_negated_ko(text: str, end: int) -> bool:
    """True if a Korean completion match ending at `end` is negated.

    Korean negation trails the verb ("완료되지 않았다", "완료 안 됨"), so the
    window FOLLOWING the match is scanned. "아직" (not yet) also counts.
    """
    suffix = text[end:end + _NEGATION_WINDOW_KO]
    if any(neg in suffix for neg in _NEGATION_MARKERS_KO):
        return True
    # "아직 완료 전" — the "not yet" cue can also precede the token.
    prefix = text[max(0, end - 24):end]
    return "아직" in prefix


def _has_completion_signal(text: str) -> bool:
    """True if text contains a completion-signal phrase that is NOT negated.

    A completion phrase under negation or in a not-yet-complete status form
    ("not done yet", "isn't complete", "완료되지 않았습니다", "완료 안 됨") does
    NOT count — the assistant is reporting incompletion, not claiming done.
    """
    for pat in _COMPLETION_EN_PATTERNS:
        for m in pat.finditer(text):
            if not _is_negated_en(text, m.start()):
                return True
    for pat in _COMPLETION_KO_PATTERNS:
        for m in pat.finditer(text):
            if not _is_negated_ko(text, m.end()):
                return True
    return False


# A gap marker that is itself reported as resolved ("미해소 항목 없음", "갭 없음",
# "no unverified items") states the opposite of a gap. Matching the marker as a
# bare substring turns those clean GO outputs into Rule 1b advisories.
_GAP_RESOLVED_WINDOW = 10  # chars following the matched gap marker
_GAP_RESOLVED_MARKERS_KO = ("없음", "없습니다", "없다", "해소됨", "해소했", "해소 완료")
_GAP_RESOLVED_MARKERS_EN = (" none", " resolved", " cleared")


def _has_unresolved_gap(text: str, normalized: str) -> bool:
    """True if a gap marker appears and is not itself reported as resolved."""
    for pat in _GAP_MARKER_PATTERNS:
        for match in pat.finditer(normalized):
            suffix = normalized[match.end():match.end() + _GAP_RESOLVED_WINDOW]
            if any(m in suffix for m in _GAP_RESOLVED_MARKERS_KO):
                continue
            if any(suffix.startswith(m) for m in _GAP_RESOLVED_MARKERS_EN):
                continue
            return True
    return False


# A turn that OPENS with a refusal is a NO-GO report end to end, so a later GO
# token is a status label ("PR 머지 가능 상태" as a table heading) or a quotation of
# the phrase being retracted — not a verdict. The guards above only ever see the
# characters immediately around the match, so a refusal carried by a *different*
# sentence is structurally out of their reach.
# Only a standalone refusal interjection counts, and only as the turn's very
# first token. Predicate forms (`안 됩니다`, `아닙니다`) are deliberately absent:
# they negate whatever clause they sit in, so "이 방법은 안 됩니다만 저 방법은
# 됩니다. 머지 가능합니다. 다만 미해소 갭 1건" — a genuine contradiction — would be
# silenced by them. The English side is the same hazard: a bare leading `no` is
# a determiner far more often than a verdict, and "No blockers. 머지 가능 — 실환경
# unverified" is exactly what Rule 1b exists to catch. Both directions are
# pinned by positive controls; widening either marker set without re-running
# them reintroduces a false negative that no fire-count regression will show.
_LEADING_REFUSAL_RE = re.compile(
    r"^(아니요|아니오|아뇨)\s*[.,!]?|^(no|nope)\s*[.,!]|^not\s+yet\b",
    re.IGNORECASE,
)


def _opens_with_refusal(text: str) -> bool:
    """True if the turn opens by refusing — the verdict is NO-GO, not GO."""
    return bool(_LEADING_REFUSAL_RE.match(text.lstrip()))


def _has_go_verdict_with_unresolved_gap(text: str) -> bool:
    """True if a GO verdict phrase coexists with an unresolved-gap marker."""
    if not text:
        return False
    if _opens_with_refusal(text):
        return False
    normalized = text.lower()
    if not _has_unresolved_gap(text, normalized):
        return False
    for pat, lang in _GO_VERDICT_PATTERNS:
        for match in pat.finditer(text):
            # A negated GO phrase ("not ready to merge", "머지 가능하지 않습니다")
            # reports the gap rather than overriding it — same rule the
            # completion-signal path applies (issue #515). The guard must match
            # the predicate's own language; see _GO_VERDICT_PATTERNS.
            if lang == "en" and _is_negated_en(text, match.start()):
                continue
            # Korean negation always trails, including when it negates an
            # English predicate ("ready to merge가 아닙니다"), so this runs for
            # both languages — unlike the English window, which would read an
            # unrelated leading "No" as negating a Korean verdict.
            suffix = text[match.end():match.end() + _NEGATION_WINDOW_KO]
            if any(neg in suffix for neg in _GO_NEGATION_MARKERS_KO):
                continue
            return True
    return False


# ---------------------------------------------------------------------------
# Rule 1 — evidence-block indicators
# ---------------------------------------------------------------------------

# Matches cited command+output lines like:
#   $ pytest → 12 passed
#   $ python3 -m py_compile → exit 0
_CITED_OUTPUT_RE = re.compile(r"^\s*\$\s+\S+.*→", re.MULTILINE)


def _has_evidence_block(
    last_text: str,
    has_bash_tool: bool,
    has_read_tool: bool,
) -> bool:
    """True if the turn contains at least one evidence-block indicator."""
    if has_bash_tool:
        return True
    if has_read_tool:
        return True
    if _CITED_OUTPUT_RE.search(last_text):
        return True
    return False


# ---------------------------------------------------------------------------
# Rule 2 — plugin-context anchoring
# ---------------------------------------------------------------------------

# Slash-command pattern: /word or /namespace:word
_SLASH_CMD_RE = re.compile(r"(?<![A-Za-z0-9_/])/([A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*)?)")

# Prefixes that indicate foreign plugin namespaces
_FOREIGN_PREFIXES = frozenset(
    {
        "laplace-dev-hub",
        "laplace-wiki",
        "oh-my-claudecode",
        "omc",
        "codex",
        "scheduler",
        "gemini",
    }
)

# Bare-form skill slugs known to belong to foreign plugins. A bare `/release`
# (without the `laplace-dev-hub:` prefix) was the original Event 2 trigger
# (see issue #392). Conservative scope — only high-confidence foreign cases.
# False-positive risk: a praxis-owned skill with the same bare slug would be
# silently mis-flagged; add it to praxis's skill set first if such a name is
# ever introduced.
_KNOWN_FOREIGN_SKILLS = frozenset(
    {
        # laplace-dev-hub
        "release",
        "hub-bulk-release",
        "hub-scan-issues",
        "dev-to-prod-pr",
    }
)


def _get_cwd_plugin_name() -> str | None:
    """Return plugin name for current cwd from .claude-plugin/marketplace.json."""
    try:
        cwd = Path(os.getcwd())
        # Walk up to find .claude-plugin/marketplace.json
        for parent in [cwd, *cwd.parents]:
            mp = parent / ".claude-plugin" / "marketplace.json"
            if mp.exists():
                data = json.loads(mp.read_text())
                return data.get("name") or data.get("plugins", [{}])[0].get("name")
    except Exception:
        pass
    return None


def _get_cwd_git_slug() -> str | None:
    """Return repo name slug from git remote origin."""
    try:
        # Clamped to the member budget the dispatcher publishes (issue
        # #1167): under the Stop group a fixed 3s here could outlive the
        # group deadline and get the whole dispatcher killed by the host.
        budget = remaining_budget(_GIT_TIMEOUT_SEC)
        if budget < MIN_SUBPROC_BUDGET_SEC:
            return None
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=min(_GIT_TIMEOUT_SEC, budget),
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Extract last path component, strip .git
            slug = url.rstrip("/").split("/")[-1]
            if slug.endswith(".git"):
                slug = slug[:-4]
            return slug or None
    except Exception:
        pass
    return None


def _detect_foreign_slash_commands(text: str, cwd_plugin: str | None) -> list[str]:
    """Return list of /commands that appear to belong to a foreign plugin namespace."""
    if not text:
        return []

    matches = _SLASH_CMD_RE.findall(text)
    if not matches:
        return []

    foreign: list[str] = []
    for cmd in matches:
        # Namespaced command like laplace-dev-hub:release
        if ":" in cmd:
            prefix = cmd.split(":")[0]
            # If the prefix is explicitly foreign
            if prefix in _FOREIGN_PREFIXES:
                # Only fire when cwd is praxis (mirrors bare-form scope)
                if cwd_plugin == "praxis" and cwd_plugin != prefix:
                    foreign.append(f"/{cmd}")
            continue
        # Bare /command — flag only if it is in the known-foreign skill set.
        # Conservative by design: unknown bare commands pass silently to avoid
        # false positives on paths (/bin, /usr) and unrelated nouns.
        if cmd in _KNOWN_FOREIGN_SKILLS and cwd_plugin == "praxis":
            foreign.append(f"/{cmd}")

    return foreign


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ADVISORY_RULE1 = (
    f"{PREFIX} completion-signal phrase detected in last turn without "
    "an evidence-block (Bash tool result, Read tool call, or cited "
    "'$ command → output' line).\n"
    f"{PREFIX} Rule: CLAUDE.md 'Verification Before Completion' — run a real "
    "verify command (test/lint/build/probe) and paste its output BEFORE "
    "declaring completion.\n"
    f"{PREFIX} Trigger: matched completion-signal token in last assistant turn. "
    "Add evidence or remove the completion phrase to suppress this advisory."
)

ADVISORY_RULE2 = (
    f"{PREFIX} cross-plugin slash command(s) {{cmds}} surfaced while cwd "
    # f-prefix required: a plain string leaves the literal `{{plugin}}`,
    # so `.replace("{plugin}", …)` would render `'{praxis}'`. The f-string
    # collapses `{{plugin}}` to the single-brace `{plugin}` placeholder the
    # caller substitutes, matching the `{cmds}` fragment above (#1097).
    f"plugin is '{{plugin}}'.\n"
    f"{PREFIX} Rule: CLAUDE.md 'Plugin-context anchoring' — do not surface skill "
    "commands from foreign plugin namespaces. Verify you are working in the "
    "correct repo/plugin context before recommending slash commands."
)


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    if not isinstance(payload, dict):
        return 0

    stop_hook_active = payload.get("stop_hook_active", False)
    if stop_hook_active:
        return 0

    # Stop reads the session transcript; SubagentStop reads the subagent's own
    # (`agent_transcript_path`) — see `_transcript.resolve_stop_transcript`.
    # An unreadable or empty turn still passes: a claim graded against
    # evidence that was never read is the one failure mode worth avoiding.
    turn = load_stop_turn(payload)
    if not turn:
        return 0

    last_text = stop_last_assistant_text(payload, turn)
    if not last_text:
        return 0

    has_bash = has_tool_in_turn(turn, "Bash")
    has_read = has_tool_in_turn(turn, "Read")

    messages: list[str] = []

    # Rule 1: completion-signal without evidence
    if _has_completion_signal(last_text) and not _has_evidence_block(
        last_text, has_bash, has_read
    ):
        messages.append(ADVISORY_RULE1)

    # Rule 1b: GO verdict phrasing with unresolved-gap marker in the same turn.
    if _has_go_verdict_with_unresolved_gap(last_text):
        messages.append(
            f"{PREFIX} go-verdict phrase detected together with unresolved-gap "
            "marker in last assistant turn.\n"
            f"{PREFIX} Rule: CLAUDE.md 'Output-Block Falsification' — "
            "do not claim GO/merge readiness while unresolved gap markers are present "
            "in the same output.\n"
            f"{PREFIX} Trigger: both a go-verdict phrase and unresolved-gap marker "
            "coexist in one turn."
        )

    # Rule 2: plugin-context anchoring
    cwd_plugin = _get_cwd_plugin_name() or _get_cwd_git_slug()
    if cwd_plugin:
        foreign = _detect_foreign_slash_commands(last_text, cwd_plugin)
        if foreign:
            cmds_str = ", ".join(foreign)
            messages.append(
                ADVISORY_RULE2.replace("{cmds}", cmds_str).replace(
                    "{plugin}", cwd_plugin
                )
            )

    if messages:
        # Single JSON object per invocation — both rules can fire in one stop.
        emit_stop_advisory("\n".join(messages))
        # Rich fire record (issue #847): this advisory exits 0, so @fail_open's
        # coarse path records only "pass" — indistinguishable from a silent
        # allow. Record the real decision (one rich record per genuine emit;
        # stop_hook_active already guards re-entrant re-fires), keeping session
        # attribution when present and forgoing it otherwise.
        # suppress_coarse_duplicate() drops the redundant coarse "pass" so
        # aggregate_fires() does not count one emit as fires=2.
        session_id = payload.get("session_id")
        if _fire_ledger.record_session_fire(
            _HOOK_NAME, _ROLE, _fire_ledger.DECISION_ADVISE,
            session_id if isinstance(session_id, str) else "", "Stop",
        ):
            # Suppress the coarse fallback ONLY when the rich record actually
            # landed — else a failed rich append would drop the fire from both
            # streams (coderabbit finding on PR #855).
            _fire_ledger.suppress_coarse_duplicate()

    return 0




if __name__ == "__main__":
    sys.exit(main())
