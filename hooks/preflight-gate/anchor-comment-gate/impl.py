#!/usr/bin/env python3
"""Two-phase guard for the PR verification anchor comment.

Issue #947. The anchor is the single place a PR's verification lives: one
comment per PR, edited in place by comment id, whose table a reviewer reads
with nothing expanded. Its rules (five required fields, SHA pinned to the
pushed HEAD) existed only as prose, and in the very session that authored them
the author violated them twice — a self-defeating update command, and a result
enum with no cell for a check that could not run. Both were caught by an
external reviewer, after the text was already written.

## Why two phases

The first design did everything in PreToolUse: decode the `gh` command, find
the body, look the PR up, decide. Six rounds of adversarial review found
twenty-five distinct ways that reading fails — an attached shorthand value
(`-Fanchor.md`), a quoted multi-line body the tokenizer shreds, `cd` in an
earlier segment, `GH_REPO=` in front of the command, gh's own `{owner}/{repo}`
placeholders, `--hostname` before the subcommand, `--input payload.json`. The
findings were all real and none of them were the last one, because statically
deciding what a shell command will do is not a bounded problem.

So the phases split along what each one can actually know:

- **PreToolUse — structure only, no network.** Decidable from the body text
  alone. Blocks a malformed anchor *before* it is published, which is the
  point of a gate. When the command cannot be decoded, it says so on stderr
  instead of passing silently — an undecodable post is unchecked, and silence
  reads as clean.
- **PostToolUse — the published comment is the oracle.** `gh` prints the
  comment URL (or a JSON response carrying it), so the check reads what was
  *actually posted* through the API. No shell parsing, no cwd, no host
  guessing, no placeholder expansion: the URL names the comment. This is where
  SHA freshness and diff coverage live, because both need the real target.

The cost is honest and worth stating: PostToolUse cannot prevent the post. A
malformed anchor caught there is already visible, and the correction is "fix
it now" rather than "you may not". The anchor is editable, so the exposure is
seconds — except a stale SHA, which was published. PreToolUse blocking the
structure case is what keeps that window small.

## What blocks (PreToolUse)

All five fields, else block:
  - `### Verification — \\`<sha>\\` (rev N)` heading (or the legacy `### 검증`
    dialect), on the first non-empty line
  - a claim table with at least one numbered row
  - an `Unverified` toggle (`미검증` in the legacy dialect)
  - one evidence toggle per numbered table row, `Evidence <n> —` (legacy `<n>.`)
  - a `History` toggle (legacy `갱신 이력`)

The dialect is chosen by the heading and every other field is then read in it,
so a body cannot pass by taking half its labels from each.

Plus `--edit-last` on an anchor body: that flag edits *the last comment of the
current user*, which the paired update notice displaces, so from rev 2 it
rewrites the notice and leaves the anchor stale.

## What warns (both phases)

An undecodable comment post and an unreadable body file.

## What PostToolUse reports

Every finding carries a tier — `blocking` (a rule violation), `advisory` (worth
considering, possibly a false positive) or `unknown` (the check did not run).
Two channels carry them. A body holding at least one `blocking` finding leaves
through stderr at exit 2, the loudest thing a PostToolUse hook has. A body
holding only `advisory` and `unknown` findings leaves through
`hookSpecificOutput.additionalContext` at exit 0 — also model-visible, and it
does not interrupt a turn over something that may be a false positive or over a
check that merely did not run. What exiting 0 does discard is bare stderr, so
neither tier is ever written there. `unknown` still has to be said out loud: a
check that silently did not run reads as a pass, which is the one thing it is
not.

## Bypass

`PRAXIS_HOOK_BYPASS_ANCHOR_GATE=1`, or `# anchor-gate: <reason>` as a trailing
shell comment. The reason is the point: it turns an unverifiable post into a
recorded decision rather than an accident.

`PRAXIS_ANCHOR_GATE_ADVISORY=1` demotes the PostToolUse exit to 0 without
silencing the pre-post block. It replaces `PRAXIS_ANCHOR_GATE_STRICT`, whose
behaviour is now the default.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _git import run_git  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    budgeted_deadline,
    fail_open,
)
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _external_write_body import (  # type: ignore[import-not-found]  # noqa: E402
    GH_API_EXPANDING_FIELD_FLAGS,
    GH_API_FLAGS_WITH_ARG,
    GH_BODY_FLAGS_WITH_ARG,
    GH_GLOBAL_FLAGS_WITH_ARG,
    GH_SHORT_FLAGS_WITH_ARG,
    parse_gh_api,
    split_gh_flag as _split_flag,
    split_gh_short_flags as _split_short_flags,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

_GH_TIMEOUT_SEC = 8
_GIT_TIMEOUT_SEC = 3
_LOOKUP_BUDGET_SEC = 15.0
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_ANCHOR_GATE"
_BYPASS_TOKEN = "# anchor-gate:"
_ADVISORY_ENV = "PRAXIS_ANCHOR_GATE_ADVISORY"

# Three tiers, two channels. `blocking` leaves through stderr at exit 2; the
# other two through additionalContext at exit 0, which is model-visible where
# bare stderr at exit 0 is not.
_BLOCKING = "blocking"
_ADVISORY = "advisory"
_UNKNOWN = "unknown"
_TIER_NOTE = {
    _BLOCKING: "convention violation — fix it now / 규약 위반 — 지금 고치세요",
    _ADVISORY: "consider it — may be a false positive / 고려하세요 — 오탐일 수 있습니다",
    _UNKNOWN: "the check did not run — not a pass / 검사가 실행되지 않았습니다 — 통과가 아닙니다",
}
_TIER_ORDER = (_BLOCKING, _UNKNOWN, _ADVISORY)
# The skeleton itself lives in the operator's own workflow doc, which is not
# part of this repo — so the reference names the file a praxis user can
# actually open, and that spec points onward.
_REFERENCE = "hooks/preflight-gate/anchor-comment-gate/spec.md"

# The anchor's first line, in either dialect. Also the prefix the id-recovery
# jq matches on, so the two must move together — see spec.md.
_ANCHOR_PREFIXES = ("### Verification", "### 검증")

# Flag vocabulary and the `gh api` command shape come from `_lib` — issue #1265
# made that module the SoT so the five hooks reading external writes and this
# gate cannot drift apart on what a `gh api` write looks like.
_SHORT_FLAGS_WITH_ARG = GH_SHORT_FLAGS_WITH_ARG
_API_FLAGS_WITH_ARG = GH_API_FLAGS_WITH_ARG

# Field names come in two dialects. `en` is what the rule prescribes; `ko` is
# kept because anchors published before it are edited in place, never
# retrofitted, so rejecting their dialect would lock their own revisions out.
# A body is read in exactly one dialect, chosen by its heading — mixing the two
# surfaces as a missing field, which is what it is.
_DIALECTS = {
    "en": {
        "heading": re.compile(
            r"^###\s+Verification\s*[—-]\s*`([0-9a-fA-F]{6,40})`\s*\(rev\s*(\d+)\)"),
        "heading_label": "SHA+rev 헤딩 (`### Verification — `<sha>` (rev N)`) — 첫 줄이어야 함",
        "unverified": "Unverified",
        "history": "History",
        "evidence": re.compile(r"^\s*(?:<b>)?\s*Evidence\s+(\d+)\s*[—-]"),
        "evidence_label": "`<details><summary>Evidence N — …`",
    },
    "ko": {
        "heading": re.compile(
            r"^###\s+검증\s*[—-]\s*`([0-9a-fA-F]{6,40})`\s*\(rev\s*(\d+)\)"),
        "heading_label": "SHA+rev 헤딩 (`### 검증 — `<sha>` (rev N)`) — 첫 줄이어야 함",
        "unverified": "미검증",
        "history": "갱신 이력",
        "evidence": re.compile(r"^\s*(?:<b>)?\s*(\d+)\s*\."),
        "evidence_label": "`<details><summary>N. …`",
    },
}
_TABLE_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|", re.MULTILINE)
_DETAILS_RE = re.compile(r"<details\b[^>]*>(.*?)</details>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary\b[^>]*>(.*?)</summary>", re.DOTALL)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# gh accepts the endpoint with or without a leading slash, and with or without
# the api.github.com host — anchor both forms or the slashless one slips past.
_COMMENTS_PATH_RE = re.compile(r"(?:^|/)repos/([^/]+)/([^/]+)/issues/comments/(\d+)")
# Same path, but anchored enough to recover host/owner/repo/id from a raw
# command when the command printed nothing to follow.
_PATCH_ENDPOINT_RE = re.compile(
    r"(?:https?://([^/\s\"']+)/(?:api/v3/)?)?repos/([^/\s\"']+)/([^/\s\"']+)"
    r"/issues/comments/(\d+)"
)
# A shell redirect or `tee` target: the file the command writes before posting.
_REDIRECT_TARGET_RE = re.compile(
    r"""(?:>>?|\btee\b(?:\s+-\w+)*)\s*(?P<q>['"]?)(?P<path>[^\s'"|;&<>]+)(?P=q)"""
)
# Both `gh pr comment` (bare URL) and `gh api` (JSON response) put this in stdout.
_COMMENT_URL_RE = re.compile(
    r"https?://([^/\s\"]+)/([^/\s\"]+)/([^/\s\"]+)/(?:pull|issues)/(\d+)#issuecomment-(\d+)"
)


# ---------------------------------------------------------------------------
# Command decoding (PreToolUse)
# ---------------------------------------------------------------------------

def _subcommand(argv: list[str]) -> str | None:
    """First real subcommand, skipping global flags and the args they consume.

    `gh --hostname ghe.example api …` is a valid GHES form whose first
    non-option token is the hostname *value*, not the subcommand.
    """
    i = 1
    while i < len(argv):
        tok, inline = _split_flag(argv[i])
        if not tok.startswith("-"):
            return tok
        if inline is None and (tok in GH_GLOBAL_FLAGS_WITH_ARG or tok in _SHORT_FLAGS_WITH_ARG):
            i += 1
        i += 1
    return None


def _read_file(path: str, cwd: str) -> str:
    """Read a body file, resolving `~` and relative paths against `cwd`.

    The shell expands `~` and applies the working directory before gh runs, but
    a PreToolUse hook sees the command string beforehand — so both have to be
    applied here or the body reads back empty.
    """
    try:
        return _resolve_path(path, cwd).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _resolve_path(path: str, cwd: str) -> _Path:
    """`path` as gh would see it: `~` expanded, relative resolved against cwd."""
    candidate = _Path(path).expanduser()
    return candidate if candidate.is_absolute() else _Path(cwd) / candidate


def _canonical(path: str, cwd: str) -> str | None:
    """A comparable form of `path`, or None if it cannot be resolved."""
    try:
        return os.path.normpath(str(_resolve_path(path, cwd)))
    except (OSError, ValueError):
        return None


def _rewritten_in_command(path: str, command: str, cwd: str) -> bool:
    """True iff `command` redirects or tees into `path` before posting it.

    Comparison is on resolved paths, not on the text: `printf … > ./anchor.md
    && gh pr comment -F anchor.md` names the same file twice and a literal
    match sees two different strings — so the hook would validate the file as
    it was *before* the rewrite and certify a body gh never posts.
    """
    target = _canonical(path, cwd)
    if target is None:
        return True  # unresolvable target — treat the body as unknowable
    return any(
        _canonical(m.group("path"), cwd) == target
        for m in _REDIRECT_TARGET_RE.finditer(command)
    )


def _parse_pr_comment(argv: list[str], cwd: str, command: str) -> dict | None:
    """Parse `gh pr comment <pr> ... --body/--body-file`, else None.

    The body reader is local rather than `_lib`'s `extract_gh_body`: that
    helper opens the path with no notion of a working directory, and this gate
    resolves a relative `--body-file` against the segment's own cwd. The flag
    set itself still comes from `_lib`, so there is one definition of what a
    body flag is.
    """
    argv = _split_short_flags(argv)
    words: list[str] = []
    body: str | None = None
    body_is_file = False
    edit_last = False
    i = 1
    while i < len(argv):
        tok, inline = _split_flag(argv[i])
        if not tok.startswith("-"):
            words.append(tok)
            i += 1
            continue
        value = inline
        if value is None and tok in (GH_GLOBAL_FLAGS_WITH_ARG | GH_BODY_FLAGS_WITH_ARG):
            i += 1
            value = argv[i] if i < len(argv) else None
        if tok == "--edit-last":
            edit_last = True
        elif tok in GH_BODY_FLAGS_WITH_ARG and value is not None:
            body_is_file = tok in ("-F", "--body-file")
            if not body_is_file:
                body = value
            elif _rewritten_in_command(value, command, cwd):
                # The file is rewritten earlier in this same command, so what
                # is on disk now is the PREVIOUS body — validating it would
                # certify content gh will never post.
                body = ""
            else:
                body = _read_file(value, cwd)
        i += 1

    if words[:2] != ["pr", "comment"]:
        return None
    if body is None:
        return {"undecodable": "body flag not found / 본문 플래그를 찾지 못함"}
    return {
        "body": body,
        "edit_last": edit_last,
        "undecodable": (
            "body file unreadable / 본문 파일을 읽지 못함"
            if body == "" and body_is_file
            else None
        ),
    }


def _parse_api_patch(argv: list[str], cwd: str, command: str) -> dict | None:
    """Parse `gh api --method PATCH .../issues/comments/<id> ... body=`.

    The command *shape* — method, endpoint, which flag carried `body=` — comes
    from `_lib`'s `parse_gh_api`, so this gate and the external-write hooks read
    one definition. What stays local is resolving that body against the command
    segment's own cwd and against a file the same command rewrites before
    posting: `_lib` is cwd-free by design, and validating a pre-rewrite file
    would certify content gh will never post.
    """
    call = parse_gh_api(argv)
    if call is None:
        return None
    body: str | None = None
    undecodable: str | None = None
    if call.has_input:
        # The whole request body comes from a JSON file (or stdin). Reading it
        # would mean re-implementing gh's request assembly; the posted comment
        # is checked in PostToolUse instead. A `body=` field alongside is NOT
        # that body — gh puts it in the query string — so the body stays None
        # rather than falling through to the field. Unknown, never `""`: an
        # empty body reads downstream as "checked and clean".
        undecodable = "--input 으로 전달된 JSON 본문"
    elif call.body_raw is not None:
        expand = call.body_flag in GH_API_EXPANDING_FIELD_FLAGS
        raw = call.body_raw
        if expand and raw.startswith("@") and _rewritten_in_command(raw[1:], command, cwd):
            body = ""
            undecodable = "body file rewritten in the same command / 본문 파일이 같은 명령에서 재작성됨"
        else:
            body = _read_body_value(raw, expand, cwd)

    if call.method != "PATCH" or not call.path or not _COMMENTS_PATH_RE.search(call.path):
        return None
    if body is None:
        return {"undecodable": undecodable or "body field not found / 본문 필드를 찾지 못함"}
    return {"body": body, "edit_last": False, "undecodable": undecodable}


def _read_body_value(raw: str, expand_at: bool, cwd: str) -> str | None:
    """Resolve a `gh api` field value to its text.

    `expand_at` mirrors gh's own split: `-F/--field` expands a leading `@` to
    the file's contents, `-f/--raw-field` never does. `@-` is stdin, which the
    hook cannot see, so it resolves to None (unknown body → undecodable).
    """
    if not expand_at or not raw.startswith("@"):
        return raw
    if raw == "@-":
        return None
    return _read_file(raw[1:], cwd) or None


def _tokenizations(command: str) -> list[list[str]]:
    """Both tokenizations of `command`, because neither alone sees every post.

    `safe_tokenize` splits on newlines — correct for Bash, where a newline
    separates commands, but it shreds a *quoted* multi-line body: an inline
    `--body '### 검증 …<newline>| 1 | … |'` loses the `gh pr comment` segment
    entirely. `shlex.split` keeps quoted newlines intact but glues shell
    operators to adjacent words, which is why it cannot replace the primary.
    """
    out = [safe_tokenize(command)]
    if "\n" in command:
        try:
            out.append(_split_glued_operators(shlex.split(command)))
        except ValueError:  # unbalanced quotes — the primary pass stands alone
            pass
    return out


def _split_glued_operators(tokens: list[str]) -> list[str]:
    """Break `ok&&gh` apart, which `shlex.split` leaves glued.

    Only newline-free tokens are split. A quoted multi-line body is precisely
    what this tokenization exists to preserve, and it is also the one token
    that could plausibly contain a literal `&&`.
    """
    out: list[str] = []
    for tok in tokens:
        if "\n" in tok:
            out.append(tok)
            continue
        out += [p for p in re.split(r"(&&|\|\||;|\|)", tok) if p]
    return out


def _comment_posts(command: str, base_cwd: str) -> tuple[list[dict], list[str]]:
    """Return (anchor posts, undecodable-post reasons) found in `command`."""
    anchors: list[dict] = []
    undecodable: list[str] = []
    seen: set[tuple] = set()
    for tokens in _tokenizations(command):
        cwd = base_cwd
        for argv in iter_command_starts(tokens):
            argv = strip_prefix(argv)
            if not argv:
                continue
            # `cd X && gh pr comment -F anchor.md` reads the file relative to X.
            # Only the plain sequential form is tracked — subshell and pushd
            # scoping belong to the advisory hooks that already model them.
            if argv[0] == "cd" and len(argv) > 1 and not argv[1].startswith("-"):
                target = _Path(argv[1]).expanduser()
                cwd = str(target if target.is_absolute() else _Path(cwd) / target)
                continue
            if not _is_gh_binary(argv[0]):
                continue
            sub = _subcommand(argv)
            parsed = (
                _parse_api_patch(argv, cwd, command) if sub == "api"
                else _parse_pr_comment(argv, cwd, command)
            )
            if not parsed:
                continue
            key = (cwd, parsed.get("edit_last"), parsed.get("body"), parsed.get("undecodable"))
            if key in seen:
                continue
            seen.add(key)
            if parsed.get("undecodable"):
                undecodable.append(parsed["undecodable"])
            elif _is_anchor(parsed["body"]):
                anchors.append(parsed)
    return anchors, undecodable


def _bypassed_with_reason(command: str) -> bool:
    """True iff the command ends with the bypass marker AND a stated reason.

    Two things have to hold, and both are load-bearing:

    - The marker is a *trailing shell comment*, not any occurrence of the
      string. A raw substring search means an anchor whose evidence block
      quotes this very marker — a test transcript, this spec — waives the gate
      on the post that contains it.
    - A reason follows it. That is what turns an unverifiable post into a
      recorded decision.
    """
    line = command.rsplit("\n", 1)[-1]
    idx = line.rfind(_BYPASS_TOKEN)
    if idx == -1 or not line[idx + len(_BYPASS_TOKEN):].strip():
        return False
    try:  # unbalanced prefix ⇒ the marker sits inside a quoted string
        shlex.split(line[:idx])
    except ValueError:
        return False
    return True


def _is_anchor(body: str) -> bool:
    for line in body.splitlines():
        if line.strip():
            return line.strip().startswith(_ANCHOR_PREFIXES)
    return False


# ---------------------------------------------------------------------------
# Structure — decidable from the body alone, so both phases share it
# ---------------------------------------------------------------------------

def _dialect(body: str) -> dict:
    """The field-name set this body is read in, chosen by its opening keyword.

    Falls back to `en` — the prescribed dialect — when the body opens with
    neither, so a body that is not an anchor at all reports the field names a
    new anchor should have had.
    """
    for line in body.splitlines():
        if line.strip():
            return _DIALECTS["ko" if line.strip().startswith("### 검증") else "en"]
    return _DIALECTS["en"]


def _heading_match(body: str) -> re.Match | None:
    """Match the SHA+rev heading on the body's FIRST non-empty line, or None.

    Searching the whole body would accept a bare `### 검증` opener with a real
    heading buried further down: the reader sees a heading with no SHA, while
    freshness is checked against a SHA they never see.
    """
    for line in body.splitlines():
        if line.strip():
            return _dialect(body)["heading"].match(line.strip())
    return None


def _claim_table_region(body: str) -> str:
    """The part of the body that can hold claim rows: before the first toggle.

    Row numbers drive the per-row evidence requirement, so a `| 200 | ok |`
    line pasted into an evidence block would otherwise read as claim row 200
    and demand a `<summary>200. …` that should not exist.
    """
    head = re.split(r"<details\b", body, maxsplit=1)[0]
    return _FENCE_RE.sub("", head)


def _toggle_summaries(body: str) -> list[str]:
    """Summaries of real `<details>…</details>` toggles, fenced blocks removed.

    A bare `<summary>` outside any `<details>` renders as plain text, and one
    quoted inside a code fence is an example — neither is a toggle a reader can
    open, so neither may satisfy a required field.
    """
    stripped = _FENCE_RE.sub("", body)
    return [
        s.strip()
        for block in _DETAILS_RE.findall(stripped)
        for s in _SUMMARY_RE.findall(block)
    ]


def _structure_findings(body: str) -> list[tuple[str, str]]:
    """Required fields that are missing or mismatched, each as (tier, message).

    Structure is decidable from the body alone, so every finding here is
    `blocking`: nothing about it depends on a lookup that could have failed.
    """
    missing: list[tuple[str, str]] = []
    d = _dialect(body)

    if not _heading_match(body):
        missing.append((_BLOCKING, d["heading_label"]))

    rows = [int(n) for n in _TABLE_ROW_RE.findall(_claim_table_region(body))]
    if not rows:
        missing.append((
            _BLOCKING,
            "verification item table (no numbered rows) / 검증 항목 표 (번호 행이 없음)",
        ))

    summaries = _toggle_summaries(body)
    if not any(d["unverified"] in s for s in summaries):
        missing.append((_BLOCKING, f"{d['unverified']} 토글"))
    if not any(d["history"] in s for s in summaries):
        missing.append((_BLOCKING, f"{d['history']} 토글"))

    evidence = {
        int(m.group(1))
        for s in summaries
        if (m := d["evidence"].match(s))
    }
    uncovered = sorted(set(rows) - evidence)
    if uncovered:
        rows_text = ", ".join(str(n) for n in uncovered)
        missing.append((
            _BLOCKING,
            f"per-row evidence toggle — table rows {rows_text} have no matching "
            f"{d['evidence_label']} / 행별 근거 토글 — 표 행 {rows_text}"
            f" 에 대응하는 {d['evidence_label']} 이 없음",
        ))
    return missing


# ---------------------------------------------------------------------------
# Published-comment verification (PostToolUse)
# ---------------------------------------------------------------------------

def _gh(args: list[str], deadline: float) -> tuple[str | None, str | None]:
    """Run gh within the remaining budget; return (stdout, error). Never raises."""
    remaining = deadline - time.monotonic()
    # The floor, not zero: a sub-floor slice cannot finish a `gh` round trip,
    # so spawning one only spends budget the later group members were counting
    # on and still answers "unknown".
    if remaining < MIN_SUBPROC_BUDGET_SEC:
        return None, "조회 예산 초과"
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True,
            timeout=min(_GH_TIMEOUT_SEC, remaining),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh 실행 실패: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or "").strip() or f"gh exit {proc.returncode}"
    return proc.stdout.strip(), None


