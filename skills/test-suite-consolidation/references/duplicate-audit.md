# Duplicate audit for test fixtures/helpers

Used by SKILL.md Step 1. Read-only. Run it on an extracted base tree so the
result is not skewed by local edits:

```bash
mkdir -p <scratch>/base
git archive origin/<base> <tests dir> | tar -x -C <scratch>/base
```

## Grouping procedure

Write the grouping as a throwaway script in scratch (Python `ast`, stdlib
only); it is not shipped with this skill.

1. Walk `*.py` under the extracted tests root, skipping `__init__.py`. Mark a
   file **shared** when it is a `conftest.py` or any module not named
   `test_*`.
2. For each module-level `def` / `async def` (skip `test_*` functions and
   class methods), build a normalized body:
   - round-trip through `ast.unparse` → `ast.parse` so formatting differences
     vanish;
   - drop a leading docstring (replace an emptied body with `pass`);
   - set the function name to `_`, drop the return annotation and every
     argument annotation;
   - rename arguments positionally (`a0`, `a1`, …) and rewrite their uses.
   Record separately whether the def carries a `fixture` decorator — keep
   fixture-ness and the decorator arguments in the key when it matters
   (`autouse=True` and `scope="session"` behave differently).
3. Group defs by the normalized text. Keep groups whose members span at least
   two files and whose defs are at least ~4 lines long.
4. Sort groups by total redundant lines (sum of member lengths minus one copy).
   For each, print the member count, length, the names used, and any member in
   a shared file as the home the copies should use.
5. Separately, list names defined in four or more files with the number of
   distinct normalized bodies and the largest identical cluster — a name with
   dozens of variants (`_render`) is bucket (c), not a copy.

Same-file repeats are not copies.

## The three buckets

| Bucket | Meaning | Action |
| -------- | --------- | -------- |
| (a) | Identical copy of a definition a shared conftest/helper already has | Delete the copy; call the shared one |
| (b) | Identical copies between test files, no shared home | Move to the shared location from Step 2 |
| (c) | Same name, different bodies | Leave alone; say why in the PR |

Then grep the copies' docstrings for phrases like "same pattern as <file>" and
run `git log --diff-filter=A --format='%h %s' -- <copy paths>`. A chain of
such lines across many PRs is the evidence that the duplication comes from
copying the nearest file — quote it in the issue.

Normalized text is not behavior: two copies that call `Store()` may resolve it
to different classes. The audit over-groups those; the copy guard's
binding-aware key
([`copy-guard.md`](copy-guard.md#detection-what-counts-as-the-same)) does not.
