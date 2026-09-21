"""Per-session repeat counting for gate blocks (issue #1405).

`postuse-correction/second-failure-advisory` exists to say "you have hit this
same failure twice — read the spec instead of reshaping the command". It
cannot say it about a gate block: it listens on `PostToolUseFailure`, and the
hooks reference states that event "does not fire for permission denials or
schema-validation rejections (those never started executing)". A block IS a
permission denial, so the advisory's own `_extract_reference` — written to
pull a `Reference:` line out of a block message — is handed a payload class it
never receives. Measured on one session: 18 identical blocks, 0 advisories,
and the block's normalized signature absent from all 42 state files on disk.

The count therefore has to happen where the block is produced. `emit_block` is
that place for 23 of the 37 `preflight-gate` directories, so one call site
covers most of the surface. `emit_decision` is the second such place (issue
#1420): the gates that deny through `permissionDecision` never render the
five-field message, and for them the reason string — not stderr — is the only
channel the model reads. The gates that hand-roll both stay uncovered.

Session id: `CLAUDE_SESSION_ID` first, then the `.current-session` latch
`completion-verify/strike-counter` writes at SessionStart — the same env-then-
latch order that hook's own `resolve_from_env` uses. The latch names the last
session SessionStart saw, so with concurrent sessions it can name a sibling;
that costs a repeat notice attributed to the wrong session, which is why the
env var is consulted first and why nothing here blocks on the answer. No id
at all means no counting and an unchanged message.

Concurrency (DESIGN.md#session-state-concurrency): Q1 — a threshold reads this
state (the notice fires at `prior >= 1`), so the read-modify-write is locked,
and Q0 — the publish stages through a per-pid name so a fail-open lock cannot
degrade into a truncated file that reads back as a fresh, empty count.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _paths import praxis_state_dir, resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _state_lock import state_lock  # type: ignore[import-not-found]  # noqa: E402

_LATCH_NAME = ".current-session"
_DISABLE_ENV = "PRAXIS_BLOCK_REPEAT_DISABLE"
_FILE_ENV = "PRAXIS_BLOCK_REPEAT_FILE"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def session_id() -> str | None:
    """This session's id, or None when neither source answers."""
    sid = (os.environ.get("CLAUDE_SESSION_ID") or "").strip()
    if sid:
        return sid
    try:
        with open(os.path.join(praxis_state_dir(), _LATCH_NAME), "r", encoding="utf-8") as fh:
            sid = fh.read().strip()
    except OSError:
        return None
    return sid or None


def _state_path(sid: str) -> str:
    override = os.environ.get(_FILE_ENV, "").strip()
    if override:
        return override
    return resolve_cache_file(f"block-repeat-{sid}.json", session_id=sid)


def _load(path: str) -> dict[str, int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rules = data.get("rules") if isinstance(data, dict) else None
        if isinstance(rules, dict):
            return {k: v for k, v in rules.items() if isinstance(v, int)}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {}


def _save(path: str, rules: dict[str, int]) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"rules": rules}, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


_MARKER_RE = re.compile(r"^\s*\[([^\]\n]{1,64})\]")


def reason_key(reason: str) -> tuple[str, str]:
    """`(state key, display label)` for a deny reason; `("", "")` when empty.

    A bracketed marker at the head of the reason (`[praxis:pipefail-advisory]`)
    is the gate's own id, which is what "the same block" means for the model
    reading it, so it keys directly. Without one the first non-empty line keys
    it instead, hashed rather than stored: a reason routinely embeds the
    command, a path, or a session id, and a state file carrying those verbatim
    would leak them into a cache file nothing else redacts.
    """
    if not reason:
        return "", ""
    marker = _MARKER_RE.match(reason)
    if marker:
        label = marker.group(1).strip()
        if label:
            return f"reason:{label.lower()}", label
    first = next((line.strip() for line in reason.splitlines() if line.strip()), "")
    if not first:
        return "", ""
    digest = hashlib.sha1(" ".join(first.lower().split()).encode("utf-8")).hexdigest()[:12]
    label = first if len(first) <= 60 else first[:59] + "…"
    return f"reason:{digest}", label


def record_reason(reason: str) -> str:
    """Count a deny reason and return the escalation notice, or "" the first time.

    `emit_block` covers the gates that render the five-field block message.
    The ones that deny through `permissionDecision` never touch it, and their
    reason string is the only channel the model reads — so the notice has to be
    appended to the reason rather than written to stderr (issue #1420).
    """
    key, label = reason_key(reason)
    if not key:
        return ""
    return record(key, notice=_decision_notice, label=label)


def _decision_notice(label: str, count: int) -> str:
    return (
        f"\n\n🔁 Repeat: this gate has denied the same thing {count} times this session "
        f"({label}).\n"
        "Reshaping the call and throwing it again is guessing. Open the file this reason\n"
        "names, restate its decision predicate in one line, and fix against that\n"
        "predicate — a bypass token is only for a skip condition that file documents."
    )


def _notice(rule_name: str, count: int) -> str:
    return (
        f"\n\n🔁 Repeat: {rule_name.upper()} has blocked {count} times this session.\n"
        "Reshaping the command and throwing it again is guessing. Open the file named on\n"
        "the Reference line above, restate its decision predicate in one line, and fix\n"
        "against that predicate — a bypass token is only for a skip condition that file\n"
        "documents."
    )


def record(
    rule_name: str,
    notice: Callable[[str, int], str] = _notice,
    label: str | None = None,
) -> str:
    """Count this block and return the repeat notice, or "" on the first one.

    Never raises: a counter that cannot be kept must not take a gate's block
    message down with it.
    """
    if _truthy(os.environ.get(_DISABLE_ENV)):
        return ""
    try:
        sid = session_id()
        if not sid:
            return ""
        path = _state_path(sid)
        with state_lock(path):
            rules = _load(path)
            count = rules.get(rule_name, 0) + 1
            rules[rule_name] = count
            _save(path, rules)
        return notice(label or rule_name, count) if count >= 2 else ""
    except Exception:
        return ""
