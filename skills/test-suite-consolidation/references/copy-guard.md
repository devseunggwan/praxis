# Copy guard for test fixtures/helpers

Used by SKILL.md Step 5. The guard is a check script in the target repo, run
by pre-commit and CI; this file is its design, not an implementation.

## Detection: what counts as "the same"

- Group module-level `def`s under the tests root (skip `test_*` functions and class methods) by normalized body: unparse → reparse, drop docstring, function name, return/arg annotations, rename args positionally, rename recursive self-calls to the same placeholder. Keep decorators: `@pytest.fixture(autouse=True)` and `@pytest.fixture(scope="session")` behave differently. Group key = `ast.unparse` text (stable across Python minor versions; `ast.dump` is not).
- Compute free names from the normalized clone, not the original node — otherwise annotation names (`Path` vs `PathLike`) leak back in after normalization erased them and split true copies into separate groups.
- Compute free names per scope, not with one `ast.walk`: a comprehension target, lambda arg or inner-function arg binds only inside that nested scope (decorators, defaults and class bases still run in the outer one). A flat walk lets `[fmt for fmt in rows]` hide the function's own read of an imported `fmt`, so helpers bound to `pkg_a.fmt` and `pkg_b.fmt` get one key — a false copy. Recurse into nested scopes, union their free names into the outer `loaded`, keep their bindings local; prove it with a different-import case (no copy) plus a same-import control (copy) that fails red on the flat version.
- **Bind free names into the key.** Same text is not same behavior: a fixture calling `Store()` in two files means two different classes unless both resolve to one definition. For each name the function reads but does not bind (minus builtins), replace the identifier in the body with a token for its binding (so `import re` vs `import re as regex` compare equal) and append the binding set to the key:
  - `import x` / `from m import y` → `import:m.y`; resolve relative imports against the file's package (`<root>/tests/a/test_x.py` → `tests.a`);
  - an import from another module inside the tests tree → follow ONE hop to that module's local definition, so "defined here" and "imported from there" compare equal;
  - a module-level def/class/assign → hash of its source; when that source reads `__file__`, append the file path (a `SQL_DIR = Path(__file__).parent` helper points somewhere different in every file).
- Ignore bodies shorter than ~60 chars after normalization (`yield`, `return "asyncio"` overrides). Mind this when writing test fixtures for the checker: a too-short body silently drops out of every group and a mutation survives.
- Same-file repeats are not copies; a group must span ≥2 files.
- Messages name the shared definition to use (`<copy>: redefines shared — use <path>::<name>`), plus the list of shared locations.

## Scope and thresholds

Tests favor readability over uniqueness (Google Testing Blog, "Tests Too DRY? Make Them DAMP!") and the usual rule is to refactor on the 3rd occurrence. Whole-tree scanning makes the check depend on other people's merges (two independently-green PRs each adding one copy turn every later PR red) and needs a baseline file. Use the "new code only" model (SonarQube's default quality gate applies to new code only):

| Case | Action |
| --- | --- |
| Scope | compare the working tree against a base revision; report only definitions whose `(impl, path::name)` pair did not exist at the base — this also catches an existing function edited into a copy |
| Group only moved/renamed (same count of distinct shared files and test files as at the base) | tolerate — renaming a file that holds a legacy copy must stay quiet; fail only groups the change grew |
| Redefines something already in a shared module (conftest / tests_common) | fail at the first copy |
| A shared definition duplicated in another shared module | fail |
| Same impl reaches ≥3 test modules | fail |
| 2 test-module copies | tolerate |
| Base revision unreadable | exit 2, never pass |

Base revision: `HEAD` for pre-commit (pre-commit stashes unstaged edits, so the tree is HEAD + staged); `HEAD^1` in CI — on `pull_request` the checkout is the merge commit whose first parent is the base tip, on push it is the previous branch tip. That needs `actions/checkout` `fetch-depth: 2`. On a dev→prod release PR the first parent is prod, so copies that exist only on dev surface there — expected, mention it in the PR. List the base with `ls-tree -z` so non-ASCII paths are not quoted. Read the base in one process: `git ls-tree -r --name-only <rev> -- <tests>` then `git cat-file --batch` fed `<rev>:<path>` lines (parse `<sha> blob <size>` headers); hundreds of `git show` calls are slow. Cost is ~2× parse (≈6 s on ~650 files) vs ~1 s for a whole-tree scan.

Validate before shipping: replay the checker on historical `base..tip` pairs (`introduced(collect(sources_at(base)), collect(sources_at(tip)))`) — including the commits that broke the old guard — and on `origin/<base>..HEAD` of the PR itself. Replays of the old guard's failures usually return 0 under the new rules (base-branch copies, 2-copy groups), so they prove tolerance, not detection: keep at least one replay that reports a real copy as the positive control, and say which is which in the verification anchor. Moving helpers to a shared module can *create* new copy groups (call sites that differed only in which local helper they called now bind to the same import); the diff-scoped check flags them, and they are this PR's to fix.

## Testing the checker

Make `collect()` take `{relpath: source}` so unit tests build trees as dicts, and keep one tmp-git-repo test for the base-reading path and one for the unreadable-base exit code. Mutation-check every decision point (binding, `__file__` pinning, import hop, base tolerance, threshold, keyed-by-name-only, shared redefinition, exit code, recursion rename, min body): each mutation must fail ≥1 test. Apply mutations with an exact-once string replace that asserts the match count — a silent no-op mutation reads as "survived".

## If a baseline is used anyway

- Key entries `path::name` (version-independent), not body hashes.
- Stale entries (no longer a copy) fail, so the list only shrinks (PHPStan `reportUnmatchedIgnoredErrors` model).
- `--update-baseline` must write `baseline ∩ current` and exit non-zero listing `current − baseline`; writing all current copies launders a new copy in while removing an old one.

## Where it runs in CI

| Placement | Cost / effect | Verdict |
| --- | --- | --- |
| Step before tests in the test job | runs on every matrix leg; a failure skips every test step after it, hiding test results | no |
| Separate workflow / job | a runner allocation per push (~20 s for a few-second check); agent PRs touch tests on nearly every push | no |
| **Last step of the existing test job, `if: ${{ !cancelled() && matrix.group == 1 }}`** (plus the job's `[skip test]` condition) | reuses the live runner, runs even when tests fail, never hides them, one leg only | yes |
| pre-commit hook only | free, but bypassable and absent on remote agents | add as a complement |

Do not leave the guard to a change-selection tool (testmon and similar): those
track imports and executed lines, so a check that only *reads* test files is
never reselected. It needs an unconditional step.

Block on `pull_request` only: add `continue-on-error: ${{ github.event_name != 'pull_request' }}` to the step. When `deploy` needs the test job, a push-time failure blocks the image build and rollout, and push can fail through a path no PR check prevents — two concurrently open PRs each add an allowed 2nd copy, and after both merge the push run sees a 3rd.

Check with `actionlint` and compare finding lists before/after (`actionlint -oneline`, strip the `file:line:col:` prefix, sort, diff) — the main workflow may carry pre-existing findings. A non-required check does not block merge; making it required is a repo-admin branch-protection change — say so, do not change it.
