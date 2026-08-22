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
text bash never executes cannot trigger the gate. `sleep` counts only in
command position, the same rule the loop keywords already follow: `do echo
sleep 20; done` passes the word along and waits for nothing.

Exit 0 (pass) otherwise: run_in_background=true, short bounded loops (< ~90s),
no-sleep loops, leading `sleep` without a loop (the runtime already handles that),
non-Bash tools.

## Background waiter-chain advisory (issue #1063)

`run_in_background: true` passes the block above — it is the correct redirect.
It is also where the blocked behaviour moved: chaining short background waiters
across turns, each one differing from the last only in its `sleep` duration,
passed the guard every time (146 of 500 Bash calls in one session, 9 waiters
left for the harness to kill hours later). A background call whose whole job is
to elapse is recorded in a session registry keyed on its sleep-normalized
command signature; a second such call arriving while the first's armed window
is still open draws an ADVISORY (exit 0) naming the already-armed waiter and
`TaskStop`. It is written to BOTH `hookSpecificOutput.additionalContext` — the
one PreToolUse channel that reaches the model at exit 0 — and stderr, which the
fire ledger reads to classify the fire as `advise`. It never blocks — the block message hands out
`run_in_background: true` as the escape, so denying it would contradict the
guard's own redirect.

## Read-gate escalation (issue #1012)

A block that is answered with the same loop shape is a block that was not read.
From the (N+1)-th block of THIS guard in the same session (N =
`_READ_GATE_AFTER_BLOCKS`), the block message escalates: it names this guard's
own `spec.md` by absolute path and states that the poll-loop retry stays denied
until that file has been Read in this session.

Three properties keep the escalation from pointing at nothing or deadlocking:

  1. The referent is this guard's own `spec.md`, not the 132-byte
     `docs/hook/…md` redirect stub it used to name.
  2. The path is resolved from the PACKAGE ROOT (`__file__`), so it is absolute
     and resolves identically whether or not the agent's cwd is a praxis
     checkout — a bare repo-relative path resolves nowhere else, so the agent
     that DID read the spec would still be denied.
  3. If that path does not exist, the escalation FAILS OPEN to the base block.
     A gate whose own satisfaction condition is unreachable must not add a
     requirement.

The prior-block count comes from the fire ledger's
`count_session_fires(hook, session_id, decision="block")` — the dispatcher
already writes a RICH `(session_id, hook, decision)` row per member per Bash
call, so no second counter is introduced. The read-set is the one
`pre-edit-md-escape-advisory` already maintains (its PostToolUse(Read) leg
records EVERY `.md` Read), so no second read-history is introduced either.

The escalation only ever changes the MESSAGE of a command this guard was
already going to block — it can never turn a pass into a deny.

## Env vars

- `PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD` — set to any non-empty value → bypass (exit 0).
- `PRAXIS_POLL_LOOP_GUARD_SPEC` — override the spec path the read-gate points at
  (tests; an unresolvable value exercises the fail-open escape).
- `PRAXIS_POLL_LOOP_READ_GATE_AFTER` — prior blocks required before the read-gate
  escalates (default 2 → the 3rd block escalates). Unparseable → default.
- `PRAXIS_POLL_LOOP_WAITER_TTL` — seconds a background waiter with no computable
  end (`while`/`until`, or a `for` over a dynamic list) counts as armed
  (default 900). Unparseable or ≤ 0 → default.

## Fail-open

Malformed stdin JSON, non-Bash tool, unparseable count/sleep (e.g. `sleep $N`) →
exit 0 (pass).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path as _Path

_HERE = _Path(__file__).resolve()
# <root>/hooks/<role>/<name>/impl.py — parents[2] is hooks/. Anchoring on
# __file__ (never on cwd) is what makes the paths below resolve the same in a
# checkout and in an installed plugin; it is the same package-root rule
# `_lib/_fire_ledger._checkout_root` states for the ledger.
_HOOKS_DIR = _HERE.parents[2]

_sys_lib = str(_HOOKS_DIR / "_lib")
if _sys_lib not in sys.path:
    sys.path.insert(0, _sys_lib)
