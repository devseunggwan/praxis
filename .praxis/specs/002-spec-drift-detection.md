# Feature Specification: Spec-drift detection

**Issue**: #1005

**Feature Branch**: `issue-1005-feat-spec-drift`

**Created**: 2026-08-14

**Input**: User description: "spec-kit 이랑 현재 하네스랑 접목을 하고 싶은데 검토 부탁합니다" (G2, the half `001` deferred to `## Out of Scope`)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask a spec what is still unmet (Priority: P1)

Someone looking at a feature that has a spec wants to know which of its
requirements the current tree does not yet satisfy — without reading the whole
diff history and without trusting a summary written at merge time.

**Why this priority**: `001` put the requirements in the tree; nothing yet
reads them back. A requirement that quietly stops holding looks exactly like
one that never stopped, and the spec's own prose is the last place that would
say so.

**Independent Test**: run the skill against `001` on a clean checkout and read
the report. Delivers value with one spec in the repo, before any second spec
exists.

**Acceptance Scenarios**:

1. **Given** a spec whose requirements all hold, **When** the report runs,
   **Then** every requirement carrying a `Verify:` line reads `implemented`
   and none reads `missing`.
2. **Given** a requirement whose `Verify:` command now exits non-zero,
   **When** the report runs, **Then** that requirement reads `missing` and the
   report carries the command and its output.
3. **Given** a requirement with no `Verify:` line, **When** the report runs,
   **Then** it reads `UNKNOWN` and the report says a verification command is
   what would close it.

---

### User Story 2 - Write a spec that can be checked (Priority: P2)

Someone writing a new spec wants to know whether their requirements are
stated in a way anything can check.

**Why this priority**: downstream of story 1 — the feedback only exists once
a report exists — but it is what keeps the corpus from decaying into prose
that reads well and verifies nothing.

**Independent Test**: write a spec with no `Verify:` lines, run the report,
and confirm every requirement reads `UNKNOWN` with the recommendation
attached.

**Acceptance Scenarios**:

1. **Given** a spec with no `Verify:` lines at all, **When** the report runs,
   **Then** it completes normally rather than erroring, and each requirement
   reads `UNKNOWN`.

---

### Edge Cases

- A requirement's prose quotes a command that must **not** run — `001`'s
  FR-001 cites `git check-ignore` precisely to reject it as an oracle. Prose
  backticks are never executed; only a `Verify:` line is.
- A `Verify:` command whose exit code reports the environment rather than the
  requirement. `001`'s SC-003 names `scripts/run-tests.sh`, which exits
  non-zero on a developer host for reasons outside the repository (#1003) —
  such a command must not become a `Verify:` line.
- A `Verify:` command that never terminates, or waits on input.
- A spec directory holding files that are not specs (`README.md`,
  `TEMPLATE.md`).
- A spec with no requirements section at all.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A requirement's verification command MUST be carried on a
  dedicated `Verify:` line, and the report MUST execute those lines only —
  never a command quoted in surrounding prose.
- **FR-002**: A `Verify:` command MUST be one whose exit code reports the
  requirement rather than the environment it ran in. A command that can exit
  non-zero for a reason outside the repository is not eligible.
- **FR-003**: Every requirement MUST receive exactly one of `implemented`
  (exit 0), `missing` (non-zero), or `UNKNOWN` (no `Verify:` line). There is
  no intermediate value: an exit code is binary, and a middle value would put
  the judgement back on a reader.
- **FR-004**: A `missing` result MUST carry the command and its output, and an
  `UNKNOWN` result MUST carry the recommendation that closes it.
- **FR-005**: The report MUST be read-only — no writes, no commits, no issue
  creation — matching `praxis:debt`'s invariant.
- **FR-006**: The report MUST print each command before running it, so the set
  of executed commands is auditable from the report alone.
- **FR-007**: The scan MUST cover `.praxis/specs/[0-9][0-9][0-9]-*.md` and MUST
  NOT treat `README.md` or `TEMPLATE.md` as specs.
- **FR-008**: `.praxis/specs/README.md` MUST document the `Verify:` convention
  alongside the conventions already listed there, and `TEMPLATE.md` MUST stay
  byte-identical to upstream apart from its attribution block — `001`'s FR-003
  is the binding constraint.
- **FR-009**: Adding this skill MUST keep every skill-enumerating surface in
  agreement — `EXPECTED_SKILLS`, the runtime-metadata test, and the two
  documentation tables.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Running the report against `001` accounts for all 9 of its
  requirements (6 FR + 3 SC) — the total is fixed even though the
  `implemented` / `UNKNOWN` split depends on FR-002's exclusions.
- **SC-002**: A deliberately-failing fixture spec produces exactly one
  `missing`, and the command and output appear in the report.
- **SC-003**: A reader can tell, from the report alone, which requirements were
  checked by execution and which were not checked at all.

## Assumptions

- **A spec is trusted input.** The report executes strings read from a tracked
  markdown file. A spec is reviewed and committed like code, and the report
  itself changes nothing, so the trust level is the repository's own. FR-006
  is what keeps that assumption inspectable rather than implicit. If a spec
  ever arrives from outside review — a vendored spec, a submodule — this
  assumption fails and the remedy is an allowlist, not a longer README.
- **Requirements are stated one per list item** under a requirements heading,
  which is what the template produces and what `001` follows.
- Specs stay in one flat directory; there is no nesting to walk.
- praxis stays a single-trunk repository on `main`, inherited from `001`.

## Out of Scope

**Enforcing that requirements hold.** This feature reports; it does not block
a commit, a PR, or a merge on a `missing` result. `001`'s Assumptions already
set the precedent — reviewers read what sits in the diff, and the remedy for
that assumption failing is a hook, decided then rather than pre-built now.

**Judging whether a `Verify:` command is a good oracle.** FR-002 states the
rule for a human writing one. Checking it mechanically would need a second
oracle, and that is the regress this design exists to avoid.
