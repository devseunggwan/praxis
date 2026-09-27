#!/usr/bin/env python3
"""Stop-hook gate: a command a hook blocked this turn, handed to the user to run.

A PreToolUse block stops one mutation. Proposing that the user run the same
command stops nothing: the mutation still happens, only by other hands, and the
hook that was built to prevent it is spent. ETHOS principle 5 already forbids
every bypass route the agent originates; `bypass-route-signal` counts such
proposals by their nouns and never blocks. This gate covers the narrower case
it cannot see, where the route is the blocked command itself, and blocks,
because only a block's text reaches the model before the report goes out.

The incident this was built from wrote `! git -C <path> push origin main` after
a guard blocked `git push origin main`. The relayed line is not a substring of
the blocked one, so matching is on the blocked segment's positional tokens as
an ordered subsequence of the relayed line's tokens.

Nothing here judges meaning beyond two lexical tests: the command appears in a
code line of the final message, and that line is framed as the user's to run:
it starts with the host's `!` run prefix, or a user-run phrase that is not
negated sits in its scope. The scope is the paragraph for a command line (a
fenced line, or a line that is only the code span), widened to the
paragraph above when that one ends with a colon and so introduces the code; for
a code span inside prose it is only the sentence around the span.

Scope is the current turn, as in `denied-action-report-gate`. Fail-open; bypass
with `PRAXIS_RELAYED_BLOCK_BYPASS=1`.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_stop_block  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _shell_tokenize import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
)
from _mutating_call import (  # type: ignore[import-not-found]  # noqa: E402
    _NONWRITING_REDIRECT_RE,
    bash_is_readonly,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    HOOK_BLOCK_DENIAL_KIND,
    load_stop_turn,
    resolve_stop_transcript,
    stop_last_assistant_text,
)

_HOOK_NAME = "relayed-blocked-command-gate"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_RELAYED_BLOCK_BYPASS"

# A signature shorter than this matches too much prose to mean anything.
_MIN_SIGNATURE = 2

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT = re.compile(r"^\d*[<>]")
# An env var the blocking hook itself offers (`Bypass (if truly needed): VAR=1`)
# is the one route ETHOS principle 5 lets the agent relay. Hooks read it from
# their own process env, so a prefix on the agent's command never reaches them.
_OFFERED_ENV = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{2,})=")

# Runtime sentence of a settings permission-rule refusal; a hook's refusal is
# the hook's own prose and never starts with it.
_SETTINGS_RULE_DENIAL = "Permission to use "

# `터미널에서` / `프롬프트에` alone only name a place (an agent says where it
# looked); they frame a relay only with a request form within two words.
_USER_RUN = re.compile(
    r"직접\s*\S{0,6}?(?:해\s*주|하시)|입력하시|실행해\s*주|실행하시|"
    r"(?:터미널에서|프롬프트에)(?:\s*\S{1,12}){0,2}?\s*(?:실행|입력|치|쳐|넣|돌려)"
    r"\S{0,4}?(?:해\s*주|하시|하세요|주세요)|"
    r"(?<![a-z])(?:run\s+(?:it|this|that|the\s+command)\s+yourself|"
    r"(?:in|from)\s+your\s+(?:terminal|shell)|you\s+(?:can|could)\s+run|"
    r"please\s+run)(?![a-z])",
    re.IGNORECASE,
)
_NEGATED_AFTER = re.compile(
    r"^.{0,8}?(?:지\s*않|지\s*말|지\s*마|하지\s*않|not\b|n't\b)"
)

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINE_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s+|\$\s+)*")
# The host accepts `!git` as well as `! git`; tokenizing needs the space.
_GLUED_RUN_PREFIX = re.compile(r"^!(?=\S)")
# A sentence ends at a terminator followed by whitespace or the end of text.
_SENTENCE_END = re.compile(r"[.!?。！？](?=\s|$)")
_INTRODUCES = (":", "：")


def signature(segment: list[str]) -> list[str]:
    """Positional tokens of one command segment, in order.

    Flags and the argument right after a flag drop out, so a relay that adds or
    removes `-C <path>` still matches. Env assignments and redirects drop out
    because they are not what the command does.
    """
    out: list[str] = []
    after_flag = False
    for tok in segment:
        if tok.startswith("-"):
            after_flag = True
            continue
        if after_flag:
            after_flag = False
            continue
        if tok == "!" or _ENV_ASSIGN.match(tok) or _REDIRECT.match(tok):
            continue
        out.append(tok)
    return out


def blocked_signatures(command: str) -> list[list[str]]:
    """One signature per mutating segment of a blocked Bash command."""
    tokens = safe_tokenize(_NONWRITING_REDIRECT_RE.sub(" ", command))
    sigs = []
    for seg in iter_command_starts(tokens or []):
        if bash_is_readonly(" ".join(seg)):
            continue
        sig = signature(seg)
        if len(sig) >= _MIN_SIGNATURE:
            sigs.append(sig)
    return sigs


def is_subsequence(needle: list[str], hay: list[str]) -> bool:
    it = iter(hay)
    return all(any(tok == h for h in it) for tok in needle)


def fence_marks(lines: list[str]) -> list[bool | None]:
    """True inside a fenced block, None on a fence delimiter, False elsewhere.

    A fence closes only on the opener's character, at least as long, with
    nothing after it, so an inner ``` line stays content of a ```` block.
    """
    marks: list[bool | None] = []
    fence: str | None = None
    for line in lines:
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
            marks.append(None if m else False)
            continue
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
            fence = None
            marks.append(None)
            continue
        marks.append(True)
    return marks


def code_lines(message: str) -> list[tuple[int, str]]:
    """(line index, code text) for every fenced line and inline code span."""
    out: list[tuple[int, str]] = []
    lines = message.splitlines()
    for i, (line, mark) in enumerate(zip(lines, fence_marks(lines))):
        if mark:
            out.append((i, line))
        elif mark is False:
            out.extend((i, span) for span in _INLINE_CODE.findall(line))
    return out


def _has_user_run_frame(text: str) -> bool:
    for m in _USER_RUN.finditer(text):
        if not _NEGATED_AFTER.match(text[m.end():]):
            return True
    return False


def paragraphs(message: str) -> list[int]:
    """Paragraph number of each line; a blank line inside a fence splits nothing."""
    out: list[int] = []
    number = 0
    lines = message.splitlines()
    for line, mark in zip(lines, fence_marks(lines)):
        if mark is False and not line.strip():
            number += 1
        out.append(number)
    return out


def _mask_code(text: str) -> str:
    """Inline code spans replaced by same-length filler, so their punctuation
    never ends a sentence."""
    return _INLINE_CODE.sub(lambda m: "`" + "x" * (len(m.group(0)) - 2) + "`", text)


def sentence_around(text: str, start: int, end: int) -> str:
    """The sentence of `text` that contains the span `text[start:end]`."""
    begin = 0
    for m in _SENTENCE_END.finditer(_mask_code(text)):
        if m.end() <= start:
            begin = m.end()
        elif m.start() >= end:
            return text[begin:m.end()]
    return text[begin:]


def is_command_line(line: str, code: str) -> bool:
    """A fenced line, or a line that is nothing but the code span (a list
    marker, quote or `$` prompt before it allowed)."""
    return _LINE_PREFIX.sub("", line).strip() in (f"`{code}`", code.strip())


def preceding_paragraph(lines: list[str], para: list[int], number: int) -> str:
    """Text of the nearest non-empty paragraph above paragraph `number`."""
    for p in range(number - 1, -1, -1):
        text = "\n".join(line for line, q in zip(lines, para) if q == p)
        if text.strip():
            return text
    return ""


def is_framed(lines: list[str], para: list[int], index: int, code: str) -> bool:
    """A `!` run prefix, or a user-run phrase in the code's scope.

    A command line (fenced, or a line that is only the code span) takes
    its paragraph as scope: a sentence reporting the block often sits a few
    lines above the option that relays it, and a line window reads the report
    as part of the relay. When that paragraph carries no frame, the paragraph
    above is read too, but only when it ends with a colon: `Run this in your
    terminal:` followed by a blank line and a fence is one utterance. A code
    span inside prose takes only its own sentence, so a report of the block
    (`` `git push` was blocked. Please run `gh pr create` instead. ``) is
    not framed by the sentence that follows it.
    """
    if code.lstrip().startswith("!"):
        return True
    number = para[index]
    own = [line for line, p in zip(lines, para) if p == number]
    text = "\n".join(own)
    if not is_command_line(lines[index], code):
        offset = sum(len(line) + 1 for line in own[: index - para.index(number)])
        start = lines[index].find(f"`{code}`")
        if start < 0:
            return False
        start += offset
        return _has_user_run_frame(sentence_around(text, start, start + len(code) + 2))
    if _has_user_run_frame(text):
        return True
    above = preceding_paragraph(lines, para, number)
    return above.rstrip().endswith(_INTRODUCES) and _has_user_run_frame(above)


def offered_env_names(block_texts: list[str]) -> set[str]:
    return {m for text in block_texts for m in _OFFERED_ENV.findall(text)}


def relays(
    message: str, sigs: list[list[str]], offered: set[str] | frozenset[str] = frozenset()
) -> str | None:
    """The first code line that hands a blocked command to the user, if any."""
    if not sigs or not message:
        return None
    lines, para = message.splitlines(), paragraphs(message)
    for index, code in code_lines(message):
        tokens = safe_tokenize(_GLUED_RUN_PREFIX.sub("! ", _LINE_PREFIX.sub("", code))) or []
        if any(_ENV_ASSIGN.match(t) and t.split("=", 1)[0] in offered for t in tokens):
            continue
        if any(is_subsequence(s, tokens) for s in sigs) and is_framed(lines, para, index, code):
            return code.strip()
    return None


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return " ".join(
            str(c.get("text") or "") for c in content if isinstance(c, dict)
        )
    return str(content or "")


def _blocks(ev: dict) -> list:
    message = ev.get("message") if isinstance(ev, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def turn_blocked_commands(turn: list[dict]) -> list[tuple[str, str]]:
    """(command, refusal text) for each Bash call a hook blocked this turn.

    Read from the turn itself rather than a whole-transcript scan: only this
    turn's blocks matter, and a scan bounded by size goes silent on a long
    session. A settings permission rule records the same denial kind, but its
    refusal leaves the command to the user by design, so it is not a bypass.
    """
    bash_calls: dict[str, str] = {}
    blocked: list[tuple[str, str]] = []
    for ev in turn or []:
        for block in _blocks(ev):
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                command = (block.get("input") or {}).get("command")
                if isinstance(block.get("id"), str) and isinstance(command, str):
                    bash_calls[block["id"]] = command
        if ev.get("toolDenialKind") != HOOK_BLOCK_DENIAL_KIND:
            continue
        for block in _blocks(ev):
            command = bash_calls.get(str(block.get("tool_use_id")))
            if block.get("type") != "tool_result" or block.get("is_error") is not True or command is None:
                continue
            text = _result_text(block)
            if not text.startswith(_SETTINGS_RULE_DENIAL):
                blocked.append((command, text))
    return blocked


def _reason(line: str) -> str:
    return (
        "Blocked command handed to the user: a hook blocked this command this "
        f"turn, and the final message proposes that the user run it (`{line}`). "
        "The hook exists to stop that mutation, not to stop you from typing it, "
        "so relaying it is a bypass. Read the hook and the issue that added it, "
        "then either fix the cause or report the block and stop.\n"
        "이번 턴에 훅이 막은 명령을 사용자가 실행하도록 권했습니다. 대신 실행시키는 것도 "
        "우회입니다. 훅과 그 출처 이슈를 읽고, 원인을 고치거나 차단 사실만 보고하십시오.\n"
        "Reference: hooks/completion-verify/relayed-blocked-command-gate/spec.md"
    )


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip() == "1":
        return 0

    payload = read_payload()
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return 0

    transcript_path, _is_agent = resolve_stop_transcript(payload)
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    session_id = payload.get("session_id")
    session_id = session_id if isinstance(session_id, str) else ""
    turn = load_stop_turn(payload)
    blocked = turn_blocked_commands(turn)
    if not blocked:
        return 0
    sigs = [sig for command, _ in blocked for sig in blocked_signatures(command)]
    offered = offered_env_names([text for _, text in blocked])
    line = relays(stop_last_assistant_text(payload, turn), sigs, offered)
    if line is None:
        return 0

    emit_stop_block(_reason(line))
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, _fire_ledger.DECISION_BLOCK, session_id, "Stop"
    ):
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