from _fire_ledger import count_session_fires  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import safe_tokenize  # type: ignore[import-not-found]  # noqa: E402
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "foreground-poll-loop-guard"  # manifest `name` — the fire-ledger key
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD"
_FOREGROUND_CEILING_S = 120  # Bash default timeout, seconds
_SAFE_MARGIN_S = 100  # block when worst-case can approach the ceiling
_READ_GATE_AFTER_BLOCKS = 2  # prior session blocks before the read-gate escalates
# A loop-shaped waiter has no computable end, so its armed window is a fixed
# default rather than its own sleep total (issue #1063).
_OPEN_ENDED_WAITER_TTL_S = 900.0
# The read-set owner: its PostToolUse(Read) leg records every `.md` Read of the
# session, spec.md included, so the escalation reuses that history instead of
# standing up a second one.
_MD_READ_HISTORY_IMPL = (
    _HOOKS_DIR / "postuse-correction" / "pre-edit-md-escape-advisory" / "impl.py"
)

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
                # Keep the terminator: the result is handed to safe_tokenize,
                # whose own heredoc pass (issue #985) would otherwise read the
                # opener as unterminated and drop every command after it.
                out.append(line)
            continue  # inside a heredoc body — drop
        line = _strip_comment(line)
        for m in _HEREDOC_RE.finditer(line):
            heredoc_delims.append((m.group(2), "<<-" in m.group(0)))
        out.append(line)
    return "\n".join(out)


def _command_position_flags(tokens: list[str]) -> list[bool]:
    """Per token: is it in command position (where bash reads a command name)?

    Command position is the start of the stream, anything after a separator, and
    the successor of a reserved word that opens a compound command (`do`,
    `then`, `else`, `elif`) — but only when that word was itself in command
    position, so a literal word list (`for i in do done`) cannot fake a
    boundary.

    Every rule this guard states about a bare word — `sleep`, `for`, `done` —
    is a rule about that word in command position. `echo sleep 20` prints a
    string; `echo done` closes nothing.
    """
    flags: list[bool] = []
    grants_next = False
    for i, tok in enumerate(tokens):
        cmd_pos = i == 0 or tokens[i - 1] in _COMMAND_SEPARATORS or grants_next
        grants_next = cmd_pos and tok in ("then", "else", "elif", "do")
        flags.append(cmd_pos)
    return flags


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
    # (`echo done`) must not open/advance/close a loop frame.
    cmd_positions = _command_position_flags(tokens)
    for i, tok in enumerate(tokens):
        if not cmd_positions[i]:
            continue
        if tok in _LOOP_KEYWORDS:
            stack.append([i, None])
        elif tok == "do":
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
    """Parseable `sleep N[smhd]` arguments inside a loop body, in seconds.

    Command position only: `do echo sleep 20; done` sleeps for nothing. A body
    slice always begins right after its `do`, so index 0 of the slice is itself
    command position.
    """
    out: list[float] = []
    cmd_positions = _command_position_flags(body)
    for i, tok in enumerate(body[:-1]):
        if not cmd_positions[i]:
            continue
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


# ---------------------------------------------------------------------------
# Background waiter-chain advisory (issue #1063)
# ---------------------------------------------------------------------------


