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


def _has_completion_signal(text: str) -> bool:
    """True if text contains a completion-signal phrase."""
    for pat in _COMPLETION_EN_PATTERNS:
        if pat.search(text):
            return True
    for pat in _COMPLETION_KO_PATTERNS:
        if pat.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# Rule 1 — evidence-block indicators
# ---------------------------------------------------------------------------

# Matches cited command+output lines like:
#   $ pytest → 12 passed
#   $ python3 -m py_compile → exit 0
_CITED_OUTPUT_RE = re.compile(r"^\s*\$\s+\S+.*→", re.MULTILINE)

# Backtick command output blocks (``` ... ```)
_CODE_FENCE_RE = re.compile(r"```", re.DOTALL)


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

# Known praxis skill slugs — derived from skills/ directory names.
# These are the slugs that BELONG to praxis:
_PRAXIS_SKILLS = frozenset(
    {
        "cmux-browser",
        "cmux-delegate",
        "cmux-recover-sessions",
        "cmux-resume-sessions",
        "cmux-save-sessions",
        "cmux-session-manager",
        "codex-review-wrap",
        "recover-sessions",
        "reset-strikes",
        "retrospect",
        "strike",
        "strikes",
        "using-praxis",
        "writing-praxis-skill",
    }
)

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
            slug = cmd.split(":")[1]
            # If the prefix is explicitly foreign
            if prefix in _FOREIGN_PREFIXES:
                # If cwd is praxis and the command is from a different plugin
                if cwd_plugin and cwd_plugin != prefix:
                    foreign.append(f"/{cmd}")
            continue
        # Bare /command — check if it matches a known praxis skill
        # Only flag if it looks like a skill invocation (no file-path chars)
        if cmd and "/" not in cmd:
            # Flag only if the bare command clearly belongs to a foreign namespace
            # We are conservative: only flag explicitly namespaced foreign commands
            # to avoid false positives on common words like /bin, /usr, etc.
            pass

    return foreign


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


def _load_transcript(path: str) -> list[dict]:
    """Load JSONL transcript, return list of event dicts. Fail-open."""
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        events.append(obj)
                except Exception:
                    continue
    except Exception:
        pass
    return events


def _get_current_turn(events: list[dict]) -> list[dict]:
    """Return events since the last real user input (non-tool-result user message)."""
    last_user_idx: int | None = None
    for i, ev in enumerate(events):
        msg = ev.get("message", {})
        if msg.get("role") != "user":
            continue
        if ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            last_user_idx = i
        elif isinstance(content, list):
            non_tool = [b for b in content if b.get("type") != "tool_result"]
            if non_tool:
                last_user_idx = i
    if last_user_idx is None:
        start = 0
    else:
        start = last_user_idx + 1
    return events[start:]


def _extract_last_assistant_text(turn: list[dict]) -> str:
    """Extract text from the last assistant message in the turn."""
    last_asst = None
    for ev in turn:
        msg = ev.get("message", {})
        if msg.get("role") == "assistant" and not ev.get("isSidechain"):
            last_asst = ev
    if last_asst is None:
        return ""
    content = last_asst.get("message", {}).get("content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        return "\n".join(parts)
    return ""


def _has_tool_in_turn(turn: list[dict], tool_name: str) -> bool:
    """True if any assistant message in the turn used the named tool."""
    for ev in turn:
        msg = ev.get("message", {})
        if msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use" and block.get("name") == tool_name:
                    return True
    return False


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


def _main_inner() -> int:
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

    events = _load_transcript(transcript_path)
    if not events:
        return 0

    turn = _get_current_turn(events)
    if not turn:
        return 0

    last_text = _extract_last_assistant_text(turn)
    if not last_text:
        return 0

    has_bash = _has_tool_in_turn(turn, "Bash")
    has_read = _has_tool_in_turn(turn, "Read")

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


def main() -> int:
    """Advisory hook — must NEVER break tool execution. Fails open on all errors."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
