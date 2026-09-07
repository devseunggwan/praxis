# PreToolUse Wrapper Signature Verification Advisory

Supported hosts: all

`hooks/advisory-nudge/advisory-wrapper-signature-verify/impl.py` fires on `PreToolUse` events for
`Write` / `Edit` tool calls. It detects writes of wrapper/client code that
delegate to another module's functions and emits an advisory reminder to
verify the wrapped signatures by reading source before authoring the wrapper.

### Why this exists

A recurring failure mode (4 occurrences across different sessions) has been
identified: when writing wrapper/client classes that delegate to underlying
functions, parameter names and return types are inferred from function
names rather than verified by reading the actual source — leading to
multiple wrong signatures per session.

Documented occurrences:

1. Exception attribute name inferred from class name (wrong attribute used).
2. Internal utility behavior assumed without reading source (pre-split mismatch).
3. Generic multi-package wrapper assuming identical factory signatures
   (3/4 packages failed silently).
4. Query wrapper class with 6 wrong signatures (non-existent params, wrong
   return types).

Root cause: no enforcement gate fires before writing wrapper code to prompt
reading the wrapped function signatures first. Memory entries and prompt-layer
rules exist for this pattern but are not retrieved at execution time.

Reference: issue [#235](https://github.com/devseunggwan/praxis/issues/235).

**Escalation criteria:** After ~1 month of advisory operation, evaluate
recurrence rate. If advisory is repeatedly bypassed without verification,
re-evaluate either (a) a stricter content heuristic, or (b) escalation to
`ask` for a narrower trigger set. Blocking outright remains undesirable —
not all `client.py` writes are wrappers.

### What is detected

The advisory fires when **all** of these conditions hold:

| Condition | Description |
| ----------- | ------------- |
| Tool name | `Write` or `Edit` |
| File extension | Path ends with `.py` |
| Not a test file | Path does **not** match `tests?/`, `test_*.py`, or `*_test.py` |
| Wrapper-shape path | Path ends with `client.py` **or** contains `_wrapper` |
| Body content | Matches at least one delegation pattern (see below) |

If any condition fails — silent pass-through (exit 0, empty stderr).

**Test-file exclusion rationale:** real codebases routinely write wrapper
assertions inside `tests/` or `test_*.py` files (mocks, fixtures, fake
clients). Firing the advisory there is noise, not signal — the test author
is intentionally re-stating a signature, not authoring a production wrapper.

#### Delegation patterns (any match fires)

| Pattern | Matches |
| --------- | --------- |
| `return\s+get_\w+\s*\(` | `return get_user(id)`, `return get_session_token()` |
| `return\s+create_\w+\s*\(` | `return create_order(data)` |
| `from\s+[\w.]+\.queries\s+import` | `from foo.queries import get_user` |
| `from\s+[\w.]+\.client\s+import` | `from acme.client import APIClient` |

For `Edit`, the body checked is `tool_input.new_string` (the proposed
replacement, not the pre-edit text). For `Write`, it is `tool_input.content`.

**This hook never blocks.** Advisory mode only — exit 0 in all cases.

### Response shape

**Advisory message** (emitted to stderr, never stdout):

```
[advisory-wrapper-signature-verify] Wrapper/client write detected
File: <file_path>

Before writing, verify actual function signatures:
  grep -n '^def ' <wrapped_module>.py
  or use Read tool to inspect the module directly

Common mistake patterns:
  - Adding non-existent parameters
  - Wrong return type (list[TypedObject] vs list[dict])
  - Parameter name typo (e.g., hours vs days, id vs data_id)
```

**Exit code:** always `0` (never blocks).

**JSON response:** none — the hook writes to stderr and exits 0.

**Channel reality (issue #874).** stderr from a `PreToolUse` hook that exits 0
does **not** reach the model. `hooks/_lib/_hook_io.py:88-92` records the same
fact for the Stop lane ("stderr with exit 0 only reaches the debug log, so the
pre-#647 stderr advisories were effectively invisible"), and the #841 canary
recorded it for `PreToolUse` — which is why `cwd-relative-exec-advisory` chose
an `ask` decision over a stderr advisory rather than "reproduce the exact gap
it is meant to close"
(`hooks/advisory-nudge/cwd-relative-exec-advisory/spec.md` § "Channel — `ask`,
not stderr advisory").

Earlier revisions of this spec asserted the opposite — that Claude Code reads
advisory `PreToolUse` stderr into the model's context — and equated stderr with
`additionalContext`. Both claims were wrong, and they contradicted `_hook_io.py`,
which is authoritative. stderr is a **transcript/debug-log** channel here, not a
model channel; `additionalContext` is a distinct stdout JSON field this hook
does not emit. Whether the debug-log channel is sufficient for this hook is
exactly the open escalation question above, not a settled yes.

### Parsing guarantees

| Condition | Behavior |
| ----------- | ---------- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `tool_name` not `Write` or `Edit` | exit 0 (silent pass) |
| Missing `file_path` or non-string value | exit 0 (silent pass) |
| Path not wrapper-shape | exit 0 (silent pass) |
| Content missing or no delegation match | exit 0 (silent pass) |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |
| Any uncaught exception | exit 0 (silent pass, no crash) |

The hook uses no external dependencies (no PyYAML, no third-party packages).
All parsing is done with the Python standard library only.

### Tests

```bash
bash tests/hooks/advisory-nudge/test_advisory_wrapper_signature_verify.sh
```

Covers 22 cases:

**Positive (advisory emitted):**
- `Write` `client.py` with `return get_*(` delegation
- `Write` `client.py` with `return create_*(` delegation
- `Write` `_wrapper` path with `from foo.queries import`
- `Edit` `client.py` with `from acme.client import`
- `Edit` `_wrapper` path with `return get_*(`
- `Write` `user_client.py` (path endswith `client.py`)

**Negative (silent pass):**
- `client.py` without any delegation pattern
- Regular `.py` path without `_wrapper` / `client.py`
- `README.md` path (not Python)
- `Read` tool (out of scope)
- `NotebookEdit` tool (out of scope)
- `client.py` with `return` that is not `get_` / `create_` prefixed
- File where `queries` appears only in a comment, not as an import
- `_wrapper` path but `.md` extension (non-Python file)
- Test file under `tests/`
- `test_*.py` file
- `*_test.py` file
- File under `test/` directory

**Edge (fail-open):**
- Malformed JSON stdin → exit 0, silent pass
- Empty content field → exit 0, silent pass
- Missing `file_path` → exit 0, silent pass
- `Edit` payload missing `new_string` → exit 0, silent pass
