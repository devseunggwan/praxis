# PreToolUse Commit Message Paren Check

Supported hosts: all

`hooks/preflight-gate/commit-message-paren-check/impl.py` intercepts every
AI-authored `git commit` Bash call and blocks (exit 2) when the commit message
holds a line that `@conventional-commits/parser` — the parser release-please
runs — cannot parse.

## Why this gate exists

release-please parses each commit with `@conventional-commits/parser`, and that
parser attempts EVERY LINE of the message as a `type(scope): summary` header. A
line whose leading non-space run is glued to `(` opens that paren as a *scope*,
and inside a scope the only valid token is `)`. Two shapes fail:

| Shape | Line | Parser error |
| --- | --- | --- |
| nested | another `(` opens before it closes | `unexpected token '('` |
| unclosed | the line ends before it closes | `unexpected token '\n'` |

A commit the parser rejects is **skipped**, and the release workflow still ends
`completed/success` — so the commit silently loses its CHANGELOG entry with no
red signal anywhere. Three commits were dropped from two releases that way
before a human read the run log (issue #1228). The loss is permanent:
release-please computes the next release from the previous tag, so a commit
skipped once never comes back, and hand-editing the CHANGELOG is overwritten by
the next run.

`scripts/check-changelog-coverage.py` (issue #1233) already promotes the skip to
a red release run. That is the detector; this gate is the preventer — it fires
before the commit exists, which is the only point at which the message is still
editable (the branch's history is push-protected afterwards).

## The rule

For each line of the message:

1. Find the first `(`. No `(`, or `(` at column 1 → **pass**.
2. Take the prefix before it. Whitespace anywhere in the prefix → **pass**
   (the word ended, so no scope opens).
3. `!` or `:` anywhere in the prefix → **pass** (the header separator was
   already consumed, so the parser is past the scope position).
4. Otherwise scan forward from the `(`: a `)` before any other `(` → **pass**;
   another `(` first → **nested**; end of line first → **unclosed**.

Every clause is a PASS the real parser was measured to grant, not an inference
from its grammar. Probed at `@conventional-commits/parser` 0.4.1, each line
appended as the body of `fix(x): subject`:

| Line | Parser | Clause |
| --- | --- | --- |
| `` `(a(b))` `` | FAIL nested | 4 |
| `` `(a b `` | FAIL unclosed | 4 |
| `` `(ab)` `` | OK | 4 — closes first |
| `word(a(b))` | FAIL nested | 4 |
| `` ` (a(b)) `` | OK | 2 — space in prefix |
| ``- `(a b`` | OK | 2 — space in prefix |
| ``x `(a(b))` `` | OK | 2 — mid-line |
| `(a(b))` | OK | 1 — column 1 |
| `f(x) g(y` | OK | 4 — first paren closed |
| `!(a(b))` | OK | 3 |
| `fix!(a(b)): x` | OK | 3 |
| `type(scope):(a(b))` | OK | 4 — the scope closes |
| `fix(a(b)): x` | FAIL nested | 4 |
| `1(a(b))` | FAIL nested | 4 |
| `Co-Authored-By: X (a(b))` | OK | 2 |

The rule is applied to the subject line too, on the same terms — a subject
whose own scope nests (`fix(a(b)): x`) fails the parser identically.

### Measured agreement

Every commit in this repository's history, each message fed to the real parser
(`@conventional-commits/parser` 0.4.1, the package release-please resolves) and
to the rule, including the two commits that add this gate:

```text
commits scanned : 659
parser rejects  : 7   rule predicts: 7
agreement       : 7 caught / 652 both-clean / 0 false alarm / 0 missed
```

## Enforcement grade: blocking

Blocking (exit 2), mirroring `commit-title-format-check` rather than
`commit-title-length-check`'s `ask`. Three grounds:

- **Measured precision.** 0 false alarms over 652 clean commits, and the check
  is pure local string work — no network call, no external state, nothing that
  can flake the way `commit-title-length-check`'s `gh pr view` path can. That
  path is advisory *because* it makes a live call; this one makes none.
- **Asymmetric damage.** A false positive costs one edit (add a leading space).
  A miss costs a CHANGELOG entry that cannot be recovered at all.
- **Sibling precedent.** The family splits on what the rule protects:
  `commit-title-length-check` asks because 50 characters is a human style
  preference, `commit-title-format-check` blocks because a malformed type
  breaks machine consumers. This gate protects the same machine consumer that
  gate does — release-please's parser — so it lands on the blocking side.

`PRAXIS_COMMIT_PAREN_STRICT=0` switches to advisory (exit 0, stderr only),
matching the sibling's `PRAXIS_COMMIT_TITLE_FORMAT_STRICT` shape.

## Where the message is read from

| Command shape | Source |
| --- | --- |
| `git commit -m "…"` / `--message` / `-m="…"` / `-mvalue` / `-am "…"` / `-am"…"` | every `-m` value, joined with a blank line as git joins them |
| `git commit -F <path>` / `--file` / `-F<path>` / `--file=<path>` | the file's whole contents |
| `git commit -m "$(cat <<'EOF' … EOF)"` | the body of the heredoc that `-m` value opens |
| `git commit -m 'subject' -m "$(cat <<'EOF' … EOF)"` | the subject **and** that body |
| ``git commit -m "`cat <<'EOF' … EOF`"`` | the same — a backtick run is a substitution too |
| `git commit -F - <<'EOF' … EOF`, `-F- <<'EOF'`, `<< 'EOF'` | the body of the last heredoc this argv opens |
| `git commit -F -` (no heredoc) | silent pass (stdin unreadable) |
| a heredoc belonging to another command in the chain | not the message — never graded |
| two heredocs sharing one delimiter word | silent pass (the name identifies neither) |
| `<<-` body | leading tabs stripped first, as bash strips them |
| an UNQUOTED `<<EOF` body | spliced and expansion-blanked first, as bash delivers it |
| a message line carrying `$(…)`, `` `…` ``, `${…}`, `$NAME` | that line is blanked — its delivered text is unknowable |
| a `-m` value with an unclosed `$(` or backtick | silent pass (bash rejects the command; no commit happens) |
| a whole `-m` value that is one single-quoted run | graded verbatim — the shell substitutes nothing in it |
| anything after `--` | a pathspec, never a message flag |
| `git commit -m "$(cat /tmp/x)"` | silent pass (unresolvable substitution) |
| anything that is not `git commit` | silent pass |

Short options are read as one grammar rather than as a list of spellings: a
cluster is leading no-value flags followed by at most one value-taking flag
(`m`, `F`), which consumes the rest of the cluster or, when the cluster ends
with it, the next token. `-am"fix: x"`, `-am "fix: x"`, `-Fmsg.txt`, `-F-` and
`-aF -` are therefore all the same shape. A cluster holding any other
character is not read at all — `-Smike@example.com` takes an attached key id,
so guessing at it would invent a message source that git never saw.

Every entry above was measured before it was written: the command is run under
bash with `git` replaced by a recorder that prints the argv and the message it
was handed, and the expected verdict is the detector applied to *that* text.
For a blocking gate the false positive is the expensive direction — it stops
valid work — so a shape whose delivered text cannot be known is passed rather
than graded.

Title extraction is the shared `hooks/_lib/git_commit_titles.py` parser, which
this hook extends with `extract_git_message_texts` — the same argv walk the two
title gates use, differing only in keeping the whole message instead of the
first line of the first `-m`. Keeping one walk is the point of that module
(issue #594): a second copy is how the pre-#594 parsers drifted.

Each heredoc read is **bound to the message source that names it**, via
`heredoc_sources` and `heredoc_delimiters` in
`hooks/_lib/_hook_utils.py` (the delimiter-keeping counterpart of the existing
`strip_heredoc_bodies`). An unresolvable `-m` value carries its own opener
inside the token — `$(cat <<'EOF'` — and a `-F -` reads stdin from a
redirection on its own argv, so in both shapes the source says which body is
its own. Two failure modes follow from getting that binding wrong, and both
were shipped once:

- **Scoping the read to an argv that produced NO readable message** let a
  malformed body ride in behind a well-formed subject: `-m 'fix: x' -m "$(cat
  <<'EOF' …)"` yields a readable first `-m`, so the heredoc was never read.
- **Reading every heredoc a SOURCE opens** graded a body bash discards.
  Redirections apply left to right, so `cat <<A <<B` reads `B` and `A` is
  opened and thrown away — grading it blocked a commit whose message was
  clean.
- **Reading every heredoc in the command** graded prose belonging to some
  other command in the same `&&` chain, blocking a valid commit whose own
  message was clean.

A delimiter word identifies a body only while it is unique in the command.
`cat <<EOF … EOF; git commit -F - <<EOF … EOF` reuses it, and nothing in the
token stream separates the two — that source contributes no text and the gate
stays silent, which is the fail-open direction this repo takes for a gate.

## What is NOT covered

- **Manual shell commits.** Same acknowledged boundary as every sibling
  PreToolUse gate: only AI-authored Bash calls pass through hooks. That is the
  population that produced all three incidents.
- **Squash-merge messages.** `gh pr merge --squash` composes its message on
  GitHub's side, and no hook sees the result. The *title* is safe for the
  reason first recorded here — it holds no body line, and the `(#N)` suffix
  GitHub appends is mid-line. The **body** is not: GitHub builds it by
  concatenating the PR's commit messages as `* <message>` entries, so every
  body line keeps the column it had in its own commit, line-initial ones
  included.

  This gate's own merge commit is the counterexample. `9ea4785a`
  (`feat(hooks): gate parens release-please rejects (#1268)`) carries at
  line 75 a line that opens a pseudo-scope and nests inside it, and
  release-please rejected it — the same shape, arriving by the path this
  section previously called impossible:

  ```console
  $ git log -1 --format='%B' 9ea4785 | sed -n '75p' | cut -c1-30
  `\tword(a(b))`, whose leading
  ```

  The rule holds; only the reach does not. Feeding that message to this gate
  blocks it, so what is missing is a hook at the composing surface, not a
  detector:

  ```console
  $ git log -1 --format='%B' 9ea4785 > /tmp/sq.txt
  $ printf '%s' '{"tool_name":"Bash","tool_input":{"command":"git commit -F /tmp/sq.txt"}}' \
      | python3 hooks/preflight-gate/commit-message-paren-check/impl.py
  rc=2
    line 75: [nested] '`\tword(a(b))`, whose leading run holds whitespace, ...'
    line 226: [nested] '`foo($(printf x))` was graded raw and read as a nested paren; git receives'
  ```

  A commit authored through this gate cannot contribute such a line, so the
  exposure is the commits that never passed it — anything predating the gate
  (which is how `9ea4785a` itself happened: the offending line was written in
  the very PR that added the gate), a human's shell commit, or another tool's.
  Closing it needs a check at `gh pr merge --squash` time, which would have to
  read the PR's commits over the network; that is a new surface with its own
  cost and is not part of this gate.
- **A message reaching git by a path the tokenizer cannot resolve** — a
  variable (`git commit -m "$MSG"`), a file written later in the same chain.
  Silent pass, per the fail-open posture in `DESIGN.md`.

## Tests

`tests/hooks/preflight-gate/test_commit_message_paren_check.py` runs the seven
real rejected commits from this repo's history as positives and four
parser-accepted commits as negative controls — including `2d558892`, whose body
carries depth-3 nested parens mid-line and parses fine. Without the negative
controls a green suite cannot distinguish "the gate caught it" from "the gate
always fires".
