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
   resolved LOCALLY (no network call; the hook's manifest timeout is 5s) and
   named in the checklist so the user can see what they are approving.

   Resolution follows `gh`'s OWN precedence instead of assuming `origin`.
   Assuming `origin` is an authorization defect, not a rough edge: the
   checklist names one repo, the write lands in another, and the user has
   approved a target they were never shown (CodeRabbit CWE-863 on PR #1149,
   the same class as the two defects this arm already fixed). The order,
   read out of `cli/cli` rather than guessed:
     1. `GH_REPO` — cmdutil.OverrideBaseRepoFunc falls back to
        `os.Getenv("GH_REPO")` when no `--repo` flag is given, so the env var
        outranks every remote. Both the `GH_REPO=` assignment prefixed to
        this very command and the hook process environment are consulted.
     2. `remote.<name>.gh-resolved` in the checkout's git config — the key
        `gh repo set-default` writes (git.Client.SetRemoteResolution) and
        Remotes.ResolvedRemote() reads. Value `base` means that remote's own
        slug; any other value is an explicit `OWNER/REPO`.
     3. Otherwise the FIRST remote in gh's preference order — upstream,
        github, origin, then the rest (context.remoteNameSortScore) — which
        is not `origin` in a fork checkout that has an `upstream`.
   A selector that is present but unparseable produces an `UNRESOLVED` ask,
   so the checklist never names `origin` when `origin` is not the target.
   If nothing resolves at all — cwd is not a git checkout, no remotes, git
   missing, subprocess error or timeout — the hook stays silent per its
   fail-open contract.

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

import os
import re
import subprocess
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    fail_open,
    remaining_budget,
)
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hosts import (  # type: ignore[import-not-found]  # noqa: E402
    installed_hook_names,
    runtime_host,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    _GROUP_PREFIX_CHARS,
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
# bounded — a single `git config --local --get-regexp` reads .git/config and
# never touches the network. No `gh api` / `gh repo view` / `gh repo
# set-default --view` here for the same reason: they are network calls, and
# shelling out to `gh` from a hook that gates `gh` invites recursion.
GIT_TIMEOUT_SEC = 2

# Name of the environment variable `gh` consults when no `--repo` flag is
# given (cmdutil.OverrideBaseRepoFunc). It outranks every git remote.
GH_REPO_ENV = "GH_REPO"

# ONE git exec answers the whole repo-less question: every remote's URL and
# every `gh repo set-default` marker in a single `--get-regexp`. Two probes
# would double the per-command cost this hook pays on every Bash call, and
# the memoization added for exactly that reason would then cache two spawns
# instead of one. `--local` is deliberate: it is scoped to THIS checkout's
# config (so a stray `remote.*` in ~/.gitconfig cannot name a repo for a
# directory that is not a checkout) and it exits 128 outside a repository,
# which is how the non-git case stays distinguishable from "repo with no
# remotes". Both still resolve to silence; only one of them is a guess.
_GIT_CONFIG_ARGS = ("config", "--local", "--get-regexp", r"^remote\..*\.(url|gh-resolved)$")

# gh's remote preference when nothing is explicitly resolved
# (context.remoteNameSortScore: upstream 3, github 2, origin 1, rest 0).
# Ties break alphabetically, matching the order `git remote -v` emits.
_REMOTE_SORT_SCORE = {"upstream": 3, "github": 2, "origin": 1}

# The value `gh repo set-default` writes when the chosen repo IS the remote's
# own repo; anything else is a literal `OWNER/REPO` (setdefault.go).
_RESOLVED_BASE = "base"

# Outcomes of repo resolution. `SILENT` is the fail-open contract (exit 0, no
# output); `UNRESOLVED` still asks, because a selector that exists but cannot
# be read means a target exists that this hook must not name wrongly.
_RESOLVED = "resolved"
_UNRESOLVED = "unresolved"
_SILENT = "silent"

# A single git-config-safe name segment (owner, repo, or host).
_NAME_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# Owner/repo extracted from common remote URL forms (same shapes the sibling
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
_REMOTE_URL_RE = re.compile(
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

def _gh_write_subcommand(seg: list[Token], argv: list[Token] | None = None) -> tuple[str, str] | None:
    """Return (object, verb) if seg is a gh write subcommand, else None.

    Walks the typed Token list after the COMMAND token, skipping FLAG and
    FLAG_VALUE tokens between the object and verb POSITIONALs. Handles
    `gh issue --repo X create` (flags between object and verb) and the
    common `gh --repo X issue create` (flags before object).

    Usage queries are NOT filtered here — see `_has_help_flag`. This detector
    answers only "is this segment a gh write subcommand", and the heredoc hard
    block depends on it staying that broad.
    """
    argv = filter_argv(seg) if argv is None else argv
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
    argv = filter_argv(seg)
    for i, tok in enumerate(argv):
        if tok.role != TokenRole.FLAG:
            continue
        if not (tok.text in ("--help", "-h") or tok.text.startswith(("--help=", "-h="))):
            continue
        # A role check alone is NOT enough, and believing it was is how this
        # hook regressed once already. FLAG_VALUE is only assigned to flags
        # listed in `_FLAG_VALUE_SPEC`; a value-taking flag missing from that
        # list leaves its value typed FLAG. So in
        # `gh issue create --repo victim/repo --add-label -h --title t`
        # the `-h` is gh's VALUE for `--add-label`, types as FLAG here, and
        # used to silence the whole segment including the `--repo` arm.
        #
        # This hook cannot hold gh's per-subcommand flag table, so it does not
        # try: a help flag directly preceded by another flag might be that
        # flag's value, and an unresolvable "might" on an authorization gate
        # resolves toward asking.
        if i > 0 and argv[i - 1].role == TokenRole.FLAG:
            continue
        return True
    return False


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


_CHDIR_WORDS = frozenset({"cd", "pushd", "popd"})


def _segment_words(seg: list[Token]) -> list[str]:
    """Every whitespace-separated word in a segment, group/subst chars stripped.

    A coalesced `$(...)` substitution arrives as ONE token whose text holds a
    whole command (`$(cd /b`), so per-token inspection alone misses what is
    inside it.
    """
    words: list[str] = []
    for tok in seg:
        for raw in tok.text.split():
            stripped = raw.lstrip(_GROUP_PREFIX_CHARS)
            if stripped:
                words.append(stripped)
    return words


def _cd_intent(seg: list[Token], argv: list[Token] | None = None) -> tuple[str, str | None]:
    """Classify a segment's effect on the working directory.

    Returns ("none", None) | ("literal", path) | ("opaque", None).

    The default is INVERTED relative to the first version of this function.
    That one recognized a small set of unmodelable forms and treated
    everything else as "not a cd", so each shape nobody had thought of —
    `( cd B && …` with a space, `$(cd B && …)`, `pushd B` — silently kept the
    outer directory and the approval then named a repository the write never
    touches. Enumerating bypasses does not converge; the classifier now asks
    the opposite question. Any segment carrying a chdir word AT ALL is opaque
    unless it is exactly the one shape this hook can model.
    """
    argv = filter_argv(seg) if argv is None else argv
    words = _segment_words(seg)
    if not any(w in _CHDIR_WORDS for w in words):
        return ("none", None)

    # A bare grouping token in the RAW segment means the chdir is scoped to a
    # subshell or brace group whose end this machine does not track, so the
    # directory change does not reach the write. This has to read `seg` rather
    # than `argv`: the shared `filter_argv` normalizes a lone `(` out of argv
    # (#1193), which leaves `( cd B && …` looking exactly like a plain `cd B`
    # at the modelable-shape check below. Placed after the chdir-word test so
    # it only speaks about segments that actually change directory — a grouped
    # segment with no chdir word already returned "none" above.
    if any(not tok.text.strip(_GROUP_PREFIX_CHARS) for tok in seg):
        return ("opaque", None)

    # Exactly `cd <literal>` and nothing else — the one modelable shape.
    if (
        len(argv) == 2
        and argv[0].text == "cd"
        and argv[1].role == TokenRole.POSITIONAL
        and len(words) == 2
    ):
        path = argv[1].text
        if path and path != "-" and not path.startswith("~") and not any(
            ch in path for ch in "$`*?"
        ):
            return ("literal", path)
    return ("opaque", None)


def _slug_from_url(url: str) -> str | None:
    """Return `owner/repo` for a GitHub remote URL, else None."""
    m = _REMOTE_URL_RE.search(url.strip()) if url else None
    return m.group(1) if m else None


def _parse_full_name(value: str) -> str | None:
    """Return `owner/repo` from a gh repo *selector*, else None.

    `gh` accepts `OWNER/REPO`, `HOST/OWNER/REPO` and a full URL wherever a
    repo is named (ghrepo.FromFullName), and both `GH_REPO` and the
    `gh-resolved` config value are read through it. Anything else is not a
    parse failure this hook may paper over: the caller turns None into an
    `UNRESOLVED` ask, never into a fallback to `origin`.
    """
    value = (value or "").strip().rstrip("/")
    if not value:
        return None
    parts = value.split("/")
    if len(parts) in (2, 3) and all(_NAME_SEGMENT_RE.match(p) for p in parts):
        return "/".join(parts[-2:])
    return _slug_from_url(value)


def _inline_env_repo(seg: list[Token]) -> str | None:
    """Return the `GH_REPO=` value assigned in this segment's prefix, or None.

    `GH_REPO=owner/repo gh issue create ...` sets the variable for that one
    command, so the process environment never sees it — reading only
    `os.environ` would miss the most direct way to redirect a repo-less write.
    The role API already models the prefix: env assignments and wrapper words
    sit BEFORE the COMMAND token (that is what `filter_argv` drops), so
    scanning up to COMMAND covers both the bare form and `env GH_REPO=x gh …`.

    Returns "" — distinct from None — for an explicit `GH_REPO= gh …`, which
    is how a caller clears an inherited value: gh's `os.Getenv` then yields
    "" and falls through to the remotes, and so must this. Last assignment
    wins, matching the shell.
    """
    value: str | None = None
    prefix = GH_REPO_ENV + "="
    for tok in seg:
        if tok.role == TokenRole.COMMAND:
            break
        if tok.text.startswith(prefix):
            value = tok.text[len(prefix):]
    return value


def _env_repo_mutation(seg: list[Token]) -> tuple[str, str | None] | None:
    """A segment that changes `GH_REPO` for the segments after it.

    Returns ("set", slug) | ("unknown", None), or None when the segment does
    not touch the variable.

    `env GH_REPO=x gh …` and the inline `GH_REPO=x gh …` prefix are scoped to
    that one command and are handled at the gh segment itself. `export
    GH_REPO=x && gh …` is NOT: it persists, and leaving it untracked made the
    approval name the checkout's own repository while the write went to the
    exported one.
    """
    words = _segment_words(seg)
    if not words or not any(w.startswith("GH_REPO=") or w == "GH_REPO" for w in words):
        return None
    # A segment that also invokes `gh` carries the variable as a prefix scoped
    # to that one command (`GH_REPO=x gh …`, `env GH_REPO=x gh …`). Those are
    # read per segment by `_inline_env_repo`; claiming them here would consume
    # the segment and skip the write it wraps.
    if any(_is_gh_binary(w) for w in words):
        return None
    head = words[0]
    if head == "env":
        return None
    if head in ("unset", "export", "declare", "typeset", "set", "local", "readonly"):
        if head == "unset":
            return ("unknown", None)
        assigns = [w for w in words[1:] if w.startswith("GH_REPO=")]
        if len(assigns) != 1:
            return ("unknown", None)
        value = assigns[0].split("=", 1)[1]
        if value == "":
            return ("unknown", None)  # cleared — gh falls back, but to what is
            # decided by the remotes at that later point; do not guess here
        slug = _parse_full_name(value)
        return ("set", slug) if slug else ("unknown", None)
    # A bare `GH_REPO=x` segment with no command is also an assignment that
    # persists; anything else mentioning the name is not modelable.
    if len(words) == 1 and head.startswith("GH_REPO="):
        slug = _parse_full_name(head.split("=", 1)[1])
        return ("set", slug) if slug else ("unknown", None)
    return ("unknown", None)


def _read_remote_config(cwd: str) -> dict[str, dict[str, str]] | None:
    """Return {remote_name: {"url": …, "gh-resolved": …}} for the checkout.

    ONE local `git` exec, no network. Returns None for every case the
    fail-open contract covers: cwd is not a directory, not a git checkout
    (git exits 128), git binary missing, subprocess error, or timeout.
    An empty dict means "a checkout with no usable remotes".

    Remotes with no `url` are dropped: `git remote -v`, which is how gh
    enumerates remotes, does not list them either, so a `gh-resolved` hung on
    one is not a default gh would honour.
    """
    if not cwd or not os.path.isdir(cwd):
        return None
    # Timeout sized from the budget the dispatcher published for this
    # member, so a group already short on time is not overrun; run
    # standalone and the constant wins unchanged (issue #1167).
    budget = remaining_budget(GIT_TIMEOUT_SEC)
    if budget < MIN_SUBPROC_BUDGET_SEC:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *_GIT_CONFIG_ARGS],
            capture_output=True,
            text=True,
            timeout=min(GIT_TIMEOUT_SEC, budget),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # 0 = matches, 1 = no matching key (a checkout with no remotes).
    # 128 = not a repository. Anything else is an unexpected git failure.
    if proc.returncode not in (0, 1):
        return None

    remotes: dict[str, dict[str, str]] = {}
    for line in (proc.stdout or "").splitlines():
        key, sep, value = line.partition(" ")
        if not sep or not key.startswith("remote."):
            continue
        # A remote name may itself contain dots (`remote.my.fork.url`), so the
        # attribute is peeled off the END, not by splitting on the first dot.
        name, dot, attr = key[len("remote."):].rpartition(".")
        if not dot or not name or attr not in ("url", "gh-resolved"):
            continue
        remotes.setdefault(name, {})[attr] = value.strip()
    return {name: attrs for name, attrs in remotes.items() if attrs.get("url")}


def _remote_order(remotes: dict[str, dict[str, str]]) -> list[str]:
    """Remote names in gh's base-repo preference order."""
    # Only remotes gh could actually resolve a repository from. gh discards
    # non-GitHub remotes before choosing a base repo, so a checkout whose
    # top-ranked remote is a local path or a GitLab `origin` still writes to
    # its GitHub remote — ranking the unusable one first made this hook go
    # silent on exactly that write.
    resolvable = [n for n in remotes if _slug_from_url(remotes[n].get("url", ""))]
    return sorted(resolvable, key=lambda n: (-_REMOTE_SORT_SCORE.get(n.lower(), 0), n))


def _resolve_effective_repo(
    cwd: str,
    env_repo: str | None,
    cache: dict[str, dict[str, dict[str, str]] | None],
) -> tuple[str, str, str]:
    """Resolve the repo a repo-less `gh` write would actually target.

    Returns (status, repo_or_reason, selector):
      (_RESOLVED,   "owner/repo", "<how it was decided>")
      (_UNRESOLVED, "<reason>",   "")   → ask, naming no repo
      (_SILENT,     "",           "")   → exit 0 with no output

    The precedence is gh's, not this hook's convenience. See the module
    docstring for where each step is read out of `cli/cli`.
    """
    # 1. GH_REPO — outranks every remote.
    if env_repo:
        slug = _parse_full_name(env_repo)
        if slug:
            return (_RESOLVED, slug, f"{GH_REPO_ENV} — overrides the checkout's remotes")
        return (
            _UNRESOLVED,
            f"UNRESOLVED — {GH_REPO_ENV} is set and outranks the checkout, "
            "but its value is not a repository name this hook can parse",
            "",
        )

    if cwd not in cache:
        cache[cwd] = _read_remote_config(cwd)
    remotes = cache[cwd]
    if not remotes:
        return (_SILENT, "", "")

    order = _remote_order(remotes)
    if not order:
        return (_SILENT, "", "")

    # 2. `gh repo set-default` — the first resolved remote in gh's order wins.
    for name in order:
        resolution = remotes[name].get("gh-resolved", "")
        if not resolution:
            continue
        selector = (
            f"git config remote.{name}.gh-resolved — set by `gh repo set-default`, "
            "local read, no network call"
        )
        if resolution == _RESOLVED_BASE:
            slug = _slug_from_url(remotes[name].get("url", ""))
            # A non-GitHub remote yields no slug and gh could not write to it
            # either — unchanged fail-open, not a guess withheld.
            return (_RESOLVED, slug, selector) if slug else (_SILENT, "", "")
        slug = _parse_full_name(resolution)
        if slug:
            return (_RESOLVED, slug, selector)
        return (
            _UNRESOLVED,
            "UNRESOLVED — `gh repo set-default` names a repository this hook cannot parse",
            "",
        )

    # 3. First remote in gh's order. NOT unconditionally `origin`: a fork
    #    checkout with an `upstream` resolves there, and naming `origin` would
    #    put the wrong repo in front of the approval.
    name = order[0]
    slug = _slug_from_url(remotes[name].get("url", ""))
    if not slug:
        return (_SILENT, "", "")
    note = "" if name.lower() == "origin" else ", which gh prefers over `origin`"
    return (
        _RESOLVED,
        slug,
        f"git remote `{name}`{note} — local read, no network call",
    )


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

# The item ② row names a sibling gate, and that gate carries a `hosts`
# whitelist while this hook carries none — so the row shipped to platforms the
# gate is stripped from (issue #1245, same shape as #1154). Membership is read
# from the manifest rather than hardcoded, so a later whitelist change cannot
# leave a stale row behind.
_CALLER_EVIDENCE_GATE = "block-pr-without-caller-evidence"


def _build_checklist(
    subcommand: tuple[str, str],
    repo: str,
    from_flag: bool = True,
    selector: str = "",
    host: str | None = None,
) -> str:
    """Render the pre-flight ask text for `subcommand` against `repo`.

    `host` is the platform this run is executing on, used only to drop item ②
    where its gate is not installed. None (an unresolvable or unknown host)
    keeps every row: naming an absent gate wastes a reader's time, while
    dropping a present one hides a hard block, so wrong-but-complete is the
    cheaper failure.
    """
    obj, verb = subcommand
    installed = installed_hook_names(host)
    show_caller_evidence = obj == "pr" and (
        installed is None or _CALLER_EVIDENCE_GATE in installed
    )
    if from_flag:
        header = [f"⚠️  Cross-boundary pre-flight: `gh {obj} {verb} --repo {repo}`"]
    else:
        # The selector is named, not just the repo. "Target: X" alone cannot be
        # checked by the person approving it; "X, because GH_REPO says so" can,
        # and this arm exists precisely because the wrong selector names the
        # wrong repo.
        header = [
            f"⚠️  Cross-boundary pre-flight: `gh {obj} {verb}` (no --repo flag)",
            f"    Target repository: {repo}",
        ]
        if selector:
            header.append(f"    (resolved from {selector})")
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
    if show_caller_evidence:
        parts += [
            f"  ② Caller chain verified ({_CALLER_EVIDENCE_GATE} hook)",
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
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
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

    # `GH_REPO` exported into the hook's own process environment. The
    # PreToolUse payload does NOT carry the command's environment — it has
    # `tool_input.command`, `cwd`, `session_id`, `transcript_path` and nothing
    # else — so this is the closest real source available: the hook is spawned by
    # the same Claude Code process that spawns the Bash tool, so an exported
    # `GH_REPO` reaches both. The gap this leaves is stated in spec.md.
    ambient_env_repo = os.environ.get(GH_REPO_ENV)

    # False once a `cd` this hook cannot model has been seen: from that point
    # `effective_cwd` is a guess, and a guess must not authorize a write.
    cwd_is_known = True
    # False once a `GH_REPO` mutation this hook cannot evaluate has been seen.
    env_repo_is_known = True
    resolved_repos: dict[str, dict[str, dict[str, str]] | None] = {}

    for seg in segments:
        # A leading `cd <path>` segment moves the checkout the *next*
        # segments run in (`cd <worktree> && gh issue create ...`).
        # One tokenization per segment, shared by both classifiers. This hook
        # runs on every Bash PreToolUse, so a duplicated walk is paid on every
        # command in the session.
        seg_argv = filter_argv(seg)

        # `export GH_REPO=x && gh …` persists into the later segments, unlike
        # the `GH_REPO=x gh …` prefix which is scoped to its own command. Left
        # untracked, the checklist named the checkout's own repository while
        # the write went to the exported one.
        env_mutation = _env_repo_mutation(seg)
        if env_mutation is not None:
            state, slug = env_mutation
            if state == "set":
                ambient_env_repo = slug
                env_repo_is_known = True
            else:
                env_repo_is_known = False
            continue

        kind, cd_to = _cd_intent(seg, seg_argv)
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

        subcommand = _gh_write_subcommand(seg, seg_argv)
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
            _emit_ask(
                _build_checklist(subcommand, repo_val, host=runtime_host())
                + compound_cascade_hint(command)
            )
            return 0

        # Check 3: no --repo flag → the write still targets the repo `gh`
        # infers from the checkout. Resolve it locally and ask with the same
        # checklist (issue #1148). Unresolvable checkout → silent, per the
        # hook's fail-open contract.
        # A `GH_REPO=` prefix belongs to THIS segment only, so it is read per
        # segment; the ambient value is the fallback. An explicit empty
        # assignment (`GH_REPO= gh …`) clears the ambient one, exactly as it
        # does for gh, which is why the two are merged on `is not None` rather
        # than on truthiness.
        inline_env_repo = _inline_env_repo(seg)
        env_repo = inline_env_repo if inline_env_repo is not None else ambient_env_repo

        # An unmodeled `cd` only matters when the checkout is what decides the
        # target. `GH_REPO` outranks every remote, so it outranks not knowing
        # which checkout the command lands in too — asking `UNRESOLVED` there
        # would hide a target this hook can name exactly.
        if not env_repo_is_known and inline_env_repo is None:
            _emit_ask(
                _build_checklist(
                    subcommand,
                    "UNRESOLVED — a `GH_REPO` change in this command could not be evaluated",
                    from_flag=False,
                    host=runtime_host(),
                )
                + compound_cascade_hint(command)
            )
            return 0

        if not cwd_is_known and not env_repo:
            # `from_flag=False` is not cosmetic: the flag header renders the
            # repo INSIDE the command line, so omitting it printed
            # "`gh issue create --repo UNRESOLVED — …`" — an approval prompt
            # quoting a `--repo` flag the command does not carry.
            _emit_ask(
                _build_checklist(
                    subcommand,
                    "UNRESOLVED — a `cd` in this command could not be modeled",
                    from_flag=False,
                    host=runtime_host(),
                )
                + compound_cascade_hint(command)
            )
            return 0

        # `resolved_repos` memoizes the git READ, not the verdict, because the
        # verdict now also depends on a per-segment `GH_REPO=` prefix. The
        # probe still runs at most once per directory for the whole command:
        # on the SUCCESS path `_emit_ask` returns immediately, but a silent
        # result keeps the loop going, and the next repo-less write segment
        # would otherwise re-spawn git for the same directory — deterministic
        # duplication. It matters when git is slow: GIT_TIMEOUT_SEC x N
        # segments accumulates, and three such segments already exceed the
        # manifest's 5s budget, at which point the hook is killed (fail-open)
        # and the user has still waited the whole timeout.
        status, implicit_repo, selector = _resolve_effective_repo(
            effective_cwd, env_repo, resolved_repos
        )
        if status in (_RESOLVED, _UNRESOLVED):
            _emit_ask(
                _build_checklist(
                    subcommand,
                    implicit_repo,
                    from_flag=False,
                    selector=selector,
                    host=runtime_host(),
                )
                + compound_cascade_hint(command)
            )
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
