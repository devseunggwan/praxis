#!/usr/bin/env python3
"""PreToolUse(Bash) guard: validate gh CLI flag-subcommand compatibility.

Intercepts every Bash tool call containing a `gh <subcmd> [<subsubcmd>]`
invocation and blocks it when any supplied `--flag` or `-x` short flag is
not in the subcommand's accepted set.

Motivation: Claude routinely pattern-matches a flag from one subcommand into
another where it does not exist (e.g. `--state all` on `gh search issues`,
`--base` on `gh issue list`), causing an `invalid argument` error that wastes
a round-trip. A static table checked before execution catches these before
the command is issued.

Design notes:
- Static frozen table: faster than runtime --help parsing, deterministic
  across gh versions in most use cases, easily extendable via issue/PR.
- Flag set sourced from `gh <subcmd> --help` output (verified live — see PR
  #176 for the captured --help outputs backing each entry).
- Unknown subcommands are transparent pass-throughs (fail-open). Only
  subcommands explicitly listed in COMPAT are validated.
- Short flags are only validated when the subcommand's table includes them.
  Many subcommands share `-R / --repo` as an inherited global flag; these
  are listed in GH_GLOBAL_FLAGS and always allowed regardless of subcommand.
- block-gh-state-all.py covers the specific `--state all` case on gh search
  subcommands. This hook may co-block the same command; both hooks run in
  parallel (PreToolUse) and deny > ask precedence means double-block is
  harmless.
- Exits 2 (deny) when an invalid flag is detected. Exits 0 otherwise.
- COMPAT values are dict[str, bool] where True = flag takes a value token.
  Value tokens after value-taking flags are consumed and never re-interpreted
  as flag identifiers. This correctly handles positional query strings that
  start with '-' (e.g. `gh search issues "-label:bug"`).
- Role-aware tokenization (issue #263): `tokenize_with_roles` does the
  value-skip centrally based on the derived `_FLAG_VALUE_SPEC`. Each FLAG
  identifier the caller sees is already separated from its value, and the
  `--` argv separator is honored automatically via SEPARATOR_DD / POST_DD
  roles. The caller's only job is whitelist-match against COMPAT.
"""
from __future__ import annotations

import json
import os
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    filter_argv,
    tokenize_with_roles,
)

# ---------------------------------------------------------------------------
# gh global flags accepted by every subcommand (inherited flags).
# These are always allowed regardless of subcommand. Sourced from
# `gh issue list --help` / `INHERITED FLAGS` section (verified 2026-05-11).
# Note: --hostname and --color appear in `gh --help` top-level help but are
# NOT accepted by subcommands — `gh issue list --hostname github.com` returns
# "unknown flag: --hostname". Only --help/-h and --repo/-R are truly inherited.
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS: frozenset[str] = frozenset({
    "--help", "-h",
    "--repo", "-R",
})

# gh global flags that consume one additional argument value token.
GH_GLOBAL_FLAGS_WITH_ARG: frozenset[str] = frozenset({
    "-R", "--repo",
})

# ---------------------------------------------------------------------------
# Compatibility table.
# Keys: (subcommand, subsubcommand) tuples where subsubcommand is "" when
#       there is no second word (e.g. ("issue", "list") vs ("search", "issues")).
# Values: dict[flag_name, takes_value] where takes_value=True means the flag
#         consumes the next token as its value (e.g. --assignee string).
#         takes_value=False means the flag is boolean (e.g. --archived).
#
# Sources: `gh <subcmd> [<subsubcmd>] --help` run on 2026-05-11.
# Short flags are listed in the FLAGS section as `-x, --longflag` pairs —
# only the short form is included here when the long form is already present.
# The GH_GLOBAL_FLAGS set covers inherited flags; entries below are
# subcommand-specific flags only (avoids duplication).
#
# Value-taking entries (True) skip the following token so that positional
# query strings starting with '-' (GitHub advanced-search exclusion syntax,
# e.g. `gh search issues "-label:bug"`) are not misidentified as flags.
# ---------------------------------------------------------------------------

