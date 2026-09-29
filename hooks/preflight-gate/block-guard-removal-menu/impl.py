#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) guard: a menu that offers to remove a guard that just blocked.

A PreToolUse hook blocked one of the agent's calls, and the agent's next move
was an AskUserQuestion whose option proposes taking the guard away rather than
satisfying it: "Add a hook exception (Recommended)", "disable the hook",
"set SOME_BYPASS=1", "allow direct write to main". The repository documented a
path that satisfies the guard; the menu never offered it, and the user had to
point it out.

This is the menu lane of `docs/hook/RULE-BACKSTOP-GAPS.md` gap #4 (ETHOS
principle 5): the agent originating a route around a block and handing it to
the user as a choice. `bypass-route-signal` meters the prose lane on Stop and
leaves this lane open by design.

Fires only when BOTH hold:
  (a) an option label or description carries guard-removal vocabulary;
  (b) the current turn (since the last real user message) holds a PreToolUse
      hook or permission-rule denial — `toolDenialKind: "permission-rule"`
      with `is_error: true` on the tool_result, the same two structural
      markers `_transcript.scan_user_rejections` uses.
(b) is what keeps an unrelated menu about, say, hook configuration quiet.

Relay carve-out (ETHOS principle 5): an option that names an env var the
blocking message itself printed as `VAR=1` is relaying the gate's own route,
which the agent MAY do. That env token alone does not fire.

Default mode: advisory — exit 0, text on stderr (fire ledger) and in
`additionalContext` (the only exit-0 channel the model reads, `_hook_io.py`).
Strict mode (PRAXIS_GUARD_REMOVAL_MENU_STRICT=1): exit 2 + stderr.

Fail-open: unreadable payload, missing transcript, or a turn the tail reader
cannot bound → silent pass.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_additional_context  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    HOOK_BLOCK_DENIAL_KIND,
    load_current_turn,
)
from ask_option_text import collect_option_texts  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block, format_block  # type: ignore[import-not-found]  # noqa: E402

STRICT_ENV = "PRAXIS_GUARD_REMOVAL_MENU_STRICT"

# ---------------------------------------------------------------------------
# Guard-removal vocabulary
# ---------------------------------------------------------------------------
#
# English patterns use ASCII-letter lookaround, not `\b`: Python's `re` treats
# Hangul as a word character, so `\b` finds no boundary in mixed-script labels
# such as `hook 비활성화` / `bypass하기` (measured in bypass-route-signal/spec.md).
# Digits and `_` are excluded too, so `GATE_BYPASS=1` is read as one env name
# (handled by the relay-aware env rule) rather than as the verb `bypass`.
_L = r"(?<![A-Za-z0-9_])"
_R = r"(?![A-Za-z0-9_])"

_GUARD_NOUN_EN = (
    r"(?:hooks?|guards?|gates?|safeguards?|protections?|branch[- ]protection|"
    r"permission[- ]rules?|deny[- ]rules?|pre-?commit)"
)
_REMOVAL_VERB_EN = (
    r"(?:disabl(?:e|ing)|bypass(?:ing)?|skip(?:ping)?|turn(?:ing)?\s+off|"
    r"remov(?:e|ing)|relax(?:ing)?|loosen(?:ing)?|overrid(?:e|ing)|"
    r"circumvent(?:ing)?|work(?:ing)?\s+around|suppress(?:ing)?|silenc(?:e|ing)|"
    r"exempt(?:ing)?|deactivat(?:e|ing)|unregister(?:ing)?|weaken(?:ing)?)"
)

