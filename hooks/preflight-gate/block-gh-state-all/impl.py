#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh search <subcmd> ... --state all`.

`gh issue list` and `gh pr list` accept `--state all`.
`gh search issues` / `gh search prs` only accept `--state {open|closed}`.
Conflating these causes `invalid argument "all" for "--state" flag` — a
recurring mistake caught by structural enforcement rather than a memo.

Uses the role-aware token API (issue #263) so flag-value attribution is
handled centrally. The check reduces to: COMMAND is `gh`, subcommand
positional is `search`, an object positional follows, and somewhere after
that a FLAG `--state` is followed by FLAG_VALUE `all` (or a single FLAG
token `--state=all`).

Exits 2 (PreToolUse blocking code) when the command is a live `gh search`
call with `--state all`. Exits 0 otherwise (transparent pass-through).

Rewrite arm (issue #1334, opt-in via `PRAXIS_BLOCK_GH_STATE_ALL_REWRITE=1`).
The fix here is not a judgement call: `--state all` is invalid for `gh search`
and omitting it returns every state, so the corrected command is the one the
caller meant. Under the arm the hook drops the flag and lets the call through
with an `updatedInput`, instead of costing a turn for the model to retype it.

Two guards keep the arm from correcting something it did not understand:

  • **Single segment only.** `--state all` is VALID for `gh issue list`, so in
    `gh search issues x --state all && gh issue list --state all` a textual
    removal would break the second half. A compound command keeps the block.
  • **Token-level readback.** The removal is textual (the tokenizer keeps no
    offsets, so rebuilding the command from tokens would lose the original
    quoting), then verified: the corrected command must re-tokenize to exactly
    the original tokens minus the `--state` / `all` pair, and must no longer
    trip the detector. Anything else falls back to the block.
"""
from __future__ import annotations

import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    _is_gh_binary,
    compound_cascade_hint,
    filter_argv,
    tokenize_with_roles,
)
from _hook_io import emit_updated_input  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

# flag_value_spec for the role-aware tokenizer.
#   gh global flags: -R / --repo take a separate-token value
#   gh search subcommand: --state takes a value (so `all` lands as FLAG_VALUE)
_FLAG_VALUE_SPEC: dict[str, set[str]] = {
    "gh": {"-R", "--repo"},
    "gh search": {"--state"},
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_blocked_gh_search(seg: list[Token]) -> bool:
    """Return True iff seg is a live `gh search <subcmd> ... --state all` call.

    Walks the typed Token list:
      1. argv[0] must be COMMAND "gh".
      2. Skip leading FLAG / FLAG_VALUE tokens (gh-level globals like
         `--no-pager`, `-R owner/repo`).
      3. The next POSITIONAL must be exactly `search`.
      4. Skip more FLAG / FLAG_VALUE tokens.
      5. The next POSITIONAL is the search object word (issues / prs /
         repos / commits / code — gh itself validates the name).
      6. After the object word, scan for `--state all` (FLAG + FLAG_VALUE)
         or `--state=all` (single FLAG token).
    """
    argv = filter_argv(seg)
    if not argv or not _is_gh_binary(argv[0].text):
        return False

    i = 1
    n = len(argv)

    # Skip pre-search FLAG / FLAG_VALUE tokens.
    while i < n and argv[i].role in (TokenRole.FLAG, TokenRole.FLAG_VALUE):
        i += 1

    if i >= n or argv[i].role != TokenRole.POSITIONAL or argv[i].text != "search":
        return False
    i += 1

    # Skip FLAG / FLAG_VALUE tokens between `search` and the object word.
    while i < n and argv[i].role in (TokenRole.FLAG, TokenRole.FLAG_VALUE):
        i += 1

    if i >= n or argv[i].role != TokenRole.POSITIONAL:
        return False  # no object word — not a valid gh search invocation
    i += 1

    # Scan remaining tokens for --state all. SEPARATOR_DD / POST_DD end the
    # flag region — `gh` does not accept POSIX `--` but we honor it defensively.
    while i < n:
        tok = argv[i]
        if tok.role in (TokenRole.SEPARATOR_DD, TokenRole.POST_DD):
            break
        if tok.role == TokenRole.FLAG:
            if tok.text == "--state=all":
                return True
            if tok.text == "--state" and i + 1 < n:
                nxt = argv[i + 1]
                if nxt.role == TokenRole.FLAG_VALUE and nxt.text == "all":
                    return True
        i += 1

    return False


# Rewrite arm switch (issue #1334). Exact value "1" only, mirroring
# `PRAXIS_PIPEFAIL_ADVISORY_CONTEXT` and the other opt-in arms.
_REWRITE_ENV = "PRAXIS_BLOCK_GH_STATE_ALL_REWRITE"

# `--state all`, in either spelling, with the value optionally quoted. Anchored
# on surrounding whitespace so `--state allowed` and `--no-state all` are not
# touched; the readback below is what actually certifies the result.
_STATE_ALL_RE = re.compile(r"""\s+--state(?:=|\s+)(?P<q>['"]?)all(?P=q)(?=\s|$)""")


def _argv_texts(command: str) -> list[list[str]]:
    """Token texts per segment — the readback's unit of comparison."""
    return [
        [tok.text for tok in filter_argv(seg)]
        for seg in tokenize_with_roles(command, _FLAG_VALUE_SPEC)
    ]


def corrected(command: str) -> str | None:
    """Return `command` without `--state all`, or None when it cannot be certified.

    None is the fail-closed answer: the caller then blocks, which is what this
    hook did before the arm existed. A correction is only returned when the
    result re-tokenizes to the original tokens minus exactly the `--state` and
    `all` entries, and no longer trips the detector.
    """
    fixed, n = _STATE_ALL_RE.subn("", command)
    if n != 1 or not fixed.strip():
        return None
    before, after = _argv_texts(command), _argv_texts(fixed)
    if len(before) != 1 or len(after) != 1:
        return None
    expected = [t for t in before[0] if t not in ("--state", "all", "--state=all")]
    dropped = [t for t in before[0] if t in ("--state", "all", "--state=all")]
    if after[0] != expected or dropped not in (["--state", "all"], ["--state=all"]):
        return None
    if any(is_blocked_gh_search(seg) for seg in tokenize_with_roles(fixed, _FLAG_VALUE_SPEC)):
        return None
    return fixed


STDERR_MESSAGE = format_block(
    rule_name="gh search --state all",
    why="`gh search` subcommands only accept --state {open|closed}, not 'all' "
        "(unlike `gh issue/pr list`)",
    correct_path="omit --state entirely (returns all states), or run two calls: "
        "--state open and --state closed",
    bypass_env=None,  # no bypass — the flag is simply invalid for gh search
    reference="docs/hook/block-gh-state-all.md; CLAUDE.md → GitHub CLI usage",
)


@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command.strip():
        return 0

    # Backslash line continuation → single space so tokenizer sees one line
    continued = "\\\n" in command
    command = command.replace("\\\n", " ")

    segments = tokenize_with_roles(command, _FLAG_VALUE_SPEC)
    if not segments:
        return 0

    if not any(is_blocked_gh_search(seg) for seg in segments):
        return 0

    # `continued` keeps the arm off a command the tokenizer had to normalize:
    # the correction would then also silently join the caller's line breaks,
    # and a rewrite must change exactly the one thing it claims to change.
    if (
        os.environ.get(_REWRITE_ENV, "").strip() == "1"
        and len(segments) == 1
        and not continued
    ):
        fixed = corrected(command)
        if fixed is not None:
            # The context line is not decoration: the transcript's tool_use
            # record keeps the command the model wrote, so without this the
            # correction is invisible to every later reader.
            base = payload.get("tool_input")
            emit_updated_input(
                {**(base if isinstance(base, dict) else {}), "command": fixed},
                f"[praxis:block-gh-state-all] dropped `--state all`, which "
                f"`gh search` rejects:\n  {command}\n-> {fixed}",
            )
            return 0

    sys.stderr.write(STDERR_MESSAGE + "\n" + compound_cascade_hint(command))
    return 2


if __name__ == "__main__":
    sys.exit(main())
