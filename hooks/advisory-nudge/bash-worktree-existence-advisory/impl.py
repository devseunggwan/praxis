#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: emit stderr warning when a `cd <path>` target
does not exist on disk.

Trigger pattern (issue #322):
  `cd <path>` at the start of a Bash command, or `cd <path> && ...` in a
  compound command. Each segment is inspected for a leading `cd <path>`.

Advisory message (stderr only — exit 0 always):
  [worktree-missing] <path> does not exist — check 'git worktree list' before retry
    When the resolved path does not exist on disk.

Scope (Phase 2 — #337 P6 + P1):
  Direct `cd <path>` and `pushd <path>` commands are detected, as well as
  both subshell forms: compact `(cd /path && ...)` and spaced `( cd /path && ... )`.
  Subshell cd/pushd emits an advisory when the target is missing, but does NOT
  update the hook's effective_cwd because subshell directory changes do not
  persist after `)` in Bash. Heredoc bodies containing `cd` are skipped for
  both space-separated (`<< EOF`) and fused (`<<EOF`, `<<'EOF'`,
  `<<"EOF"`, `<<-EOF`) heredoc openers.

Silent cases (no output emitted):
  - `cd <path>` where path exists on disk
  - `pushd <path>` where path exists on disk
  - `(cd <path> && ...)` where path exists on disk (compact subshell form)
  - `( cd <path> && ... )` where path exists on disk (spaced subshell form)
  - `pushd +N` or `pushd -N` — directory-stack rotation, not a path
  - `cd /path` inside a `cat<<EOF` heredoc body (command-attached fused opener)
  - subshell cd/pushd where subshell-local cwd tracking is not yet resolved
  - bare `cd` (no argument — cd to $HOME)
  - `cd $VAR` or `cd $(...)` — variable/substitution expansion, unresolvable
  - `cd -` — previous dir, unresolvable
  - `cd ~` or `cd ~/...` — tilde expansion; hook process $HOME may differ
    from the agent's effective $HOME (e.g. sudo to different user)
  - `cd ~user/...` — user-specific tilde, unresolvable
  - `cd /path` inside a heredoc body — both space-separated opener `<< EOF`
    and fused openers (`<<EOF`, `<<'EOF'`, `<<"EOF"`, `<<-EOF`) are detected
    by _extract_heredoc_end_marker; body segments are skipped.
  - opt-out marker `# worktree-advisory:ack` anywhere in command
  - non-Bash tool name
  - malformed JSON stdin

Deduplication:
  Per-session marker files in
  `${TMPDIR:-/tmp}/praxis-bash-worktree-advisory/<session_id>.<path-hash>.<state>`
  suppress repeat advisories for the same (session, path, state) triple.
  State is `missing`. If path state changes (e.g. directory is created),
  the old marker does not suppress the new state's advisory.

Design:
  - Advisory only — writes to stderr, exits 0. Never blocks tool execution.
  - Fail-open — malformed JSON, missing session_id, git failure, timeout
    all result in a silent exit 0.
  - Role-aware tokenization via `tokenize_with_roles` / `filter_argv` from
    `_hook_utils.py` — consistent with sibling advisory hooks.

Opt-out: embed `# worktree-advisory:ack` anywhere in the command to suppress
all advisories for that invocation.

Relationship to sibling hooks:
  cross-boundary-preflight — gh write subcommands across --repo boundary.
    This hook fires on the `cd <path>` step itself before any git operation,
    catching the case where the worktree directory was removed externally
    and the agent does not know yet.

Dedupe cleanup:
  Stale marker files are cleaned up by the OS tmp purge policy. No explicit
  cleanup is performed by this hook. Markers accumulate at
  ~1 file/unique-path/session, negligible footprint.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    filter_argv,
    tokenize_with_roles,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPT_OUT_MARKER = "# worktree-advisory:ack"

# Dedupe dir — per-session markers suppress repeat advisories for the same
# (path, state) pair. State is encoded in the marker filename suffix so that
# a path transitioning from missing → exists triggers a fresh advisory.
_DEDUPE_BASE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "praxis-bash-worktree-advisory")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_path(path: str) -> str:
    """Resolve symlinks and strip trailing slash for display."""
    return os.path.realpath(path.rstrip("/"))


def _extract_cd_target(seg: list[Token]) -> tuple[str | None, bool]:
    """Return (path, skip_cwd_update) for a `cd`/`pushd` segment, else (None, False).

    `skip_cwd_update` is True when the effective_cwd must NOT be updated after
    this command:
      - Subshell forms `(cd /path ...)` and `( cd /path ... )`: directory
        changes inside a subshell do not persist after `)` in Bash.
      - `pushd`: pushd manipulates a directory stack; a subsequent `popd` would
        restore the previous directory. The hook has no stack model, so updating
        effective_cwd on pushd would cause false positives for relative paths
        following a `popd`. The conservative choice is to not update effective_cwd
        for pushd at all (advisory for the missing-path case still fires).

    Two subshell forms are handled:
      - Compact: `(cd /path && ...)` — tokenize_with_roles fuses `(cd` into a
        single COMMAND token. Detected by cmd.startswith("(").
      - Spaced: `( cd /path && ... )` — tokenize_with_roles emits `(` as the
        COMMAND token and `cd` as the first POSITIONAL token. Detected by
        cmd == "(" and argv[1].text in ("cd", "pushd").

    Silent cases (return None, False):
      - argv[0] is not `cd`, `pushd`, `(cd`, or `(` with `cd`/`pushd` next
      - bare `cd` / `pushd` (no positional argument after COMMAND)
      - target starts with `$` (variable expansion, unresolvable)
      - target starts with `$(` (command substitution, unresolvable)
      - target is `-` (cd to previous dir, unresolvable)
      - target is `~` or starts with `~/` or `~<user>` (tilde expansion;
        hook process $HOME may differ from agent's effective $HOME)

    Relative targets are returned as-is; the caller resolves them against
    the effective_cwd by joining with os.path.join and normpath.
    """
    argv = filter_argv(seg)
    if not argv:
        return None, False
    cmd = argv[0].text
    is_subshell = False
    # Spaced subshell form: `( cd /path && ... )` — tokenize_with_roles emits
    # `(` as the COMMAND token and `cd`/`pushd` as the first POSITIONAL token.
    # Check exact `(` match first so that the fused-form branch below does not
    # consume it (both `"(" == "("` and `"(".startswith("(")` are true).
    if cmd == "(":
        if len(argv) >= 2 and argv[1].text in ("cd", "pushd"):
            is_subshell = True
            cmd = argv[1].text
            argv = [argv[1]] + argv[2:]  # shift so the loop below starts at the target
        else:
            return None, False
    # Compact subshell form: `(cd /path && ...)` — tokenize_with_roles fuses
    # the opening paren with the command into a single COMMAND token `(cd`.
    elif cmd.startswith("("):
        is_subshell = True
        cmd = cmd[1:]
    if cmd not in ("cd", "pushd"):
        return None, False
    # pushd manipulates a directory stack; popd would restore the previous dir.
    # Without a stack model, updating effective_cwd on pushd causes false
    # positives for relative paths following a popd. Mark as skip_cwd_update.
    if cmd == "pushd":
        is_subshell = True  # reuse flag: skip_cwd_update
    for tok in argv[1:]:
        if tok.role in (TokenRole.FLAG, TokenRole.FLAG_VALUE, TokenRole.SEPARATOR_DD):
            continue
        target = tok.text
        if not target:
            return None, False
        # Unresolvable cases — silent skip.
        if target == "-":
            return None, False
        # pushd +N / pushd -N: directory-stack rotation, not a path.
        if re.fullmatch(r"[+-]\d+", target):
            return None, False
        if target.startswith("$"):
            return None, False
        if target == "~" or target.startswith("~/") or target.startswith("~"):
            # Covers `~`, `~/foo`, `~user/foo` (any tilde prefix).
            return None, False
        return target, is_subshell
    return None, False


def _dedupe_marker(session_id: str, path: str, state: str) -> str:
    """Return path to the per-session dedupe marker for (path, state).

    The `state` suffix ensures that if a path transitions from `missing` to
    exists (worktree added), the old marker does not suppress the new state's
    advisory (or silence for an existing worktree path).

    sha1 truncated to 16 hex chars: 64-bit collision space is more than
    sufficient for the ~hundreds of unique paths a single session touches.
    """
    path_hash = hashlib.sha1(path.encode()).hexdigest()[:16]
    safe_sid = session_id.replace("/", "_").replace("\\", "_")[:64]
    return os.path.join(_DEDUPE_BASE, f"{safe_sid}.{path_hash}.{state}")


def _is_deduped(session_id: str, path: str, state: str) -> bool:
    return os.path.exists(_dedupe_marker(session_id, path, state))


def _record_dedupe(session_id: str, path: str, state: str) -> None:
    os.makedirs(_DEDUPE_BASE, exist_ok=True)
    marker = _dedupe_marker(session_id, path, state)
    try:
        open(marker, "w").close()
    except OSError:
        pass


def _delete_dedupe(session_id: str, path: str, state: str) -> None:
    """Remove a dedupe marker when path state has changed.

    Called when a path is observed to exist (missing → exists transition)
    so that a subsequent `cd` to the same path after it was deleted again
    will fire a fresh [worktree-missing] advisory rather than being suppressed
    by the stale marker from when the path still existed as missing.
    """
    marker = _dedupe_marker(session_id, path, state)
    try:
        os.unlink(marker)
    except OSError:
        pass  # fail-open: marker may not exist


def _extract_heredoc_end_marker(seg: list[Token]) -> str | None:
    """Return the heredoc end-marker word if this segment opens a heredoc.

    Three tokenization forms are handled:

    Space-separated form: `cat << EOF` — tokenizes as ['cat', '<<', 'EOF'].
      Detection: find a POSITIONAL token that is exactly `<<` or `<<-` and
      return the next token as the end-marker word.

    Fused redirect (POSITIONAL): `cat <<EOF`, `cat <<'EOF'`, `cat <<"EOF"`,
      `cat <<-EOF` — shlex collapses these into a single POSITIONAL token
      (`<<EOF` or `<<-EOF`). shlex strips quotes so `<<'EOF'` and `<<"EOF"`
      both produce `<<EOF`. Detection: POSITIONAL starting with `<<` where
      the remainder (after stripping an optional `-`) is non-empty.

    Command-attached fused form: `cat<<EOF` (no space between command and
      redirect) — shlex emits the entire `cat<<EOF` as a single COMMAND token.
      Detection: the COMMAND token contains `<<`; extract the heredoc marker
      from the substring after `<<` (strip optional leading `-`).

    Returns None when no heredoc opener is present.
    """
    argv = filter_argv(seg)
    for i, tok in enumerate(argv):
        text = tok.text
        # Command-attached fused: `cat<<EOF` emitted as a single COMMAND token.
        if tok.role == TokenRole.COMMAND and "<<" in text:
            after = text[text.index("<<") + 2:]  # everything after <<
            if after.startswith("-"):
                after = after[1:]
            if after:
                return after
            continue
        if tok.role != TokenRole.POSITIONAL:
            continue
        # Space-separated form: token is exactly `<<` or `<<-`.
        if text in ("<<", "<<-"):
            if i + 1 < len(argv):
                # End-marker may be quoted in the source (`<< 'EOF'`) but
                # shlex in posix mode strips quotes, so the token text is
                # always the bare word.
                return argv[i + 1].text
        # Fused form: token starts with `<<` followed by the marker (with
        # optional `-` prefix for tab-stripping heredocs: `<<-EOF`).
        elif text.startswith("<<"):
            remainder = text[2:]  # strip the `<<`
            if remainder.startswith("-"):
                remainder = remainder[1:]  # strip the optional `-`
            if remainder:  # non-empty remainder is the end-marker word
                return remainder
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main_inner() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    if OPT_OUT_MARKER in command:
        return 0

    session_id = payload.get("session_id", "") or ""

    segments = tokenize_with_roles(command.replace("\\\n", " "), {})
    if not segments:
        return 0

    # Thread effective_cwd across compound segments so that relative paths
    # are resolved correctly (matching the shell's sequential execution order).
    effective_cwd = os.getcwd()

    # Heredoc tracking: when a segment opens a heredoc (`<< EOF`), the
    # subsequent segments up to and including the matching end-marker segment
    # are heredoc body lines — they must NOT be processed as shell commands.
    # `_heredoc_end` holds the expected end-marker word while inside a body.
    _heredoc_end: str | None = None

    # Subshell tracking: compact form `(cd /a && cd b)` — shlex fuses the
    # opening paren with the first command (`(cd`), then emits subsequent
    # commands as independent segments, and fuses the closing `)` into the
    # last token of the final segment.
    #
    # To resolve relative paths inside a subshell correctly, we track a
    # separate `subshell_cwd` that is updated for cd/pushd segments inside
    # the subshell. When the subshell closes (last token ends with `)`), we
    # discard it and resume from `effective_cwd`. The outer `effective_cwd`
    # is never updated by subshell-internal cds (they don't persist after `)`).
    #
    # Detection heuristics (compact form only):
    #   open:  first token of a segment starts with `(` (e.g. `(cd` or `(`)
    #   close: last raw token of the same or a later segment ends with `)`
    #   strip: trailing `)` is stripped from the path token before resolution
    #
    # Spaced form `( cd /path )` is handled entirely by _extract_cd_target
    # (which detects cmd == "(") and returns is_subshell=True; the `)` is a
    # separate POSITIONAL token so no stripping is needed here.
    in_subshell: bool = False
    subshell_cwd: str = effective_cwd

    for seg in segments:
        # --- Heredoc body skip ---
        if _heredoc_end is not None:
            # Check if this segment IS the end-marker (e.g. a segment whose
            # sole COMMAND token is `EOF`).
            argv = filter_argv(seg)
            if argv and argv[0].text == _heredoc_end:
                _heredoc_end = None  # end marker consumed; resume normal scan
            # Either way (body line or end marker), skip cd detection.
            continue

        # --- Check if this segment opens a heredoc ---
        heredoc_end = _extract_heredoc_end_marker(seg)
        if heredoc_end is not None:
            _heredoc_end = heredoc_end
            # The opener segment itself is not a `cd`; continue after setting.
            continue

        # --- Subshell open/close detection (compact form) ---
        # Open: first raw token starts with `(` — covers `(cd` and bare `(`.
        first_raw = seg[0].text if seg else ""
        seg_opens_subshell = first_raw.startswith("(")
        # Close: last raw token ends with `)` — the closing paren is fused by
        # shlex into the final token of the last segment inside the subshell.
        last_raw = seg[-1].text if seg else ""
        seg_closes_subshell = last_raw.endswith(")")

        if seg_opens_subshell and not in_subshell:
            in_subshell = True
            subshell_cwd = effective_cwd

        # Choose the cwd to resolve relative paths against.
        resolve_cwd = subshell_cwd if in_subshell else effective_cwd

        # Strip one trailing `)` from the last raw token when inside a compact
        # subshell — shlex fuses the closing paren into the last path token.
        # Only done here (main loop), never in _extract_cd_target, so that
        # legitimate paths ending in `)` outside a subshell are not mutated.
        seg_for_extract = seg
        if in_subshell and seg_closes_subshell and seg:
            last_tok = seg[-1]
            if last_tok.text.endswith(")"):
                # Rebuild the segment with the last token's text stripped.
                stripped_text = last_tok.text[:-1]
                new_last = Token(text=stripped_text, role=last_tok.role)
                seg_for_extract = seg[:-1] + [new_last]

        target, is_subshell_flag = _extract_cd_target(seg_for_extract)

        if seg_closes_subshell and in_subshell:
            in_subshell = False  # subshell closed; revert to outer cwd next seg

        if target is None:
            # Not a `cd` segment — no effective_cwd update here since we do
            # not execute the segment; the next `cd` segment will still resolve
            # relative paths against the last known effective_cwd.
            continue

        # Resolve target to absolute path using the appropriate cwd.
        if target.startswith("/"):
            abs_target = os.path.normpath(target)
        else:
            abs_target = os.path.normpath(os.path.join(resolve_cwd, target))

        real_target = _normalize_path(abs_target)

        # Check: path does not exist on disk.
        if not os.path.isdir(abs_target):
            # Dedupe: suppress if we already emitted [worktree-missing] this session.
            if session_id and _is_deduped(session_id, real_target, "missing"):
                continue
            sys.stderr.write(
                f"[worktree-missing] {real_target} does not exist"
                " — check 'git worktree list' before retry\n"
            )
            if session_id:
                _record_dedupe(session_id, real_target, "missing")
        else:
            # Path exists. Update the appropriate cwd tracker:
            # - inside a subshell: always update subshell_cwd (outer not
            #   affected regardless of is_subshell_flag; the flag only guards
            #   the outer effective_cwd update for pushd/subshell-opener).
            # - pushd at parent-shell level (is_subshell_flag=True): skip.
            # - bare cd at parent-shell level: update effective_cwd.
            if in_subshell:
                subshell_cwd = abs_target
            elif not is_subshell_flag:
                effective_cwd = abs_target
            if session_id:
                _delete_dedupe(session_id, real_target, "missing")

    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