COMPAT: dict[tuple[str, str], dict[str, bool]] = {
    # -----------------------------------------------------------------------
    # gh search issues
    # Source: `gh search issues --help`
    # -----------------------------------------------------------------------
    ("search", "issues"): {
        "--app": True,
        "--archived": False,
        "--assignee": True,
        "--author": True,
        "--closed": True,
        "--commenter": True,
        "--comments": True,
        "--created": True,
        "--include-prs": False,
        "--interactions": True,
        "--involves": True,
        "--jq": True, "-q": True,
        "--json": True,
        "--label": True,
        "--language": True,
        "--limit": True, "-L": True,
        "--locked": False,
        "--match": True,
        "--mentions": True,
        "--milestone": True,
        "--no-assignee": False,
        "--no-label": False,
        "--no-milestone": False,
        "--no-project": False,
        "--order": True,
        "--owner": True,
        "--project": True,
        "--reactions": True,
        "--repo": True, "-R": True,
        "--sort": True,
        "--state": True,        # accepts {open|closed} only — NOT "all"
        "--team-mentions": True,
        "--template": True, "-t": True,
        "--updated": True,
        "--visibility": True,
        "--web": False, "-w": False,
    },

    # -----------------------------------------------------------------------
    # gh search prs
    # Source: `gh search prs --help`
    # -----------------------------------------------------------------------
    ("search", "prs"): {
        "--app": True,
        "--archived": False,
        "--assignee": True,
        "--author": True,
        "--base": True, "-B": True,
        "--checks": True,
        "--closed": True,
        "--commenter": True,
        "--comments": True,
        "--created": True,
        "--draft": False,
        "--head": True, "-H": True,
        "--interactions": True,
        "--involves": True,
        "--jq": True, "-q": True,
        "--json": True,
        "--label": True,
        "--language": True,
        "--limit": True, "-L": True,
        "--locked": False,
        "--match": True,
        "--mentions": True,
        "--merged": False,
        "--merged-at": True,
        "--milestone": True,
        "--no-assignee": False,
        "--no-label": False,
        "--no-milestone": False,
        "--no-project": False,
        "--order": True,
        "--owner": True,
        "--project": True,
        "--reactions": True,
        "--repo": True, "-R": True,
        "--review": True,
        "--review-requested": True,
        "--reviewed-by": True,
        "--sort": True,
        "--state": True,        # accepts {open|closed} only — NOT "all"
        "--team-mentions": True,
        "--template": True, "-t": True,
        "--updated": True,
        "--visibility": True,
        "--web": False, "-w": False,
    },

    # -----------------------------------------------------------------------
    # gh search repos
    # Source: `gh search repos --help`
    # -----------------------------------------------------------------------
    ("search", "repos"): {
        "--archived": False,
        "--created": True,
        "--followers": True,
        "--forks": True,
        "--good-first-issues": True,
        "--help-wanted-issues": True,
        "--include-forks": True,
        "--jq": True, "-q": True,
        "--json": True,
        "--language": True,
        "--license": True,
        "--limit": True, "-L": True,
        "--match": True,
        "--number-topics": True,
        "--order": True,
        "--owner": True,
        "--size": True,
        "--sort": True,
        "--stars": True,
        "--template": True, "-t": True,
        "--topic": True,
        "--updated": True,
        "--visibility": True,
        "--web": False, "-w": False,
    },

    # -----------------------------------------------------------------------
    # gh issue list
    # Source: `gh issue list --help`
    # -----------------------------------------------------------------------
    ("issue", "list"): {
        "--app": True,
        "--assignee": True, "-a": True,
        "--author": True, "-A": True,
        "--jq": True, "-q": True,
        "--json": True,
        "--label": True, "-l": True,
        "--limit": True, "-L": True,
        "--mention": True,
        "--milestone": True, "-m": True,
        "--search": True, "-S": True,
        "--state": True, "-s": True,    # accepts {open|closed|all}
        "--template": True, "-t": True,
        "--web": False, "-w": False,
    },

    # -----------------------------------------------------------------------
    # gh pr list
    # Source: `gh pr list --help`
    # -----------------------------------------------------------------------
    ("pr", "list"): {
        "--app": True,
        "--assignee": True, "-a": True,
        "--author": True, "-A": True,
        "--base": True, "-B": True,
        "--draft": False, "-d": False,
        "--head": True, "-H": True,
        "--jq": True, "-q": True,
        "--json": True,
        "--label": True, "-l": True,
        "--limit": True, "-L": True,
        "--search": True, "-S": True,
        "--state": True, "-s": True,    # accepts {open|closed|merged|all}
        "--template": True, "-t": True,
        "--web": False, "-w": False,
    },

    # -----------------------------------------------------------------------
    # gh issue create
    # Source: `gh issue create --help`
    # -----------------------------------------------------------------------
    ("issue", "create"): {
        "--assignee": True, "-a": True,
        "--body": True, "-b": True,
        "--body-file": True, "-F": True,
        "--editor": False, "-e": False,
        "--label": True, "-l": True,
        "--milestone": True, "-m": True,
        "--project": True, "-p": True,
        "--recover": True,
        "--template": True, "-T": True,
        "--title": True, "-t": True,
        "--web": False, "-w": False,
    },

    # -----------------------------------------------------------------------
    # gh pr create
    # Source: `gh pr create --help`
    # -----------------------------------------------------------------------
    ("pr", "create"): {
        "--assignee": True, "-a": True,
        "--base": True, "-B": True,
        "--body": True, "-b": True,
        "--body-file": True, "-F": True,
        "--draft": False, "-d": False,
        "--dry-run": False,
        "--editor": False, "-e": False,
        "--fill": False, "-f": False,
        "--fill-first": False,
        "--fill-verbose": False,
        "--head": True, "-H": True,
        "--label": True, "-l": True,
        "--milestone": True, "-m": True,
        "--no-maintainer-edit": False,
        "--project": True, "-p": True,
        "--recover": True,
        "--reviewer": True, "-r": True,
        "--template": True, "-T": True,
        "--title": True, "-t": True,
        "--web": False, "-w": False,
    },

    # -----------------------------------------------------------------------
    # gh issue comment
    # Source: `gh issue comment --help`
    # -----------------------------------------------------------------------
    ("issue", "comment"): {
        "--body": True, "-b": True,
        "--body-file": True, "-F": True,
        "--create-if-none": False,
        "--delete-last": False,
        "--edit-last": False,
        "--editor": False, "-e": False,
        "--web": False, "-w": False,
        "--yes": False,
    },

    # -----------------------------------------------------------------------
    # gh pr comment
    # Source: `gh pr comment --help`
    # -----------------------------------------------------------------------
    ("pr", "comment"): {
        "--body": True, "-b": True,
        "--body-file": True, "-F": True,
        "--create-if-none": False,
        "--delete-last": False,
        "--edit-last": False,
        "--editor": False, "-e": False,
        "--web": False, "-w": False,
        "--yes": False,
    },
}


