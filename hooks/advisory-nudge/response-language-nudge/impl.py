#!/usr/bin/env python3
"""PostToolUse(*) hook: nudge back to the user's response language mid-turn.

Issue #1476 — a user-specified response language drifts across a
compaction (see the sibling `postcompact-context` hook, which re-injects the
instruction at the one boundary it can reach) and, separately, drifts inside
one uninterrupted turn: a session that measured 268 text blocks found the
drift concentrated in tool-call *narration* — the prose between tool calls —
while `AskUserQuestion` bodies in the same stretch stayed on the specified
language. Narration is not a "response" Stop grades, so nothing downstream of
it ever sees the drift.

Reachability (from the issue body): a Stop hook only sees the FINAL message
of a turn, by which point every drifted narration line already reached the
user — blocking there corrects nothing already displayed. A PreToolUse deny
can only stop the *next* tool call, not the text that already went out ahead
of it. `PostToolUse` is the earliest point after a drifted block where a hook
can still speak: not before the block reaches the user (nothing is), but
before the assistant's next line does.

Behavior
========

1. `PRAXIS_RESPONSE_LANGUAGE` unset or blank -> silent, exit 0 (opt-in; same
   contract as `postcompact-context`, same env var).
2. The value must normalize to Korean (`ko` / `kr` / `korean` / `ko-kr` /
   `ko_kr` / `한국어`, case-insensitive on the ASCII forms) — anything else is
   an unsupported language for THIS hook and is silent (YAGNI: issue #1476
   scopes the ratio check to Korean; a non-Korean value is not "no rule", it
   is "no detector for that language yet").
3. Read `transcript_path` from the payload and load the current turn (events
   since the last real user input) via the shared `_transcript` reader.
4. Take the LAST assistant message's text content (tool_use / tool_result
   blocks excluded) in that turn — the narration that went out alongside (or
   just before) the tool call this PostToolUse event reports on — together
   with that message's transcript `uuid`, for dedup.
5. Strip code fences, inline code, URLs, filesystem paths, and
   snake_case/camelCase identifiers — none of those carry a language signal
   the way prose does, and a code-heavy block would otherwise register a
   false low ratio. If fewer than 20 non-whitespace characters remain, the
   block is too short to judge and is ignored (a fixture of pure code, or a
   two-word aside, both land here).
6. Compute the fraction of the remaining characters that are Hangul
   syllables (U+AC00-U+D7A3). `>= 0.3` is left alone. Below it, and the
   message's `uuid` was not already nudged this session, emit one
   `hookSpecificOutput.additionalContext` line and record the `uuid` so a
   second tool call from the SAME assistant message (parallel tool_use
   blocks in one message, each producing its own PostToolUse event) does not
   nudge twice for prose that was written once.

Scope (acceptance criteria, issue #1476): chat prose only. Commit messages,
PR titles, and `tool_input` are never inspected — only the assistant's own
text blocks are.

This hook NEVER blocks. It always exits 0.
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
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _state_lock import state_lock  # type: ignore[import-not-found]  # noqa: E402
from _transcript import load_current_turn  # type: ignore[import-not-found]  # noqa: E402

LANGUAGE_ENV = "PRAXIS_RESPONSE_LANGUAGE"

# ASCII aliases are matched case-insensitively; the Korean literal is matched
# as-is (it has no case to fold). Deliberately small (YAGNI) — issue #1476
# scopes this hook to Korean only; a value outside this set is "unsupported",
# not "no rule", and the hook stays silent rather than guessing a detector
# for a language it was never asked to check.
_KOREAN_ASCII_ALIASES = {"ko", "kr", "korean", "ko-kr", "ko_kr"}
_KOREAN_LITERAL = "한국어"

_STATE_SCHEMA_VERSION = 1
# Bound on the per-session list of already-nudged assistant-message uuids.
# One session rarely drifts more than a handful of times; this is headroom,
# not a tuned ceiling.
_RECENT_NUDGED_MAX = 64

# A block shorter than this (after stripping code/URLs/paths/identifiers) is
# too little prose to judge a language ratio from — issue #1476's acceptance
# criteria names this bound explicitly.
_MIN_CONTENT_CHARS = 20

# Below this Hangul-character fraction of the remaining prose, the block is
# read as drifted.
_KOREAN_RATIO_THRESHOLD = 0.3

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_URL_RE = re.compile(r"https?://\S+")
# A path-shaped token: starts with `/`, `./`, `../`, `~/`, or a Windows drive
# letter, and runs to the next whitespace. `(?<!\S)` keeps this from cutting
# into the middle of an unrelated non-whitespace run.
_PATH_RE = re.compile(r"(?<!\S)(?:\.{0,2}/\S+|~/\S+|[A-Za-z]:\\\S+)")
# snake_case: any word carrying an internal underscore (identifiers, env
# vars, file stems already stripped by _PATH_RE keep this from re-matching
# their own extension).
_SNAKE_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*\b")
# camelCase: lowercase-leading, at least one internal uppercase letter.
_CAMEL_RE = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")
_WS_RE = re.compile(r"\s+")
_HANGUL_RE = re.compile(r"[가-힣]")


def _is_korean(value: str) -> bool:
    v = value.strip()
    if not v:
        return False
    if v == _KOREAN_LITERAL:
        return True
    return v.lower() in _KOREAN_ASCII_ALIASES


def _strip_non_prose(text: str) -> str:
    """Remove code fences, inline code, URLs, paths, and identifiers.

    Order matters: fences and inline code are removed before URL/path/
    identifier stripping, so a path or identifier written *inside* a code
    span is not double-processed (it is already gone).
    """
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _PATH_RE.sub(" ", text)
    text = _SNAKE_RE.sub(" ", text)
    text = _CAMEL_RE.sub(" ", text)
    return text


def _korean_ratio(cleaned: str, content_len: int) -> float:
    if content_len <= 0:
        return 0.0
    korean_chars = len(_HANGUL_RE.findall(cleaned))
    return korean_chars / content_len


def _last_assistant_uuid_and_text(turn: list[dict]) -> tuple[str | None, str]:
    """The last assistant message's `uuid` and text-only content in `turn`.

    Mirrors `_transcript.extract_last_assistant_text`'s selection (last
    non-sidechain assistant message, text-type content blocks only, joined
    with newlines) while also carrying the message's `uuid` for dedup —
    `extract_last_assistant_text` alone drops it, and duplicating the small
    selection loop here is cheaper than widening that shared helper's return
    shape for one caller.
    """
    last_uuid: str | None = None
    last_msg: dict | None = None
    for ev in turn:
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        if ev.get("isSidechain"):
            continue
        last_msg = msg
        uuid = ev.get("uuid")
        last_uuid = uuid if isinstance(uuid, str) and uuid else None
    if last_msg is None:
        return None, ""
    content = last_msg.get("content", [])
    if isinstance(content, str):
        return last_uuid, content
    if isinstance(content, list):
        text = "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
        return last_uuid, text
    return last_uuid, ""


def _extract_event_name(payload: dict) -> str:
    name = payload.get("hook_event_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "PostToolUse"


def _state_path(session_id: str) -> str:
    override = os.environ.get("PRAXIS_RESPONSE_LANGUAGE_NUDGE_FILE", "").strip()
    if override:
        return override
    return resolve_cache_file(
        f"response-language-nudge-{session_id}.json", session_id=session_id
    )


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {"schema_version": _STATE_SCHEMA_VERSION, "nudged_uuids": []}


def _save_state(path: str, state: dict) -> bool:
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def _emit_nudge(lang_raw: str, ratio: float, event_name: str) -> None:
    """Emit the drift nudge as `hookSpecificOutput.additionalContext`.

    Mirrors `second-failure-advisory`'s PostToolUse emission shape:
    `hookEventName` echoes the event actually delivered, since the harness
    accepts a reply only under that event.
    """
    message = (
        "[response-language-nudge] The last narration drifted from the "
        f"specified response language ({lang_raw}) — Hangul ratio "
        f"{ratio:.0%} of the prose portion. Continue in {lang_raw} for all "
        "user-facing prose, tool-call narration included. "
        f"(직전 서술이 지정 응답 언어({lang_raw})에서 벗어났습니다 — 산문 구간의 "
        f"한글 비율 {ratio:.0%}. 도구 호출 사이 진행 서술을 포함해 {lang_raw}로 "
        "계속 작성하세요.)"
    )
    json.dump(
        {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": message,
            },
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


@fail_open
def main() -> int:
    lang_raw = os.environ.get(LANGUAGE_ENV, "")
    if not isinstance(lang_raw, str):
        return 0
    lang_raw = lang_raw.strip()
    if not lang_raw:
        return 0
    if not _is_korean(lang_raw):
        return 0

    payload = read_payload()
    if payload is None or not isinstance(payload, dict):
        return 0

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return 0
    if not os.path.isfile(transcript_path):
        return 0

    turn = load_current_turn(transcript_path)
    uuid, text = _last_assistant_uuid_and_text(turn)
    if not text or not text.strip():
        return 0

    cleaned = _strip_non_prose(text)
    content_len = len(_WS_RE.sub("", cleaned))
    if content_len < _MIN_CONTENT_CHARS:
        return 0

    ratio = _korean_ratio(cleaned, content_len)
    if ratio >= _KOREAN_RATIO_THRESHOLD:
        return 0

    event_name = _extract_event_name(payload)

    if uuid:
        path = _state_path(session_id)
        with state_lock(path):
            state = _load_state(path)
            nudged = state.get("nudged_uuids")
            if not isinstance(nudged, list):
                nudged = []
            if uuid in nudged:
                return 0
            nudged.append(uuid)
            state["nudged_uuids"] = nudged[-_RECENT_NUDGED_MAX:]
            saved = _save_state(path, state)
        if not saved:
            return 0

    _emit_nudge(lang_raw, ratio, event_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
