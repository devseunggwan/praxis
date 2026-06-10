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
    Read tool call, cited `$ ... → output` lines), emit advisory to stderr.

  Rule 2 — plugin-context anchoring (Event 2):
    Scan the last assistant message text for /command patterns.
    Cross-check against the cwd's active plugin namespace (from
    .claude-plugin/marketplace.json or git remote slug).
    If mismatch detected, emit advisory.

Tier: advisory (stderr only, no decision JSON, exit 0).
Advisory is a system-reminder signal; Claude will see it as additional context
in the next turn. No blocking until tier promotion (follow-up issue).

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
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    extract_last_assistant_text,
    get_current_turn,
    has_tool_in_turn,
    load_transcript,
)

# ---------------------------------------------------------------------------
# Prefix
# ---------------------------------------------------------------------------

PREFIX = "[praxis:completion-signal-gate]"

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
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=3,
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
    "plugin is '{{plugin}}'.\n"
    f"{PREFIX} Rule: CLAUDE.md 'Plugin-context anchoring' — do not surface skill "
    "commands from foreign plugin namespaces. Verify you are working in the "
    "correct repo/plugin context before recommending slash commands."
)


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0

    stop_hook_active = payload.get("stop_hook_active", False)
    if stop_hook_active:
        return 0

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    events = load_transcript(transcript_path)
    if not events:
        return 0

    turn = get_current_turn(events)
    if not turn:
        return 0

    last_text = extract_last_assistant_text(turn)
    if not last_text:
        return 0

    has_bash = has_tool_in_turn(turn, "Bash")
    has_read = has_tool_in_turn(turn, "Read")

    # Rule 1: completion-signal without evidence
    if _has_completion_signal(last_text) and not _has_evidence_block(
        last_text, has_bash, has_read
    ):
        sys.stderr.write(ADVISORY_RULE1 + "\n")

    # Rule 2: plugin-context anchoring
    cwd_plugin = _get_cwd_plugin_name() or _get_cwd_git_slug()
    if cwd_plugin:
        foreign = _detect_foreign_slash_commands(last_text, cwd_plugin)
        if foreign:
            cmds_str = ", ".join(foreign)
            msg = ADVISORY_RULE2.replace("{cmds}", cmds_str).replace(
                "{plugin}", cwd_plugin
            )
            sys.stderr.write(msg + "\n")

    return 0




if __name__ == "__main__":
    sys.exit(main())
