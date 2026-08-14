# Feature Specification: Tracked feature-spec convention

**Issue**: #1001

**Feature Branch**: `issue-1001-docs-spec-convention`

**Created**: 2026-08-14

**Input**: User description: "spec-kit 이랑 현재 하네스랑 접목을 하고 싶은데 검토 부탁합니다"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read a hook's intent from the checkout alone (Priority: P1)

Someone with only a clone of praxis — no GitHub access, no session history —
wants to know what a given hook or skill was built to satisfy, and which of
those requirements are load-bearing versus incidental.

**Why this priority**: this is the whole gap. Today the answer lives in a
remote issue body or in `.omc/plans/`, which `.gitignore` excludes. Every
other story here is downstream of design intent being present in the tree.

**Independent Test**: clone the repo with no network access, open
`.praxis/specs/`, and answer "what must this feature do?" for any feature that
has a spec. Delivers value even if nothing else in this issue ships.

**Acceptance Scenarios**:

1. **Given** a fresh clone and no network, **When** a reader opens
   `.praxis/specs/NNN-slug.md`, **Then** they can read that feature's
   requirements and its back-reference to the originating issue.
2. **Given** a spec file on a branch, **When** the branch is deleted after
   merge, **Then** the spec survives on the base branch with its `**Issue**`
   line intact.

---

### User Story 2 - Review the design and the code in one pass (Priority: P2)

A reviewer on a PR wants to check the diff against stated requirements
without leaving the diff.

**Why this priority**: valuable, but it only pays off once specs exist, and a
reviewer can still fall back to the issue body in the meantime.

**Independent Test**: open a PR that changes both a spec and its
implementation; confirm both appear in the same `gh pr diff` output.

**Acceptance Scenarios**:

1. **Given** a PR implementing a spec'd feature, **When** the reviewer reads
   the diff, **Then** requirement changes and code changes appear together.

---

### Edge Cases

- **Two branches claim the same `NNN`.** Numbers are assigned from the tree at
  authoring time, so parallel worktrees can collide. Resolution is a rename on
  rebase — the number carries no meaning beyond ordering, and nothing links to
  it by number.
- **A spec outlives the feature it described.** A removed feature's spec is
  deleted in the same PR as the removal; a spec with no corresponding code is
  the failure this convention is meant to make visible, not a state to keep.
- **A change ships with no spec.** Expected and allowed — see the skip
  conditions in `README.md`. Absence of a spec is not evidence of a skipped
  process.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Specs MUST live at `.praxis/specs/NNN-slug.md` and MUST be
  tracked by git — verified by `git ls-files --error-unmatch <path>` exiting 0.
  `git check-ignore` is **not** the oracle here: it exits 1 for a file that is
  merely un-ignored, which every untracked file also is, so it cannot tell
  "will be tracked once added" from "is tracked".
- **FR-002**: Every spec MUST carry an `**Issue**: #N` line under its title.
- **FR-003**: The template MUST be spec-kit's `spec-template.md` verbatim,
  carrying attribution for its MIT license and the version it was taken from.
- **FR-004**: `README.md` MUST state when a spec is required and when it is
  skipped, so that a missing spec is a readable decision rather than an
  oversight.
- **FR-005**: The convention MUST NOT introduce a runtime dependency on the
  `specify` CLI or on any `.specify/` directory.
- **FR-006**: Adopting this convention MUST NOT change any existing enforcement
  tier, hook, or skill surface — verified by `./scripts/check-plugin-manifests.py`
  reporting no surface change.

### Key Entities

*Not applicable — this feature adds documentation conventions and no data.*

## Out of Scope

**Automated drift detection** — reporting which of a spec's requirements the
current tree does not yet satisfy. It is a separate feature with its own
oracle, and it gets its own spec and issue rather than a requirement here.

The distinction is what a requirement means: everything under `Requirements`
above is a contract this change must satisfy, so a `MUST` that ships unmet
would make the spec self-contradictory and set the precedent that a `MUST` is
optional. A convention that is worth reading and a report that checks it are
two features, not one feature delivered in halves.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A reader with a fresh offline clone can state the requirements of
  any spec'd feature without consulting GitHub.
- **SC-002**: `git ls-files .praxis` lists every spec file — zero specs are
  silently untracked.
- **SC-003**: The full CI entrypoint (`scripts/run-tests.sh`, invoked by
  `.github/workflows/ci.yml:109`) passes unchanged after adoption.

## Assumptions

- praxis stays a single-trunk repository on `main`; spec numbering assumes no
  long-lived parallel release lines.
- Specs are written by whoever opens the issue, in the same session — the
  convention adds a document, not a handoff.
- spec-kit's `spec-template.md` stays MIT-licensed; the attribution block
  records v0.16.3 so a future re-sync is a diff rather than a guess.
- Reviewers read the spec because it sits in the diff; no gate forces it. If
  that assumption fails, the remedy is a hook, not a longer README.