def _comment_refs(output: str) -> list[tuple[str, str, str, str, str]]:
    """Every distinct comment the output names, as (host, owner, repo, pr, id).

    Every one, not the first: a compound command can post two anchors, and the
    PreToolUse side already checks each segment — verifying only the first
    published one would leave the second unexamined on exactly the surface
    that decides.
    """
    seen: set[str] = set()
    refs: list[tuple[str, str, str, str, str]] = []
    for m in _COMMENT_URL_RE.finditer(output):
        if m.group(5) in seen:
            continue
        seen.add(m.group(5))
        refs.append(m.groups())  # type: ignore[arg-type]
    return refs


def _fetch_anchor(
    ref: tuple[str, str, str, str, str], deadline: float
) -> tuple[dict | None, str | None]:
    """Read one published comment back; return it only if it is an anchor."""
    host, owner, repo, pr, comment_id = ref
    is_dotcom = host in ("github.com", "www.github.com", "api.github.com")
    api_host = [] if is_dotcom else ["--hostname", host]
    body, err = _gh(
        ["api", *api_host, f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
         "--jq", ".body"],
        deadline,
    )
    if err:
        return None, (
            f"failed to fetch posted comment {comment_id} — {err}"
            f" / 게시된 코멘트 {comment_id} 조회 실패"
        )
    if not body or not _is_anchor(body):
        return None, None
    return {
        "body": body,
        "repo": f"{owner}/{repo}" if is_dotcom else f"{host}/{owner}/{repo}",
        "pr": pr,
        "url": f"https://{host}/{owner}/{repo}/pull/{pr}#issuecomment-{comment_id}",
    }, None


