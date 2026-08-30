#!/usr/bin/env python3
"""PreToolUse(Bash) guard: cross-boundary pre-flight for gh write operations.

Intercepts three patterns:

1. CROSS_REPO_WRITE — `gh pr create / issue create/comment/edit --repo <X>`
   Emits permissionDecision "ask" with a four-point checklist before the
   command executes. The user must confirm all contracts are satisfied.
   Fires for every `--repo` target regardless of owner: a public repo owned
   by the user or their own org is a cross-boundary write too, and needs the
   same per-action prior approval as a third-party repo (issue #993). The
   checklist says so explicitly — labelling the gate "external only" was
   what let own-org writes through without per-action approval.

2. IMPLICIT_REPO_WRITE — the same write subcommands with NO `--repo`/`-R`.
   The write still lands on a real remote repo — the one `gh` infers from
   the checkout — so gating on flag style produced an asymmetry where
   `gh issue create --repo devseunggwan/praxis --title t` asked and
   `gh issue create --title t` was silent (issue #1148). The target repo is
   resolved LOCALLY from `git remote get-url origin` (no network call; the
   hook's manifest timeout is 5s) and named in the checklist so the user can
   see what they are approving. If the repo cannot be resolved — cwd is not
   a git checkout, no `origin` remote, git missing, subprocess error or
   timeout — the hook stays silent per its fail-open contract.

3. HEREDOC_BODY — `gh pr create / issue create` with a `<<` heredoc operator
   in the same command segment. Hard-blocks (exit 2) and suggests --body-file.

Related hooks that cover adjacent scenarios:
  block-gh-state-all               → gh search --state all
  block-pr-without-caller-evidence → gh pr create without Caller chain verified:
  pre-merge-approval-gate          → gh pr merge without per-PR approval

Role-aware tokenization (issue #263): subcommand detection and --repo
value extraction use the typed `Token` API from `_hook_utils`. Heredoc
detection still inspects raw token text for `<<` since the role API does
not classify the redirect operator itself — but the quoted-string guard
(tokens with internal whitespace cannot be unquoted shell words) keeps
literal `<<` inside `--body` values from triggering a false block.

Opt-out: embed `# cross-boundary:ack` in the shell command portion of the
invocation (e.g., as a trailing comment on the `gh` line or after the heredoc
terminator), NOT inside the heredoc body. The heredoc body becomes the
published artifact — a marker placed there leaks into the issue/PR text on
the remote surface. After manually confirming all checklist items, re-run
with the marker in the command shell portion only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    _is_gh_binary,
    compound_cascade_hint,
    filter_argv,
    tokenize_with_roles,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Role-aware tokenizer spec. Globals consume separate-token values; per-object
# entries list the value-taking flags that appear after the subcommand verb
# (`--title VALUE`, `--body-file PATH`, etc.) so the role API attributes
# them as FLAG_VALUE rather than POSITIONAL.
#
# `gh issue` and `gh pr` aggregate value flags across `create / new / edit /
# comment` since `_resolve_subcommand` is single-level — the verb after
# `gh issue` is a POSITIONAL in the segment. Whitelist breadth here only
# affects role attribution; the checklist gate cares about (object, verb)
# membership, not which flags appeared.
_FLAG_VALUE_SPEC: dict[str, set[str]] = {
    "gh": {"-R", "--repo"},
    "gh issue": {
        "-R", "--repo",
        "-t", "--title",
        "-b", "--body",
        "-F", "--body-file",
        "-a", "--assignee",
        "-l", "--label",
        "-m", "--milestone",
        "-p", "--project",
    },
    "gh pr": {
        "-R", "--repo",
        "-t", "--title",
        "-b", "--body",
        "-F", "--body-file",
        "-a", "--assignee",
        "-B", "--base",
        "-H", "--head",
        "-l", "--label",
        "-m", "--milestone",
        "-p", "--project",
        "-r", "--reviewer",
    },
}

# gh subcommand pairs that write to a repo
GH_WRITE_SUBCOMMANDS = frozenset({
    ("pr", "create"), ("pr", "new"),
    ("issue", "create"), ("issue", "new"),
    ("issue", "comment"), ("issue", "edit"),
    ("pr", "comment"), ("pr", "edit"),
})

OPT_OUT_MARKER = "# cross-boundary:ack"

# Local-only resolution budget for the repo-less arm. The hook's manifest
# timeout is 5s for the whole process, so the git probe must be cheap and
# bounded — `git remote get-url origin` reads .git/config and never touches
# the network. No `gh api` / `gh repo view` here for the same reason.
GIT_TIMEOUT_SEC = 2

# Owner/repo extracted from common origin URL forms (same shapes the sibling
# `pre-gh-pr-create-dedup-gate` parses, widened to GitHub Enterprise hosts):
#   git@github.com:owner/repo.git
#   https://github.com/owner/repo.git
#   ssh://git@github.example.com/owner/repo
# A remote on a non-GitHub host does not match — `gh` could not write to it
# anyway, and an unparseable remote must stay silent, not guess.
# The `github` marker must sit in the HOST, not anywhere in the path. An
# earlier form allowed any path component, so a GitLab mirror
# (`https://gitlab.com/github/tools/repo.git`) or a local clone under a
# directory literally named `github` resolved to a plausible-looking
# `owner/repo` and the checklist then named a repo the write never touches.
# Host = the part after `scheme://[user@]` or before the `:` in scp syntax.
_ORIGIN_URL_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9+.\-]*://)?(?:[^/@\s]+@)?"
    r"[A-Za-z0-9_.\-]*github[A-Za-z0-9_.\-]*(?::\d+)?[:/]"
    r"(?:.*/)??"
    r"([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)(?:\.git)?/?$"
)

# Heredoc body matcher used to strip body content before opt-out detection.
# Pattern: `<<` (optionally `-`), optional quote around tag, the tag, any
# remaining flags / redirects on that line, newline, body, then the tag alone
# (possibly indented when `<<-` is used) on its own line.
_HEREDOC_BODY_RE = re.compile(
    r"<<-?\s*[\"']?(?P<tag>[A-Za-z0-9_]+)[\"']?[^\n]*\n"
    r".*?^[\t ]*(?P=tag)\s*$",
    re.MULTILINE | re.DOTALL,
)


def _strip_heredoc_bodies(command: str) -> str:
    """Return command with all heredoc bodies removed.

    Used to ensure `OPT_OUT_MARKER` placed inside a heredoc body — which
    would otherwise leak into the published artifact — does not satisfy the
    opt-out check. Only markers in the shell command portion remain visible.
    """
    return _HEREDOC_BODY_RE.sub("", command)

HEREDOC_BLOCK_MSG = """\
❌ BLOCKED: heredoc (`<<`) in `gh pr/issue create`.

