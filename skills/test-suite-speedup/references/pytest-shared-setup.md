# Sharing expensive pytest setup safely

Use when one test file spends most of its time rebuilding the same heavy object (e.g. `DagBag(...)` re-parsing every DAG per test or per helper call).

## Find the hot constructor

- Count construction sites, including inside module-level helpers that many tests call — helpers multiply the cost far beyond the visible site count.
- Before quoting a saving, size the *shareable* part, not the factory's total: list the factory's callers from a one-process profile (`pstats.Stats(...).stats[<factory key>][4]` → caller file:line, call count, cumtime) and read each caller's arguments and surrounding patches. A factory built hundreds of times from dozens of sites, each 1–8 times with different variables or patches, shares only the repeated-identical calls; the rest would hand a test a DAG built under someone else's patch. Quote that subset as the estimate, and when it falls well below the figure already given to the user, say so and ask before dropping or shrinking the item.
- `grep -rn "scope=\"session\"\|scope=\"module\"" <tests>/conftest.py`: the repo usually already has a session fixture for that object, with a comment explaining its CI contract. Mirror it instead of adding `functools.cache` or a new module fixture.

## Prove sharing is safe (before editing)

1. **Fresh-per-construction state**: know what the old per-test construction gave each test. A `DagBag` parse re-executes each DAG file as a fresh module, so callables' `__globals__` were private per test; once shared they are not.
2. **Patch restoration**: grep for writes to shared state — `__globals__\[...\] =`, `setattr(`, attribute assignment on task/dag objects, `.params[...] =`, `set_upstream`. `mock.patch.dict(obj.__globals__, ...)` and `mock.patch(...)` contexts restore on exit and are safe.
3. **Patch-before-construct**: list calls to the constructor/helpers nested inside `with mock.patch` blocks. If a patch is active *before* construction, the old code captured the patched object at parse time; sharing changes that. Calls nested only inside `assert`/`pytest.raises` are fine.
4. **Module-level mutable globals** in the files being loaded (`grep -nE "^\s*global |^[A-Za-z_]+\s*=\s*(\{|\[|set\()|lru_cache"`): mutation by one test leaks to the next.

## Prove it after editing

- **Order independence**: collect ids (`set -o pipefail; pytest --collect-only -q <file> | grep '::' > ids`, so a collection error fails instead of leaving a partial list), then run them reversed (`tac ids` with GNU coreutils; `tail -r ids` on BSD/macOS, where GNU `tail` has no `-r`) and seeded-shuffled (`random.Random(<seed>).shuffle`) as explicit node-id args. pytest runs explicit ids in argument order, so this needs no plugin and catches state left by one test for the next.
- **Same mutants caught as before**: a cache or shared fixture must not let a test pass that used to fail. Pick one production mutation per behavior the file pins (e.g. a feature branch disabled with `if False and <flag>:`), and under that one mutation run both the base copy of the test file (`git show origin/<base>:<t> > <dir>/<stem>_basecopy.py` next to the original, so its conftest still applies) and the branch copy; the two `FAILED` id sets must be equal. Name the copy with `_` — a `<stem>.basecopy.py` is not an importable module name and pytest errors at collection. Restore production from a scratch copy, confirm `git diff --quiet -- <prod file>`, and delete the base copy in the same script.
- **Cross-file sharing**: a session object is now shared with every other file that uses it, so run the CI step that groups them (e.g. `pytest -m <marker> <tests>`) in one session, not file by file.
- An ad hoc bundle of test files (e.g. every caller's tests in one session) can fail on cross-file state the CI grouping never creates — a UNIQUE-key collision on database state another file left behind, or a dropped database connection during a fixture's schema setup. Before blaming the change, run the identical file set with the touched files at the base ref in a disposable worktree, leaving the active worktree and index untouched (`git worktree add --detach <scratch>/cmp HEAD`, then `git -C <scratch>/cmp checkout <base> -- <files>`, run there, `git worktree remove --force <scratch>/cmp`); a `git checkout <base> -- <files>` / `git checkout HEAD -- <files>` pair in the active tree would overwrite staged and unstaged edits to those files. The same failures there mean pre-existing, so report them as such and rerun the affected file alone.
- Triage failures there by the error before suspecting the change. Example: if a DB-container fixture reports `ConnectionRefusedError` from the Docker API on a host whose docker context is not the default socket (OrbStack), rerun with `DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}')`. Then rerun the whole step once more in a single session, so the pass covers the cross-file interaction too.

## Wire it with minimal churn

- When many plain helper functions need the object, an autouse fixture that requests the session fixture and binds it to a module global (`_bag()` accessor) avoids rewriting every helper signature; replace the constructor expression with the accessor via one `sed`.
- Declare that binding fixture `@pytest.fixture(autouse=True, scope="module")`: without a scope it rebinds the same object before every test, which reviewers read as a hand-rolled module fixture. A module-scoped autouse fixture still puts the session fixture in every test's `fixturenames`, so conftest auto-markers keep applying — confirm with `pytest <file> -m <marker> --collect-only -q` (all collected) and `-m "not <marker>"` (none).
- Type the binding fixture (`def _bind(x: T) -> None`) and narrow the accessor (`assert _x is not None` before `return`): a module global typed `T | None` returned as `T` is a new pyright/basedpyright error that the repo's lint bot reports on the PR, and repos that require type hints get a review finding for the bare fixture.
- When the lint bot reports N type errors on a file that already had many, measure the delta instead of reading the total: `git show <base>:<path> > <dir>/_before_tmp.py`, run the same checker (`uvx basedpyright --pythonpath <venv>/bin/python <before> <after>`) and count errors per file; fix only what the change added, then delete the temp copy.
- Requesting the shared fixture also inherits any auto-marker the conftest attaches by fixture name — check what CI does with that marker (it may now rerun these tests every run).

## testmon / change-selection interaction

- testmon selects tests by the lines they executed. With a shared session object, only the first test executes the loaded files' top-level code, so structure-only tests stop being reselected when those files change. Keep them covered by an unconditional step (e.g. a marker-driven `pytest -m <marker>` step) rather than dropping the sharing.

## Measure

- Before/after with `pytest <file> --durations=30 --durations-min=0.5 -p no:cacheprovider`, run in the background, never concurrently with the other measurement. Report the file alone *and* note that in a full suite the session parse is already paid by other files.
- Adding `--durations=30` to the CI main pytest leaves evidence for the next bottleneck hunt at zero cost.
