# pytest-direct-exec-advisory

Supported hosts: all

`hooks/advisory-nudge/pytest-direct-exec-advisory/impl.py` runs on
`PreToolUse(Bash)`. It writes a stderr advisory when a Bash command directly
executes a pytest-shaped Python file with `python` or `python3`. It never
blocks the command.

## Why this exists

Executing a pytest file as a plain Python script can exit successfully while
running zero tests. Issue
[#909](https://github.com/devseunggwan/praxis/issues/909) records the concrete
case: `python3 tests/test_ask_option_text.py` returned exit 0 with no output,
while `python3 -m pytest tests/test_ask_option_text.py -q` collected and passed
11 tests. That file has plain `test_*` functions and no `pytest` import, so an
import-only detector would miss the incident.

## Detection contract

All of these conditions must hold:

1. A Bash command segment invokes a binary whose basename is `python` or
   `python3`. Environment assignments, the `env` wrapper, absolute interpreter
   paths, and common interpreter flags are accepted.
2. The first script operand is a readable `.py` file whose basename starts
   with `test_`, or whose path contains a `tests` directory.
3. Parsing the file's AST finds at least one pytest signal:
   - an explicit `pytest` import or `pytest.*` attribute;
   - a module-level `test_*` function, including `async def`;
   - a `Test*` class with a `test_*` method.

The advisory recommends `python3 -m pytest <path>`.

## Enumerated command surface

| Input shape | Result |
| --- | --- |
| `python3 tests/test_api.py` | advisory |
| `/usr/bin/python3 -u tests/test_api.py` | advisory |
| `env MODE=test python -W error tests/test_api.py` | advisory |
| `python3 -- tests/test_api.py` | advisory |
| `echo ready && python3 tests/test_api.py` | advisory |
| `python3 -m pytest tests/test_api.py` | silent |
| `python3 -c '...'`, `python3 -` | silent |
| missing, unreadable, invalid, or dynamically expanded path | silent |
| comments, echoed strings, and heredoc bodies | silent |

Only the first Python script operand is inspected. A later positional argument
that looks like a test path does not make an ordinary first script a match.

## Heredoc handling

The hook removes heredoc body lines before command segmentation while retaining
the opener's physical line. Therefore a direct execution before a later
heredoc, on the heredoc opener line, or after the closing delimiter is still
inspected. Quoted `"<<EOF"` text and the `<<<` here-string operator are not
treated as heredoc openers.

## Source false-positive controls

Comments and string literals containing `import pytest` or `def test_*` do not
count because detection uses the parsed AST. A `Test*` class without test
methods and a nested helper named `test_*` do not count.

A file with an intentional `if __name__ == "__main__"` diagnostic runner can
still receive the advisory if it also contains tests. This is accepted because
the hook is advisory-only and cannot know whether the caller intended the
runner or the test suite.

Custom pytest filename patterns such as `*_test.py` outside a `tests`
directory are out of scope until a concrete recurrence justifies widening the
path predicate.

## Fail-open contract

| Condition | Behavior |
| --- | --- |
| malformed or missing stdin JSON | exit 0, silent |
| `tool_name != "Bash"` | exit 0, silent |
| empty command | exit 0, silent |
| source file cannot be read or parsed | exit 0, silent |
| any uncaught exception | exit 0 via `@fail_open` |

## Registration

`hooks/manifest.json` registers the hook as `advisory-nudge`,
`PreToolUse`, matcher `Bash`, timeout 5 seconds.

## Tests

```bash
bash tests/hooks/advisory-nudge/test_pytest_direct_exec_advisory.sh
```
