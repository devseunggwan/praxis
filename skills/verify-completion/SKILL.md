---
name: verify-completion
description: Enforce verification evidence before any completion claim. Tests, builds, and lint must be run with output shown — no claims without proof. Triggers on "verify", "verification", "done check", "completion check".
---

# Verify Completion

## Overview

Claiming work is complete without verification is dishonesty, not efficiency.

**Core principle:** Evidence before claims, always.

**Delegates to:** OMC `ultraqa` (test → verify → fix → repeat cycle)

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this message, you cannot claim it passes.

**Violating the letter of this rule is violating the spirit of this rule.**

## When to Use

**ALWAYS before:**
- ANY variation of "done", "fixed", "complete", "passes"
- ANY expression of satisfaction ("Great!", "Perfect!")
- Committing, pushing, or creating PRs
- Moving to the next task
- Trusting agent success reports

**Workflow position:**
```
[implementation] → [code-review] → [verify-completion] → [create-pr]
```

## The Gate Function

```
BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. ONLY THEN: Make the claim

Skip any step = lying, not verifying
```

## Process

### Step 1: Identify Verification Targets

| Target | Typical Command | Required? |
|--------|----------------|-----------|
| Unit tests | `pytest -v`, `npm test`, `go test ./...` | **Always** |
| Lint | `ruff check .`, `eslint .`, `golangci-lint run` | **Always** |
| Build | `npm run build`, `cargo build`, `go build ./...` | If build system exists |
| Type check | `mypy .`, `tsc --noEmit` | If type system exists |
| Functional test | DAG trigger, API call, CLI execution | If external systems changed |

### Step 2: Run Verification Cycle

Delegate to OMC `ultraqa` when available:

```
ultraqa cycle: test → verify → fix (on failure) → repeat (until pass)
```

If `ultraqa` is unavailable, run manually:

```bash
# 1. Tests
pytest -v <test-path>

# 2. Lint
ruff check . && ruff format --check .

# 3. Build (if applicable)
npm run build

# 4. Functional test (if applicable — project-specific)
# e.g., hit real API endpoint, run real CLI, trigger pipeline run
# Examples: curl http://localhost:8000/health | jq, cargo run -- <args>, <your-cli> trigger <job>
```

### Step 3: Report Evidence

**Show actual output for each verification target:**

```
✅ Verification results:
- Tests: 34/34 pass (0 failures) — `pytest -v` output confirmed
- Lint: 0 errors, 0 warnings — `ruff check .` output confirmed
- Build: exit code 0 — `npm run build` output confirmed
```

### Step 4: Handle Failures

If verification fails:

1. **Report failure with exact output** (include error messages)
2. **Fix immediately** (do not ask user for permission)
3. **Re-run from Step 2**
4. **If 2+ failures on same issue** → use `debug` skill for root cause analysis

## Common Failures

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Linter clean | Linter output: 0 errors | Partial check, extrapolation |
| Build succeeds | Build command: exit 0 | Linter passing, "logs look good" |
| Bug fixed | Test original symptom: passes | "Code changed, assumed fixed" |
| Requirements met | Line-by-line checklist verified | "Tests pass" alone |
| API works | Response body content verified | HTTP 200 status code alone |

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence ≠ evidence |
| "Just this once" | No exceptions |
| "Linter passed so build will too" | Linter ≠ compiler |
| "Agent said success" | Verify independently |
| "I'm tired" | Exhaustion ≠ excuse |
| "Partial check is enough" | Partial proves nothing |
| "Different wording so rule doesn't apply" | Spirit over letter |
| "Simple change, no verification needed" | Simple changes break too. Run it. |
| "HTTP 200 means it works" | Check response body content, not just status code |

## Red Flags — STOP

If you catch yourself about to:

- Use "should", "probably", "seems to"
- Express satisfaction before verification ("Great!", "Done!")
- Commit / push / create PR without verification
- Trust agent success reports at face value
- Rely on partial verification
- Think "just this once"
- **Use ANY wording implying success without having run verification**

**ALL of these mean: STOP. Run the Gate Function.**

## Integration

**Previous step:** `code-review` (code quality self-review)
**Next step:** PR creation (via project's PR skill or `gh pr create`)

**OMC delegation:**
- `ultraqa`: test → verify → fix → repeat cycle
- `debugger` agent: root cause analysis on persistent failures