def _head_and_base(post: dict, deadline: float) -> tuple[str | None, str | None, str | None]:
    """The PR's current head SHA and base branch, from one `gh pr view`."""
    out, err = _gh(
        ["pr", "view", post["pr"], "--repo", post["repo"],
         "--json", "headRefOid,baseRefName",
         "--jq", '.headRefOid + " " + .baseRefName'],
        deadline,
    )
    if err:
        return None, None, f"PR HEAD 조회 실패 — {err}"
    sha, _, base = (out or "").partition(" ")
    if not sha:
        return None, None, "PR HEAD 가 비어 있음"
    return sha, (base.strip() or None), None


def _uncovered_files(
    body: str, base: str | None, head: str | None, deadline: float, cwd: str
) -> list[str]:
    """Changed files no table row mentions. Best effort; [] on any failure.

    Both endpoints come from the PR itself — its own `baseRefName` and its own
    `headRefOid` — never from local checkout state. The anchor is routinely
    posted from somewhere other than the PR branch (the base worktree after a
    context switch, a second worktree, a fork clone), and a local `HEAD` there
    describes a different diff entirely: files the PR never touched get
    reported as uncovered, while the ones it did touch go unmentioned. A wrong
    advisory is worse than none, so an unresolvable endpoint yields nothing.
    """
    if not base or not head:
        return []

    def _git(args: list[str]) -> str | None:
        # Deadline-aware wrapper over the shared runner (hooks/_lib/_git.py):
        # returns stdout on success, None on failure / spent budget.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        return run_git(args, timeout=min(_GIT_TIMEOUT_SEC, remaining), cwd=cwd)

    # The PR's head commit has to exist locally for any of this to mean
    # anything — after a fresh push it does, and when it does not (fork, stale
    # clone) there is no diff to take.
    if _git(["cat-file", "-e", f"{head}^{{commit}}"]) is None:
        return []
    merge_base = (_git(["merge-base", f"origin/{base}", head]) or "").strip()
    if not merge_base:
        return []
    out = _git(["diff", "--name-only", merge_base, head])
    if out is None:
        return []

    table = "\n".join(
        line for line in body.splitlines() if line.lstrip().startswith("|")
    )
    return [
        path
        for path in (p for p in out.splitlines() if p.strip())
        if path not in table and _Path(path).name not in table
    ]


