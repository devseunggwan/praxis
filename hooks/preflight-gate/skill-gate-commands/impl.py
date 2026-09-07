#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block configured external-mutation commands when
the required skill has not been invoked in the current session.

This hook generalises the single-skill gate in `block-commit-without-codex-review`
to a config-driven set of (command-pattern → required-skill) pairs. Each pair is
declared via `PRAXIS_SKILL_GATED_COMMANDS`; the hook is a NO-OP when that env
var is unset or empty, so praxis ships safely out-of-the-box without interfering
with any existing workflow.

Default behaviour: **NO-OP** (exit 0) when `PRAXIS_SKILL_GATED_COMMANDS` is
unset or empty. Opt-in only.

Config env vars:
  PRAXIS_SKILL_GATED_COMMANDS
    Comma-separated entries, each: <command-pattern>=><required-skill>
    The `=>` separator is chosen because skill names routinely contain colons
    (e.g. ``org:skill-name``) and arrow-right is unambiguous.  The command
    pattern is matched after normalisation (see below).  Example:

      "gh pr create=>laplace-dev-hub:create-hub-pr,gh pr merge=>praxis:codex-review-wrap"

    Supported patterns (matched against the normalised token sequence):
      "gh pr create"   — matches ``gh [global-flags] pr create ...``
      "gh pr merge"    — matches ``gh [global-flags] pr merge ...``
      "git push origin" — matches ``git [global-flags] push origin ...``

    Malformed entries (no ``=>`` separator or empty fields) are silently
    skipped — fail-safe PASS.

  PRAXIS_HOOK_BYPASS_SKILL_GATE
    When set to any non-empty value, the hook exits 0 (pass) without checking.
    Use for one-off situations where the required skill was intentionally skipped.

Block conditions (ALL must hold):
  1. ``PRAXIS_SKILL_GATED_COMMANDS`` is set and contains ≥1 valid entry.
  2. The Bash command matches a configured pattern.
  3. The session transcript contains NO ``Skill`` tool_use with
     ``input.skill == <required-skill>``.
  4. ``PRAXIS_HOOK_BYPASS_SKILL_GATE`` is unset / empty.

Pass conditions (any one sufficient):
  - ``PRAXIS_SKILL_GATED_COMMANDS`` unset / empty → NO-OP.
  - All config entries malformed → fail-safe PASS.
  - Command does not match any configured pattern.
  - Matching config entry: required skill IS found in transcript.
  - ``PRAXIS_HOOK_BYPASS_SKILL_GATE`` set (non-empty).
  - Missing / unreadable / oversized (>50 MB) transcript → fail-open.
  - Malformed JSON stdin → fail-open.
  - Non-Bash tool call → pass.

Transcript scanning: whole-transcript scan (reuses the proven mechanism from
``block-commit-without-codex-review``). One skill invocation anywhere in the
session satisfies all subsequent matching commands — same coarse session-level
granularity as the other skill-gate hooks.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import TranscriptReadError, iter_transcript_bounded, json_needle  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import safe_tokenize  # type: ignore[import-not-found]  # noqa: E402

_MAX_BYTES = 50 * 1024 * 1024

# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class GatedCommand(NamedTuple):
    pattern: str        # normalised pattern string, e.g. "gh pr create"
    required_skill: str  # qualified skill name, may contain colons


def _parse_gated_commands(raw: str) -> list[GatedCommand]:
    """Parse PRAXIS_SKILL_GATED_COMMANDS into a list of GatedCommand.

    Each comma-separated entry: <command-pattern>=><required-skill>
    Entries with no ``=>`` separator or empty fields are silently skipped.
    """
    configs: list[GatedCommand] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        sep = entry.find("=>")
        if sep == -1:
            continue  # malformed — no separator
        pattern = entry[:sep].strip()
        required_skill = entry[sep + 2:].strip()
        if not pattern or not required_skill:
            continue  # malformed — empty field
        configs.append(GatedCommand(pattern=pattern, required_skill=required_skill))
    return configs


# ---------------------------------------------------------------------------
# Command matching
#
# Pattern tokens are matched against the normalised token sequence produced by
# the command classifier. Global flags (e.g. ``-R``, ``--repo``) before the
# subcommand group are skipped so ``gh -R owner/repo pr create`` still matches
# the pattern ``gh pr create``.
#
# Supported patterns and their matching logic:
#   "gh pr create"   → gh [global-flags] pr create (any suffix)
#   "gh pr merge"    → gh [global-flags] pr merge (any suffix)
#   "git push origin" → git [global-flags] push [flags] origin (any suffix)
#
# Unrecognised patterns are matched naively: the pattern tokens must appear as
# a contiguous ordered subsequence in the top-level non-flag tokens.
# ---------------------------------------------------------------------------

_SHELL_SEPARATORS = {";", "&", "&&", "|", "||", "\n"}

