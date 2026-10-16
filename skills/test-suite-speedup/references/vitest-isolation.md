# Vitest suites: where the time goes and how to cut per-file isolation

## Reading the cost

- Only the summary's `Duration X` is wall time. Through vitest 4 the parenthesised sub-items (`transform`, `setup`, `import`, `tests`, `environment`) are times summed **across workers**; vitest 5 prints them as percentage shares instead (`transform 51%, import 29%, worker 10%, tests 9%, environment 1%`), so read the shares there and take absolute phase sums from a reporter (below). When `import` dominates and `tests` is a small share, the suite pays per-file bootstrap (module graph re-evaluation + DOM environment), not assertions — speeding up test bodies will not help.
- Sum of sub-items ≈ wall × workers (vitest ≤4 time form, or summed reporter phases) means workers are saturated. Read the runner's real vCPU (job log `Instance type:` / runner label → instance spec) and compare to `maxWorkers`: more workers than vCPUs inflates `import` with CPU wait.
- Per-file lines (`✓ <project> <file> (N tests) Xs`) give test time only, not import. To split cost by project (e.g. `shared` vs `isolated`), run each project alone: `vitest run --project <name> --reporter=dot`, same `maxWorkers` as CI, and compare `Duration` and `import`.
- `--changed <ref>` selection: count `Test Files` per PR run across a sample and correlate with the PR's touched paths. A hub module everything imports (an i18n locale index, a barrel) reselects a large share of the suite on every edit to it — check how often main commits touch that path (`git log --since=... --format=%H -- <path> | wc -l`).

## Finding which modules make `import` expensive

- Do this before choosing between "move more files to the shared pool" and "cut a heavy common import": the first saves (per-file bootstrap) × (files moved), the second saves (module cost) × (files that load it). Answer with numbers, not the static guess.
- Per-module timing (vitest ≥4.1) needs a throwaway reporter, kept in scratch rather than the repo. In its `onTestModuleEnd(mod)` hook, read `mod.diagnostic()` and append one JSONL row per test file: `mod.moduleId`, `mod.project.name`, the phase durations (`environmentSetupDuration`, `prepareDuration`, `collectDuration`, `setupDuration`, `duration`), and `importDurations` as `{moduleId: [selfTime, totalTime, external]}`. Run the full suite in the background with `--experimental.importDurations.limit=100000` (the default limit collects nothing) and `--reporter=dot --reporter=<abs path to the reporter>`. Smoke it on one small directory first. Use a reporter rather than `--experimental.importDurations.print`: the printed breakdown is a top-N list and gives no per-file rows to aggregate.
- Aggregate the JSONL in a short scratch script: per project, the sum and average of each phase; the distribution of `collect` per file (p50/p90/p99/max and the top-10% share — is the cost flat or concentrated?); and summed import `selfTime` grouped by package (`node_modules/(.pnpm/<x>/node_modules/)?<pkg>`) or by source directory, and by single module with its load count.
- In the shared pool a file's `importDurations` also lists modules earlier files of the same worker loaded, so count identical `(module, selfTime, totalTime)` rows once — without that dedupe the sums roughly double. A test file whose *own* entry has a multi-second `selfTime` is doing heavy top-level work (e.g. an eager glob), not importing slowly.
- Convert summed saving to wall time with wall ≈ summed phases ÷ `maxWorkers` (check the identity on the run first). Rank levers in a table of estimated wall saving × cost, label every number as an estimate, and recommend the cheapest lever first as a check that the estimate holds.
- Static fan-in as a cheap first look (scratch script walking relative + `src/`-alias imports from each spec): exclude `import type`/`export type` lines, which are erased at runtime — counting them inflated the median spec closure ~17× (whole app vs ~90 files) and made type barrels look like the hub. A type barrel that still shows up at runtime holds runtime values (enums, constants) — a split candidate.
- When the user asks for "the total reduction", give the interleaved base-vs-branch means with each side's min–max range and say plainly when the difference sits inside the range; then explain the gain as per-file saving × files moved so they see why a small batch did not move the total.

## Source-text guard specs (scan all of `src`)

- An eager `import.meta.glob("<src>/**/*.{ts,tsx}", { query: "?raw", eager: true })` sends every source file through the Vite transform path: each such guard costs 6–13s of import. Replace it with a shared test helper that reads the same files from disk (`readdirSync(src, { recursive: true })`, `.ts/.tsx`, skip dot segments and `node_modules`, `src`-relative keys) and memoizes per worker.
- Match glob semantics exactly: glob omits the calling file. Take the caller's `__filename` and drop it, or a guard's own comments/regex text satisfy its "pattern exists somewhere" non-vacuity check.
- Prove equivalence with a throwaway spec comparing helper vs glob key sets and contents. Glob keys for files under the globbing spec's own directory come back as `./x`, not `../dir/x` — normalize before diffing, or the diff shows a false mismatch.
- Positive control: a throwaway production file with one planted offender per guard must turn every guard red naming that line; delete it and the probe spec, then time the guards alone base vs branch (3 reps each).