def _tool_output(tool_response: object) -> str:
    if not isinstance(tool_response, dict):
        return str(tool_response or "")
    return "\n".join(
        str(tool_response.get(key) or "") for key in ("output", "stdout", "stderr")
    )


def _refs_from_command(command: str) -> list[tuple[str, str, str, str, str]]:
    """Comment refs recoverable from the command when the output names none.

    `gh api --silent`, a `--jq` that projects the URL away, and `> /dev/null`
    all publish an anchor while printing nothing this hook can follow — and
    the freshness check is the *only* place a stale SHA is caught, so a silent
    post must not be a silent pass. A comment-id `PATCH` carries its target in
    the endpoint literal, which is enough to fetch it; the PR number is not
    there, so it is resolved from the comment's `issue_url` at fetch time.
    """
    return [
        (m.group(1) or "github.com", m.group(2), m.group(3), "", m.group(4))
        for m in _PATCH_ENDPOINT_RE.finditer(command)
    ]


def _resolve_pr(ref: tuple[str, str, str, str, str], deadline: float) -> str | None:
    """The PR number a comment belongs to, when only its id is known."""
    host, owner, repo, _, comment_id = ref
    is_dotcom = host in ("github.com", "www.github.com", "api.github.com")
    out, _ = _gh(
        ["api", *([] if is_dotcom else ["--hostname", host]),
         f"/repos/{owner}/{repo}/issues/comments/{comment_id}", "--jq", ".issue_url"],
        deadline,
    )
    m = re.search(r"/issues/(\d+)\s*$", out or "")
    return m.group(1) if m else None