# Global options that consume the next token as their value (common to gh and git).
_GH_GLOBAL_OPTS_WITH_VALUE = {"-R", "--repo", "-C", "--config"}
_GIT_GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}

# Per-binary global-flag value sets, used by the custom-pattern fallback so a
# global flag's value (e.g. `gh -R owner/repo issue create`) does not break
# contiguity of the pattern tokens (issue #514 결함4).
_KNOWN_BINARY_GLOBAL_OPTS = {
    "gh": _GH_GLOBAL_OPTS_WITH_VALUE,
    "git": _GIT_GLOBAL_OPTS_WITH_VALUE,
}

# git-push options that consume the next token as their value (space form only;
# the ``=``-joined form is already a single token and is handled by startswith("-")).
_GIT_PUSH_OPTS_WITH_VALUE = {"-o", "--push-option", "--receive-pack", "--exec"}

def _tokenize(command: str) -> list[str] | None:
    """Tokenize via the shared ``safe_tokenize`` so whitespace-free operators
    (``gh pr create&&echo``) split into their own tokens instead of gluing
    ``create&&echo`` past the matchers — the bypass plain ``shlex.split`` left
    open (issue #514; DESIGN.md "Structural tokenization, not regex"). Empty
    list on an unparseable command → matchers find nothing → fail-open.
    """
    return safe_tokenize(command)


def _skip_global_flags(tokens: list[str], i: int, opts_with_value: set[str]) -> int:
    """Advance ``i`` past global flags (including their value arguments)."""
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in _SHELL_SEPARATORS:
            break
        if tok in opts_with_value:
            i += 2  # skip flag + its value
            continue
        if tok.startswith("-"):
            # combined short flag with value: -Rowner/repo (no space) — skip as one token
            i += 1
            continue
        break
    return i


def _matches_gh_pr_create(tokens: list[str]) -> bool:
    """Match ``gh [global-flags] pr create ...``."""
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if tok in _SHELL_SEPARATORS:
            i += 1
            continue
        if tok == "gh" or tok.endswith("/gh"):
            j = _skip_global_flags(tokens, i + 1, _GH_GLOBAL_OPTS_WITH_VALUE)
            if j < n and tokens[j] == "pr":
                k = j + 1
                if k < n and tokens[k] == "create":
                    return True
        i += 1
    return False


def _matches_gh_pr_merge(tokens: list[str]) -> bool:
    """Match ``gh [global-flags] pr merge ...``."""
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if tok in _SHELL_SEPARATORS:
            i += 1
            continue
        if tok == "gh" or tok.endswith("/gh"):
            j = _skip_global_flags(tokens, i + 1, _GH_GLOBAL_OPTS_WITH_VALUE)
            if j < n and tokens[j] == "pr":
                k = j + 1
                if k < n and tokens[k] == "merge":
                    return True
        i += 1
    return False


def _matches_git_push_origin(tokens: list[str]) -> bool:
    """Match ``git [global-flags] push [flags] origin ...``."""
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if tok in _SHELL_SEPARATORS:
            i += 1
            continue
        if tok == "git" or tok.endswith("/git"):
            j = _skip_global_flags(tokens, i + 1, _GIT_GLOBAL_OPTS_WITH_VALUE)
            if j < n and tokens[j] == "push":
                # skip push flags; value-consuming flags (-o/--push-option/
                # --receive-pack/--exec in space form) eat the next token too.
                k = j + 1
                while k < n and tokens[k].startswith("-"):
                    if tokens[k] in _GIT_PUSH_OPTS_WITH_VALUE:
                        k += 2  # skip flag + its value
                    else:
                        k += 1
                if k < n and tokens[k] == "origin":
                    return True
        i += 1
    return False


# Dispatch table for known patterns
_PATTERN_MATCHERS = {
    "gh pr create": _matches_gh_pr_create,
    "gh pr merge": _matches_gh_pr_merge,
    "git push origin": _matches_git_push_origin,
}


def _matches_custom_pattern(tokens: list[str], pattern_tokens: list[str]) -> bool:
    """Flag-aware fallback matcher for custom (non-built-in) patterns.

    Walks each command start; for a known binary (``gh``/``git``) it skips
    leading global flags (and their values) before comparing the pattern
    tokens against the contiguous run of subsequent non-flag tokens. This
    fixes ``gh -R owner/repo issue create`` breaking contiguity because the
    flag value ``owner/repo`` previously landed in the non-flag list between
    ``gh`` and ``issue`` (issue #514 결함4).

    Patterns whose first token is not a known binary fall back to the prior
    naive contiguous non-flag-token subsequence match.
    """
    if not pattern_tokens:
        return False
    binary = pattern_tokens[0]
    opts_with_value = _KNOWN_BINARY_GLOBAL_OPTS.get(binary)
    if opts_with_value is None:
        # Unknown binary — preserve prior naive contiguous match.
        non_flag = [
            t for t in tokens
            if t not in _SHELL_SEPARATORS and not t.startswith("-")
        ]
        plen = len(pattern_tokens)
        for i in range(len(non_flag) - plen + 1):
            if non_flag[i:i + plen] == pattern_tokens:
                return True
        return False

    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if tok in _SHELL_SEPARATORS:
            i += 1
            continue
        if tok == binary or tok.endswith("/" + binary):
            j = _skip_global_flags(tokens, i + 1, opts_with_value)
            # Compare the remaining pattern tokens against the contiguous run
            # of non-flag tokens beginning at j.
            ok = True
            k = j
            for pt in pattern_tokens[1:]:
                if k >= n or tokens[k] in _SHELL_SEPARATORS or tokens[k] != pt:
                    ok = False
                    break
                k += 1
            if ok:
                return True
        i += 1
    return False