GUARD_REMOVAL_PATTERNS_EN = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "disable the hook", "bypass this guard", "turn off branch protection"
        rf"{_L}{_REMOVAL_VERB_EN}{_R}(?:\W+[A-Za-z0-9_.-]+){{0,3}}?\W+{_GUARD_NOUN_EN}{_R}",
        # "hook exception", "guard bypass", "gate override", "hook allowlist"
        rf"{_L}{_GUARD_NOUN_EN}\s+(?:exceptions?|exemptions?|bypass|override|"
        rf"allow-?lists?|white-?lists?|carve-?outs?){_R}",
        # "add an exception" — but not "add an exception handler" or
        # "add an exception to the error handler"
        rf"{_L}(?:add|adding|create|register|set\s+up)\s+(?:an?\s+|the\s+)?"
        rf"(?:{_GUARD_NOUN_EN}\s+)?exception{_R}"
        rf"(?!(?:\s+(?:to|for|in|into|on)\s+(?:(?:the|an?|this)\s+)?(?:[A-Za-z_]+\s+){{0,2}})?"
        rf"\s*(?:handler|handling|class|type|clause)s?{_R})",
        # "allow direct write to main", "allow direct push"
        rf"{_L}allow(?:ing)?\s+direct\s+(?:writes?|edits?|commits?|push(?:es)?){_R}",
        # "write directly to main", "commit directly on master"
        rf"{_L}(?:write|edit|commit|push)\w*\s+directly\s+(?:to|on|into)\s+"
        rf"(?:main|master|prod|the\s+protected){_R}",
        # "--no-verify"
        r"(?<![A-Za-z-])--no-verify(?![A-Za-z-])",
    )
)

# Korean: plain substring match (no ASCII word-boundary hazard). Each entry
# names the guard noun together with the removal act, so an ordinary label
# such as `훅 설정 확인` does not match.
GUARD_REMOVAL_MARKERS_KO = (
    "훅 예외",
    "훅예외",
    "훅 비활성",
    "훅비활성",
    "훅 끄",
    "훅을 끄",
    "훅 우회",
    "훅을 우회",
    "훅 해제",
    "훅 제거",
    "훅을 제거",
    "가드 우회",
    "가드를 우회",
    "가드 비활성",
    "가드 해제",
    "가드 예외",
    "가드를 끄",
    "게이트 우회",
    "게이트 비활성",
    "게이트 예외",
    "예외 추가",
    "예외 설정",
    "예외 등록",
    "예외를 추가",
    "예외를 설정",
    "보호 해제",
    "보호 규칙 해제",
    "브랜치 보호 우회",
    "직접 쓰기 허용",
    "직접 커밋 허용",
    "직접 푸시 허용",
    "우회 설정",
    "권한 규칙 추가",
    "허용 목록에 추가",
)
_MARKERS_KO_RE = re.compile("|".join(map(re.escape, GUARD_REMOVAL_MARKERS_KO)))
# `훅 우회 없음` / `가드 우회 없이` / `훅 예외를 추가하지 않고` state the route
# is NOT taken. One Hangul verb chunk may sit between the particle and the
# negation (`를 추가하지 않고`).
_NEGATION_KO_RE = re.compile(r"\S{0,2}\s*(?:[가-힣]{1,4}\s*)?(?:없|안\s*함|안\s*하|하지\s*않|않)")
# English negation precedes the phrase: `do not disable the hook`,
# `never bypass this guard`, `without a hook exception`. One word may sit
# between (`do not simply disable`); punctuation ends the reach, so
# `No, disable the hook` still fires.
_NEGATION_EN_BEFORE_RE = re.compile(
    r"(?:(?<![A-Za-z])(?:not|never|no|without)|n't)(?:\s+[A-Za-z]+)?\s+(?:an?\s+|the\s+)?$",
    re.IGNORECASE,
)

# `SOME_BYPASS=1` / `SKIP_X=true`: an env assignment proposing to switch a
# guard off. Group 1 is the variable name, compared against the relay set.
_ENV_ASSIGN_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*(?:BYPASS|SKIP|DISABLE|ALLOW|OVERRIDE|"
    r"EXEMPT|NO_VERIFY|OFF|ADVISORY)[A-Z0-9_]*)\s*=\s*(?:1|true|on|yes)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
# Any `VAR=1` printed by the blocking message: the gate's own offered route.
_OFFERED_ENV_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{2,})=1(?![A-Za-z0-9_])")

