#!/usr/bin/env python3
"""PreToolUse(Bash) guard: redirect foreground poll-loops away from the 120000ms ceiling.

A `for/while/until ... sleep N ... done` loop run in the FOREGROUND hits the Bash
default 120000ms (2-min) timeout and dies with SIGTERM (Exit 143) mid-poll, even
though the underlying async op (CloudFormation deploy, `gh pr checks`, CI wait)
usually succeeds. The runtime block only fires on a *leading* `sleep N && cmd`; a
loop-internal sleep slips through, so this gate closes the semantic gap.

## Why this exists

gen-3 escalation of the `native_async_over_sleep_chain` feedback family. The
pattern recurred 6x in a single session (14x Exit 143), 5 of them AFTER the first
Exit 143 was already visible in-transcript — proving the advisory tier (memory +
Bash tool-schema prose) is structurally insufficient. Keys on the LOOP SHAPE, not
the polled command (the command allowlist is always incomplete). REDIRECTS (names
the native async-wait primitives) rather than merely rejecting.

## Detection is token-based (structural-tokenization contract) and per-loop scoped

Loop keywords, `sleep`, and iteration counts are matched against `safe_tokenize`
output, never against the raw command string. A quoted literal containing the
words (`git commit -m "retry while deploy sleeps 30s"`,
`echo "while true; do sleep 5; done"`) tokenizes to a single string token and can
neither trigger a block nor downgrade an unbounded loop to a short bounded one.

Each loop is judged on its OWN `do…done` body: a `sleep` outside a loop's body
neither blocks that loop nor combines with another loop's iteration count
(`while …; break; done; sleep 200` passes; `for A 50-iter no-sleep; for B
2-iter sleep 10` passes regardless of textual order).

## Block conditions

Exit 2 (block) when the command is a FOREGROUND Bash call (NOT run_in_background)
contains a poll loop that can approach/exceed the 120s ceiling:

  1. Unbounded `while`/`until` loop whose body contains a parseable `sleep`
     (bare or path-invoked, e.g. `/bin/sleep`; no fixed iteration count →
     runs until the ceiling kills it), EXCLUDING `while read` /
     `while IFS= read` line-consumer loops (they terminate on input, not on
     time).
  2. Bounded `for` loop where iterations x (sum of body sleeps per iteration)
     >= 100s. Iteration count parsed from `seq 1 N` / `seq N` / `seq A B` /
     `seq A STEP B`, `{A..B}` (either direction), C-style
     `((i=A; i<N; i+=S))`, or a literal word list. `sleep` accepts s/m/h/d
     unit suffixes.

Comments and heredoc bodies are stripped before tokenization — loop-shaped
text bash never executes cannot trigger the gate.

Exit 0 (pass) otherwise: run_in_background=true, short bounded loops (< ~90s),
no-sleep loops, leading `sleep` without a loop (the runtime already handles that),
non-Bash tools.

## Env vars

- `PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD` — set to any non-empty value → bypass (exit 0).

## Fail-open

Malformed stdin JSON, non-Bash tool, unparseable count/sleep (e.g. `sleep $N`) →
exit 0 (pass).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

_sys_lib = str(_Path(__file__).resolve().parent.parent.parent / "_lib")
if _sys_lib not in sys.path:
    sys.path.insert(0, _sys_lib)
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import safe_tokenize  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

_FOREGROUND_CEILING_S = 120  # Bash default timeout, seconds
_SAFE_MARGIN_S = 100  # block when worst-case can approach the ceiling

_LOOP_KEYWORDS = {"for", "while", "until"}
# Separator tokens after which the next token sits in command position (bash
# grants reserved-word meaning to for/while/until/do/done only there).
_COMMAND_SEPARATORS = {";", ";;", "&&", "||", "|", "&", "{"}
_SLEEP_ARG_RE = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)[;)]*$")  # 20 / 2.5 / 30s / 1m / 20;
_SLEEP_UNIT_S = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_SEQ_HEAD_RE = re.compile(r"^[$(`]*seq$")  # seq / $(seq / `seq
_NUMERIC_RE = re.compile(r"^(\d+)[;)`]*$")  # 40 / 40) / 40`
_BRACE_RANGE_RE = re.compile(r"\{(\d+)\.\.(\d+)\}")  # {A..N} / {N..A}
_C_INIT_RE = re.compile(r"=\s*(\d+)")  # ((i=0; …
_C_BOUND_RE = re.compile(r"([<>]=?)\s*(\d+)")  # … i<40 / i<=40 …
_C_STEP_RE = re.compile(r"[+-]=\s*(\d+)")  # … i+=2 ))
# Delimiter = any non-special word (covers `~EOF` etc. — bash has only `<<`
# and `<<-`; a leading `~` is part of the delimiter, not an operator variant).
_HEREDOC_RE = re.compile(r"<<-?\s*([\"']?)([^\s\"'<>|&;()]+)\1")

def _strip_comment(line: str) -> str:
    """Cut the line at an unquoted `#` that starts a word (bash comment rule)."""
    in_single = in_double = escaped = False
    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not in_single:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double and (i == 0 or line[i - 1] in " \t;"):
            return line[:i]
    return line


def _strip_non_executable(command: str) -> str:
    """Remove comments and heredoc bodies — text bash never executes.

    A hard gate must not block on loop-shaped text inside a trailing comment
    (`echo hi # while true; do sleep 20; done`) or a heredoc body
    (`cat <<'EOF' … EOF`). Both fail toward PASS: over-stripping can only
    suppress a block, never cause one.
    """
    out: list[str] = []
    heredoc_delims: list[tuple[str, bool]] = []  # (delimiter, strip_tabs)
    for line in command.split("\n"):
        if heredoc_delims:
            delim, strip_tabs = heredoc_delims[0]
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == delim:
                heredoc_delims.pop(0)
            continue  # inside a heredoc body — drop
        line = _strip_comment(line)
        for m in _HEREDOC_RE.finditer(line):
            heredoc_delims.append((m.group(2), "<<-" in m.group(0)))
        out.append(line)
    return "\n".join(out)


def _iter_loops(tokens: list[str]) -> list[tuple[str, list[str], list[str]]]:
    """Split the token stream into (keyword, header, body) per loop.

    Header = tokens between the loop keyword and its `do`; body = tokens
    between `do` and the matching `done`. Nesting is handled with a stack; a
    nested loop appears both as its own entry and inside its parent's body.
    A loop with no `do` is unparseable → skipped (fail-open); a loop whose
    `done` never arrives gets the remainder of the stream as its body
    (conservative — the loop really does extend to the end of the command).
    """
    loops: list[tuple[str, list[str], list[str]]] = []
    stack: list[list[int | None]] = []  # [kw_idx, do_idx]
    # Bash honours reserved words only in command position; an argument
    # (`echo done`) must not open/advance/close a loop frame. A `do` grants
    # command position to its successor only when it was itself reserved —
    # tracked via `grants_next`, so a literal word list (`for i in do done`)
    # cannot fake a boundary.
    grants_next = False
    for i, tok in enumerate(tokens):
        cmd_pos = i == 0 or tokens[i - 1] in _COMMAND_SEPARATORS or grants_next
        grants_next = False
        if not cmd_pos:
            continue
        if tok in ("then", "else", "elif"):
            grants_next = True
        elif tok in _LOOP_KEYWORDS:
            stack.append([i, None])
        elif tok == "do":
            grants_next = True
            for frame in reversed(stack):
                if frame[1] is None:
                    frame[1] = i
                    break
        elif tok == "done" and stack:
            kw_idx, do_idx = stack.pop()
            if do_idx is not None:
                assert kw_idx is not None
                loops.append(
                    (tokens[kw_idx], tokens[kw_idx + 1 : do_idx], tokens[do_idx + 1 : i])
                )
    while stack:  # unclosed loop → body runs to end of stream
        kw_idx, do_idx = stack.pop()
        if do_idx is not None and kw_idx is not None:
            loops.append((tokens[kw_idx], tokens[kw_idx + 1 : do_idx], tokens[do_idx + 1 :]))
    return loops


def _sleep_args(body: list[str]) -> list[float]:
    """Parseable `sleep N[smhd]` arguments inside a loop body, in seconds."""
    out: list[float] = []
    for i, tok in enumerate(body[:-1]):
        if tok != "sleep" and not tok.endswith("/sleep"):
            continue
        m = _SLEEP_ARG_RE.match(body[i + 1])
        if m:
            out.append(float(m.group(1)) * _SLEEP_UNIT_S[m.group(2)])
    return out


def _is_line_consumer(header: list[str]) -> bool:
    """`while read ...` / `while IFS= read ...` terminate on input, not time."""
    if not header:
        return False
    if header[0] == "read":
        return True
    return header[0].startswith("IFS=") and len(header) > 1 and header[1] == "read"


def _for_iterations(header: list[str]) -> int | None:
    """Iteration count from a `for` header, else None (fail-open).

    Recognized forms: `$(seq 1 N)` / `$(seq N)` / `$(seq A B)` / `$(seq A S B)`,
    `{A..B}` (either direction), C-style `((i=A; i<N; i++))` (init/operator/step
    honored), and a literal word list (`for i in a b c` counts the words —
    globs count as 1 word each, an undercount that only ever passes; a list
    containing `$`/`` ` `` expansions has an unknowable count → fail-open).
    """
    for i, tok in enumerate(header):
        m = _BRACE_RANGE_RE.search(tok)
        if m:
            return abs(int(m.group(2)) - int(m.group(1))) + 1
        if not _SEQ_HEAD_RE.match(tok):
            continue
        nums: list[int] = []
        for nxt in header[i + 1 : i + 4]:
            nm = _NUMERIC_RE.match(nxt)
            if not nm:
                break
            nums.append(int(nm.group(1)))
        if len(nums) == 1:  # seq N
            return nums[0]
        if len(nums) == 2:  # seq A B → B-A+1 iterations
            return max(nums[1] - nums[0] + 1, 0)
        if len(nums) == 3 and nums[1] > 0:  # seq A STEP B
            return max((nums[2] - nums[0]) // nums[1] + 1, 0)
        return None
    if any(tok.startswith("((") or tok.endswith("))") for tok in header):
        # C-style ((i=A; i<N; i+=S)) — punctuation-split tokens
        joined = " ".join(header)
        init_m = _C_INIT_RE.search(joined)
        bound_m = _C_BOUND_RE.search(joined)
        if not init_m or not bound_m:
            return None  # missing init or comparison → fail-open
        init, op, bound = int(init_m.group(1)), bound_m.group(1), int(bound_m.group(2))
        step_m = _C_STEP_RE.search(joined)
        step = int(step_m.group(1)) if step_m else 1
        if step <= 0:
            return None
        if op == "<":
            span = bound - init
        elif op == "<=":
            span = bound - init + 1
        elif op == ">":
            span = init - bound
        else:  # >=
            span = init - bound + 1
        return max(-(-span // step), 0)  # ceil-div, clamped
    if "in" in header:  # literal word list
        words = [t for t in header[header.index("in") + 1 :] if t != ";"]
        if not words:
            return None
        if any("$" in w or "`" in w for w in words):
            return None  # dynamic expansion → unknowable count → fail-open
        return len(words)
    return None


def _detect(command: str) -> str | None:
    """Return a human-readable reason when the command is a ceiling-risk poll loop, else None."""
    tokens = safe_tokenize(_strip_non_executable(command))
    if not tokens:
        return None

    # Per-loop scoping: a sleep outside a loop's own do…done body neither
    # blocks that loop nor combines with another loop's iteration count.
    for kw, header, body in _iter_loops(tokens):
        sleeps = _sleep_args(body)
        if not sleeps:
            continue
        if kw in ("while", "until"):
            if _is_line_consumer(header):
                continue
            return (
                f"unbounded {kw}+sleep poll loop — no fixed iteration count, so it runs "
                f"until the {_FOREGROUND_CEILING_S}s foreground ceiling"
            )
        iterations = _for_iterations(header)
        if iterations is None:
            continue  # unparseable count → fail-open
        per_iter = sum(sleeps)
        max_dur = iterations * per_iter
        if max_dur >= _SAFE_MARGIN_S:
            return (
                f"bounded loop worst-case ≈ {iterations} × {per_iter}s = {max_dur}s "
                f"(≥ {_SAFE_MARGIN_S}s, approaches the {_FOREGROUND_CEILING_S}s foreground ceiling)"
            )
    return None


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD"):
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    # Backgrounded polling is the CORRECT pattern — never block it.
    if tool_input.get("run_in_background") is True:
        return 0

    command = tool_input.get("command", "") or ""
    if not command.strip():
        return 0

    reason = _detect(command)
    if reason is None:
        return 0

    emit_block(
        rule_name="foreground poll-loop guard",
        why=f"{reason} — the foreground call dies with SIGTERM (Exit 143) "
            "mid-poll even when the awaited async op succeeds",
        correct_path=(
            "use a native async-wait primitive: run_in_background: true "
            "(Claude is re-invoked on exit — no polling loop needed), a "
            "Monitor until-loop, `aws cloudformation wait`, `gh run watch` / "
            "`gh pr checks --watch` / `kubectl wait`; a short bounded "
            "foreground poll (worst-case < 90s) is fine"
        ),
        bypass_env="PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD",
        reference="docs/hook/foreground-poll-loop-guard.md",
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
