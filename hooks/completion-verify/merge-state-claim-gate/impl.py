#!/usr/bin/env python3
"""Stop-hook advisory: gate unverified merge / PR / issue / worktree state claims.

Background (issue #503): a praxis ultrawork session hallucinated review/merge
state four times in a row — "PR #495/#497 created, merged, issue closed,
worktree cleaned" — none of which had happened (the PR create was hook-blocked
and the cited numbers were unrelated worktrees). The behavioural remedy lived in
memory only, and "REPEATED PATTERN + MEMORY = FAILED REMEDY -> ESCALATE" (Iron
Law) was crossed. PreToolUse cannot see in-flight assistant text (issue #487
A3), but a Stop-hook sees the final assistant output — the exact complement.

This hook scans the final assistant message for a *completed* merge / PR / issue
/ worktree state assertion and, if no fresh state query
(`gh pr|issue view/list/merge`, or a GitHub MCP pull_request/issue/merge read)
appears in the recent transcript, emits an advisory. It is advisory by default
(exit 0 + stderr; the model has already stopped, so the note reaches the user);
`PRAXIS_MERGE_CLAIM_STRICT=1` escalates to exit 2 (re-prompts the model to
verify). Fully fail-open; bypass with `PRAXIS_MERGE_CLAIM_BYPASS=1`.
"""
from __future__ import annotations

import json
import os
import re
import sys

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

_PREFIX = "[merge-state-claim-gate]"
_BYPASS_ENV = "PRAXIS_MERGE_CLAIM_BYPASS"
_STRICT_ENV = "PRAXIS_MERGE_CLAIM_STRICT"
_EVIDENCE_WINDOW = 80  # how many recent transcript events to scan for evidence

# ---------------------------------------------------------------------------
# Claim detection — a claim needs a SUBJECT token and a COMPLETED-state token
# on the same line (localizes the match, cutting false positives on long
# final messages). Completion tokens are past/perfective so future intent
# ("I'll create a PR", "ready to merge") does not trigger.
# ---------------------------------------------------------------------------

_SUBJECT_RE = re.compile(
    r"\bPR\b|\bpull request\b|\bMR\b|이슈|\bissue\b|worktree|워크트리|#\d+",
    re.IGNORECASE,
)

_CLAIM_KINDS: list[tuple[str, re.Pattern[str]]] = [
    ("merged", re.compile(r"\b(squash[- ]?)?merged\b|머지\s*(됐|했|됨|되었|완료|함)", re.IGNORECASE)),
    ("created", re.compile(r"\bcreated\b|\bopened\b|생성\s*(했|됨|완료|함)|만들었|올렸|작성했", re.IGNORECASE)),
    ("closed", re.compile(r"\bclosed\b|닫(았|힘|았습)|종료\s*(했|됨)", re.IGNORECASE)),
    ("cleaned", re.compile(r"\b(removed|cleaned|deleted)\b|정리\s*(했|됨|완료)|삭제\s*(했|됨)|제거\s*(했|됨)", re.IGNORECASE)),
]

# Negation present on the line -> skip (conservative; avoids noisy advisories).
_NEGATION_RE = re.compile(
    r"\bnot\b|n't\b|\bno\b|\bwithout\b|\byet\b|아직|않|못\s|안\s|없|실패|fail",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Evidence detection — a fresh state query in the recent transcript.
# ---------------------------------------------------------------------------

_GH_EVIDENCE_RE = re.compile(r"\bgh\b[^|;&\n]*\b(pr|issue)\b\s+[a-z]", re.IGNORECASE)
_MCP_GH_EVIDENCE_RE = re.compile(r"pull_request|\bissue\b|merge|pr_", re.IGNORECASE)


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
    """Return events since the last real user input (non-tool-result user msg)."""
    last_user_idx: int | None = None
    for i, ev in enumerate(events):
        msg = ev.get("message", {})
        if msg.get("role") != "user" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            last_user_idx = i
        elif isinstance(content, list):
            if [b for b in content if isinstance(b, dict) and b.get("type") != "tool_result"]:
                last_user_idx = i
    start = 0 if last_user_idx is None else last_user_idx + 1
    return events[start:]


def _extract_last_assistant_text(turn: list[dict]) -> str:
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
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def detect_claims(text: str) -> list[str]:
    """Return the distinct claim kinds asserted in `text` (subject + completed
    state on the same line, not negated)."""
    kinds: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not _SUBJECT_RE.search(line) or _NEGATION_RE.search(line):
            continue
        for kind, pat in _CLAIM_KINDS:
            if kind not in kinds and pat.search(line):
                kinds.append(kind)
    return kinds


def has_fresh_state_query(events: list[dict]) -> bool:
    """True if a recent assistant tool_use is a gh pr|issue query or a GitHub
    MCP pull_request/issue/merge call."""
    for ev in events[-_EVIDENCE_WINDOW:]:
        msg = ev.get("message", {})
        if msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "") or ""
            if name.startswith("mcp__github__"):
                if _MCP_GH_EVIDENCE_RE.search(name):
                    return True
            elif name == "Bash":
                inp = block.get("input", {})
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                if cmd and _GH_EVIDENCE_RE.search(cmd):
                    return True
    return False


def _advisory(kinds: list[str]) -> str:
    return (
        f"{_PREFIX} final message asserts a {'/'.join(kinds)} state change but no "
        "fresh state query (`gh pr|issue view/list/merge` or a GitHub MCP "
        "pull_request/issue read) appears in the recent transcript.\n"
        f"{_PREFIX} Rule: re-read the state you are about to assert "
        "(gh pr view / gh issue view / mcp__github__pull_request_read) and cite "
        "the result BEFORE declaring it — merge/close/create claims from memory "
        "have repeatedly hallucinated (issue #503).\n"
        f"{_PREFIX} bypass: {_BYPASS_ENV}=1\n"
    )


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

    if payload.get("stop_hook_active"):
        return 0  # avoid re-entrant loops

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    events = _load_transcript(transcript_path)
    if not events:
        return 0

    turn = _get_current_turn(events)
    last_text = _extract_last_assistant_text(turn) if turn else ""
    if not last_text:
        return 0

    kinds = detect_claims(last_text)
    if not kinds:
        return 0

    if has_fresh_state_query(events):
        return 0  # claim is backed by a recent state query

    sys.stderr.write(_advisory(kinds))
    return 2 if os.environ.get(_STRICT_ENV, "").strip() == "1" else 0


if __name__ == "__main__":
    sys.exit(main())