def _post_failed(tool_response: object) -> bool:
    """Whether the Bash call reported a failure — unparseable exit reads as success."""
    if not isinstance(tool_response, dict):
        return False
    if tool_response.get("interrupted") is True or tool_response.get("isError") is True:
        return True
    exit_code = tool_response.get("exit")
    if exit_code is None:
        return False
    try:
        return int(exit_code) != 0
    except (TypeError, ValueError):
        return False


def _post_tool_use(payload: dict) -> int:
    """Verify the anchors that were actually published. Advisory unless strict."""
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command or os.environ.get(_BYPASS_ENV, "").strip() or _bypassed_with_reason(command):
        return 0

    # Under the dispatcher this clamps to what is left of the Bash group's
    # shared node budget; standalone the hook's own budget wins unchanged.
    deadline = budgeted_deadline(_LOOKUP_BUDGET_SEC)
    refs = _comment_refs(_tool_output(payload.get("tool_response")))
    if not refs:
        refs = [
            (host, owner, repo, _resolve_pr((host, owner, repo, "", cid), deadline) or "", cid)
            for host, owner, repo, _, cid in _refs_from_command(command)
        ]

    # A failed response is only evidence that *something* in the command failed.
    # `gh pr comment ...; false` exits 1 with a real comment URL still in the
    # output, and a `gh api --method PATCH …/comments/999 …; false` carries its
    # target in the command whether or not it printed anything — so the failure
    # is read after *both* ref sources, never instead of them. A recovered id
    # means an anchor to check, and it is checked exactly as it would be on
    # exit 0. Only with nothing recoverable does the failure decide, and then it
    # decides nothing was published — which keeps the URL-loss branch below from
    # reporting an anchor that a failed `gh pr comment` (auth, network) never
    # created.
    if not refs and _post_failed(payload.get("tool_response")):
        return 0
    problems: list[tuple[str, str]] = []
    urls: list[str] = []

    if not refs:
        # A `gh pr comment` whose URL was redirected away leaves no id
        # anywhere. Nothing was checked, and an unrun check is its own state:
        # reporting it as clean is what makes a stale SHA ship unremarked.
        posts, _ = _comment_posts(command, payload.get("cwd") or os.getcwd())
        if posts:
            problems.append((
                _UNKNOWN,
                "the anchor was posted but the output carries no comment URL, so the "
                "structure and SHA-freshness checks did not run at all — keep the output, "
                "or PATCH by comment id.\n"
                "앵커를 게시했지만 출력에 코멘트 URL 이 없어 구조·SHA 신선도 검사를 "
                "아예 실행하지 못했습니다 — 출력을 버리지 말거나 comment id 로 PATCH 하세요.",
            ))
        return _report(problems, urls)

    for ref in refs:
        tag = f"[comment {ref[4]}] " if len(refs) > 1 else ""
        post, err = _fetch_anchor(ref, deadline)
        if err:
            problems.append((
                _UNKNOWN,
                tag + f"{err} — could not verify the posted anchor / 게시된 앵커를 검증하지 못했습니다.",
            ))
            continue
        if post is None:
            continue
        urls.append(post["url"])
        problems += [(tier, tag + msg) for tier, msg in _structure_findings(post["body"])]

        heading = _heading_match(post["body"])
        if not heading:
            continue
        if not post["pr"]:
            problems.append((_UNKNOWN, tag + "SHA 신선도 확인 불가 (코멘트의 PR 번호를 찾지 못함)"))
            continue
        head, base, lookup_err = _head_and_base(post, deadline)
        if lookup_err:
            problems.append((_UNKNOWN, tag + f"SHA 신선도 확인 불가 ({lookup_err})"))
        elif head and not (head.startswith(heading.group(1)) or heading.group(1).startswith(head)):
            problems.append((
                _BLOCKING,
                tag + f"앵커 SHA `{heading.group(1)}` 가 현재 HEAD `{head[:7]}` 와 다름 — "
                "stale 앵커는 없는 코드에 대한 증거를 주장한다",
            ))
        cwd = payload.get("cwd") or os.getcwd()
        uncovered = list(_uncovered_files(post["body"], base, head, deadline, cwd))
        if uncovered:
            files_text = ", ".join(uncovered)
            problems.append((
                _ADVISORY,
                tag + f"changed files no table row mentions: {files_text} "
                "(file↔claim is not 1:1, so this may be a false positive)\n"
                f"표 행이 언급하지 않는 변경 파일: {files_text} "
                "(파일↔주장은 1:1 이 아니므로 오탐일 수 있습니다)",
            ))

    return _report(problems, urls)


