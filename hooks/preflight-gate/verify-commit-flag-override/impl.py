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

import json
import os
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

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


def detect_overrides(argv: list[str]) -> list[str]:
    """Return human-readable override tokens found in a `git commit` argv.

    Returns an empty list when argv is not a `git commit` invocation or
    has no problematic overrides.
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
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

To proceed:
  - Set PRAXIS_SKIP_COMMIT_FLAG_CHECK=1 in the environment (justify the
    bypass in the commit message body).
  - Or remove the override flag(s) that triggered the block.
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


def main() -> int:
    # Operator-justified bypass.
    if os.environ.get("PRAXIS_SKIP_COMMIT_FLAG_CHECK") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
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

    _emit_deny(_build_reason(all_overrides) + compound_cascade_hint(command))
    return 2


if __name__ == "__main__":
    sys.exit(main())