# ---------------------------------------------------------------------------
# Role-aware flag-value spec derivation (issue #263).
#
# The role-aware tokenizer needs to know which flags consume a separate-token
# value so that the value lands as FLAG_VALUE (not POSITIONAL). Since
# `_resolve_subcommand` is single-level, we derive the per-(command,verb)
# spec from COMPAT and aggregate at the "<command> <verb>" key. Globals
# include every value-taking flag that appears anywhere in COMPAT, so a
# misplaced subcommand flag at the global position (e.g. `gh --base main
# issue list`) still consumes its value cleanly — and the caller then denies
# on the flag name itself (it is not in GH_GLOBAL_FLAGS).
# ---------------------------------------------------------------------------


def _build_flag_value_spec() -> dict[str, set[str]]:
    """Derive `tokenize_with_roles` spec from COMPAT + GH_GLOBAL_FLAGS_WITH_ARG."""
    spec: dict[str, set[str]] = {"gh": set(GH_GLOBAL_FLAGS_WITH_ARG)}
    for (obj, _), flags in COMPAT.items():
        sub_key = f"gh {obj}"
        spec.setdefault(sub_key, set())
        for flag_name, takes_value in flags.items():
            if takes_value:
                spec[sub_key].add(flag_name)
                # Also accept the same flag at the gh-level position so that
                # erroneous placements before the subcommand still consume
                # their value — the caller denies on the name.
                spec["gh"].add(flag_name)
    return spec


_FLAG_VALUE_SPEC: dict[str, set[str]] = _build_flag_value_spec()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_flag_form(text: str) -> bool:
    """True iff text has the form of a real CLI flag (--long or -x).

    Tokens like '-label:bug' (single dash + multiple chars) are GitHub
    advanced-search exclusion qualifiers used as positional arguments.
    They tokenize with a leading '-' and are tagged FLAG by the role API,
    but they are NOT real flag identifiers and should be skipped.
    """
    if text.startswith("--"):
        return True
    if len(text) == 2 and text[0] == "-" and text[1] != "-":
        return True
    return False


