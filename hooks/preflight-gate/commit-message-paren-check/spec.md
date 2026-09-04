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
| `git commit -m "…"` / `--message` / `-m="…"` / `-mvalue` / `-am "…"` | every `-m` value, joined with a blank line as git joins them |
| `git commit -F <path>` / `--file` | the file's whole contents |
| `git commit -m "$(cat <<'EOF' … EOF)"` | the heredoc body |
| `git commit -F - <<'EOF' … EOF` | the heredoc body |
| `git commit -F -` (no heredoc) | silent pass (stdin unreadable) |
| `git commit -m "$(cat /tmp/x)"` | silent pass (unresolvable substitution) |
| anything that is not `git commit` | silent pass |

Title extraction is the shared `hooks/_lib/git_commit_titles.py` parser, which
this hook extends with `extract_git_message_texts` — the same argv walk the two
title gates use, differing only in keeping the whole message instead of the
first line of the first `-m`. Keeping one walk is the point of that module
(issue #594): a second copy is how the pre-#594 parsers drifted.

The heredoc fallback (`heredoc_bodies` in `hooks/_lib/_hook_utils.py`, the
counterpart of the existing `strip_heredoc_bodies`) is **scoped to a `git
commit` argv that produced no readable message**. That is exactly the
`-m "$(cat <<'EOF' …)"` and `-F -` shape, where the heredoc body IS the message
and nothing else in the command produced one. Reading heredocs unconditionally
would grade prose belonging to some other command in the same `&&` chain.

## What is NOT covered

- **Manual shell commits.** Same acknowledged boundary as every sibling
  PreToolUse gate: only AI-authored Bash calls pass through hooks. That is the
  population that produced all three incidents.
- **Squash-merge titles.** `gh pr merge --squash` composes its message on
  GitHub's side. A PR title cannot hold a body line, and the `(#N)` suffix
  GitHub appends is mid-line, so the shape this gate detects cannot arise there.
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