# ---------------------------------------------------------------------------
# Option-side detection
# ---------------------------------------------------------------------------


def collect_texts(tool_input: dict) -> list[str]:
    """Every option label and description across all questions."""
    texts: list[str] = []
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return texts
    for q in questions:
        if not isinstance(q, dict):
            continue
        options = q.get("options")
        if isinstance(options, list):
            texts.extend(collect_option_texts(options))
    return texts


def find_removal_phrase(texts: list[str], relayed_envs: set[str]) -> str | None:
    """The first guard-removal phrase found in the option texts, or None."""
    for text in texts:
        for ko in _MARKERS_KO_RE.finditer(text):
            if not _NEGATION_KO_RE.match(text, ko.end()):
                return ko.group(0)
        for pattern in GUARD_REMOVAL_PATTERNS_EN:
            for m in pattern.finditer(text):
                if not _NEGATION_EN_BEFORE_RE.search(text[: m.start()]):
                    return m.group(0)
        for m in _ENV_ASSIGN_RE.finditer(text):
            if m.group(1).upper() not in relayed_envs:
                return m.group(0)
    return None


# ---------------------------------------------------------------------------
# Transcript-side detection
# ---------------------------------------------------------------------------


def _tool_result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def turn_denials(turn: list[dict]) -> list[str]:
    """Texts of the hook / permission-rule denials recorded in the turn."""
    out: list[str] = []
    for ev in turn:
        if ev.get("isSidechain") or ev.get("toolDenialKind") != HOOK_BLOCK_DENIAL_KIND:
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("is_error") is True
            ):
                out.append(_tool_result_text(block))
    return out


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

RULE_NAME = "guard-removal menu"
REFERENCE = "hooks/preflight-gate/block-guard-removal-menu/spec.md; ETHOS.md principle 5"

_WHY = (
    "a hook or permission rule blocked a tool call earlier in this turn, and "
    'this AskUserQuestion offers to remove or loosen that guard (matched: "{phrase}"). '
    "An agent-originated guard-removal option hands the user a route around the "
    "block; a yes widens the guard for every later session"
)
_CORRECT_PATH = (
    "find the path that satisfies the gate before asking. Re-read the blocking "
    "message in full (most blocks name the correct path), read the blocking "
    "hook's spec or source if it does not, and check the repository's documented "
    "workflow (CONTRIBUTING, AGENTS.md, CLAUDE.md, a project CLI) for the "
    "sanctioned way to make this change. Offer that path as the option. If no "
    "satisfying path exists, stop and tell the user what was blocked and why, "
    "without proposing a way around it"
)


def _render(phrase: str) -> tuple[str, str, str, None, str]:
    return RULE_NAME, _WHY.format(phrase=phrase), _CORRECT_PATH, None, REFERENCE


@fail_open
def main() -> int:
    payload = read_payload()
    if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    texts = collect_texts(tool_input)
    if not texts:
        return 0

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return 0
    denials = turn_denials(load_current_turn(transcript_path))
    if not denials:
        return 0

    relayed = {m.group(1).upper() for d in denials for m in _OFFERED_ENV_RE.finditer(d)}
    phrase = find_removal_phrase(texts, relayed)
    if phrase is None:
        return 0

    strict = os.environ.get(STRICT_ENV, "").strip() == "1"
    if strict:
        emit_block(*_render(phrase))
        return 2
    # format_block's header says "blocked"; this path lets the call through.
    body = format_block(*_render(phrase)).replace(" blocked\n", " (advisory)\n", 1)
    message = f"[advisory] {body}\nAdvisory mode; {STRICT_ENV}=1 makes this a block.\n"
    sys.stderr.write(message)
    emit_additional_context(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
