"""Shared external-write body extraction for PreToolUse hooks.

Two hooks (`external-write-falsify-check`, `source-citation-probe-gate`)
each carried a byte-identical copy of this surface detection + body
extraction. Both specs recorded the repo's 2-copy convention with the
same directive: extract to `_lib/_external_write_body.py` at the third
consumer. This module is that extraction; the copies were semantically
identical (comment wording aside), so migration is behavior-preserving
and each hook's existing test suite is the parity oracle.

What lives here is only "which tool calls write to an external surface,
and what is the body text" — every claim-detection heuristic stays in the
consuming hook, since that is what distinguishes them.

The surface covers two command shapes: the `gh <noun> <verb>` write pairs,
and `gh api` against the comment/review REST endpoints (issue #1265). The
second was invisible until then, which mattered because the verification
anchor's own convention forces it — a rev ≥2 anchor is a PATCH by comment id,
which no noun/verb form can issue.
"""
from __future__ import annotations

import os
import re
from typing import NamedTuple

from _hook_utils import _is_gh_binary, strip_prefix  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Bash gh detection
# ---------------------------------------------------------------------------

GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# `gh <obj> <sub>` pairs that write to external surfaces.
# `pr review` accepts --body / -b / --body-file / -F and posts a public
# review comment, so it belongs here alongside the comment/create/edit forms.
GH_WRITE_SUBCOMMANDS = frozenset({
    ("issue", "comment"),
    ("pr", "comment"),
    ("issue", "create"),
    ("pr", "create"),
    ("issue", "edit"),
    ("pr", "edit"),
    ("pr", "review"),
})

GH_BODY_FLAGS_WITH_ARG = frozenset({"-b", "--body", "-F", "--body-file"})

# gh's short flags that take a value. Splitting these apart is what makes the
# attached shorthand (`-XPOST`, `-Fbody=@anchor.md`) readable at all — pflag
# accepts it, and a scan matching only the bare token misses the form entirely.
GH_SHORT_FLAGS_WITH_ARG = frozenset({"-b", "-F", "-R", "-f", "-X", "-q", "-H", "-t", "-p"})

# ---------------------------------------------------------------------------
# `gh api` detection
# ---------------------------------------------------------------------------
# `gh api` reaches the same public comment surfaces as `gh pr comment`, and for
# an anchor revision it is the *only* path: rev ≥2 is a PATCH against a comment
# id, which no `gh <noun> <verb>` form can issue. Detecting only the noun/verb
# pairs above therefore left the repo's own prescribed write path unscanned.

GH_API_FLAGS_WITH_ARG = frozenset({
    "-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header",
    "-q", "--jq", "-t", "--template", "--input", "--hostname", "-p",
    "--preview", "--cache",
})

GH_API_METHOD_FLAGS = frozenset({"-X", "--method"})
GH_API_FIELD_FLAGS = frozenset({"-f", "--raw-field", "-F", "--field"})
# gh's own split: `-F/--field` expands a leading `@` to the file's contents,
# `-f/--raw-field` never does.
GH_API_EXPANDING_FIELD_FLAGS = frozenset({"-F", "--field"})

# Absent `--method`, gh sends GET — but POST as soon as any parameter is added,
# which is the vendor's own canonical comment-post form:
#   $ gh api repos/{owner}/{repo}/issues/123/comments -f body='Hi from CLI'
# `gh api --help`: "The default HTTP request method is `GET` normally and `POST`
# if any parameters were added. Override the method with `--method`." `--input`
# switches it the same way (verified on gh 2.98.0 with `--verbose`, which prints
# `POST /api/v3/...`). An explicit `--method GET` still wins — gh then moves the
# parameters into the query string — so inference never overrides a stated method.
GH_API_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT"})

# The comment and review endpoint families a public body actually goes out on.
# Deliberately narrow: `gh api` is overwhelmingly used for reads, and a family
# that carries no `body` field would buy nothing but false positives.
GH_API_WRITE_PATH_RE = re.compile(
    r"(?:^|/)repos/[^/\s]+/[^/\s]+/"
    r"(?:issues/comments/[^/\s?#]+"
    r"|issues/[^/\s]+/comments"
    r"|pulls/comments/[^/\s?#]+"
    r"|pulls/[^/\s]+/comments"
    r"|pulls/[^/\s]+/reviews)"
    r"(?:[/?#]|$)"
)


