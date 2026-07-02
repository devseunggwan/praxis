# PreToolUse Version-Bump Evidence Check

Supported hosts: claude, codex

`hooks/version-bump-evidence-check.py` is a PreToolUse advisory (opt-in strict)
that warns — and optionally blocks — when a `gh issue create`, `gh issue edit`,
`gh pr create`, or `gh pr edit` body describes an external version bump but
contains no changelog / breaking-changes evidence.

### Why this exists

Agents rationalize bypassing verification gates for "mechanical" external-version
bumps (a single-line constant change from `v24` to `v25`) and create GitHub issues
or PRs without first fetching the official changelog or breaking-changes page.
The user then has to redo the work from scratch.  This hook enforces the evidence
artifact at the last checkpoint before shared-state mutation.

### Trigger surface

| Command pattern                      | Condition                           |
| ------------------------------------ | ----------------------------------- |
| `gh issue create` (any flags)        | Body contains a version-bump signal |
| `gh issue edit --body / --body-file` | Body contains a version-bump signal |
| `gh pr create` (any flags)           | Body contains a version-bump signal |
| `gh pr edit --body / --body-file`    | Body contains a version-bump signal |

### Detection — version-bump signals

Three signal types are recognized; **S3 alone (no version pair) never triggers**
to avoid false positives on general retrospects or design docs that mention
"deprecated" in prose:

| Signal                            | Pattern                                                                                                                                                | Example                                                         |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| S1 — version pair                 | version pair around an arrow (`→`, `->`, `-->`, …) or `to`; **at least one side** must carry a `v`/`V` prefix; whitespace around the arrow is optional | `v24.0 → v25.0`, `v1.2-->v1.3`, `v0.9 to v1.0`, `24.0 -> v25.0` |
| S2 — bump phrase                  | `bump (sdk\|api\|client\|lib\|library\|version) from ... to ...` (case-insensitive)                                                                    | `Bump SDK from 3.0 to 4.0`                                      |
| S3 — deprecation keyword **+ S1** | any of: `deprecated`, `deprecation`, `sunset`, `eol`, `end of life`, `breaking change`, `breaking-change` **combined with** a version pair             | `v1.0 -> v2.0 breaking change`                                  |

### Required evidence

At least one of the following must be present in the body for the hook to stay silent:

| Evidence                            | Pattern                                                                                                                 |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| E1 — Changelog URL                  | `https?://.../(releases\|changelog\|CHANGELOG\|release-notes\|breaking\|migration\|upgrade\|whats-new\|what-is-new)...` |
| E2 — Fetched line                   | Line starting with `Fetched:` (case-insensitive)                                                                        |
| E3 — Cross-reference matrix heading | Markdown heading `## Cross-reference matrix` or `## Inventory` (case-insensitive)                                       |
| E4 — Bypass env var                 | `PRAXIS_VERSION_BUMP_BYPASS=1`                                                                                          |

### Output format (advisory)

```text
[praxis:version-bump-evidence] External version bump detected: version pair (v24.0 → v25.0)
[praxis:version-bump-evidence] Required evidence missing: official changelog URL, cross-reference matrix, or Fetched: lines
[praxis:version-bump-evidence] Fix: fetch the official changelog/breaking-changes page first, then include
  a cross-reference matrix in the body before creating the issue/PR.
[praxis:version-bump-evidence] Bypass: PRAXIS_VERSION_BUMP_BYPASS=1
```

### Modes

| Mode               | How to enable                  | Behavior                       |
| ------------------ | ------------------------------ | ------------------------------ |
| Advisory (default) | —                              | Emit warning to stderr, exit 0 |
| Strict             | `PRAXIS_VERSION_BUMP_STRICT=1` | Exit 2 (hard block)            |
| Bypass             | `PRAXIS_VERSION_BUMP_BYPASS=1` | Silent pass, exit 0            |

### Fail-open guarantees

- Malformed stdin → exit 0
- Missing / unreadable `--body-file` path → treat as empty body → trigger if version signal was already found elsewhere in the combined body; otherwise exit 0
- `--body-file -` (stdin body) → skip that body segment, fail-open
- Tokenization error → exit 0

### Body extraction

Reuses the `_extract_gh_body` pattern from `external-write-falsify-check.py`:
flags `-b`, `--body`, `-F`, `--body-file` (space-separated or `=` form).
`_hook_utils.safe_tokenize` + `iter_command_starts` handle env prefixes,
wrapper commands (`sudo`, `env`), and multi-command Bash strings.

### Tests

```bash
bash tests/test_version_bump_evidence_check.sh
```

35 cases covering:

| Case                                                                 | Expected                      |
| -------------------------------------------------------------------- | ----------------------------- |
| Version pair `→` / `->` / `to`                                       | Warn                          |
| Version pair `-->` / `->` with no surrounding whitespace (M1)        | Warn                          |
| `v`/`V` prefix on right side only                                    | Warn                          |
| Uppercase `V` prefix pair                                            | Warn                          |
| Language-runtime mention (`Python 3.11 to 3.12`, no `v` prefix) (M2) | Pass — false-positive control |
| `bump sdk/api/client from ... to ...` phrase                         | Warn                          |
| Deprecation keyword + version pair                                   | Warn                          |
| Deprecation keyword **alone** (no pair)                              | Pass — false-positive control |
| Changelog URL in body                                                | Pass                          |
| `Fetched:` line in body                                              | Pass                          |
| `Cross-reference matrix` heading                                     | Pass                          |
| `Inventory` heading                                                  | Pass                          |
| `PRAXIS_VERSION_BUMP_BYPASS=1`                                       | Pass                          |
| Strict mode + no evidence                                            | Block (exit 2)                |
| Strict mode + evidence                                               | Pass                          |
| `--body-file` path read                                              | Warn / pass                   |
| Empty body file                                                      | Pass                          |
| Missing body file                                                    | Fail-open (exit 0)            |
| Malformed stdin                                                      | Fail-open (exit 0)            |
| Non-Bash tool                                                        | Pass                          |
| `git push` / `gh pr list`                                            | Pass                          |