## Why isolation can't just be turned off

- With `isolate: false` the module registry is shared inside a worker, and a `vi.mock` in one file can be observed by **other** files that share a transitively imported module — including files that declare no mock. Moving mocking files into a non-isolated pool contaminates the files already there, and fewer workers (the CI case) makes it worse.
- The working route: remove the mock from a file so the repo's classifier moves it to the shared pool by itself. Before picking a target, compute which files are isolated for exactly one reason (one mock target, no other trigger) — removing a mock from a file that has other mocks gains nothing. Replicate the repo's classifier (often a regex over the spec source) in a throwaway script and rank targets by "files that would move".
- A replacement helper must not reintroduce a trigger: if the classifier isolates any spec that imports `react-i18next` or a helper that calls `initReactI18next`, provide a helper under a non-matching path whose instance is passed only through `I18nextProvider` (`createInstance()` without `.use(initReactI18next)`), and pin "global instance unchanged" (`getI18n()` before === after) in its own test; mutate the helper to call `initReactI18next` once to prove the test fails.

## Widening the shared pool in bulk (higher yield than per-file migration)

- Check what the classifier keys on. Import-name rules ("any spec importing `i18next`/`react-i18next`/a test helper") isolate files that only *read* (`useTranslation`, `I18nextProvider`, `createInstance`). Narrow the rule to the APIs that *mutate* global state (app singleton import, default-instance import of the library, `initReactI18next`/`setI18n`, helpers wrapping them, local setup files that `.init()` the global) — keep every incident the old comments name covered.
- Fix shared helpers first: if a widely used render helper calls `initReactI18next`, make it build a provider-only instance with the same options; every spec using it leaves the isolated pool at once. Run all its consumers before and after (counts must match).
- A source-text classifier (`source.includes("vi.mock(")`) also matches string literals: the classifier's own table test, or any spec carrying `vi.mock(` inside test data, gets isolated. Build such literals by concatenation or accept it knowingly.
- Move the classifier out of the config into its own module with a table-driven unit test (one `isolated` case per trigger naming the fault, one `shared` case per safe pattern), and add that module to `forceRerunTriggers` — `--changed` does not trace config imports, so a classifier edit would otherwise run no tests.
- Re-count pools with `vitest list --project <p> --filesOnly | wc -l` after each step, then run the shuffle probe; widening the pool raises the leak-audit load below, so add a setup-level guard (compare key globals at file end, fail the leaking file) before moving hundreds of files.

## i18next test-instance recipe (replacing a `t` mock without changing assertions)

- Key echo: `parseMissingKeyHandler: (key) => key`, `keySeparator: false`, `nsSeparator: false`.
- To carry a mock's custom `t` (defaultValue fallback, `${key}:${count}` suffixes) over unchanged, register a `postProcessor` and set `postProcess` to it — and **drop** `parseMissingKeyHandler` in that mode: it runs after post-processing and overwrites the result.
- Fixed dictionaries for components that call `useTranslation("<ns>")`: put messages in `translation` and set `fallbackNS: "translation"`, otherwise namespaced lookups miss them.
- Components that read `i18n.language` need the app's language code (`lng`), not the library default.

## Verifying a move into the shared pool

