#!/usr/bin/env python3
"""PreToolUse(Bash) guard: validate gh --json <fields> against the subcommand's schema.

Intercepts every Bash tool call containing a `gh <subcmd> ... --json <fields>`
invocation and blocks it when any requested field is not in the subcommand's
valid JSON field set, which is probed via `gh <subcmd> --json help 2>&1`.

Motivation: gh CLI accepts `--json <fields>` whose valid field set differs from
the GitHub REST API. A recurring failure mode is requesting a field that exists
in the REST API but not in gh's JSON projection — e.g. `gh pr view --json
merged` fails because `merged` is not a valid gh pr field (use `state`,
`mergedAt`, or `mergeCommit` instead). This has recurred across 6+ sessions;
the hook layer is the remaining enforcement escalation path after memory-rule
addition was blocked by the 3-generation threshold rule.

Design notes:
- Field sets are fetched at runtime via `gh <subcmd> --json help 2>&1` so they
  are always accurate for the installed gh version. Results are cached per
  subcommand per session (session_id-keyed file under /tmp/) to amortize cost.
- Tokenization uses `safe_tokenize` → `iter_command_starts` → `strip_prefix`
  from `_hook_utils.py`, consistent with all sibling hooks.
- Bypass: `# PRAXIS_GH_JSON_BYPASS=skip` comment in the command text causes
  immediate pass-through. Also supported: env var PRAXIS_GH_JSON_BYPASS=1.
- `gh api` subcommand is explicitly excluded (follows REST schema, different
  concern). Unknown subcommands pass through silently (fail-open).
- On any infrastructure error (gh missing, network fail, parse error) the hook
  exits 0 so the session is never broken.
- Exits 2 (deny) with a structured JSON block when an invalid field is found.
- Closest-match suggestion: substring match against valid fields; falls back
  to listing a sample of known valid fields for the subcommand.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    shared_probe_deadline,
)
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _paths import praxis_cache_dir  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Cap for ONE `gh ... --json help` probe. All probes in a single invocation
# (the probe + its dummy-positional retry, across every command segment) share
# one deadline derived from the hook's budget — see main(). Two 10s probes
# back to back would otherwise exceed the Bash dispatch group's shared node
# budget and starve every later member (issue #1167).
_GH_HELP_TIMEOUT_SEC = 10

# hooks/manifest.json gives this gate a 15s timeout; standalone (per-process)
# the shared probe deadline is that budget minus a margin for interpreter
# startup and process spawn — NOT the single-probe cap, which would shrink
# the standalone budget from "10s per probe" to "10s total" (PR #1195
# review). Under the dispatcher the deadline clamps to the remaining group
# budget instead (see _hook_runtime.shared_probe_deadline). Manifest-timeout
# parity is pinned by tests/hooks/preflight-gate/test_gh_json_validator.sh
# against hooks/manifest.json — bump both together.
_HOOK_TIMEOUT_SEC = 15
_HOOK_TIMEOUT_MARGIN_SEC = 2

_BYPASS_COMMENT_RE = re.compile(r"#\s*PRAXIS_GH_JSON_BYPASS\s*=\s*skip")

# Fast prefilter: must contain `gh` and `--json` before doing full parse.
_GH_JSON_RE = re.compile(r"\bgh\b.+--json\b")

# Subcommands excluded from validation (gh api uses REST schema, not gh --json schema).
_EXCLUDED_FIRST_TOKENS: frozenset[str] = frozenset({"api"})


# ---------------------------------------------------------------------------
# Cache helpers (session_id-keyed per DESIGN.md convention)
# ---------------------------------------------------------------------------

def _session_cache_dir(session_id: str) -> Path:
    """Return per-session cache directory for gh JSON field sets."""
    return Path(praxis_cache_dir()) / f"gh-json-{session_id}"


def _cache_key(subcommand_tokens: tuple[str, ...]) -> str:
    return "_".join(subcommand_tokens)


def _load_cached_fields(
    cache_dir: Path, subcommand_tokens: tuple[str, ...]
) -> frozenset[str] | None:
    path = cache_dir / f"{_cache_key(subcommand_tokens)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return frozenset(str(f) for f in data)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _save_cached_fields(
    cache_dir: Path, subcommand_tokens: tuple[str, ...], fields: frozenset[str]
) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{_cache_key(subcommand_tokens)}.json"
        path.write_text(json.dumps(sorted(fields)), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Field discovery via `gh <subcmd> --json help`
# ---------------------------------------------------------------------------

def _fetch_valid_fields(
    subcommand_tokens: tuple[str, ...], deadline: float
) -> frozenset[str] | None:
    """Run `gh <subcmd> --json help` and parse the valid field list.

    Returns None on any failure (gh missing, network, parse error) — caller
    treats this as fail-open.

    The help output format (on stderr, exit code != 0):
      Unknown JSON field: "help"
      Available fields:
        fieldOne
        fieldTwo
        ...

    Some subcommands (e.g. `gh pr view`, `gh issue view`, `gh release view`)
    require a positional argument. When an arg-count error appears in the
    output, a dummy positional "0" is injected and the call is retried.

    Both calls share `deadline`: each subprocess timeout is the remaining
    budget capped at `_GH_HELP_TIMEOUT_SEC`, so their SUM can never exceed the
    hook's member budget; an exhausted deadline fails open without spawning gh.
    """
    remaining = deadline - time.monotonic()
    if remaining < MIN_SUBPROC_BUDGET_SEC:
        return None
    cmd = ["gh"] + list(subcommand_tokens) + ["--json", "help"]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=min(remaining, _GH_HELP_TIMEOUT_SEC),
        )
    except (OSError, subprocess.SubprocessError):
        return None

    output = r.stdout + r.stderr

    # Retry with a dummy positional when the subcommand requires an argument.
    if "accepts" in output and "arg" in output and "received 0" in output:
        remaining = deadline - time.monotonic()
        if remaining < MIN_SUBPROC_BUDGET_SEC:
            return None
        cmd_with_dummy = ["gh"] + list(subcommand_tokens) + ["0", "--json", "help"]
        try:
            r2 = subprocess.run(
                cmd_with_dummy,
                capture_output=True,
                text=True,
                timeout=min(remaining, _GH_HELP_TIMEOUT_SEC),
            )
            output = r2.stdout + r2.stderr
        except (OSError, subprocess.SubprocessError):
            return None

    if "Available fields:" not in output:
        return None

    fields: set[str] = set()
    in_fields = False
    for line in output.splitlines():
        if "Available fields:" in line:
            in_fields = True
            continue
        if in_fields:
            stripped = line.strip()
            if stripped and re.match(r"^\w+$", stripped):
                fields.add(stripped)
            elif stripped:
                break  # non-word content ends the field list section
    return frozenset(fields) if fields else None


def _get_valid_fields(
    subcommand_tokens: tuple[str, ...],
    cache_dir: Path,
    deadline: float,
) -> frozenset[str] | None:
    """Return valid JSON fields for a subcommand, with per-session caching."""
    cached = _load_cached_fields(cache_dir, subcommand_tokens)
    if cached is not None:
        return cached
    fetched = _fetch_valid_fields(subcommand_tokens, deadline)
    if fetched is not None:
        _save_cached_fields(cache_dir, subcommand_tokens, fetched)
    return fetched


# ---------------------------------------------------------------------------
# Argument extraction (from flat argv list)
# ---------------------------------------------------------------------------

def _extract_json_fields(argv: list[str]) -> list[str] | None:
    """Extract --json field names from a flat argv list.

    Returns None if --json is absent. Returns comma-separated fields as
    individual stripped strings.

    Handles both `--json fields` (separate token) and `--json=fields` forms.
    """
    for i, tok in enumerate(argv):
        if tok == "--json" and i + 1 < len(argv):
            return [f.strip() for f in argv[i + 1].split(",") if f.strip()]
        if tok.startswith("--json="):
            val = tok[len("--json="):]
            return [f.strip() for f in val.split(",") if f.strip()]
    return None


def _resolve_subcommand_tokens(argv: list[str]) -> tuple[str, ...] | None:
    """Extract (sub, sub-sub) command tokens from an argv starting with 'gh'.

    Collects up to 2 non-flag positional tokens after `gh`:
      ['gh', 'pr', 'view', ...]     → ('pr', 'view')
      ['gh', 'issue', 'list', ...]  → ('issue', 'list')
      ['gh', 'search', 'issues', …] → ('search', 'issues')
      ['gh', 'release', 'view', …]  → ('release', 'view')

    Flags and their values are skipped with a simple heuristic (next token
    not starting with '-' is consumed as the flag value). This is sufficient
    for subcommand detection; full flag parsing is not needed here.

    Returns None if argv[0] is not the gh binary or no subcommand token found.
    """
    if not argv or not _is_gh_binary(argv[0]):
        return None

    positionals: list[str] = []
    i = 1
    n = len(argv)
    while i < n and len(positionals) < 2:
        tok = argv[i]
        if tok == "--":
            break
        if tok.startswith("-"):
            # Skip value-taking flags heuristically.
            if "=" not in tok and i + 1 < n and not argv[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        positionals.append(tok)
        i += 1

    return tuple(positionals) if positionals else None


# ---------------------------------------------------------------------------
# Closest-match suggestion (substring heuristic)
# ---------------------------------------------------------------------------

def _suggest_field(bad_field: str, valid_fields: frozenset[str]) -> str | None:
    """Return the closest valid field name for bad_field, or None.

    Priority:
    1. Case-insensitive exact match.
    2. A valid field contains bad_field as a substring (case-insensitive).
    3. bad_field contains a valid field as a substring.
    """
    bl = bad_field.lower()
    for f in valid_fields:
        if f.lower() == bl:
            return f
    for f in sorted(valid_fields):
        if bl in f.lower():
            return f
    for f in sorted(valid_fields):
        if f.lower() in bl:
            return f
    return None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _emit_deny(reason: str) -> None:
    emit_decision("deny", reason)


# ---------------------------------------------------------------------------
# Per-segment check
# ---------------------------------------------------------------------------

def _check_segment(
    raw_argv: list[str],
    cache_dir: Path,
    deadline: float,
) -> tuple[bool, str]:
    """Check one command segment for gh --json field validity.

    Returns (is_invalid, reason). is_invalid=True triggers deny.
    """
    argv = strip_prefix(raw_argv)
    if not argv or not _is_gh_binary(argv[0]):
        return False, ""

    subcommand_tokens = _resolve_subcommand_tokens(argv)
    if subcommand_tokens is None:
        return False, ""

    # Exclude gh api — REST schema, different concern.
    if subcommand_tokens[0] in _EXCLUDED_FIRST_TOKENS:
        return False, ""

    json_fields = _extract_json_fields(argv)
    if json_fields is None:
        return False, ""

    # `--json help` is gh's own field-introspection (prints valid fields, not
    # data) — the very command our remediation tells the agent to run, so it
    # must pass (issue #514 결함5).
    if json_fields == ["help"]:
        return False, ""

    valid_fields = _get_valid_fields(subcommand_tokens, cache_dir, deadline)
    if valid_fields is None:
        return False, ""  # fail-open on gh/network errors

    invalid = [f for f in json_fields if f not in valid_fields]
    if not invalid:
        return False, ""

    subcmd_display = "gh " + " ".join(subcommand_tokens)
    parts = []
    for bad in invalid:
        suggestion = _suggest_field(bad, valid_fields)
        if suggestion:
            parts.append(f"'{bad}' (did you mean '{suggestion}'?)")
        else:
            parts.append(f"'{bad}'")

    sample = sorted(valid_fields)[:15]
    suffix = f" (+ {len(valid_fields) - 15} more)" if len(valid_fields) > 15 else ""
    reason = (
        f"BLOCKED: {subcmd_display} --json has invalid field(s): "
        + ", ".join(parts)
        + f". Run '{subcmd_display} --json help' to see all valid fields."
        + f" Sample valid fields: {', '.join(sample)}{suffix}."
    )
    return True, reason


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

    # Bypass via env var.
    if os.environ.get("PRAXIS_GH_JSON_BYPASS") == "1":
        return 0

    # Bypass via inline comment marker.
    if _BYPASS_COMMENT_RE.search(command):
        return 0

    # Collapse backslash line continuations before any regex matching so that
    # multiline `gh --json` invocations (e.g. `gh pr view 1 \<newline>  --json
    # merged`) are not silently bypassed by the prefilter.
    command = command.replace("\\\n", " ")

    # Fast prefilter: must contain `gh` and `--json` before full parse.
    if not _GH_JSON_RE.search(command):
        return 0

    # Session-id keyed cache per DESIGN.md convention; PPID as back-compat
    # fallback for direct CLI / test invocation.
    session_id = payload.get("session_id") or str(os.getppid())
    cache_dir = _session_cache_dir(session_id)

    # Use shared tokenization pipeline (safe_tokenize → iter_command_starts
    # → strip_prefix) consistent with all sibling hooks.
    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    # One shared deadline for EVERY `gh --json help` probe this invocation
    # spawns (issue #1167), so their SUM is bounded by the hook's budget.
    # Standalone (no dispatcher deadline published) that budget is the 13s
    # manifest-derived self-budget; under the dispatcher it clamps to the
    # remaining group budget — see _hook_runtime.shared_probe_deadline. Each
    # individual probe stays capped at _GH_HELP_TIMEOUT_SEC inside
    # _fetch_valid_fields.
    deadline = shared_probe_deadline(_HOOK_TIMEOUT_SEC, _HOOK_TIMEOUT_MARGIN_SEC)

    for raw_argv in iter_command_starts(tokens):
        is_invalid, reason = _check_segment(list(raw_argv), cache_dir, deadline)
        if is_invalid:
            _emit_deny(reason)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
