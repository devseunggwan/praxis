#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: nudge (or ask in strict mode) before
destructive filesystem / privilege-escalation commands.

Issue #463. Source pattern: `Yeachan-Heo/gajae-code`
`examples/hooks/permission-gate.ts` (MIT). praxis already gates mutation
CLIs via `side-effect-scan` (git/gh/kubectl) but has no structural backstop
for filesystem destruction (`rm -rf`, `mkfs`, `dd of=/dev/...`) or
privilege escalation (`sudo`/`doas`). Under auto / acceptEdits permission
modes the agent can issue these without an out-of-band confirmation.

Default behaviour: advisory only (stderr nudge, exit 0). Strict mode
(`PRAXIS_DESTRUCTIVE_BASH_STRICT=1`) escalates to `permissionDecision: ask`
via emit_decision JSON. Bypass (`PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH=1`)
skips the check entirely.

Detection model — uses the shared `safe_tokenize` / `iter_command_starts`
pipeline (DESIGN.md structural tokenization convention) so compound
commands like `cd /tmp && rm -rf .` are decomposed and each segment is
checked independently. `sudo`/`doas` are detected BEFORE `strip_prefix`
runs, since strip_prefix would peel them as wrapper commands and hide the
escalation signal.

Detected surfaces (issue #463 enumeration):

  • `rm -rf` / `rm -fr` / `rm -r --force` / `rm --recursive --force`
    (recursive force delete — `rm -i` and `rm <file>` are silent)
  • `sudo` / `doas` prefix (any subcommand — escalation is the signal)
  • `dd if=...` AND `dd of=...` (block-device write or arbitrary input)
  • `mkfs` / `mkfs.*` (filesystem creation)
  • `chmod -R 777` / `chown -R` (recursive permission/owner change)
  • Redirect to block device `> /dev/sd*`, `> /dev/nvme*`, `> /dev/disk*`
    (NOT `> /dev/null` or `> /dev/stderr`)
  • `git clean -fdx` / `git clean -fd` / `git clean -fx` etc.
  • `git reset --hard`
  • `find ... -delete`
  • `truncate -s 0` / `truncate --size 0`
  • `shred` (overwrite-and-delete)
  • Fork bomb `:(){ :|:& };:` — detected as the literal `:()` function-def
    token followed by recursive expansion

Fail-open contract:

  • Malformed JSON stdin → exit 0
  • Non-Bash tool → exit 0
  • Empty / whitespace command → exit 0
  • Any uncaught exception in inner logic → swallowed, exit 0
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_utils import safe_tokenize, iter_command_starts, strip_prefix  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402

# Privilege-escalation commands. Detected BEFORE strip_prefix — `sudo` is
# in PREFIX_WRAPPERS and would otherwise be silently peeled.
_ESCALATION_BINS = frozenset({"sudo", "doas"})

# Block-device redirect targets. The redirect token is `>` glued to or
# adjacent to a `/dev/sd*` / `/dev/nvme*` / `/dev/disk*` path.
_BLOCK_DEVICE_RE = re.compile(r"^/dev/(sd[a-z]+\d*|nvme\d+n\d+(p\d+)?|disk\d+(s\d+)?|hd[a-z]+\d*)$")

# Redirect-allowed device sinks (special device files that are safe to
# write to — these are common scripting idioms).
_SAFE_DEVICE_TARGETS = frozenset({
    "/dev/null",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/zero",  # write to zero is harmless (no-op for most ops)
    "/dev/tty",
})

# `find` mutation tokens (subset of inspection-chain-advisory's set — kept
# separate because the failure mode here is "deletes files", not "silent
# chain drop").
_FIND_DELETE_TOKENS = frozenset({"-delete", "--delete"})


def _segment_starts_with_escalation(raw_argv: list[str]) -> str | None:
    """Return the escalation binary name if sudo/doas appears anywhere
    in the prefix that `strip_prefix` would peel (env assignments,
    shell keywords, wrapper commands), or as the first real command.

    Naive `raw_argv[0]` check misses `VAR=1 sudo X`, `env sudo X`,
    `time sudo X`, and the `then sudo X` segment inside
    `if ...; then sudo X; fi`. Since strip_prefix lists `sudo` in
    PREFIX_WRAPPERS, scanning everything strip_prefix would peel covers
    all of those wrapper-position forms.
    """
    if not raw_argv:
        return None
    stripped = strip_prefix(raw_argv)
    prefix_len = len(raw_argv) - len(stripped)
    for tok in raw_argv[:prefix_len]:
        bare = os.path.basename(tok)
        if bare in _ESCALATION_BINS:
            return bare
    if stripped:
        bare = os.path.basename(stripped[0])
        if bare in _ESCALATION_BINS:
            return bare
    return None


def _is_rm_recursive_force(argv: list[str]) -> bool:
    """True iff argv invokes `rm` with both recursive AND force flags.

    Bundled short flags (`-rf`, `-fr`, `-Rf`) and long forms
    (`--recursive`, `--force`) both qualify. Anything after the literal
    `--` argv separator is a filename per POSIX, so `rm -r -- -f` is
    "recursive, target is a file literally named `-f`" — NOT force.
    """
    if not argv or os.path.basename(argv[0]) != "rm":
        return False
    has_recursive = False
    has_force = False
    for tok in argv[1:]:
        if tok == "--":
            break
        if tok in {"--recursive", "--force"}:
            if tok == "--recursive":
                has_recursive = True
            else:
                has_force = True
            continue
        if tok.startswith("-") and not tok.startswith("--"):
            flags = tok[1:]
            if "r" in flags or "R" in flags:
                has_recursive = True
            if "f" in flags:
                has_force = True
    return has_recursive and has_force


def _is_dd_dangerous(argv: list[str]) -> bool:
    """True iff argv invokes `dd` with either `if=...` or `of=...` operand.

    Bare `dd` without operands is a no-op; only `if=`/`of=` forms touch
    block devices or arbitrary files. The issue spec explicitly lists
    both — capturing both is intentional (a `dd if=/dev/urandom of=file`
    that floods disk is just as destructive as `dd of=/dev/sda`).
    """
    if not argv or os.path.basename(argv[0]) != "dd":
        return False
    return any(tok.startswith("if=") or tok.startswith("of=") for tok in argv[1:])


def _is_mkfs(argv: list[str]) -> bool:
    if not argv:
        return False
    bare = os.path.basename(argv[0])
    return bare == "mkfs" or bare.startswith("mkfs.")


def _is_chmod_recursive_open(argv: list[str]) -> bool:
    """True iff argv is `chmod` with recursive flag AND world-write mode.

    The recursive flag and the mode token can appear in any order
    (`chmod -R 777 .` and `chmod 777 -R .` are equivalent under POSIX),
    so the walk does NOT break early on the first non-flag positional.
    """
    if not argv or os.path.basename(argv[0]) != "chmod":
        return False
    has_recursive = False
    mode_token: str | None = None
    for tok in argv[1:]:
        if tok == "--":
            break
        if tok in {"-R", "--recursive"}:
            has_recursive = True
            continue
        if tok.startswith("-"):
            continue
        if mode_token is None:
            mode_token = tok
    if not has_recursive or mode_token is None:
        return False
    return mode_token in {"777", "0777", "666", "0666"}


def _is_chown_recursive(argv: list[str]) -> bool:
    if not argv or os.path.basename(argv[0]) != "chown":
        return False
    return any(tok in {"-R", "--recursive"} for tok in argv[1:])


def _is_redirect_to_block_device(argv: list[str]) -> bool:
    """True iff argv contains a `>` followed by a `/dev/sd*` etc. target.

    Forms covered (safe_tokenize splits on `>` so these end up as
    separate tokens):
      `cmd > /dev/sda`         → tokens [..., '>', '/dev/sda']
      `cmd >/dev/sda`          → tokens [..., '>/dev/sda']  (glued; we
                                   split that out below)
      `cmd 2>/dev/sda`         → tokens [..., '2>/dev/sda']
    """
    standalone_redirects = {">", ">>", "&>", "&>>"}
    for i, tok in enumerate(argv):
        if tok in standalone_redirects and i + 1 < len(argv):
            target = argv[i + 1].strip().strip('"').strip("'")
            if target in _SAFE_DEVICE_TARGETS:
                continue
            if _BLOCK_DEVICE_RE.match(target):
                return True
            continue
        # Glued forms: `>/dev/sda`, `2>/dev/sda`, `&>/dev/sda`, `3>>/dev/sda`
        m = re.match(r"^(\d+|&)?>>?(.+)$", tok)
        if m:
            target = m.group(2).strip().strip('"').strip("'")
            if target in _SAFE_DEVICE_TARGETS:
                continue
            if _BLOCK_DEVICE_RE.match(target):
                return True
    return False


def _is_git_clean_force(argv: list[str]) -> bool:
    if not argv or os.path.basename(argv[0]) != "git":
        return False
    # Find the subcommand, accounting for `git -C dir` etc.
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok in {"-C", "-c"} and i + 1 < n:
            i += 2
            continue
        if tok.startswith("--git-dir") or tok.startswith("--work-tree"):
            if "=" in tok:
                i += 1
            else:
                i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        break
    if i >= n or argv[i] != "clean":
        return False
    # `git clean -n` / `--dry-run` reports without deleting — destructive
    # only if force is present AND dry-run is not. Bundled short flags
    # (`-nf`, `-fn`) carry both.
    has_force = False
    has_dry_run = False
    for tok in argv[i + 1:]:
        if tok in {"-n", "--dry-run"}:
            has_dry_run = True
            continue
        if tok == "--force":
            has_force = True
            continue
        if tok.startswith("-") and not tok.startswith("--"):
            flags = tok[1:]
            if "f" in flags:
                has_force = True
            if "n" in flags:
                has_dry_run = True
    return has_force and not has_dry_run


def _is_git_reset_hard(argv: list[str]) -> bool:
    if not argv or os.path.basename(argv[0]) != "git":
        return False
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok in {"-C", "-c"} and i + 1 < n:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        break
    if i >= n or argv[i] != "reset":
        return False
    return any(tok == "--hard" for tok in argv[i + 1:])


def _is_find_delete(argv: list[str]) -> bool:
    if not argv or os.path.basename(argv[0]) != "find":
        return False
    return any(tok in _FIND_DELETE_TOKENS for tok in argv[1:])


def _is_truncate_zero(argv: list[str]) -> bool:
    if not argv or os.path.basename(argv[0]) != "truncate":
        return False
    # `truncate -s 0 <file>` or `truncate --size 0 <file>` or
    # `truncate -s0 file` (glued)
    for i, tok in enumerate(argv[1:]):
        if tok in {"-s", "--size"} and i + 2 <= len(argv) - 1:
            nxt = argv[i + 2]
            if nxt in {"0", "0K", "0M", "0B"}:
                return True
        if tok.startswith("-s") and len(tok) > 2:
            val = tok[2:]
            if val in {"0", "0K", "0M", "0B"}:
                return True
        if tok.startswith("--size="):
            val = tok.split("=", 1)[1]
            if val in {"0", "0K", "0M", "0B"}:
                return True
    return False


def _is_shred(argv: list[str]) -> bool:
    if not argv:
        return False
    return os.path.basename(argv[0]) == "shred"


def _is_fork_bomb(command: str) -> bool:
    """Detect the canonical fork-bomb idiom `:(){ :|:& };:`.

    Token-based detection is brittle for this case because the entire
    construct is a function definition followed by recursion — string-level
    matching on the literal `:()` form is intentional (it is the canonical
    idiom and the form attackers actually use). Custom function names
    (`bomb(){ bomb|bomb& };bomb`) are out of scope — they are an arbitrary
    user-defined recursive function and indistinguishable from a benign
    self-referential helper without runtime analysis.
    """
    normalized = command.replace(" ", "").replace("\t", "")
    return ":(){:|:&};:" in normalized


def _classify_segment(raw_argv: list[str]) -> str | None:
    """Return a one-line reason string if `raw_argv` is destructive, else None.

    Order: escalation check first (before strip_prefix peels sudo/doas),
    then strip and classify the real argv.
    """
    if not raw_argv:
        return None

    esc = _segment_starts_with_escalation(raw_argv)
    if esc is not None:
        return f"`{esc}` prefix — privilege escalation"

    argv = strip_prefix(raw_argv)
    if not argv:
        return None

    if _is_rm_recursive_force(argv):
        return "`rm -rf` — recursive force delete"
    if _is_dd_dangerous(argv):
        return "`dd if=/of=` — raw block I/O"
    if _is_mkfs(argv):
        return f"`{os.path.basename(argv[0])}` — filesystem creation"
    if _is_chmod_recursive_open(argv):
        return "`chmod -R 777`/`666` — recursive open-permission"
    if _is_chown_recursive(argv):
        return "`chown -R` — recursive ownership change"
    if _is_redirect_to_block_device(argv):
        return "`> /dev/sd*`/`nvme*`/`disk*` — block-device redirect"
    if _is_git_clean_force(argv):
        return "`git clean -f` — force-delete untracked files"
    if _is_git_reset_hard(argv):
        return "`git reset --hard` — discard working-tree changes"
    if _is_find_delete(argv):
        return "`find ... -delete` — bulk filesystem delete"
    if _is_truncate_zero(argv):
        return "`truncate -s 0` — file content drop"
    if _is_shred(argv):
        return "`shred` — overwrite-and-delete"

    return None


_ADVISORY_HEADER = "[destructive-bash-guard] destructive command detected"


def _advisory_text(reasons: list[str], strict: bool) -> str:
    mode = "BLOCKED-for-ask (strict mode)" if strict else "ADVISORY"
    body_lines = "\n".join(f"    - {r}" for r in reasons)
    return (
        f"{_ADVISORY_HEADER} — {mode}\n"
        "\n"
        f"  Detected:\n{body_lines}\n"
        "\n"
        "  Destructive commands can permanently delete files, overwrite\n"
        "  partitions, or escalate privileges. Verify the target path and\n"
        "  scope before proceeding.\n"
        "\n"
        "  Bypass options:\n"
        "    • Skip this single call: PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH=1\n"
        "    • Soften strict mode: unset PRAXIS_DESTRUCTIVE_BASH_STRICT\n"
        "\n"
        "  Reference: issue #463"
    )


def _main_inner() -> int:
    if os.environ.get("PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH", "").strip():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
        return 0

    reasons: list[str] = []
    seen: set[str] = set()
    if _is_fork_bomb(command):
        reason = "fork-bomb idiom `:(){ :|:& };:`"
        reasons.append(reason)
        seen.add(reason)

    for raw_argv in iter_command_starts(tokens):
        reason = _classify_segment(raw_argv)
        if reason and reason not in seen:
            reasons.append(reason)
            seen.add(reason)

    if not reasons:
        return 0

    strict = os.environ.get("PRAXIS_DESTRUCTIVE_BASH_STRICT", "").strip() == "1"

    if strict:
        emit_decision("ask", _advisory_text(reasons, strict=True))
        return 0

    sys.stderr.write(_advisory_text(reasons, strict=False) + "\n")
    return 0


def main() -> int:
    """Advisory hook — fail-open on uncaught error."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