def _report(problems: list[tuple[str, str]], urls: list[str]) -> int:
    """Print the findings grouped by tier and return the exit code.

    Both channels reach the model, and which one a finding takes has to match
    what the finding actually is. Exit 2 is read as a *deny* one layer up —
    `_dispatch.run_group` returns 2 for the whole group and
    `_fire_ledger.classify_decision` records `block` — so sending a coverage
    hint or a `gh` timeout out that way files it in the ledger next to a rule
    violation, and the fire-rate audit that scores this hook then cannot tell
    the two apart.

    So `blocking` exits 2 and everything else leaves through
    `hookSpecificOutput.additionalContext` at exit 0, mirroring
    `second-failure-advisory` and `builtin-task-postuse`. The earlier form
    exited 2 for all three tiers on the claim that stderr-at-exit-0 is the
    only alternative — true of stderr, and beside the point: those two hooks
    were already using this channel on this event.

    The fix instruction rides with the exit-2 branch only. "Fix the comment
    now" is the wrong thing to say about a check that could not run.
    `PRAXIS_ANCHOR_GATE_ADVISORY=1` demotes the blocking branch to 0 as well.
    """
    if not problems:
        return 0
    lines = []
    for tier in _TIER_ORDER:
        found = [msg for t, msg in problems if t == tier]
        if found:
            lines.append(f"  {tier} ({_TIER_NOTE[tier]}):")
            # A bilingual finding continues on its own line, indented under
            # the bullet so the Korean detail reads as part of the same item.
            lines += ["    - " + m.replace("\n", "\n      ") for m in found]
    header = "[anchor-gate] posted-anchor check result / 게시된 앵커 검사 결과 — " + (
        ", ".join(urls) or "(URL unknown / URL 미상)"
    )
    body = header + "\n" + "\n".join(lines)

    if not any(tier == _BLOCKING for tier, _ in problems):
        json.dump(
            {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": body,
                },
            },
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0

    print(
        body
        + "\n  Fix the comment now / 코멘트를 지금 수정하세요 (`gh api --method PATCH "
          ".../issues/comments/<id> -F body=@<file>`). "
          f"Convention / 규약: {_REFERENCE}",
        file=sys.stderr,
    )
    return 0 if os.environ.get(_ADVISORY_ENV, "").strip() == "1" else 2


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _pre_tool_use(payload: dict) -> int:
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0
    if os.environ.get(_BYPASS_ENV, "").strip() or _bypassed_with_reason(command):
        return 0

    posts, undecodable = _comment_posts(command, payload.get("cwd") or os.getcwd())

    # A post the hook could not decode is not a known-bad anchor, so it is not
    # blocked — the body may not even be an anchor. It is not silent either:
    # silence reads as "checked and clean", which is the one thing it is not.
    # PostToolUse re-checks it against what was actually published.
    for reason in undecodable:
        print(
            "[anchor-gate] could not decode the comment body, so the pre-check was "
            f"skipped ({reason}); an anchor is checked after it is posted instead.\n"
            f"  코멘트 본문을 해독하지 못해 사전 검사를 건너뜀 ({reason}). "
            "앵커였다면 게시 후 검사로 넘어갑니다.",
            file=sys.stderr,
        )

    reasons: list[str] = []
    for idx, post in enumerate(posts):
        tag = f"[{idx + 1}/{len(posts)}] " if len(posts) > 1 else ""
        # PreToolUse blocks on every structure finding regardless of tier: the
        # post has not happened yet, so there is still something to protect.
        reasons += [tag + msg for _, msg in _structure_findings(post["body"])]
        if post.get("edit_last"):
            reasons.append(
                tag + "`--edit-last` 로 앵커를 갱신할 수 없음 — 이 플래그는 "
                "'현재 사용자의 마지막 코멘트'를 고치므로, 갱신 고지가 뒤에 붙는 "
                "순간부터 고지를 덮어쓰고 앵커는 stale 로 남는다. "
                "`gh api --method PATCH .../issues/comments/<id>` 로 id 를 지정하세요"
            )

    if not reasons:
        return 0

    emit_block(
        rule_name="ANCHOR VERIFICATION COMMENT",
        why="; ".join(reasons),
        correct_path=(
            "fix the anchor to the convention and post it again — five required fields "
            "(SHA+rev heading / item table / unverified toggle / per-row evidence toggle "
            "/ update history); SHA freshness is confirmed by the PostToolUse check "
            "against the posted comment / "
            "앵커를 규약대로 고친 뒤 다시 게시하세요 — 필수 필드 5종"
            "(SHA+rev 헤딩 / 항목 표 / 미검증 토글 / 행별 근거 토글 / 갱신 이력). "
            "SHA 신선도는 게시 후 PostToolUse 검사가 실제 코멘트로 확인합니다"
        ),
        bypass_env=_BYPASS_ENV,
        reference=_REFERENCE,
        bypass_reason_hint=(
            f"or append `{_BYPASS_TOKEN} <reason>` to the command"
            f" / 또는 명령 끝에 `{_BYPASS_TOKEN} <사유>` 를 붙이세요"
        ),
    )
    hint = compound_cascade_hint(command)
    if hint:
        print(hint, file=sys.stderr)
    return 2


@fail_open
def main() -> int:
    payload = read_payload(("Bash",))
    if payload is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    event = payload.get("hookEventName") or payload.get("hook_event_name")
    if event == "PostToolUse":
        return _post_tool_use(payload)
    return _pre_tool_use(payload)


if __name__ == "__main__":
    sys.exit(main())
