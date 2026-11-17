# Step 2: Check for parallel work on the same defect

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

Before reviving a fix, search for an existing PR or umbrella issue on the same
mechanism — `gh search issues` and `gh search prs` with the mechanism's
keywords (function name, config key), plus `git worktree list` for a local
attempt. A stalled PR is often one of two parallel fixes for the same defect.

Include **merged** PRs, not only open ones (`gh pr list --state merged
--search "<file or mechanism>"`, `git log <base-remote>/<deploy-branch> -- <files
the PR touches>`): the goal often shipped through a different PR while this one
waited. Then read the deploy branch's version of the touched files and compare
it to the PR's intent line by line — a flag the PR adds may now be the
operator's default, so the PR's remaining delta can shrink to a tuning value.

Also check whether the base grew a **house-standard mechanism for the whole
class of change** while the PR waited (a shared operator, builder, or tier
table now used by several siblings: `git grep -l <ClassName> <base-remote>/<base>`).
When it did, narrowing the old diff keeps a one-off path whose original
justification may now be handled by that mechanism (re-read the PR body's
"why not X" against the new code's docstring). Offer the user the choice
before Step 3 — narrow the residual, or rewrite the PR onto the standard on
the same branch (new commits, retitled body, no force-push) — with the
trade-off in one table (path, resource cost per account, precedent count).
A rewrite follows the two closest siblings' wiring rather than inventing
constants, and the change comment names them with file:line.
Re-run the PR's reproduction against the **current** base build and live
vendor before porting anything (a control run: the base binary, the same
input the PR's body says misbehaves). The vendor or an upstream change may now
refuse that input itself (a new server-side validation returning 400), which
turns the revival into a much smaller change — often only docs or a clearer
error — and the PR into one to close with that evidence. Recount any impact
number the PR body states from the field the system actually acts on, not a
compatibility echo; the old count is frequently inflated. When the premise
shrinks, stop and re-ask scope before committing the full port.

Also check that the **code path the PR hooks into still runs** — a provider or
subsystem migration that landed meanwhile can leave the DAG, job, or route the
PR attaches to in place but gated off (an early short-circuit, a deleted
trigger), so the PR would merge cleanly and do nothing. Read the gate on the
base, then confirm with the runtime's own history (last run of that DAG/job on
prod, with a wider-window query as the positive control that the probe returns
rows at all). When the host path is dead, the PR is a close-and-rescope
decision for the user, not a revival; record what the new path already
carries (e.g. the fields the PR wanted to copy) in the issue comment.

If the goal is already live, do not revive: close the PR with a comment naming
the superseding PR and the residual difference, and close the linked issue only
after a deploy-branch run shows the goal working (cite the run and the log
lines). Ask the user before closing when the residual difference is a real
behavior choice.

Ask whether the work still has demand before reviving a large PR or a
cross-repo PR set whose rollout needs human prerequisites (secret seeding,
infra apply, deploy ordering): a one-line question with the cost (repos,
files, prerequisites) is cheap, while a full multi-worker revival the user then
closes as unneeded is wasted. Small single-file revivals can proceed without
asking.

Also ask whether the target repository is under a freeze or a large in-flight
refactor before spending a revival on it (one line in the plan, or a quick
question when several PRs in one repo are queued). A revived head merged into
a moving refactor conflicts again within days and the owners may refuse
outside merges until it lands, so the whole Step 3–8 cost is thrown away;
record such PRs as on hold with their worktree kept, not revived.

Done when: the searches and their keywords are listed, any overlapping PR
or issue (open or merged) is named with its URL (or "none found" with the scan
scope), a superseded PR is routed to close instead of Step 3, and the
repository's freeze/refactor status is known.
