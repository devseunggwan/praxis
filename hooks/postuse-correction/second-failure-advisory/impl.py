#!/usr/bin/env python3
"""PostToolUse hook: advisory on repeated identical failures.

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

`tool_response` reaches this hook in two shapes, and the failing one is a
**string** (issue #1265):

- a plain string — the shape a FAILED call actually arrives in. Censused over
  10,467 unique Bash `toolUseResult` entries in 120 live transcripts: 388 are
  strings, every one with `tool_result.is_error == True`, and not one
  successful Bash call is ever a string. Matched by whitelist, `_is_failed` ->
  `_string_failure_text`: text opening with `Error: `, or the literal
  `User rejected tool use` (a denial carries no prefix). The single
  `Error: `-prefixed non-failure — the harness's oversized-output notice for a
  *successful* call whose result was spilled to a file, emitted with
  `is_error == False` — is excluded by name.
- a dict — the shape a SUCCESSFUL call arrives in, plus synthetic/legacy
  payloads. Failure markers, in order:

- `isError`/`is_error is True`
- `status == "error"`
- a non-empty `error` field
- `interrupted is True`
- `exit` is a non-zero integer (synthetic/legacy payloads; real Bash omits it)
- **non-Bash only**: `error`/`stderr` non-empty (legacy/SDK text-only shape),
  read through the harness-noise filter below

Non-empty `stderr` alone is NOT a failure for Bash (issue #1096)
---------------------------------------------------------------

Real Bash `tool_response` payloads carry no exit status and no error field —
verified as `{stdout, stderr, interrupted, isImage, noOutputExpected}` against
live session transcripts — so the only failure signals actually available for a
Bash call are `interrupted` (a killed/timed-out run) and, if one is ever
present, an explicit `isError`/`is_error`/`status == "error"`/`error` marker.
Crucially, a *successful* command that writes to `stderr` — `git
fetch`/`clone`/`checkout` progress, `curl` progress meters, deprecation
warnings, all exit 0 — is byte-for-byte indistinguishable, by its `stderr`
content, from a genuine failure. #1042 stripped only the one harness cwd-reset
line; any *other* `stderr` content still fell through to the back-compat check
and was mislabelled a failure, so the second identical success-with-stderr call
injected a false "N회째 실패" advisory into context. `_is_failed` therefore no
longer treats `stderr` text alone as failure evidence for Bash — the
back-compat `error`/`stderr` fallback runs for non-Bash tools only.

`stderr` is read through the harness-noise filter below before the non-Bash
back-compat check runs — see issue #1042.

That Bash guard does NOT extend to the string path (issue #1265). It exists
because a *dict* payload's `stderr` cannot separate an exit-0 Bash success from
a failure, and both #1042 and #1096 were exactly that: exit-0 successes
arriving as dicts with non-empty `stderr`. A string payload has no `stderr`
field to be ambiguous about — the harness has already made the call and encoded
it in the text. With both roads closed the hook fired 135,030 times and
recorded `decision: pass` on every one.

Harness noise in `stderr` (issue #1042)
=======================================

The `exit` key this hook was written against is not what real Bash
`tool_response` payloads carry: verified against live session transcripts
(`~/.claude/projects/.../*.jsonl`, `toolUseResult` for a `Bash` tool_use),
the actual shape is `{stdout, stderr, interrupted, isImage,
noOutputExpected}` — no `exit`, no `isError`. Every one of those calls
falls straight to the back-compat `error`/`stderr` check, and this harness
appends `"\nShell cwd was reset to <cwd>"` to `stderr` on **every** Bash
call, success or not (this repo's own cwd-reset-between-calls behavior).
That line is not failure evidence, but the back-compat check could not
tell it apart from a real one:

- Every exit-0 command carrying only that line was counted as a failure
  (68 firings in one real session, the last 5 all on exit-0 commands).
- Once normalized, that line is the same string regardless of the
  command that ran, so unrelated calls collapsed onto one
  `(tool_name, signature)` pair and one constant signature
  (`ede370078f51`, confirmed against six independent real session state
  files, all sharing that exact hash).

`_strip_harness_noise` removes that line (and only that line) from
`stderr` before it is used as failure evidence or signature material.
Genuine content on other lines of the same `stderr` blob survives.

The strip is also gated on `tool_name == "Bash"` — the noise line is a
verified property of this harness's own Bash execution path, not of `stderr`
in general. Without the gate, any other tool (or a hypothetical genuine
failure whose only stderr line happens to match that shape) would have its
real failure text silently deleted and be misread as a success or merged
into an unrelated signature.

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

For a string payload the string itself is the signature material — it is the
only failure evidence there is, which is what keeps two unrelated string
failures on distinct signatures rather than collapsing them onto one pair
(the issue #1042 defect-2 shape). One string shape carries nothing to tell two
failures apart — a bare `Error: Exit code N` with no output under it (6 of 388
observed), byte-identical whatever command died. For that shape only, the
command from `tool_input` is appended as the distinguishing text
(`_BARE_EXIT_CODE_RE`), so two unrelated commands no longer share a pair while
the same command failing twice still does.

For a dict payload the candidate text is drawn from
`error`/`stderr`/`output`/`stdout` in that order (`_derive_failure_text`); `stderr` goes through the same harness-noise
filter as failure detection (issue #1042) before it is used, so a failure
whose `stderr` carries only the harness's cwd-reset notice falls through to
`output`/`stdout` for its distinguishing text instead of normalizing to the
same constant string as every other call in the session.
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


# String-shaped `tool_response` (issue #1265). A failed tool call reaches this
# hook not as a dict but as a plain string; the two shapes below are the whole
# observed failure surface, censused over 10,467 unique Bash `toolUseResult`
# entries in 120 session transcripts: 388 of them are strings, every one
# carrying `tool_result.is_error == True`, and no successful Bash call is ever a
# string. Matching is a whitelist, not "any string is a failure", so a shape
# that has not been observed as a failure cannot start firing the advisory.
_STRING_FAILURE_PREFIX = "Error: "
_STRING_REJECTION_TEXT = "user rejected tool use"

# The one string that opens with `Error: ` and is NOT a failure: the harness's
# oversized-output notice, emitted with `is_error == False` when a *successful*
# call's result is spilled to a file (27 observations, all on MCP tools). It has
# to be excluded by name, or every large successful result reads as a failure.
_STRING_OVERSIZED_OUTPUT_RE = re.compile(
    r"^Error: result \(.*?\) exceeds maximum allowed tokens", re.IGNORECASE
)

# A string failure that is ONLY the exit-code line, with no command output under
# it — 6 of the 388 observed. Every command that dies this way produces the exact
# same bytes, so unrelated failures would share one signature and the second one
# would advise "the same error pattern twice" about two different commands. That
# is the #1042 defect-2 shape, and this hook must not ship it.
_BARE_EXIT_CODE_RE = re.compile(r"^Error: Exit code \d+$")


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

# Injected by this harness into every Bash `stderr`, success or not, when it
# resets the shell's cwd between calls — not failure evidence (issue #1042).
_HARNESS_CWD_RESET_RE = re.compile(r"(?im)^[ \t]*Shell cwd was reset to .*$")


def _strip_harness_noise(text: str, tool_name: str) -> str:
    """Remove the harness's own cwd-reset notice from a `stderr` blob.

    Verified against real `Bash` `tool_response` payloads (session
    transcripts): this exact line is present on every call regardless of
    success, and nothing else in those payloads carries an `exit` field for
    the failure check below to consult instead.

    Gated on `tool_name == "Bash"` (issue #1071 review) — that verification
    only covers Bash. Applying the same regex to every tool's `stderr`
    regardless of origin would delete a non-Bash tool's genuine error text if
    it happened to match the same line shape, misreading a real failure as a
    success.
    """
    if tool_name != "Bash":
        return text.strip()
    return _HARNESS_CWD_RESET_RE.sub("", text).strip()


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


def _string_failure_text(tool_response: str) -> str:
    """Failure text carried by a string-shaped `tool_response`, else `""`.

    Returning the text rather than a bool is what keeps unrelated failures on
    distinct signatures (issue #1042 defect 2): the string *is* the only failure
    evidence the payload carries, so it is both the failure marker and the
    signature material.
    """
    text = tool_response.strip()
    if not text:
        return ""
    if _STRING_OVERSIZED_OUTPUT_RE.match(text):
        return ""
    if text.startswith(_STRING_FAILURE_PREFIX):
        return text
    if text.lower() == _STRING_REJECTION_TEXT:
        return text
    return ""


def _derive_failure_text(tool_response: Any, tool_name: str) -> str:
    if isinstance(tool_response, str):
        return _string_failure_text(tool_response)
    if not isinstance(tool_response, dict):
        return ""
    if isinstance(tool_response.get("error"), str):
        error_text = tool_response.get("error", "").strip()
        if error_text:
            return error_text
    if isinstance(tool_response.get("stderr"), str):
        err = _strip_harness_noise(tool_response.get("stderr", ""), tool_name)
        if err:
            return err
    if isinstance(tool_response.get("output"), str):
        out = tool_response.get("output", "").strip()
        if out:
            return out
    if isinstance(tool_response.get("stdout"), str):
        out = tool_response.get("stdout", "").strip()
        if out:
            return out
    return ""


def _derive_error_text(tool_response: Any, tool_name: str) -> str:
    """Error-channel text only.

    Distinct from `_derive_failure_text`, which also reads `output`/`stdout` to
    build a signature: those two carry a *successful* tool's normal output, so
    treating them as evidence of failure classifies every repeated success as a
    repeated failure.
    """
    if not isinstance(tool_response, dict):
        return ""
    error_text = tool_response.get("error")
    if isinstance(error_text, str) and error_text.strip():
        return error_text.strip()
    stderr_text = tool_response.get("stderr")
    if isinstance(stderr_text, str):
        stripped = _strip_harness_noise(stderr_text, tool_name)
        if stripped:
            return stripped
    return ""


def _is_failed(tool_response: Any, tool_name: str) -> bool:
    # String-shaped payload (issue #1265) — decided before, and independently
    # of, the `tool_name == "Bash"` guard at the bottom of this function. That
    # guard exists because a *dict* payload's `stderr` cannot tell an exit-0
    # Bash success from a failure (#1042, #1096), and both of those defects were
    # exit-0 successes arriving as dicts with non-empty `stderr`. A string
    # payload has no `stderr` field to be ambiguous about: the harness has
    # already made the success/failure call and encoded it in the text itself.
    # Extending the Bash guard here would re-close the only road left open —
    # which is exactly the 135,030-fire, zero-advisory silence of #1265.
    if isinstance(tool_response, str):
        return bool(_string_failure_text(tool_response))

    if not isinstance(tool_response, dict):
        return False

    # Explicit failure markers — trusted for every tool, Bash included. Real
    # Bash payloads do not carry any of these (issue #1096), but if one ever
    # appears we honour it so a genuinely-marked failure still fires.
    if tool_response.get("isError") is True or tool_response.get("is_error") is True:
        return True

    status = tool_response.get("status")
    if isinstance(status, str) and status.strip().lower() == "error":
        return True

    error_field = tool_response.get("error")
    if isinstance(error_field, str) and error_field.strip():
        return True

    interrupted = tool_response.get("interrupted")
    if interrupted is True:
        return True

    exit_code = tool_response.get("exit")
    if exit_code is not None:
        try:
            return int(exit_code) != 0
        except (TypeError, ValueError):
            pass

    # Back-compat path (issue #944): older/SDK payloads that surfaced a failure
    # only through non-empty `error`/`stderr` text.
    #
    # Issue #1096: NOT applied to Bash. A real Bash `tool_response` carries no
    # exit status and no error field — `{stdout, stderr, interrupted, isImage,
    # noOutputExpected}`, verified against live session transcripts — so a
    # success-with-stderr command (git fetch/clone/checkout progress, curl
    # meters, deprecation warnings; all exit 0) is indistinguishable, by its
    # `stderr` content, from a genuine failure. #1042 stripped only the one
    # harness cwd-reset line; any other `stderr` content still fell through here
    # and was mislabelled a failure, letting a repeated success-with-stderr call
    # inject a false "N회째 실패" advisory. For Bash a failure is now signalled
    # only by the real indicators above (interrupted, or an explicit
    # error/isError/status marker when actually present), never by `stderr`
    # content. The text-only fallback stays for non-Bash tools (issue #1071
    # gate; see case 17).
    if tool_name == "Bash":
        return False
    return bool(_derive_error_text(tool_response, tool_name))


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


def _compute_signature(
    tool_name: str, tool_response: Any, tool_input: dict[str, Any] | None = None
) -> str:
    text = _derive_failure_text(tool_response, tool_name)

    # The bare exit-code line carries nothing to tell two failures apart, so the
    # command that produced it is appended as the distinguishing text. It comes
    # from `tool_input` in the same hook payload — the signature still depends on
    # nothing outside the call being judged. Narrow by design: any failure whose
    # text has real content is already distinguishable and is left untouched.
    if isinstance(tool_response, str) and _BARE_EXIT_CODE_RE.match(text):
        command = (tool_input or {}).get("command")
        if isinstance(command, str) and command.strip():
            text = f"{text}\n{command.strip()}"

    normalized = _normalize_signature(text)
    if not normalized:
        normalized = "<empty>"
    digest = hashlib.sha1(f"{tool_name}\0{normalized}".encode("utf-8")).hexdigest()
    return digest


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
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _emit_advisory(tool_name: str, signature: str, reference: str, occurrence: int) -> None:
    """Emit the advisory for the `occurrence`-th failure of this pair (>= 2).

    Written as `hookSpecificOutput.additionalContext` (DESIGN.md, PostToolUse
    corrective emissions), mirroring `builtin-task-postuse`. A PostToolUse hook
    that exits 0 has its stderr routed to the debug log, never to the model — so
    the stderr form would leave the retry loop uncorrected, which is the one
    thing this hook exists to do.
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
                "hookEventName": "PostToolUse",
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

    tool_response = payload.get("tool_response")
    if not _is_failed(tool_response, tool_name):
        return 0

    tool_input = _extract_tool_input(payload)
    signature = _compute_signature(tool_name, tool_response, tool_input)
    ref = _extract_reference(
        tool_input,
        _derive_failure_text(tool_response, tool_name),
    )

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

        prior_count = 0
        count = failures.get(pair_key)
        if isinstance(count, int) and count > 0:
            prior_count = count

        failures[pair_key] = prior_count + 1

        # Persist before advising: a lost write would let the same advisory
        # fire again on the next failure of this pair.
        saved = _save_state(path, state)

    if not saved:
        return 0

    occurrence = prior_count + 1
    if occurrence >= _ADVISORY_FROM_OCCURRENCE:
        _emit_advisory(tool_name, signature, ref, occurrence)
    return 0


if __name__ == "__main__":
    sys.exit(main())
