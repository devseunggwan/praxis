#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block commit-flag overrides without verification.

Blocks `git commit` invocations that override commit hooks or signing
without the operator having verified the environment first:

  - `--no-verify`, `-n`            (skip pre-commit hooks)
  - `--no-gpg-sign`                (force unsigned commit)
  - `-S`, `-S<keyid>`, `--gpg-sign=<keyid>`  (force signing)
  - `-c commit.gpgsign=true|false` (config-level signing override)
  - `-c core.hooksPath=...`        (redirect pre-commit hooks)
  - `-c commit.template=...`       (override commit template)

Uses shlex tokenization (same approach as block-gh-state-all.py and
gh-flag-verify.py) so that pattern references inside quoted strings,
heredoc bodies, command substitutions, or echo arguments are not
mistakenly blocked. This is the principal motivation for porting the
hook into praxis: the project-local predecessor regex-matched the bare
substring `-n` anywhere in the bash command, producing false positives
on benign invocations like `echo -n "$VAR"`, `head -n 5`, `sed -n`, or
heredoc message bodies containing such expressions (see #184).

Allow conditions:
  - `PRAXIS_SKIP_COMMIT_FLAG_CHECK=1` env var (justify the bypass in
    the commit message body or PR description).
  - Operator manually verifies the environment with the commands listed
    in the deny message, then re-runs.

Exits 2 (PreToolUse blocking code) when a live `git commit` override
without verification is detected. Exits 0 otherwise.
"""
from __future__ import annotations

import os
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hosts import (  # type: ignore[import-not-found]  # noqa: E402
    installed_hook_names,
    runtime_host,
)
from block_message import filter_gate_rows  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# Shell grouping / command-substitution chars that may prefix a binary token
# when it sits inside a subshell or substitution (`(git …)`, `$(git …)`,
# `` `git …` ``). Stripped before the basename comparison so the binary-name
# check is not fooled by the wrapper syntax. Mirrors `_is_git_binary` in the
# commit-gate hooks and `_is_gh_binary` in `_hook_utils`.
_GROUP_PREFIX_CHARS = "(){}$`"


def _is_git_binary(token: str) -> bool:
    stripped = token.lstrip(_GROUP_PREFIX_CHARS)
    return stripped == "git" or stripped.endswith("/git")

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

PROBLEM_CONFIG_PREFIXES: tuple[str, ...] = (
    "commit.gpgsign=",
    "core.hooksPath=",
    "commit.template=",
)

# Git global options that take a value as the NEXT argv token (not `=`-joined).
# Skipping just the flag without its value would let the value be misread as the
# subcommand — e.g. `git -C /tmp commit -n` would treat `/tmp` as the subcommand
# and bail out before the `commit` check, allowing the `-n` override through.
GLOBAL_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-C",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--config-env",
        "--exec-path",
        "--super-prefix",
    }
)

# Token -> human-readable description (used in deny reason).
COMMIT_FLAG_TOKENS: dict[str, str] = {
    "-n": "-n (short form of --no-verify)",
    "--no-verify": "--no-verify",
    "--no-gpg-sign": "--no-gpg-sign",
    "-S": "-S (force signing)",
    # Codex review P2: bare `--gpg-sign` (no keyid) was undetected — only
    # `--gpg-sign=<keyid>` and `-S<keyid>` were matched by the elif branches.
    # Bare form is a complete, valid invocation, so list it explicitly.
    "--gpg-sign": "-S (force signing)",
}

# git commit value-less short options — valid inner chars of a bundled POSIX
# short cluster (e.g. -vn, -anm). See _GIT_COMMIT_VALUE_SHORT for the rest.
GIT_COMMIT_NO_VALUE_SHORT = frozenset("aesvnqzp")

# Why each override is blocked (one line per distinct override).
ENV_ISSUE_FOR: dict[str, str] = {
    "-n (short form of --no-verify)": (
        "--no-verify / -n: bypasses pre-commit hooks (lint/test/format). "
        "Global rule: never skip hooks unless user explicitly asked."
    ),
    "--no-verify": (
        "--no-verify / -n: bypasses pre-commit hooks (lint/test/format). "
        "Global rule: never skip hooks unless user explicitly asked."
    ),
    "--no-gpg-sign": (
        "--no-gpg-sign: bypasses commit signing without env verification. "
        "Confirm the repo policy permits unsigned commits before using."
    ),
    "-S (force signing)": (
        "-S / --gpg-sign: forces signing. Confirm a secret key is available "
        "(gpg --list-secret-keys) and the repo expects signing."
    ),
}


# git commit short options that take a value — when one appears inside a
# cluster it consumes the remaining chars as its value, so decomposition stops
# there (e.g. `-nm"msg"` is `-n -m "msg"`, but `-mn` is `-m "n"`).
_GIT_COMMIT_VALUE_SHORT = frozenset("mFCctuS")


def _cluster_has_no_verify(tok: str) -> bool:
    """True iff a bundled short cluster (e.g. `-vn`, `-anm`) carries `-n`.

    Walks char-by-char; a value-taking short option swallows the cluster
    remainder as its argument, so scanning stops there. Unknown chars also
    stop the scan to avoid false positives on clusters we don't recognize.
    """
    for ch in tok[1:]:
        if ch == "n":
            return True
        if ch in _GIT_COMMIT_VALUE_SHORT:
            return False
        if ch not in GIT_COMMIT_NO_VALUE_SHORT:
            return False
    return False


def detect_overrides(argv: list[str]) -> list[str]:
    """Return human-readable override tokens found in a `git commit` argv.

    Returns an empty list when argv is not a `git commit` invocation or
    has no problematic overrides.
    """
    argv = strip_prefix(argv)
    if not argv or not _is_git_binary(argv[0]):
        return []

    overrides: list[str] = []

    # Step 1: scan git-level global options before the subcommand. The
    # `-c key=value` form can appear here (e.g. `git -c commit.gpgsign=false
    # commit -m "..."`). Stop at the first non-flag token.
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "-c" and i + 1 < len(argv):
            kv = argv[i + 1]
            for prefix in PROBLEM_CONFIG_PREFIXES:
                if kv.startswith(prefix):
                    overrides.append(f"-c {kv}")
                    break
            i += 2
            continue
        if tok.startswith("-c") and "=" in tok and len(tok) > 2:
            kv = tok[2:]
            for prefix in PROBLEM_CONFIG_PREFIXES:
                if kv.startswith(prefix):
                    overrides.append(f"-c {kv}")
                    break
            i += 1
            continue
        # Value-bearing global with value as the NEXT token (e.g. `-C /tmp`,
        # `--git-dir /tmp/foo`). Skip both flag AND value, otherwise the value
        # gets misread as the subcommand and the `commit` check below fails,
        # allowing the override through silently (Codex review P2, PR #194).
        if tok in GLOBAL_VALUE_FLAGS and i + 1 < len(argv):
            i += 2
            continue
        # `=`-joined global (e.g. `--git-dir=/path`). Value is already attached;
        # advance one token.
        if tok.startswith("--") and "=" in tok:
            i += 1
            continue
        if tok.startswith("-"):
            # Boolean / bare-flag global (`--bare`, `--no-replace-objects`, etc.)
            # — skip one token only.
            i += 1
            continue
        # First non-flag token: must be the subcommand.
        break

    # Step 2: only flag if the subcommand is actually `commit`. A non-commit
    # invocation like `git -c commit.gpgsign=false log` is irrelevant — git
    # config overrides only matter for commit-time policy.
    if i >= len(argv) or argv[i] != "commit":
        return []

    # Step 3: scan commit's args for short/long flag overrides.
    j = i + 1
    while j < len(argv):
        tok = argv[j]
        if tok in COMMIT_FLAG_TOKENS:
            overrides.append(COMMIT_FLAG_TOKENS[tok])
        elif (
            tok.startswith("-")
            and not tok.startswith("--")
            and len(tok) > 2
            and _cluster_has_no_verify(tok)
        ):
            # Bundled short cluster carrying `n` (=`--no-verify`), e.g. `-vn`,
            # `-anm`. The exact-match branch above only sees standalone `-n`, so
            # without this the bundled forms slip through (#512).
            overrides.append(COMMIT_FLAG_TOKENS["-n"])
        elif tok.startswith("-S") and len(tok) > 2:
            # `-S<keyid>` (signing with explicit keyid, no space).
            overrides.append("-S (force signing)")
        elif tok.startswith("--gpg-sign="):
            overrides.append("-S (force signing)")
        j += 1

    return overrides


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

# Sibling gates that also fire on `git commit`, listed once at first block so
# an author who clears THIS gate does not discover the next one only on the
# following retry (issue #941). Kept local to this hook rather than in
# `hooks/_lib/block_message.py` — this hook is the only caller, and each
# token below is transcribed from the owning hook's own source (verified,
# not recalled), the same discipline #931's `_VERB_CHECKLISTS` used.
GIT_COMMIT_GATE_CHECKLIST = """
📋 Other gates that also fire on `git commit` (satisfy the ones that apply):
  Codex review pass this session            ← block-commit-without-codex-review
    Skill(skill="praxis:codex-review-wrap"), OR a `[skip-codex-review]` token
    in the commit message, OR CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1 set
    BEFORE the session started (an inline command prefix never reaches it).
  Conventional Commits title format         ← commit-title-format-check
    <type>(<scope>): <lowercase-description>; run with a bad title to see
    the exact allowed `Types:` list.
  Title <= 50 characters                    ← commit-title-length-check
    Or embed `# title-length:ack` on the command to bypass a longer title.
  Staged additions never seen via Read/Edit ← pre-commit-staged-file-enumeration
    Advisory only — never blocks.
"""

# The `← <hook-name>` row anchor, the host resolution, and the manifest lookup
# all moved to shared code once #1245 found two more surfaces printing sibling
# gate names (`hooks/_lib/_hosts.py`, `block_message.filter_gate_rows`). The
# literal above stays here — it is still the single place a row's text and its
# owning hook are paired, and `scripts/check-sibling-commit-gates.py` reads it
# from this file.


def render_gate_checklist(host: str | None) -> str:
    """`GIT_COMMIT_GATE_CHECKLIST` with rows the host does not install removed.

    Two of the four rows name `hosts: ["claude"]` hooks while this hook itself
    carries no `hosts` key, so on codex/cursor the unfiltered text told the
    operator to satisfy gates that are not installed and offered remedies
    (`praxis:codex-review-wrap`, CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE) that do
    not exist there. The row set is derived from the manifest rather than
    copied, so a gate added later with a `hosts` whitelist cannot drift back in.

    A host outside `manifest.schema.json`'s enum — an absent value, or a typo
    in a hand-edited hooks.json — is treated as unknown and the full list is
    printed. Naming a gate the host does not install wastes a reader's time,
    while dropping one it does install hides the next block entirely, so
    wrong-but-complete is the cheaper of the two failures.
    """
    installed = installed_hook_names(host)
    if installed is None:
        return GIT_COMMIT_GATE_CHECKLIST
    return filter_gate_rows(GIT_COMMIT_GATE_CHECKLIST, installed)


DENY_TEMPLATE = """BLOCKED: Commit-flag override(s) detected.

Detected override(s): {overrides}

Environment issues:
{env_issues}

Required investigation before this command:
  1. git config --get commit.gpgsign            (repo default)
  2. gpg --list-secret-keys                     (key availability)
  3. git log --pretty=format:%G? -1             (recent commit signing)
  4. git config --get core.hooksPath            (hook path default)

These checks help you decide whether the override is appropriate. The
hook does NOT recognize their completion — re-running the same git
commit invocation will be blocked again.

To proceed, BOTH of the following are required (global CLAUDE.md: lint/hook
suppression needs a stated reason AND explicit user approval — neither one
alone is sufficient):
  1. State the concrete reason a root fix / normal commit is not possible.
  2. Get the user's explicit approval for this specific override.
  Then set PRAXIS_SKIP_COMMIT_FLAG_CHECK=1 in the environment, with the
  reason recorded in the commit message body.

Or remove the override flag(s) that triggered the block.
"""


def _emit_deny(reason: str) -> None:
    emit_decision("deny", reason)


def _build_reason(overrides: list[str]) -> str:
    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for ov in overrides:
        if ov not in seen:
            seen.add(ov)
            ordered.append(ov)

    env_issue_lines: list[str] = []
    env_seen: set[str] = set()
    for ov in ordered:
        msg = ENV_ISSUE_FOR.get(ov)
        if msg is None:
            # Generic message for `-c key=value` overrides.
            if ov.startswith("-c commit.gpgsign="):
                msg = (
                    "-c commit.gpgsign: forces signing policy. Verify gpg "
                    "key availability and repo expectation before use."
                )
            elif ov.startswith("-c core.hooksPath="):
                msg = (
                    "-c core.hooksPath: redirects pre-commit hooks. "
                    "Confirm the target path exists and is intentional."
                )
            elif ov.startswith("-c commit.template="):
                msg = (
                    "-c commit.template: overrides commit template. "
                    "Confirm intent."
                )
            else:
                msg = f"{ov}: bypasses normal commit policy."
        if msg not in env_seen:
            env_seen.add(msg)
            env_issue_lines.append(f"  - {msg}")

    return DENY_TEMPLATE.format(
        overrides=", ".join(ordered),
        env_issues="\n".join(env_issue_lines),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    # Operator-justified bypass.
    if os.environ.get("PRAXIS_SKIP_COMMIT_FLAG_CHECK") == "1":
        return 0

    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not command.strip():
        return 0

    # Backslash line continuation → single space so tokenizer sees one line.
    command = command.replace("\\\n", " ")

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    all_overrides: list[str] = []
    for argv in iter_command_starts(tokens):
        all_overrides.extend(detect_overrides(argv))

    if not all_overrides:
        return 0

    _emit_deny(
        _build_reason(all_overrides)
        + render_gate_checklist(runtime_host())
        + compound_cascade_hint(command)
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
