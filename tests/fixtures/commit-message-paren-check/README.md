# commit-message-paren-check fixtures

Corpus for `tests/hooks/preflight-gate/test_commit_message_paren_check.py`,
the coverage for the `commit-message-paren-check` preflight gate.

## Provenance

Each `<sha>.txt` is a **verbatim copy** of one real commit message from this
repository's own history, captured once with

```bash
git log -1 --format=%B <sha> > tests/fixtures/commit-message-paren-check/<sha>.txt
```

Nothing was edited: byte-for-byte `%B` output, trailing newline included. The
test compares every file against the live commit whenever the history is
present, so a copy that drifts from its commit fails there.

The commits are the ones release-please's Conventional Commits parser logged
as unparseable (issue #1228) — the positives — plus commits the same parser
accepted over the same range, which are the negative controls that keep a
green run from meaning "the gate always fires".

| File            | Role     | Parser verdict (line, shape) |
| --------------- | -------- | ---------------------------- |
| `2d86ff6c.txt`  | rejected | line 127, nested             |
| `b328852b.txt`  | rejected | line 33, unclosed            |
| `4b0df391.txt`  | rejected | line 9, unclosed             |
| `36f937f7.txt`  | rejected | line 156, nested             |
| `e399693e.txt`  | rejected | line 18, unclosed            |
| `4d83c916.txt`  | rejected | line 189, nested             |
| `54128d0c.txt`  | rejected | line 17, unclosed            |
| `ed44c51.txt`   | accepted | —                            |
| `5fdff21.txt`   | accepted | —                            |
| `3d6a72f.txt`   | accepted | —                            |
| `2d558892.txt`  | accepted | — (depth-3 nesting mid-line) |

## Why files and not `git log`

The test used to read each message back with `git log -1 --format=%B <sha>`
at run time. That works only when the clone carries the whole history: on a
shallow checkout (`--depth 1`, the default for most CI and agent sandboxes)
none of these commits exist and `git log` exits 128, failing eleven cases for
a reason unrelated to the gate (issue #1302). The copies make the suite
independent of clone depth while the live-compare case keeps them honest.