def _command_matches_pattern(tokens: list[str], pattern: str) -> bool:
    """Return True if ``tokens`` match ``pattern``."""
    matcher = _PATTERN_MATCHERS.get(pattern)
    if matcher is not None:
        return matcher(tokens)
    # Custom pattern: flag-aware fallback (issue #514 결함4).
    return _matches_custom_pattern(tokens, pattern.split())


# ---------------------------------------------------------------------------
# Transcript scan (reused from block-commit-without-codex-review)
# ---------------------------------------------------------------------------

def _scan_transcript(path: str, required_skill: str) -> bool | None:
    """Return True if ``required_skill`` Skill tool_use appears in transcript,
    False if not, None if the transcript cannot be read or is past
    ``_MAX_BYTES`` (caller treats None as fail-open).

    Streams through ``_transcript.iter_transcript_bounded`` and parses only
    the lines that carry the skill name as a JSON string value (issue #1312):
    a satisfying record holds it in the tool_use's ``skill`` field, so a line
    without the quoted token is rejected before ``json.loads``. The previous
    shape — ``read_text().splitlines()`` and a parse per line — materialized
    the whole session on every ``git commit`` inside the shared Bash dispatch
    deadline (#1167). ``json_needle`` answers None for a name the default
    encoder would escape (a non-ASCII skill name written as ``\\uXXXX`` by
    the host), and the scan then parses every line rather than miss the
    record and wrongly block. The reader owns the byte bound, the per-read cap
    and the fail-open classes.
    """
    try:
        for obj in iter_transcript_bounded(path, _MAX_BYTES, json_needle(required_skill)):
            if _has_skill_tool_use(obj, required_skill):
                return True
    except TranscriptReadError:  # missing, unreadable, or past _MAX_BYTES
        return None
    return False


def _has_skill_tool_use(obj: dict, required_skill: str) -> bool:
    """Return True if ``obj`` contains a Skill tool_use with the given skill."""
    message = obj.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "tool_use"
            and item.get("name") == "Skill"
        ):
            tool_input = item.get("input")
            if isinstance(tool_input, dict) and tool_input.get("skill") == required_skill:
                return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed payload

    if os.environ.get("PRAXIS_HOOK_BYPASS_SKILL_GATE", ""):
        return 0

    # No config → NO-OP (out-of-the-box no commands are affected)
    raw_config = os.environ.get("PRAXIS_SKILL_GATED_COMMANDS", "").strip()
    if not raw_config:
        return 0

    gated = _parse_gated_commands(raw_config)
    if not gated:
        return 0  # all entries malformed → fail-safe pass

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    tokens = _tokenize(command)
    if tokens is None:
        return 0  # unparseable command → fail-open

    transcript_path = payload.get("transcript_path")

    for cfg in gated:
        if not _command_matches_pattern(tokens, cfg.pattern):
            continue
        # Pattern matched — check transcript for the required skill.
        if not transcript_path:
            return 0  # no transcript → cannot enforce → fail-open
        found = _scan_transcript(transcript_path, cfg.required_skill)
        if found is None:
            return 0  # unreadable/oversized → fail-open
        if found:
            continue  # required skill ran this session → continue checking other patterns
        # Skill was NOT found — block.
        _emit_block(cfg)
        return 2

    return 0




def _emit_block(cfg: GatedCommand) -> None:
    emit_block(
        rule_name="skill-gate-commands",
        why=(
            f"Command matching pattern ``{cfg.pattern}`` was called without "
            f"first invoking the required skill ``{cfg.required_skill}`` this "
            "session. The gate enforces that high-impact mutations are preceded "
            "by the appropriate review / validation skill."
        ),
        correct_path=(
            f"Invoke Skill(\"{cfg.required_skill}\") first, then re-run the "
            "command. One invocation satisfies all subsequent matching commands "
            "in the same session."
        ),
        bypass_env="PRAXIS_HOOK_BYPASS_SKILL_GATE",
        reference=(
            "docs/skill-gated-commands.md; "
            "CLAUDE.md → Mandatory Workflow Gates"
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