def _waiter_profile(command: str) -> tuple[str, float, str] | None:
    """`(target_key, armed_seconds, display)` when this command is a waiter, else None.

    A *waiter* is a call whose whole job is to elapse: it contains a parseable
    `sleep` in command position. Launching the awaited work itself
    (`bash run-tests.sh > /tmp/out &`) carries no `sleep` and is therefore
    never recorded — this lane is about waiting on work, not doing it.

    **The target key is the sleep-normalized command signature.** The three
    candidates #1063 names are not equally usable. A *job id* names the waiter,
    not what it waits on, and does not exist yet at PreToolUse time. An *output
    file path* is present only when the waiter redirects or reads a file, so
    `until gh pr checks; do sleep 20; done` — a waiter with no file anywhere —
    would key on nothing. The command signature is the only one every waiting
    call has, and it is what the observed failure actually looked like: the
    retries differed from each other *only in the sleep duration* (240 → 595).
    Dropping each `sleep`'s argument, and only that argument, collapses exactly
    that family into one key.

    **`sleep` counts only in command position.** `echo sleep 20` prints a word
    and waits for nothing; matching it at any token position would register a
    waiter for a command that does no waiting, and then advise against the next
    genuine one. The rule is the one `_iter_loops` already applies to `for` /
    `done` — start of stream, after a separator, or after `do` / `then` /
    `else` / `elif`.

    Only the sleep argument. Dropping bare numerals generally would fold
    `gh run watch 123` and `gh run watch 456` together — two genuinely
    different targets whose sole discriminator is a bare number — and turn the
    guard into one that fires on every second background call.
    """
    tokens = safe_tokenize(_strip_non_executable(command))
    if not tokens:
        return None

    normalized: list[str] = []
    total_sleep = 0.0
    saw_sleep = False
    skip_next = False
    cmd_positions = _command_position_flags(tokens)
    for i, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if not cmd_positions[i] or (tok != "sleep" and not tok.endswith("/sleep")):
            normalized.append(tok)
            continue
        m = _SLEEP_ARG_RE.match(tokens[i + 1]) if i + 1 < len(tokens) else None
        if m is None:
            normalized.append(tok)  # `sleep $VAR` — unparseable, keep verbatim
            continue
        saw_sleep = True
        total_sleep += float(m.group(1)) * _SLEEP_UNIT_S[m.group(2)]
        normalized.append("sleep")  # `/bin/sleep` and `sleep` are one shape
        skip_next = True
    if not saw_sleep:
        return None

    armed = _armed_window(tokens, total_sleep)
    key = hashlib.sha1("\x00".join(normalized).encode("utf-8")).hexdigest()[:16]
    display = " ".join(command.split())
    if len(display) > 120:
        display = display[:117] + "..."
    return key, armed, display


def _armed_window(tokens: list[str], total_sleep: float) -> float:
    """Seconds this waiter counts as armed. `total_sleep` = each `sleep` counted once.

    A loop's sleep is per-iteration, so the flat sum undercounts it. When the
    iteration count is unknowable — `while` / `until`, or a `for` over a dynamic
    word list — there is no computable end at all and the fixed TTL is the only
    honest window (`_open_ended_ttl`).

    A `for` loop whose count IS computable is not that shape. `for i in 1 2; do
    sleep 1; done` returns in ~2s, and arming it for 900s makes a re-launch
    minutes later — long after it returned — report a live waiter that does not
    exist. The same `_for_iterations` the block path already uses supplies the
    count; the body sleeps are each counted once in `total_sleep`, so only the
    remaining `n - 1` iterations are added.

    Nested finite loops are UNDER-counted: an inner loop's sleeps appear in its
    own entry and again in its parent's body, and the parent multiplies them
    only once. An under-counted window expires early, which costs an advisory
    that does not fire — the same direction every other fail-open here takes.
    """
    extra = 0.0
    for kw, header, body in _iter_loops(tokens):
        per_iteration = sum(_sleep_args(body))
        if per_iteration <= 0:
            continue
        iterations = _for_iterations(header) if kw == "for" else None
        if iterations is None:
            return _open_ended_ttl()
        extra += max(iterations - 1, 0) * per_iteration
    return max(total_sleep + extra, 0.0)


def _open_ended_ttl() -> float:
    """Armed window for a loop-shaped waiter. `PRAXIS_POLL_LOOP_WAITER_TTL` overrides."""
    raw = os.environ.get("PRAXIS_POLL_LOOP_WAITER_TTL", "").strip()
    if not raw:
        return _OPEN_ENDED_WAITER_TTL_S
    try:
        value = float(raw)
    except ValueError:
        return _OPEN_ENDED_WAITER_TTL_S
    return value if value > 0 else _OPEN_ENDED_WAITER_TTL_S


def _waiter_registry_path(session_id: str | None) -> str:
    key = session_id or str(os.getppid())
    return resolve_cache_file(f"poll-loop-waiters-{key}.json", session_id=key)