Inline heredoc bodies bypass the praxis PreToolUse hook chain — shlex
tokenization does not read heredoc content, so the caller-chain evidence
check and external-write falsify-check see an empty body.

Correct pattern:
  1. Write body to a temp file (Write tool):
       /tmp/pr-body.md  or  /tmp/issue-body.md

  2. Pass via --body-file:
       gh issue create --title "..." --body-file /tmp/issue-body.md
       gh pr create    --title "..." --body-file /tmp/pr-body.md

  3. If the Write-tool + --body-file path is itself blocked by another guard,
     use `# cross-boundary:ack` to bypass the ASK gate after manually
     confirming all checklist items. Place the marker in the shell command
     portion ONLY — on the same `gh` line or after the heredoc terminator,
     never inside the heredoc body. The heredoc body becomes the published
     artifact; a marker inside it leaks verbatim into the issue/PR text on
     the remote surface.
       gh pr create --title "..." --body-file /tmp/b.md  # cross-boundary:ack
"""


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _gh_write_subcommand(seg: list[Token]) -> tuple[str, str] | None:
    """Return (object, verb) if seg is a gh write subcommand, else None.

    Walks the typed Token list after the COMMAND token, skipping FLAG and
    FLAG_VALUE tokens between the object and verb POSITIONALs. Handles
    `gh issue --repo X create` (flags between object and verb) and the
    common `gh --repo X issue create` (flags before object).

    Usage queries are NOT filtered here — see `_has_help_flag`. This detector
    answers only "is this segment a gh write subcommand", and the heredoc hard
    block depends on it staying that broad.
    """
    argv = filter_argv(seg)
    if not argv or not _is_gh_binary(argv[0].text):
        return None

    n = len(argv)
    i = 1

    # Skip pre-object FLAG / FLAG_VALUE tokens to find the object word.
    while i < n:
        tok = argv[i]
        if tok.role == TokenRole.POSITIONAL:
            break
        if tok.role in (TokenRole.SEPARATOR_DD, TokenRole.POST_DD):
            return None
        i += 1

    if i >= n or argv[i].role != TokenRole.POSITIONAL:
        return None
    obj = argv[i].text
    i += 1

    # Skip FLAG / FLAG_VALUE tokens between object and verb.
    while i < n:
        tok = argv[i]
        if tok.role == TokenRole.POSITIONAL:
            break
        if tok.role in (TokenRole.SEPARATOR_DD, TokenRole.POST_DD):
            return None
        i += 1

    if i >= n or argv[i].role != TokenRole.POSITIONAL:
        return None
    verb = argv[i].text

    pair = (obj, verb)
    return pair if pair in GH_WRITE_SUBCOMMANDS else None


def _has_help_flag(seg: list[Token]) -> bool:
    """True if the segment carries `--help` / `-h` as an actual FLAG token.

    `gh` prints usage and exits without touching any remote, so a usage query
    is not a write and neither ask arm should fire on it.

    The role check is the whole point. Matching on token *text* alone lets a
    flag VALUE disable the segment: `--title "-h"` is a FLAG_VALUE, and an
    attacker-supplied or merely unlucky title of `-h` would otherwise silence
    the gate on a `--repo victim/repo` write. This helper is also called
    *after* the heredoc hard block, never before it, so a usage query can
    never be used to smuggle a heredoc body past Check 1.
    """
    return any(
        tok.role == TokenRole.FLAG
        and (tok.text in ("--help", "-h") or tok.text.startswith(("--help=", "-h=")))
        for tok in seg
    )


def _has_repo_flag(seg: list[Token]) -> tuple[bool, str]:
    """Return (True, repo_value) if --repo / -R flag is present, else (False, '').

    The role API has already attributed the value as FLAG_VALUE for the
    space form. The equals form (`--repo=value`) appears as a single FLAG
    token with the value embedded; the `-Rvalue` shorthand likewise stays
    as a single FLAG token whose text starts with `-R`.
    """
    argv = filter_argv(seg)
    n = len(argv)
    for i, tok in enumerate(argv):
        if tok.role != TokenRole.FLAG:
            continue
        text = tok.text
        if text in ("-R", "--repo") and i + 1 < n and argv[i + 1].role == TokenRole.FLAG_VALUE:
            return True, argv[i + 1].text
        if text.startswith("--repo="):
            return True, text.split("=", 1)[1]
        if text.startswith("-R") and len(text) > 2:
            return True, text[2:]
    return False, ""


def _cd_intent(seg: list[Token]) -> tuple[str, str | None]:
    """Classify a segment's effect on the working directory.

    Returns one of:
      ("none",    None)  — not a `cd`; the cwd is unchanged
      ("literal", path)  — a bare `cd <literal>`; the cwd moves, knowably
      ("opaque",  None)  — a `cd` whose destination cannot be modeled

    The third case is the point, and it used to be folded into the first.
    `cd "$WORKTREE" && gh issue create ...` — the dominant idiom in this
    repo's own worktree skills — needs shell expansion this hook does not
    perform, and a subshell `(cd x && gh ...)` tokenizes its command word as
    `(cd`. Treating either as "not a cd" left `effective_cwd` pointing at the
    OUTER directory, so the write was authorized against a repo it never
    touches, and when that outer directory was not a checkout at all the gate
    went silent on a write that lands somewhere real. That is a fail-open on
    an authorization decision (CodeRabbit CWE-863 on PR #1149), so the caller
    now asks with an unresolved target instead of guessing.
    """
    argv = filter_argv(seg)
    if not argv:
        return ("none", None)

    word = argv[0].text
    if word.lstrip("(").strip() == "cd" and word != "cd":
        # `(cd ...` — a subshell. Its chdir does not persist past `)`, but a
        # `gh` call inside the same subshell runs under it, and the segment
        # machinery carries no per-subshell state to tell the two apart.
        return ("opaque", None)
    if word != "cd":
        return ("none", None)

    rest = [tok for tok in argv[1:] if tok.role == TokenRole.POSITIONAL]
    if len(rest) != 1 or len(argv) != 2:
        return ("opaque", None)  # `cd` bare, or with flags/extra words
    path = rest[0].text
    if not path or path == "-" or path.startswith("~"):
        return ("opaque", None)
    if any(ch in path for ch in "$`*?"):
        return ("opaque", None)  # needs expansion this hook does not perform
    return ("literal", path)


def _resolve_origin_repo(cwd: str) -> str | None:
    """Return `owner/repo` for the checkout at `cwd`, or None.

    Local only: `git -C <cwd> remote get-url origin` reads `.git/config`.
    Returns None — and the caller then stays silent — for every failure the
    fail-open contract covers: cwd is not a git checkout, no `origin` remote,
    git binary missing, non-zero exit, timeout, or an origin URL this parser
    cannot turn into an `owner/repo` slug.
    """
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    if not url:
        return None
    m = _ORIGIN_URL_RE.search(url)
    return m.group(1) if m else None


def _has_heredoc(seg: list[Token]) -> bool:
    """Return True if seg contains a `<<` heredoc redirect operator.

    Two tokenization forms handled (both invalid in gh write commands):

    1. Space-separated (whitespace between command and redirect):
         gh issue create <<EOF  →  token '<<EOF'  (starts with '<<')

    2. Attached to preceding word (no space before redirect):
         gh issue create --title foo<<EOF  →  token 'foo<<EOF'
         Shell parses this as stdin-redirect on the command; '<<' does
         not become part of the --title value.

    False-positive guard: body content with '<<' inside quoted strings
    (e.g. --body "comparison: a << b") tokenizes as 'comparison: a << b'
    where '<<' is surrounded by spaces on both sides. We skip such tokens.

    Separate-line guard: VAR=$(cat <<EOF\\n...\\nEOF\\n)\\ngh pr create
    has the heredoc on a different newline, which safe_tokenize separates
    into a different segment with a synthetic ';'. The heredoc token does
    NOT appear in the gh write argv slice.

    Role agnostic — we iterate every typed Token's text since `<<` does
    not survive the role API's `-` prefix check and may appear in FLAG /
    FLAG_VALUE / POSITIONAL / SUBST_RUN texts depending on attachment.
    """
    for tok in seg:
        text = tok.text
        if "<<" not in text:
            continue
        # Quoted-string guard: shlex strips quotes but preserves internal
        # spaces. A token containing a space cannot be an unquoted shell
        # word, so `<<` inside it is a literal (e.g. `--body "code: a<<b"`
        # tokenizes as `code: a<<b`). Skip such tokens entirely.
        if " " in text or "\t" in text:
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _build_checklist(
    subcommand: tuple[str, str],
    repo: str,
    from_flag: bool = True,
) -> str:
    obj, verb = subcommand
    is_pr = obj == "pr"
    if from_flag:
        header = [f"⚠️  Cross-boundary pre-flight: `gh {obj} {verb} --repo {repo}`"]
    else:
        header = [
            f"⚠️  Cross-boundary pre-flight: `gh {obj} {verb}` (no --repo flag)",
            f"    Target resolved from the checkout: {repo}",
            "    (`git remote get-url origin` — local read, no network call)",
        ]
    parts = header + [
        "",
        "Confirm ALL contracts before proceeding:",
        "",
        "  ① Per-action authorization gate (CLAUDE.md §External-repo write)",
        "     Explicit approval required for THIS specific action.",
        "     General 'proceed' / 'ok' / 'continue' does NOT count.",
        "     Ownership does NOT exempt: a public repo owned by you or your",
        "     own org is a cross-boundary write and needs the same per-action",
        "     prior approval as a third-party repo (praxis #993).",
        "     Flag style does NOT exempt either: omitting --repo does not make",
        "     the write local — it lands on the repo named above (praxis #1148).",
        "",
    ]
    if is_pr:
        parts += [
            "  ② Caller chain verified (block-pr-without-caller-evidence hook)",
            "     PR body must contain: `Caller chain verified: <source>`",
            "     Without this line the hook hard-blocks the command.",
            "",
        ]
    parts += [
        "  ③ Body delivery format",
        "     Use --body-file /tmp/<slug>.md (write body via Write tool first).",
        "     Heredoc (`<<EOF`) is blocked by the praxis hook chain.",
        "",
        "  ④ Language & content rules (CLAUDE.md §External-repo content isolation)",
        "     English only. No internal identifiers (org/team prefixes,",
        "     internal ticket refs, internal chat/wiki links, internal CLIs).",
        "     No absolute local paths.",
        "",
        "If all are satisfied, re-run with `# cross-boundary:ack` appended.",
    ]
    return "\n".join(parts)


def _emit_ask(reason: str) -> None:
    emit_decision("ask", reason)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    # OPT_OUT_MARKER is intentionally NOT consulted before the heredoc check —
    # the marker is an opt-out for the cross-repo `--repo` checklist only,
    # not for the heredoc hard-block. A heredoc-in-gh-write segment bypasses
    # caller-chain evidence regardless of marker presence; treat both
    # (marker-in-shell-portion) and (no-marker) the same way for heredocs.
    opt_out_present = OPT_OUT_MARKER in _strip_heredoc_bodies(command)

    segments = tokenize_with_roles(command.replace("\\\n", " "), _FLAG_VALUE_SPEC)
    if not segments:
        return 0

    # Effective working directory for the repo-less arm. Claude Code's
    # PreToolUse payload carries `cwd`; siblings (gh-label-verify,
    # anchor-comment-gate, path-probe-gate) all read it with an os.getcwd()
    # fallback, so this hook does the same rather than assuming os.getcwd().
    effective_cwd = payload.get("cwd") or os.getcwd()

    # False once a `cd` this hook cannot model has been seen: from that point
    # `effective_cwd` is a guess, and a guess must not authorize a write.
    cwd_is_known = True

    for seg in segments:
        # A leading `cd <path>` segment moves the checkout the *next*
        # segments run in (`cd <worktree> && gh issue create ...`).
        kind, cd_to = _cd_intent(seg)
        if kind == "opaque":
            cwd_is_known = False
            continue
        if kind == "literal":
            candidate = os.path.normpath(os.path.join(effective_cwd, cd_to))
            # Only follow a `cd` that could actually succeed. `cd /nope ; gh
            # issue create ...` leaves the shell in the ORIGINAL checkout, so
            # trusting the target blindly resolved `origin` somewhere that
            # does not exist and silenced the gate on a write that really
            # does land in this repo.
            if os.path.isdir(candidate):
                effective_cwd = candidate
            continue

        subcommand = _gh_write_subcommand(seg)
        if subcommand is None:
            continue

        # Check 1: heredoc in same segment → hard block (marker-independent)
        if _has_heredoc(seg):
            sys.stderr.write(HEREDOC_BLOCK_MSG + compound_cascade_hint(command))
            return 2

        # A usage query is not a write. Deliberately placed AFTER Check 1 so
        # `--help` cannot be used to slip a heredoc body past the hard block.
        if _has_help_flag(seg):
            continue

        # Check 2: --repo flag present → surface pre-flight checklist
        # (opt-out marker, if any, skips the checklist here only)
        if opt_out_present:
            return 0
        has_repo, repo_val = _has_repo_flag(seg)
        if has_repo:
            _emit_ask(_build_checklist(subcommand, repo_val) + compound_cascade_hint(command))
            return 0

        # Check 3: no --repo flag → the write still targets the repo `gh`
        # infers from the checkout. Resolve it locally and ask with the same
        # checklist (issue #1148). Unresolvable checkout → silent, per the
        # hook's fail-open contract.
        if not cwd_is_known:
            _emit_ask(
                _build_checklist(subcommand, "UNRESOLVED — a `cd` in this command could not be modeled")
                + compound_cascade_hint(command)
            )
            return 0

        implicit_repo = _resolve_origin_repo(effective_cwd)
        if implicit_repo:
            _emit_ask(
                _build_checklist(subcommand, implicit_repo, from_flag=False)
                + compound_cascade_hint(command)
            )
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