def check_gh_flags(seg: list[Token]) -> tuple[bool, str]:
    """Check a single command segment for gh flag compatibility.

    Returns (is_invalid, reason_message). is_invalid=True means deny.

    Walk strategy (typed Token):
      1. argv[0] must be COMMAND `gh`.
      2. Pre-subcommand pass: every FLAG before the first POSITIONAL must
         be in GH_GLOBAL_FLAGS (whitelist). FLAG_VALUE tokens are skipped
         (already consumed by the role API).
      3. First POSITIONAL → subcommand verb. Skip subsequent FLAG / FLAG_VALUE
         tokens to find the second POSITIONAL → sub-subcommand verb.
      4. Lookup `(subcommand, sub_subcommand)` in COMPAT — fail-open (silent
         pass-through) if not present.
      5. Post-subcommand pass: every FLAG after the sub-subcommand must be
         in COMPAT[key] ∪ GH_GLOBAL_FLAGS. SEPARATOR_DD / POST_DD ends the
         flag region.
    """
    argv = filter_argv(seg)
    if not argv or argv[0].text != "gh":
        return False, ""

    n = len(argv)
    i = 1

    # Step 2: Validate pre-subcommand flags against GH_GLOBAL_FLAGS.
    while i < n:
        tok = argv[i]
        if tok.role == TokenRole.POSITIONAL:
            break  # found subcommand verb
        if tok.role == TokenRole.FLAG:
            if not _is_valid_flag_form(tok.text.split("=", 1)[0]):
                i += 1
                continue
            bare = tok.text.split("=", 1)[0]
            if bare not in GH_GLOBAL_FLAGS:
                allowed_list = ", ".join(sorted(GH_GLOBAL_FLAGS))
                reason = (
                    f"Global flag '{bare}' is not a recognized gh inherited flag. "
                    f"Allowed: {allowed_list}. "
                    f"Note: --hostname and --color are not accepted by gh subcommands."
                )
                return True, reason
        # FLAG_VALUE / SUBST_RUN / etc. — skip.
        i += 1

    if i >= n:
        return False, ""  # no subcommand present

    subcommand = argv[i].text
    i += 1

    # Step 3: find sub-subcommand POSITIONAL. Skip intervening flag tokens.
    sub_subcommand = ""
    sub_idx: int | None = None
    j = i
    while j < n:
        tok = argv[j]
        if tok.role in (TokenRole.SEPARATOR_DD, TokenRole.POST_DD):
            break
        if tok.role == TokenRole.POSITIONAL:
            sub_subcommand = tok.text
            sub_idx = j
            break
        j += 1

    if sub_idx is None:
        return False, ""  # unknown subcommand shape — pass through

    key = (subcommand, sub_subcommand)
    if key not in COMPAT:
        return False, ""  # unknown subcommand — pass through

    subcommand_flags = COMPAT[key]
    allowed: frozenset[str] = frozenset(subcommand_flags) | GH_GLOBAL_FLAGS

    # Step 5: validate every FLAG after the sub-subcommand against allowed set.
    # Also re-check any FLAGs that appeared BETWEEN the subcommand and the
    # sub-subcommand — those belong to the same allowed set (cf. test T08
    # and `gh issue --repo X create` form).
    for k in range(i, n):
        if k == sub_idx:
            continue  # the sub-sub POSITIONAL itself
        tok = argv[k]
        if tok.role in (TokenRole.SEPARATOR_DD, TokenRole.POST_DD):
            break
        if tok.role != TokenRole.FLAG:
            continue
        bare = tok.text.split("=", 1)[0]
        if not _is_valid_flag_form(bare):
            continue  # positional that looks like a search qualifier — ignore
        if bare not in allowed:
            subcmd_display = (
                f"gh {subcommand} {sub_subcommand}".strip()
                if sub_subcommand
                else f"gh {subcommand}"
            )
            reason = (
                f"Flag '{bare}' is not valid for '{subcmd_display}'. "
                f"Run 'gh {subcommand}"
                + (f" {sub_subcommand}" if sub_subcommand else "")
                + " --help' to see accepted flags."
            )
            return True, reason

    return False, ""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _emit_deny(reason: str) -> None:
    emit_decision("deny", reason)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


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

    # Collapse backslash line continuations so multi-line invocations parse
    # as a single command segment (same pre-processing as sibling hooks).
    command = command.replace("\\\n", " ")

    segments = tokenize_with_roles(command, _FLAG_VALUE_SPEC)
    if not segments:
        return 0

    for seg in segments:
        is_invalid, reason = check_gh_flags(seg)
        if is_invalid:
            _emit_deny(reason)
            return 2  # deny exit code

    return 0


if __name__ == "__main__":
    sys.exit(main())
