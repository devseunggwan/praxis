#!/usr/bin/env python3
"""PostToolUseFailure hook: advisory on repeated identical failures.

Issue #944 — when a tool keeps failing with the same `(tool_name,
error_signature)` pattern, the second retry should surface an advisory instead of
replaying the same failed action in a blind loop.

Scope
=====

Only the repeated *failure* path is handled:

- Malformed stdin -> fail-open (no output, exit 0)
- Missing `session_id` -> fail-open (no output, exit 0)
- Successful tool calls -> no state update, no output
- First failure for a `(tool_name, signature)` pair -> no output
- Second failure for the same pair in one session -> advisory
- Third+ failure for the same pair -> the advisory REPEATS, carrying the running
  occurrence number (issue #1012)

PostToolUseFailure (issue #1337)
================================

`PostToolUseFailure` is the only event this hook is registered on. It fires
"when a tool that started executing fails" and carries a top-level `error`
string whose first line, for Bash, is `Exit code N` (interleaved output
follows). The event is itself the failure evidence, so no allowlist is
applied to the text: every non-interrupted `PostToolUseFailure` counts, MCP
tools included. Three rules, in order:

- `is_interrupt: true` is NOT a failure of the command — the run was aborted
  before it could fail on its own — so the event is dropped without touching
  state.
- `error` that is not a string is an unknown shape -> fail-open, silent.
- Otherwise `error` is the failure text: it seeds the signature, and the
  bare-`Exit code N` rule folds the command in as a separate digest.

Each counted failure records its `tool_use_id` in the state file and an event
carrying an already-recorded id is dropped before the count moves, so one
tool call is counted once however many times it reaches the hook. The id list
is bounded (`_RECENT_TOOL_USE_IDS_MAX`) and ordered, so interleaved parallel
calls still dedupe.

A payload naming any other event is not a failure report and returns without
touching state — the reply's `hookEventName` has to match the event being
delivered, so an advisory raised on one would be discarded anyway.

Why 3..N also advise (issue #1012)
==================================

The hook used to fire on the exact `prior_count == 1` boundary, so a session that
kept replaying the same failure got exactly one advisory and then silence — and
that silence is indistinguishable, to the model reading the transcript, from the
loop having been noticed and accepted. The measured failure mode this hook exists
for is precisely the long run (the poll-loop family recurred 6x in one session,
5 of them after the first correction signal was already in-transcript), i.e. the
occurrences the boundary suppressed. The advisory now repeats from the 2nd
occurrence onward and names the count, so the signal gets stronger rather than
vanishing exactly where the loop is worst.

Failure detection
================

Arrival is the evidence: the harness fires this event only for a tool that
started executing and failed, so the payload needs no classifier and no
allowlist. Two shapes are dropped before the count moves — `is_interrupt:
true` and a non-string `error` — and both are stated in
`_failure_event_text`.

The hook once read `tool_response` on a second registration and derived
failure from its contents. Nothing here does that any more; why it existed,
what it cost (#1042, #1096, #1265) and why it was retired live in spec.md's
Registration history.

Signature derivation
===================

`error_signature` is a normalized version of the failure text used to suppress
retries that differ only by volatile values:

- file/path-like tokens -> `<path>`
- UUID-like identifiers -> `<uuid>`
- long hex hashes -> `<hash>`
- timestamps -> `<ts>`
- numeric IDs (`id=...`, `..._id=...`) -> `<id>`
- extra whitespace -> single spaces

This keeps retries that only changed `/tmp/run-<rand>.log`/timestamps/hash IDs from
being treated as distinct failures.

The `error` text is the whole signature material, which is what keeps two
unrelated failures on distinct signatures rather than collapsing them onto
one pair (the issue #1042 defect-2 shape). One shape carries nothing to tell
two failures apart — a bare `Exit code N` with no output under it (6 of 388
observed), byte-identical whatever command died. For that shape only
(`_BARE_EXIT_CODE_RE`), the command from `tool_input` joins the key as a
**separate digest** (`_command_discriminator`), so two unrelated commands no
longer share a pair while the same command failing twice still does.

One leading `Error: ` is stripped from the material (`_signature_material`,
issue #1337). The event's `error` field does not carry that envelope, but a
tool's own text can open with it, and a failure re-typed with and without it
is one failure.

The digest hashes the command as written, stripped of leading/trailing
whitespace only. Internal whitespace is shell-significant — `false\nfalse` is
two commands where `false false` is one, and `test 'a  b' = x` compares a
different string than `test 'a b' = x` — so collapsing it digested distinct
commands to one hash and re-created the very collision the discriminator
prevents. A command re-typed with different inner spacing now gets its own key
and stays silent on its second failure: a missed advisory, not a false one.

The digest is separate because normalization would otherwise eat the
discriminator: appending the command to the signature *text* runs it through
`_normalize_signature`, which turns `cat /tmp/a` and `cat /tmp/b` alike into
`cat <path>` — the collision the discriminator exists to prevent. The normalizer
is not weakened to fix this; the command is simply hashed outside it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any

from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _state_lock import state_lock  # type: ignore[import-not-found]  # noqa: E402


# `{n}` is the running occurrence number (2, 3, 4, …) — issue #1012 replaced the
# fixed "2회째" wording when the advisory stopped firing only on the 2nd failure.
_ADVISORY_PREFIX_TMPL = (
    "[second-failure-advisory] "
    "Failure #{n} of the same error pattern in this session — retrying "
    "without a root-cause read risks a blind retry loop. "
    "(동일한 오류 패턴으로 세션 내 {n}회째 실패가 감지되었습니다. "
    "원인 분석 없이 즉시 재시도하는 루프가 될 수 있습니다.) "
)

# The advisory starts on the 2nd occurrence of a pair and repeats for every
# occurrence after it (issue #1012).
_ADVISORY_FROM_OCCURRENCE = 2

_STATE_SCHEMA_VERSION = 1
_MAX_SIGNATURE_LEN = 4_096

# The one event this hook is registered on (issue #1337). `hook_event_name`
# is read from the payload and anything else — an absent field included —
# returns without touching state.
_EVENT_POSTTOOLUSE_FAILURE = "PostToolUseFailure"

# Bound on the per-session list of counted `tool_use_id`s (issue #1337). One
# call redelivered must count once; keeping only the last id would miss it
# when parallel calls interleave (A, B, A-again), so a short ordered window is
# kept instead. Sixteen covers far more concurrent calls than the harness
# runs, at 16 ids of state.
_RECENT_TOOL_USE_IDS_MAX = 16


# The harness envelope a failure text may open with. The event's `error`
# field does not carry it, but a tool's own message can, and it is stripped
# from the signature material so one failure written both ways is one pair.
_STRING_FAILURE_PREFIX = "Error: "


# A string failure that is ONLY the exit-code line, with no command output under
# it — 6 of the 388 observed. Every command that dies this way produces the exact
# same bytes, so unrelated failures would share one signature and the second one
# would advise "the same error pattern twice" about two different commands. That
# is the #1042 defect-2 shape, and this hook must not ship it.
#
# Matched against the signature MATERIAL (`_signature_material`), i.e. after
# any `Error: ` envelope is dropped, so `Exit code 1` — the doc's first-line
# contract for Bash — is what the pattern sees (issue #1337).
_BARE_EXIT_CODE_RE = re.compile(r"^Exit code \d+$")


# Reference candidates inside a blocking message, most explicit first.
_REFERENCE_LABEL_RE = re.compile(r"Reference:\s*([^\s\"'`,;]+)")
_HOOK_PATH_RE = re.compile(r"(?<!\S)(hooks/[^\s\"'`,;]+)")
_SPEC_PATH_RE = re.compile(r"(?<!\S)([^\s\"'`,;]*spec\.md)")

_PATH_RE = re.compile(r"(?<!\S)/[^\s\"'`]+")
_WIN_PATH_RE = re.compile(r"(?<!\S)[A-Za-z]:\\[^\s\"'`]+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HASH_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b|\b\d{4}-\d{2}-\d{2}\b"
)
_ID_RE = re.compile(r"\b([\w.-]*id)[:=]\s*[\"']?[\w-]+[\"']?", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _extract_session_id(payload: dict[str, Any]) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str):
        sid = sid.strip()
        if sid:
            return sid
    return None


def _extract_tool_name(payload: dict[str, Any]) -> str:
    name = payload.get("tool_name")
    if isinstance(name, str):
        return name.strip()
    return ""


def _extract_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        return tool_input
    return {}


def _extract_event_name(payload: dict[str, Any]) -> str:
    """`hook_event_name`, or `""` when absent or malformed.

    An empty name is not the failure event, so `main` returns on it — a
    payload that does not say which event it is cannot be a failure report.
    """
    name = payload.get("hook_event_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return ""


def _extract_tool_use_id(payload: dict[str, Any]) -> str:
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str):
        return tool_use_id.strip()
    return ""


def _failure_event_text(payload: dict[str, Any]) -> str | None:
    """Failure text of a PostToolUseFailure payload, or None when not counted.

    The event fires only for a tool that started executing and failed, so
    arrival is the failure evidence and the text needs no allowlist: unlike a
    `tool_response`, this payload cannot describe a call that succeeded.

    None for two shapes: `is_interrupt: true`, where the harness says the run
    reached it as an abort rather than as the command's own failure (counting
    it would advise on the user's interruptions), and a non-string `error`,
    which is a shape this hook has not seen and fails open on. An empty
    string is a failure with no text and normalizes to `<empty>`, exactly as
    the dict path does.
    """
    if payload.get("is_interrupt") is True:
        return None
    error = payload.get("error")
    if not isinstance(error, str):
        return None
    return error.strip()


def _extract_reference(tool_input: dict[str, Any], failure_text: str = "") -> str:
    """Path the agent should read before retrying.

    The failure text wins over `tool_input`: a gate that blocks names the spec
    holding its decision predicate (`Reference: hooks/.../spec.md`), while
    `tool_input` only names whatever the agent was already touching.
    """
    for pattern in (_REFERENCE_LABEL_RE, _HOOK_PATH_RE, _SPEC_PATH_RE):
        match = pattern.search(failure_text)
        if match:
            return match.group(1).strip()

    for key in ("file_path", "path", "target"):
        candidate = tool_input.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _normalize_signature(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""

    text = _PATH_RE.sub("<path>", text)
    text = _WIN_PATH_RE.sub("<path>", text)
    text = _UUID_RE.sub("<uuid>", text)
    text = _HASH_RE.sub("<hash>", text)
    text = _TIMESTAMP_RE.sub("<ts>", text)
    text = _ID_RE.sub(r"\1=<id>", text)

    text = _WS_RE.sub(" ", text).strip()
    if len(text) > _MAX_SIGNATURE_LEN:
        text = text[:_MAX_SIGNATURE_LEN]
    return text.lower()


def _command_discriminator(tool_input: dict[str, Any] | None) -> str:
    """Digest of the command text, kept OUT of the normalized signature.

    `_normalize_signature` absorbs paths, ids, hashes and timestamps so that two
    genuinely-equivalent errors match — which is exactly what destroys a command
    used as a discriminator: `cat /tmp/a` and `cat /tmp/b` both become
    `cat <path>`. Weakening the normalizer to save the discriminator would break
    the matching the normalizer exists for, so the command is hashed on its own
    and mixed into the pair key beside the normalized signature instead.

    The command text is hashed as written. Internal whitespace is NOT collapsed:
    in shell it is significant, so `false\nfalse` and `false false` are two
    programs, and `test 'a  b' = x` and `test 'a b' = x` compare different
    strings. Collapsing them digested distinct commands to one hash, which is
    the collision this function exists to prevent. Only leading/trailing
    whitespace is stripped — that is insignificant outside quotes, and it is
    what makes an all-whitespace command behave like an absent one.

    The cost is the reverse direction: a command re-typed with different inner
    spacing now gets its own key, so its second failure stays silent. That is a
    missed advisory, not a false one, and matching those retypes was never worth
    a discriminator that cannot discriminate.

    Case is NOT folded, because `cat A` and `cat a` are different files.
    Truncated to the same bound as a signature.
    """
    command = (tool_input or {}).get("command")
    if not isinstance(command, str):
        return ""
    stripped = command.strip()
    if not stripped:
        return ""
    return hashlib.sha1(stripped[:_MAX_SIGNATURE_LEN].encode("utf-8")).hexdigest()


def _signature_material(text: str) -> str:
    """Failure text with one leading `Error: ` envelope removed (issue #1337).

    The same failure reaches the hook with and without the prefix depending on
    whether the harness or the tool wrote the text; stripping it here lands
    both on one pair key.
    """
    text = text.strip()
    if text.startswith(_STRING_FAILURE_PREFIX):
        text = text[len(_STRING_FAILURE_PREFIX):].strip()
    return text


def _signature_from_text(
    tool_name: str,
    text: str,
    tool_input: dict[str, Any] | None,
    discriminate_bare: bool,
) -> str:
    """Pair signature for `text`, the failure evidence already extracted.

    `discriminate_bare` is True when a bare `Exit code N` line can be the whole
    text, which is what makes the command a necessary part of the key. It is a
    parameter rather than an assumption because the signature path is reached
    from one caller today and the bare-line rule is not a property of every
    text that could reach it.
    """
    material_text = _signature_material(text)
    normalized = _normalize_signature(material_text)
    if not normalized:
        normalized = "<empty>"

    # The bare exit-code line carries nothing to tell two failures apart, so the
    # command that produced it joins the key as a separate digest. It comes from
    # `tool_input` in the same hook payload — the signature still depends on
    # nothing outside the call being judged. Narrow by design: any failure whose
    # text has real content is already distinguishable and is left untouched, and
    # an absent/empty command leaves the key byte-identical to the old one.
    material = f"{tool_name}\0{normalized}"
    if discriminate_bare and _BARE_EXIT_CODE_RE.match(material_text):
        discriminator = _command_discriminator(tool_input)
        if discriminator:
            material = f"{material}\0{discriminator}"

    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _state_path(session_id: str) -> str:
    override = os.environ.get("PRAXIS_SECOND_FAILURE_ADVISORY_FILE", "").strip()
    if override:
        return override
    return resolve_cache_file(f"second-failure-advisory-{session_id}.json", session_id=session_id)


def _load_state(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {"schema_version": _STATE_SCHEMA_VERSION, "failures": {}}


def _save_state(path: str, state: dict[str, Any]) -> bool:
    """Publish the counter state, staging through a per-process name.

    `main()` holds `state_lock` over this, and that lock is fail-open by
    contract, so a shared `<path>.tmp` is one unacquired lock away from being
    live again: two processes writing that one name interleave, and the short
    write published over a longer one's tail leaves bytes `_load_state`
    answers with a FRESH dict — the session's whole failure count restarts
    rather than one increment going missing. The pid is the floor under that
    degraded path (issue #970 established the same floor for
    `jq-config-empty-dict-advisory`).
    """
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        # One staging file per pid, so a failed publish leaks an unbounded
        # number of them rather than reusing one name — unlink it here.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def _emit_advisory(
    tool_name: str,
    signature: str,
    reference: str,
    occurrence: int,
    event_name: str,
) -> None:
    """Emit the advisory for the `occurrence`-th failure of this pair (>= 2).

    Written as `hookSpecificOutput.additionalContext` (DESIGN.md, PostToolUse
    corrective emissions), mirroring `builtin-task-postuse`. A post-tool hook
    that exits 0 has its stderr routed to the debug log, never to the model — so
    the stderr form would leave the retry loop uncorrected, which is the one
    thing this hook exists to do.

    `event_name` is echoed as `hookEventName`: the harness accepts the reply
    only under the event it delivered (issue #1337).
    """
    message = _ADVISORY_PREFIX_TMPL.format(n=occurrence)
    message += f"{tool_name} failure pattern recurring, occurrence #{occurrence}. "
    message += f"signature={signature[:12]}"
    if reference:
        message += (
            f" Reference: {reference}"
            f" — before retrying, Read {reference} and restate its blocking"
            f" predicate in one line"
            f" (재시도 전에 {reference}를 read하고 차단 판정 술어를 한 줄로"
            f" 재진술하세요)."
        )
    else:
        message += (
            " Before retrying, restate the blocking predicate in one line"
            " (재시도 전에 차단 판정 술어를 한 줄로 재진술하세요)."
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
    payload = read_payload()
    if payload is None:
        return 0

    if not isinstance(payload, dict):
        return 0

    session_id = _extract_session_id(payload)
    if not session_id:
        return 0

    tool_name = _extract_tool_name(payload)
    if not tool_name:
        return 0

    tool_input = _extract_tool_input(payload)
    event_name = _extract_event_name(payload)

    # The event IS the failure evidence, and it is the only one this hook is
    # registered on. An absent or unexpected `hook_event_name` is therefore not
    # a failure report: the reply's `hookEventName` would name an event the
    # harness is not delivering, and it would be discarded anyway.
    if event_name != _EVENT_POSTTOOLUSE_FAILURE:
        return 0
    failure_text = _failure_event_text(payload)
    if failure_text is None:
        return 0
    signature = _signature_from_text(
        tool_name, failure_text, tool_input, discriminate_bare=True
    )

    ref = _extract_reference(tool_input, failure_text)
    tool_use_id = _extract_tool_use_id(payload)

    path = _state_path(session_id)
    pair_key = f"{tool_name}\0{signature}"

    # The whole read-modify-write is serialized (issue #951). Two processes that
    # read the same count both write count+1, so without the lock the same
    # occurrence number is reported twice and one increment is lost — the
    # duplicate fire recorded as unverified on #950. Persisting inside the lock
    # is what makes each process observe the other's increment. (Issue #1012
    # widened the fire condition from the `prior_count == 1` boundary to every
    # occurrence >= 2; the lost increment still mis-numbers the advisory, so the
    # lock is still load-bearing.)
    with state_lock(path):
        state = _load_state(path)
        failures = state.get("failures")
        if not isinstance(failures, dict):
            failures = {}
            state["failures"] = failures

        # Issue #1337: one call can reach the hook more than once. The first
        # arrival counts it and records the id; a redelivery finds the id and
        # leaves the count — and the model's context — untouched. Decided
        # inside the lock so two deliveries cannot both read "unseen".
        recent_ids = state.get("recent_tool_use_ids")
        if not isinstance(recent_ids, list):
            recent_ids = []
        if tool_use_id and tool_use_id in recent_ids:
            return 0

        prior_count = 0
        count = failures.get(pair_key)
        if isinstance(count, int) and count > 0:
            prior_count = count

        failures[pair_key] = prior_count + 1

        if tool_use_id:
            recent_ids.append(tool_use_id)
            state["recent_tool_use_ids"] = recent_ids[-_RECENT_TOOL_USE_IDS_MAX:]

        # Persist before advising: a lost write would let the same advisory
        # fire again on the next failure of this pair.
        saved = _save_state(path, state)

    if not saved:
        return 0

    occurrence = prior_count + 1
    if occurrence >= _ADVISORY_FROM_OCCURRENCE:
        _emit_advisory(tool_name, signature, ref, occurrence, event_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
