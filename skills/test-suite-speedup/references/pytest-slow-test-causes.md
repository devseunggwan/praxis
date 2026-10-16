# Classifying a slow pytest file

Use after ranking files (SKILL.md Step 3). Look at the phase (`setup` / `call` / `teardown` in `--durations`) and the shape before proposing a fix.

| Signature | Cause | Action |
| --- | --- | --- |
| Every test in the file pays ~the same seconds in `call`; a helper builds a heavy object (`DagBag(...)`, app, schema) | Repeated construction per test or per helper call | Reuse the repo's session fixture → `pytest-shared-setup.md`. Grep the whole test tree for the same constructor: the pattern usually recurs in sibling files |
| One test per runner shows a huge `setup` (tens of seconds) and the rest of the file is fast | One-time session fixture (DB container start, ORM `create_all`/`drop_all`) paid by whichever test first requests it | Not that file's fault: confirm it is the first requester in each leg's `-v` log (alphabetical order usually decides), and say that deleting or skipping it only moves the cost to the next requester. Count how many *separate* session schemas/containers each runner builds; duplicates are the saving, and whether they can share a DB is a hypothesis to test, not a fact |
| Narrowing a test fixture's `create_all(tables=...)` does not reduce setup | The code under test itself calls `Base.metadata.create_all(...)` at runtime, so the first call recreates the full schema on the fresh test DB | The real fix is in production code (narrow its `create_all` to the tables it uses, which also cuts per-call existence queries in prod). Offer it next to the test-only alternative (reuse an existing session DB for one of the connections); once chosen, add it as its own commit on the open PR of the same work stream and name the prod-behavior change in the PR body (split it out only if the user asks). Recipe below |
| Few tests, long `call`, cProfile top line is `select.kqueue.control` / `select.epoll.poll` / `time.sleep` | Real sleeping (retry backoff, rate limiter, throttle) the test meant to disable | Find which sleep escaped the patch (below) |
| Lock-wait / timeout tests that hold DB locks on purpose | Intended server-side timeout | Leave it; say so in the report |
| Each test pays ~1s `setup` for a function-scoped DB fixture | New database per test | Candidate for a session schema + per-test truncate, only if the repo already has that pattern |

## Finding an escaped sleep

- `python -m cProfile -s tottime -m pytest -p no:cacheprovider -q <file>::<test> | grep -A14 "Ordered by"`: event-loop or sleep wait time at the top with ~N calls of ~retry-wait seconds confirms real waiting.
- `patch("asyncio.sleep", ...)` / `patch("time.sleep", ...)` only reaches code that looks the name up on the module at call time. Libraries that bind it at import (`from asyncio import sleep`) or as a default argument keep the real function — tenacity's `AsyncRetrying(sleep=sleep)` is the common case, so its backoff waits still sleep. Fix in the test by patching the class where the code under test looks it up, pre-bound to a no-op sleep — `patch("<module_under_test>.AsyncRetrying", functools.partial(AsyncRetrying, sleep=no_wait))` next to the existing `asyncio.sleep` patch (which still covers plain request-interval sleeps). Keep the retry-count and exception assertions untouched so the test still proves retries happen, then re-measure: randomized backoff (`wait_random_exponential`) makes the old timing vary run to run, so compare against several before-runs or the CI value.

## Narrowing a runtime `create_all` safely

- Table set = the models the service queries, their FK targets, **and every table a DDL trigger writes to**: grep the model module for `event.listen(<table>, "after_create", DDL(...))` — MySQL creates a trigger that references a missing table without error and only fails on the first write (`1146 ... doesn't exist`). Build the list from `Base.metadata.tables[model.__tablename__]` in one helper the code and the test fixtures both use (fixture `create_all` and the per-test truncate loop must iterate the same list, or truncate hits missing tables).
- Prove the list with a mutation: drop one table from it, rerun the hook tests, expect the 1146 failure, restore.
- Caller list for the PR body comes from one grep over every public entry point of the class (`git grep -lE "<Class>|<method1>|<method2>" <src>`), not from memory or the first page of hits; the list is usually 2–3× what you expect. Run the callers' test files too — one of them may be a slow test whose cost was this same `create_all` (it gets faster, confirm by timing it against the base ref).

## Splitting a big setup into its parts

`python -m cProfile -s cumtime -m pytest -p no:cacheprovider -q <file>::<first test> 2>&1 | grep -E "create_all|get_container|wait_for_container|<fixture names>|ncalls"` gives seconds per fixture and per phase (container wait vs each `create_all`). Run it with the DB container stopped or not yet attached if you need the container share; on CI the first requester in the leg already paid it.

## Report shape

A table per file: CI seconds and test count, cause, verdict (fixable now / one-time cost / intended / needs a look). State the ranking method (log-timestamp estimate, which run) and its positive control, and keep unverified causes labelled as hypotheses.
