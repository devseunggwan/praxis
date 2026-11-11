#!/usr/bin/env python3
"""merge-gate check: require explicit user approval before `gh pr merge`.

Moved from pre-merge-approval-gate (issue #1650); the verdict is unchanged.

Every Claude session MUST receive per-PR merge approval from the user — merge
is shared-state and irreversible.

Detection: tokenizes the Bash command with the praxis shlex pipeline and scans
for any segment whose argv[0..2] == ("gh", "pr", "merge"). If found, emit
permissionDecision "ask" so Claude Code surfaces a confirmation dialog before
executing the merge.

No opt-out marker and no environment bypass are provided. Issue #180's contract
is that a merge ALWAYS surfaces a per-PR approval prompt — an agent-attachable
comment marker would let the agent silently self-bypass the same gate it is
meant to enforce. The background-agent exemption that #180 originally carved out
was removed in #1055: a delegated worker now runs as an ordinary session with a
live stdin, so the human at the pane answers the prompt just as in a direct
session.

Host approval provider (issue #1639). A host that collects the human's merge
answer in its own channel can name an executable in this hook process's
environment (`PRAXIS_MERGE_APPROVAL_PROVIDER`); a merge it verifies skips the
prompt. This is not an agent-attachable bypass: an inline env prefix on the
command never reaches the hook, and the verdict comes from the same function
the briefing check uses (`host_merge_decision`), which still checks
the briefing and spends one approval per merge. Unset, nothing changes.
"""
from __future__ import annotations

import os
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    _is_gh_binary,
    is_help_invocation,
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

# gh global flags that consume one additional argument (value).
# When these appear between `gh` and the subcommand object, they must be
# skipped so the subcommand check lands at the correct argv position.
# Mirrors the same constant in external-write-falsify-check.py.
GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

REASON = format_block(
    rule_name="gh pr merge approval",
    why="merge is shared-state and irreversible — direct interactive sessions "
        "require explicit per-PR merge approval",
    correct_path="confirm this merge when the dialog surfaces; approval is "
        "per-PR and does not transfer to sibling/companion PRs",
    # No agent-attachable bypass by design — a self-bypass would defeat the
    # gate.
    bypass_env=None,
    reference="CLAUDE.md → Pre-Merge Reporting / No Approval Transfer Across "
        "Companion PRs; docs/hook/pre-merge-approval-gate.md",
)


def is_gh_pr_merge(argv: list[str]) -> bool:
    """Return True iff the argv segment is a `gh pr merge` invocation.

    gh global flags (-R/--repo, --hostname, --color) may appear between
    `gh` and the subcommand object. Walk past them before checking so
    `gh -R owner/repo pr merge` is detected correctly.
    """
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return False

    # Walk past any global flags (and their arguments) to find the subcommand.
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1

    if i + 1 >= len(argv):
        return False
    if argv[i] != "pr" or argv[i + 1] != "merge":
        return False
    # `gh pr merge --help` prints usage and merges nothing (issue #985).
    return not is_help_invocation(argv[i + 2:], GH_MERGE_VALUE_FLAGS)


def emit_ask(reason: str) -> None:
    emit_decision("ask", reason)


# Read here only to skip loading the briefing check when no provider is
# configured; _lib/_host_merge_approval.py owns the contract.
HOST_PROVIDER_ENV = "PRAXIS_MERGE_APPROVAL_PROVIDER"


def _briefing():
    """The briefing check module (merge-gate/briefing.py), loaded once."""
    import importlib.util

    name = "_praxis_merge_gate_briefing"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _Path(__file__).resolve().parent / "briefing.py")
    if spec is None or spec.loader is None:
        raise ImportError("merge-gate briefing check not loadable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def bulk_approved(payload: dict) -> bool:
    """True when the user already approved this merge in chat as one of a
    bulk-approved set (the briefing check decides; one parser, so the two
    checks cannot disagree about what was approved).

    Any failure returns False, so a broken lookup keeps the prompt.
    """
    try:
        return bool(_briefing().bulk_merge_approved(payload))
    except Exception:
        return False


def host_approval(payload: dict) -> tuple[bool, str]:
    """(released, note) from the host approval provider, decided by the same
    function the briefing check uses, so the provider is asked and the
    approval spent once per tool call. Only called when the host set
    `PRAXIS_MERGE_APPROVAL_PROVIDER`. Any failure keeps the prompt.
    """
    try:
        decision = _briefing().host_merge_decision(payload)
        return bool(decision.released), str(decision.note())
    except Exception as exc:
        return False, f"host approval provider: check failed ({type(exc).__name__})"


def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command.strip():
        return 0

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    found = any(is_gh_pr_merge(argv) for argv in iter_command_starts(tokens))
    if not found:
        return 0
    # The chat answer to a bulk briefing is the per-PR consent this prompt
    # stands in for; asking again per merge only invites the blind click the
    # briefing gate was built against.
    if bulk_approved(payload):
        return 0
    # A host that collects the human's merge answer in its own channel names
    # a provider in the hook environment; unset, this block never runs.
    note = ""
    if os.environ.get(HOST_PROVIDER_ENV):
        released, note = host_approval(payload)
        if released:
            return 0
        note = "\n" + note

    emit_ask(REASON + compound_cascade_hint(command) + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