class GhApiCall(NamedTuple):
    """What a `gh api` command line says, before any file is opened."""

    method: str
    path: str | None
    body_flag: str | None
    body_raw: str | None
    has_input: bool


def split_gh_flag(tok: str) -> tuple[str, str | None]:
    """Split `--flag=value` into (`--flag`, `value`); else (tok, None)."""
    if tok.startswith("-") and "=" in tok:
        name, _, value = tok.partition("=")
        return name, value
    return tok, None


def split_gh_short_flags(argv: list[str]) -> list[str]:
    """Split gh's attached shorthand values (`-banchor`, `-XPOST`) apart."""
    out: list[str] = []
    for tok in argv:
        if len(tok) > 2 and tok[0] == "-" and tok[1] != "-" and f"-{tok[1]}" in GH_SHORT_FLAGS_WITH_ARG:
            value = tok[2:]
            # pflag reads one leading `=` as the separator, so `-X=PATCH` is the
            # method PATCH and `-f=body=hi` the field `body`. Keeping the `=`
            # yields the method `=PATCH` and the key `=body` — neither matches.
            if value.startswith("="):
                value = value[1:]
            out += [tok[:2], value] if value else [tok[:2]]
        else:
            out.append(tok)
    return out


def parse_gh_api(argv: list[str]) -> GhApiCall | None:
    """Structure of a `gh api` invocation, or None when argv is not one.

    Structure only — nothing is opened here, because the callers resolve a body
    path differently: `anchor-comment-gate` resolves it against the command
    segment's own cwd, the advisory hooks against the process cwd. Keeping the
    parse pure is what lets both share one definition of the command's shape.
    """
    argv = split_gh_short_flags(strip_prefix(argv))
    if not argv or not _is_gh_binary(argv[0]):
        return None

    consumes = GH_API_FLAGS_WITH_ARG | GH_GLOBAL_FLAGS_WITH_ARG
    method = ""
    path: str | None = None
    body_flag: str | None = None
    body_raw: str | None = None
    has_input = False
    has_field = False
    seen_api = False
    i = 1
    while i < len(argv):
        tok, inline = split_gh_flag(argv[i])
        if not tok.startswith("-"):
            if not seen_api:
                if tok != "api":
                    return None
                seen_api = True
            elif path is None:
                path = tok
            i += 1
            continue
        value = inline
        if value is None and tok in consumes:
            i += 1
            value = argv[i] if i < len(argv) else None
        if tok in GH_API_METHOD_FLAGS:
            method = (value or "").upper()
        elif tok == "--input":
            has_input = True
        elif tok in GH_API_FIELD_FLAGS and value:
            has_field = True
            key, _, raw = value.partition("=")
            if key == "body":
                body_flag, body_raw = tok, raw
        i += 1

    if not seen_api:
        return None
    implied = "POST" if has_field or has_input else "GET"
    return GhApiCall(method or implied, path, body_flag, body_raw, has_input)


def is_gh_api_external_write(argv: list[str]) -> bool:
    """True iff argv is a `gh api` call that writes a body to a public surface."""
    call = parse_gh_api(argv)
    if call is None or call.method not in GH_API_WRITE_METHODS:
        return False
    return bool(call.path and GH_API_WRITE_PATH_RE.search(call.path))


def extract_gh_api_body(call: GhApiCall) -> str | None:
    """Body text of a `gh api` call, or None when it cannot be read.

    `--input` hands gh a whole JSON request body, and `@-` reads stdin — the
    hook sees neither, so both are an *unknown* body rather than an empty one.

    `--input` wins over any `body=` field, because gh does not merge the two:
    `gh api --help` — "from file specified by `--input` ... When passing the
    request body this way, any parameters specified via field flags are added
    to the query" string. Returning the field value would hand every consumer
    text that is never posted, so a gap inside the real body passes silently.
    """
    if call.has_input:
        return None
    if call.body_raw is None:
        return None
    if call.body_flag in GH_API_EXPANDING_FIELD_FLAGS and call.body_raw.startswith("@"):
        if call.body_raw == "@-":
            return None
        return read_body_file(call.body_raw[1:])
    return call.body_raw


def read_body_file(path: str) -> str:
    """Read a body file, best effort — unreadable resolves to an empty body.

    A non-UTF-8 file raises UnicodeDecodeError, which is not an OSError. Left
    uncaught it escapes to the hook's `fail_open` wrapper, so ONE undecodable
    body silently skips the whole call's scan — including sibling writes
    chained in the same command. Same category as an unreadable file, so it
    takes the same empty-body fallback and the other bodies still get scanned.
    """
    try:
        # The shell expands `~` before gh ever sees the path, but a hook reads
        # the command string *before* the shell runs — so a literal
        # `~/anchor.md` opens nothing and the body silently reads empty.
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""


