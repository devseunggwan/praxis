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
"""
from __future__ import annotations

import json
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    compound_cascade_hint,
    filter_argv,
    tokenize_with_roles,
)
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
    if not argv or argv[0].text != "gh":
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
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    # Backslash line continuation → single space so tokenizer sees one line
    command = command.replace("\\\n", " ")

    segments = tokenize_with_roles(command, _FLAG_VALUE_SPEC)
    if not segments:
        return 0

    for seg in segments:
        if is_blocked_gh_search(seg):
            sys.stderr.write(STDERR_MESSAGE + "\n" + compound_cascade_hint(command))
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