- Run the target files on the CI runtime major first (`npx -y node@<major> node_modules/vitest/vitest.mjs run ...`); a newer local Node can fail tests on its own (e.g. a built-in `localStorage` shadowing the DOM environment's).
- Contamination probe: base vs branch in two throwaway worktrees, `--project shared --maxWorkers=<CI vCPU> --sequence.shuffle --sequence.seed=<S>` for several seeds, in the background; compare failing file sets, not just counts.
- **Audit every moved spec for global state before pushing.** In the shared pool a worker keeps one `window`, so direct prototype assignment (`Element.prototype.getBoundingClientRect = vi.fn(...)`), un-restored `vi.spyOn` from `beforeAll`, `window.confirm = ...`, `Object.defineProperty(navigator, "clipboard", ...)`, and `localStorage` writes all reach the next file. Grep the moved files for `prototype\.|window\.\w+ *=|defineProperty|spyOn|localStorage|useFakeTimers` and fix each: `vi.spyOn(...)` + `afterAll(() => spy.mockRestore())`; save the original value/descriptor and put it back in `onTestFinished`; `afterEach(localStorage.clear)`. The shuffle probe does not catch these — nothing on main happens to read the leaked value yet, and reviewers (Codex) will.
- `beforeEach(() => vi.restoreAllMocks())` does not protect the file's **last** test: a spy created inside a test body (e.g. `vi.spyOn(Storage.prototype, "getItem").mockImplementation(throw)`) survives into the next file when shuffle puts that test last. Restore it in the test itself with `onTestFinished(() => spy.mockRestore())`, or add an `afterEach(vi.restoreAllMocks)`. Write-side storage cleanup goes in `afterEach`, not only `beforeEach`.
- Leak probe per moved file: write a throwaway checker spec asserting the pristine state (`getBoundingClientRect().width === 0`, `!vi.isMockFunction(window.confirm)`, `!vi.isMockFunction(navigator.clipboard?.writeText)`, storage key null), run `<file> + checker` with `--project shared --maxWorkers=1 --no-file-parallelism --reporter=verbose`, and read the printed file order — vitest may schedule the checker first; vary `--sequence.shuffle --sequence.seed=N` until the target runs first. Require FAIL on the unfixed tree (a throwaway `git worktree add --detach <scratch>/unfixed <base>` with the checker copied in — not `git stash`, which moves your uncommitted fix aside and pops whatever sits at `stash@{0}`) and PASS after the fix; delete the checker and the worktree.
- **Setup-level leak guard** (build it before bulk moves; it also finds leaks in files that were already shared):
  - Snapshot key globals in `setup.ts` `beforeAll` and compare in `afterAll` with `Object.is`; throw naming the changed globals so the *leaking* file fails, not its victim. Probes: window fns (`confirm/alert/open/matchMedia/scrollTo`), prototype `getBoundingClientRect`/`scrollIntoView`/`offsetHeight|Width` getters, `ResizeObserver`/`IntersectionObserver`/`fetch`, `vi.isFakeTimers()`, sorted Web Storage keys. Keep the probes in their own module with a unit test per probe (unchanged → empty list, each mutation → its name).
  - Enable it only in the shared project: per-project `env: { VITEST_SHARED_POOL: isolate ? "0" : "1" }` and read `import.meta.env` in setup (avoids needing node types). After hooks run in stack order, so the guard sees the spec's own `afterAll` restores.
  - Clear `localStorage`/`sessionStorage` at guard `beforeAll`: otherwise a clean file is blamed for the previous file's leftovers.
  - Do not probe `navigator.clipboard`; restore it in the guard instead — `@testing-library/user-event` stubs it on every `setup()` but registers its detaching `afterAll` once per module evaluation, so in a shared pool every later file leaks it through no fault of its own.
  - Prove the guard: a throwaway spec that leaves `window.confirm = vi.fn()` must fail with the guard message; then run the whole shared project and fix every file it names.
  - The guard only sees what it probes and only at file end: after it is green, still run the shared project under several shuffle seeds (procedure below) — spies on unprobed prototypes (`Storage.prototype`) and within-file order leaks only show there. When one does, either fix the spec or add the prototype to the probe list.
  - Read the per-run failing file set from the dot reporter with `sed -E 's/\x1b\[[0-9;]*m//g' log | grep -E '^ FAIL'` and the guard reason by grepping for the guard's own message prefix; compare sets across seeds, not counts.
- Mechanical rewrites of `render(` must also catch `...render(` (spread) — a lookbehind that excludes `.` misses it and leaves an undefined `render`.
- Do not leave a per-file `const render = (ui) => renderWithX(ui, opts)` wrapper after a mass migration: front-end guidelines commonly reject one-line pass-through wrappers. Pass the options at each call site, and turn any `t` lambda repeated across files (e.g. the defaultValue fallback) into one exported preset from the helper.
- Repo tsconfigs often exclude `*.test.tsx`, so `pnpm typecheck` never sees migrated specs. Type-check them ad hoc: a temp tsconfig extending the app's, `files` = the touched specs + `node_modules/vitest/globals.d.ts` + `node_modules/@testing-library/jest-dom/vitest.d.ts` + the app's global d.ts/setup; run it on base and branch and require the error sets to match (pre-existing errors are not yours).

## Shuffle contamination check

Run after each migration step that moves files into the shared pool. Run it in
the background and read the summary when it finishes; never edit the commands'
script while it is live.

1. For each of several seeds (e.g. `11 22 33`):
   `CI=1 node <vitest.mjs> run --project shared --maxWorkers=<CI vCPU> --sequence.shuffle --sequence.seed=<S> --reporter=dot > shared_<S>.log 2>&1`,
   using the Node binary on the CI's runtime major.
2. Run the full suite once the same way without shuffle, to `full.log`.
3. Per log, record the exit code, the `Test Files` / `Tests` / `Duration`
   summary lines, and the failing file set: strip ANSI with
   `sed -E 's/\x1b\[[0-9;]*m//g'`, keep `^ FAIL` lines, cut each to its file
   path, `sort -u`.
4. Compare failing file **sets** across seeds and against base, not counts.