def resolve_body(flag: str, value: str) -> str:
    """Read body content. For --body-file, read file contents (best effort)."""
    if flag in {"-F", "--body-file"}:
        return read_body_file(value)
    return value


def extract_gh_body(argv: list[str]) -> str | None:
    """Pull body text from a gh argv. None if absent or unreadable.

    `gh api` is dispatched first because `-F` means two different things: a
    body *file* to `gh pr comment`, a `key=value` *field* to `gh api`. Reading
    an api call with the noun/verb flag set would open a file named
    `body=@anchor.md`.
    """
    api = parse_gh_api(argv)
    if api is not None:
        return extract_gh_api_body(api)
    for i, tok in enumerate(argv):
        if "=" in tok:
            key, _, val = tok.partition("=")
            if key in GH_BODY_FLAGS_WITH_ARG:
                return resolve_body(key, val)
            continue
        if tok in GH_BODY_FLAGS_WITH_ARG and i + 1 < len(argv):
            return resolve_body(tok, argv[i + 1])
    return None


def is_gh_external_write(argv: list[str]) -> bool:
    """Return True iff argv invokes a gh subcommand that writes to a public surface."""
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return False

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

    if i < len(argv) and argv[i] == "api":
        return is_gh_api_external_write(argv)

    if i + 1 >= len(argv):
        return False
    obj, sub = argv[i], argv[i + 1]
    return (obj, sub) in GH_WRITE_SUBCOMMANDS


# ---------------------------------------------------------------------------
# MCP detection
# ---------------------------------------------------------------------------

MCP_EXTERNAL_WRITE_PATTERNS = (
    re.compile(r".*slack.*send.*", re.IGNORECASE),
    re.compile(r".*slack.*post.*", re.IGNORECASE),
    re.compile(r".*slack.*update.*", re.IGNORECASE),
    re.compile(r".*notion.*create.*page.*", re.IGNORECASE),
    re.compile(r".*notion.*update.*page.*", re.IGNORECASE),
    re.compile(r".*notion.*append.*block.*", re.IGNORECASE),
)


def is_mcp_external_write(tool_name: str) -> bool:
    return any(p.match(tool_name) for p in MCP_EXTERNAL_WRITE_PATTERNS)


# Leaf keys whose value is body content (collect descendant strings).
BODY_LEAF_KEYS = frozenset({
    "text", "content", "body", "message", "page_content",
})

# Container keys that wrap block/rich-text lists. Inside a container we
# traverse wrapper dicts (paragraph, heading_1, section, ...) until we hit
# a body leaf or another container.
BODY_CONTAINER_KEYS = frozenset({
    "children", "blocks", "rich_text",
})


def _collect_under_leaf(node, parts: list[str]) -> None:
    """Collect every string descendant. Called once a leaf key is entered."""
    if isinstance(node, str):
        parts.append(node)
    elif isinstance(node, list):
        for item in node:
            _collect_under_leaf(item, parts)
    elif isinstance(node, dict):
        for val in node.values():
            _collect_under_leaf(val, parts)


def _walk_in_container(node, parts: list[str]) -> None:
    """Inside `children` / `blocks` / `rich_text`: traverse wrapper dicts
    transparently, switching to leaf-collection only at body keys."""
    if isinstance(node, list):
        for item in node:
            _walk_in_container(item, parts)
    elif isinstance(node, dict):
        for key, val in node.items():
            if isinstance(key, str) and key.lower() in BODY_LEAF_KEYS:
                _collect_under_leaf(val, parts)
            else:
                _walk_in_container(val, parts)


def extract_mcp_body(tool_input: dict) -> str:
    """Body extraction from MCP tool_input gated by recognized entry points.

    Gating on recognized container/leaf entry points keeps property metadata
    (`properties.{name}.title[].text.content`) from surfacing as body.
    """
    parts: list[str] = []
    for key, val in tool_input.items():
        if not isinstance(key, str):
            continue
        kl = key.lower()
        if kl in BODY_LEAF_KEYS:
            _collect_under_leaf(val, parts)
        elif kl in BODY_CONTAINER_KEYS:
            _walk_in_container(val, parts)
    return "\n".join(parts)
