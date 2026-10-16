# Parallel test runner and CI split

Used by SKILL.md Step 5. Written for a shell entrypoint that runs many test
files (shell suites, pytest groups); the rules carry to other runners.

## Probe parallel safety before changing the runner

1. Run every test file with `xargs -P 8` under one throwaway state dir, each to
   its own `<file>.out`, `<file>.err` and `<file>.rc`.
2. List the files with a non-zero rc.
3. Re-run each failure **alone** with the runner's own interpreter. A failure
   that persists alone is environment, not concurrency — for example Apple's
   `/usr/bin/python3` writes `.pyc` files under `$HOME/Library/Caches`, which
   trips "nothing written under HOME" assertions regardless of parallelism.

A parallel-probe failure is not automatically a concurrency bug.

## Partition by a marker the suites already carry

Suites that edit the working tree must run alone. Key the serial group on the
lock helper they already source (for example
`grep -q '_tree_mutation_lock\.sh' <file>`), not on a hand-kept list.

Find tree-mutators that forgot the lock: `grep -lE "trap '?\"?cp |BACKUP="`
across the test files, cross-checked against the lock grep. Adding the lock to
them fixes a real race — give that its own commit.

## Runner implementation rules

- Fan out with `xargs -P "$JOBS" -I{} bash -c '...' _ {} "$dir"`, not
  `wait -n` — macOS ships bash 3.2.
- Each worker writes stdout, stderr and rc to per-file files. After the batch,
  replay them **in file order** and fold them through the same function the
  serial path uses, so logs and skip/failure accounting are unchanged. A
  missing rc file counts as a failure.
- Default jobs = `getconf _NPROCESSORS_ONLN`, overridable by env; `1` restores
  the serial run. Validate the value is a positive integer.
- Empty arrays under `set -u` on bash 3.2: iterate `${arr[@]+"${arr[@]}"}`.
- If a test extracts runner functions with `sed -n '/^name() {/,/^}/p'`, keep
  new helpers at column 0, extend that test to extract them, add a
  parallel-path case (skip fold, failure fold, replay order), and prove it can
  fail by mutating the rc read to `rc=0` in a temp copy.
- CI split selector: one env var (`PART=all|python|shell`) with guards around
  steps. Leave long step bodies unindented under the guard to keep line
  history.

## CI matrix split

```yaml
strategy:
  fail-fast: false
  matrix:
    part: [python, shell]
name: test (${{ matrix.part }})
env:
  PART: ${{ matrix.part }}
```

Gate leg-only steps (a coverage summary, for instance) with
`if: matrix.part == 'python'`.

Before renaming the job, check required status checks — a renamed job breaks a
required `test` check:

```bash
gh api repos/<o>/<r>/branches/main/protection/required_status_checks
gh api repos/<o>/<r>/rulesets
```