def _load_waiters(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_waiters(path: str, waiters: dict) -> None:
    """Publish the registry atomically, staging through a per-process name.

    Per-process because a `<path>.tmp` shared with a sibling hook process on
    the same `session_id` corrupts rather than loses (DESIGN.md Q0): the
    shorter write lands over the longer one's tail and every reader's
    `except ValueError` answers *empty registry*.

    Unlocked, by the same DESIGN.md criterion: no threshold reads this state
    (the test is "a live entry exists", not an exact count) and no gate decides
    block-vs-pass from it — this lane is advisory-only. A lost update costs one
    advisory that does not fire, which is Q3.
    """
    tmp_path = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(waiters, fh)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _background_waiter_advisory(command: str, session_id: str | None) -> str | None:
    """Advisory when a live waiter for this same target is already armed, else None.

    Records the waiter as a side effect. An entry is written only when no live
    one exists for the key: refreshing on every duplicate would push the armed
    window forward indefinitely and make the reported age of the original
    waiter a lie.
    """
    profile = _waiter_profile(command)
    if profile is None:
        return None
    key, armed, display = profile

    path = _waiter_registry_path(session_id)
    waiters = _load_waiters(path)
    now = time.time()

    live = {
        k: v
        for k, v in waiters.items()
        if isinstance(v, dict)
        and isinstance(v.get("at"), (int, float))
        and isinstance(v.get("armed"), (int, float))
        and now - v["at"] < v["armed"]
    }

    existing = live.get(key)
    if existing is None:
        live[key] = {"at": now, "armed": armed, "cmd": display}
        _save_waiters(path, live)
        return None

    if live != waiters:  # expired entries pruned — publish the smaller registry
        _save_waiters(path, live)

    age = int(now - existing["at"])
    remaining = max(int(existing["armed"] - (now - existing["at"])), 0)
    return (
        f"[{_HOOK_NAME}] A background waiter for this same target was launched "
        f"{age}s ago and stays armed for ~{remaining}s more:\n"
        f"    $ {existing.get('cmd', '<unknown>')}\n"
        "This call waits on it a second time. A background call re-invokes Claude "
        "when it exits, so the notification is already armed — a second waiter "
        "does not make it arrive sooner, and every one of them outlives the turn "
        "that launched it.\n"
        "Correct path: wait for the notification already armed. If waiters have "
        "piled up, list them (/tasks) and TaskStop every one but the newest — a "
        "waiter nobody reaps is killed by the harness hours later.\n"
        f"Set {_BYPASS_ENV}=1 to silence this guard."
    )


def _emit_additional_context(advisory: str) -> None:
    """Write the advisory as `hookSpecificOutput.additionalContext` (issue #1063).

    stderr alone leaves this lane inert. A PreToolUse hook's stderr is fed to the
    model only when the dispatcher exits 2 (`docs/hook/INDEX.md`); on the exit-0
    path it reaches the debug log and nothing else, so the advisory would never
    be seen by the agent it is written for. `additionalContext` is the one
    PreToolUse channel that does reach the model (`_dispatch.py` forwards and
    merges it once deny and ask have both missed).

    The stderr write is KEPT alongside it: `_fire_ledger.classify_decision`
    derives `advise` from stderr, so moving the text to stdout would reclassify
    every fire as `pass`. Shape mirrors `pipefail-advisory` — `PreToolUse` event
    name, no top-level `continue` (a PreToolUse stop-processing switch whose
    default is already what is wanted here).
    """
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": advisory,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


def _reference_path() -> str:
    """Absolute path to this guard's own spec.md (issue #1012).

    Package-root-anchored, never cwd-relative: the reference is both what the
    block message prints AND the key the read-gate looks up in the session's
    read-set, so a repo-relative string would name a file that does not exist
    when the agent is working outside the praxis checkout — the agent that
    followed the reference would still be denied. `PRAXIS_POLL_LOOP_GUARD_SPEC`
    overrides it so tests can point the gate at a path that does not resolve.
    """
    override = os.environ.get("PRAXIS_POLL_LOOP_GUARD_SPEC", "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return str(_HERE.parent / "spec.md")


def _read_gate_threshold() -> int:
    """Prior session blocks required before the read-gate escalates."""
    raw = os.environ.get("PRAXIS_POLL_LOOP_READ_GATE_AFTER", "").strip()
    try:
        return max(int(raw), 0) if raw else _READ_GATE_AFTER_BLOCKS
    except ValueError:
        return _READ_GATE_AFTER_BLOCKS  # unparseable → default, never a crash


def _spec_read_in_session(session_id: str, spec_path: str) -> bool:
    """True when `spec_path` was Read in this session (issue #1012).

    Reuses the read-set `pre-edit-md-escape-advisory` already maintains — its
    PostToolUse(Read) leg records EVERY `.md` path Read in the session, so
    spec.md is already in there and a second read-history mechanism would only
    be a second thing to drift. Loaded by file location under a unique module
    name (that sibling hook's module is also called `impl`) and only from the
    escalation branch, which runs solely on a command this guard is already
    blocking — the hot Bash path never pays for the import.

    Any failure returns True (= "treat as read"), so a broken lookup relaxes the
    gate instead of denying on the strength of state it could not read.
    """
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_praxis_poll_loop_md_read_history", _MD_READ_HISTORY_IMPL
        )
        if spec is None or spec.loader is None:
            return True
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        history_path = module.resolve_history_path(session_id)
        return module.normalize_path(spec_path) in module.get_read_set(history_path)
    except Exception:
        return True


def _read_gate_engaged(payload: dict, reference: str) -> int | None:
    """Prior-block count when the read-gate should escalate, else None.

    Returns None (no escalation) when: the payload carries no session_id, the
    reference does not resolve on disk (the mandatory fail-open escape — a gate
    that cannot find its own spec must not add a requirement nobody can
    satisfy), the session has not been blocked enough times yet, or the spec has
    already been Read this session.
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not os.path.isfile(reference):
        return None
    # The dispatcher records a member's fire AFTER running its main(), so this
    # is the count of PRIOR blocks this session, not including the in-flight one.
    prior_blocks = count_session_fires(_HOOK_NAME, session_id, decision="block")
    if prior_blocks < _read_gate_threshold():
        return None
    if _spec_read_in_session(session_id, reference):
        return None
    return prior_blocks


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV):
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""
    if not command.strip():
        return 0

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = None

    # Backgrounded polling is the CORRECT pattern and is never blocked. It is,
    # however, where the behaviour this guard was built to stop moved once the
    # foreground form started being denied (issue #1063): chaining short
    # background waiters across turns passed every time. Advisory, not a block —
    # the guard's own redirect message names `run_in_background: true` as the
    # correct path, so denying it would contradict the escape it hands out.
    if tool_input.get("run_in_background") is True:
        advisory = _background_waiter_advisory(command, session_id)
        if advisory:
            sys.stderr.write(advisory + "\n")
            _emit_additional_context(advisory)
        return 0

    reason = _detect(command)
    if reason is None:
        return 0

    # Issue #1012: the referent used to be `docs/hook/foreground-poll-loop-guard.md`,
    # a 132-byte redirect stub whose entire content is "Moved to <spec.md>". The
    # gate now names the spec itself, by absolute package-root-anchored path.
    reference = _reference_path()
    correct_path = (
        "use a native async-wait primitive: run_in_background: true "
        "(Claude is re-invoked on exit — no polling loop needed), a "
        "Monitor until-loop, `aws cloudformation wait`, `gh run watch` / "
        "`gh pr checks --watch` / `kubectl wait`; a short bounded "
        "foreground poll (worst-case < 90s) is fine"
    )
    why = (
        f"{reason} — the foreground call dies with SIGTERM (Exit 143) "
        "mid-poll even when the awaited async op succeeds"
    )

    prior_blocks = _read_gate_engaged(payload, reference)
    if prior_blocks is not None:
        why = (
            f"{why}. READ-GATE: this guard has already blocked {prior_blocks}x in "
            "this session and the same loop shape came back, so the block message "
            "is not being consumed"
        )
        correct_path = (
            f"Read {reference} FIRST — a poll-loop retry stays denied until that "
            f"Read is recorded in this session. Then {correct_path}"
        )

    emit_block(
        rule_name="foreground poll-loop guard",
        why=why,
        correct_path=correct_path,
        bypass_env=_BYPASS_ENV,
        reference=reference,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
