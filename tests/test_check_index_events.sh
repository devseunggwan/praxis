#!/bin/bash
# test_check_index_events.sh — verify check-plugin-manifests.py Rule 29 (#1376):
# docs/hook/INDEX.md's Trigger cell must name exactly the events
# hooks/manifest.json registers for that hook.
#
# Rule 7 only asserts the hook NAME appears somewhere in INDEX.md, so a row
# could go on naming an event whose registration was removed — which is what
# #1365 did. This suite mutates a real row in both directions and restores it.
#
# This suite edits docs/hook/INDEX.md in place and restores it on EXIT; the
# lock sourced below keeps a concurrent run from snapshotting a mutated file
# as its baseline (#1377).
#
# Usage: bash tests/test_check_index_events.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_tree_mutation_lock.sh"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
INDEX="$ROOT_DIR/docs/hook/INDEX.md"

PASS=0
FAIL=0
run_case() {
  local name="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$want got=$got"
    FAIL=$((FAIL + 1))
  fi
}

if [ ! -f "$INDEX" ]; then
  echo "FAIL  [index_exists] expected=yes got=no"
  exit 1
fi

# The row this suite mutates. Fail loudly if it ever stops matching rather
# than testing nothing.
TARGET_HOOK="second-failure-advisory"
if ! grep -q "^| \[$TARGET_HOOK\]" "$INDEX"; then
  echo "FAIL  [target_row_present] expected=yes got=no ($TARGET_HOOK row not found)"
  exit 1
fi

# Trigger-cell fixtures are built from the manifest, never written in. Hard
# coding this hook's events made five cases assert the fixture rather than the
# rule, so dropping one of its registrations failed them all while the rule
# itself was intact.
read_events() {
  python3 - "$ROOT_DIR/hooks/manifest.json" "$1" <<'PY'
import json, sys
path, hook = sys.argv[1], sys.argv[2]
print(" + ".join(sorted({h["event"] for h in json.load(open(path))["hooks"]
                         if h["name"] == hook})))
PY
}

TARGET_EVENTS="$(read_events "$TARGET_HOOK")"
if [ -z "$TARGET_EVENTS" ]; then
  echo "FAIL  [target_events_read] expected=nonempty got=empty ($TARGET_HOOK unregistered)"
  exit 1
fi
TARGET_FIRST="${TARGET_EVENTS%% + *}"
TARGET_LAST="${TARGET_EVENTS##* + }"
TARGET_AFTER_FIRST="${TARGET_EVENTS#"$TARGET_FIRST"}"

# Case 4 needs a row whose hook is registered on PostToolUse — the SHORTER of
# the two prefix-sharing names. It cannot be the target row: no hook carries
# both events any more, so the shorter one has to come from its own row.
PREFIX_HOOK="$(python3 - "$ROOT_DIR/hooks/manifest.json" <<'PY'
import json, sys
per = {}
for h in json.load(open(sys.argv[1]))["hooks"]:
    per.setdefault(h["name"], set()).add(h["event"])
for name in sorted(per):
    if per[name] == {"PostToolUse"}:
        print(name)
        break
PY
)"
if [ -z "$PREFIX_HOOK" ] || ! grep -q "^| \[$PREFIX_HOOK\]" "$INDEX"; then
  echo "FAIL  [prefix_hook_present] expected=yes got=no (no PostToolUse-only row)"
  exit 1
fi

BACKUP="$(mktemp)" || exit 1
# `set +e` is on, so an unchecked copy here fails silently and the EXIT trap
# then restores an EMPTY backup over a tracked file. Both directions are
# checked, and the backup is removed only once the restore succeeded.
if ! cp "$INDEX" "$BACKUP"; then
  echo "FAIL  [backup_created] expected=yes got=no (could not copy $INDEX)"
  rm -f "$BACKUP"
  exit 1
fi
restore_index() {
  if ! cp "$BACKUP" "$INDEX"; then
    echo "FAIL  [index_restored] expected=yes got=no — $INDEX is left mutated;"
    echo "      the backup is kept at $BACKUP. Restore it by hand, or"
    echo "      \`git checkout -- $INDEX\`."
    return 1
  fi
  return 0
}
trap 'restore_index && rm -f "$BACKUP"' EXIT

mutate_trigger_of() {
  # Rewrite the Trigger cell (2nd column) of $1's row.
  python3 - "$INDEX" "$1" "$2" <<'PY'
import re, sys
path, hook, trigger = sys.argv[1], sys.argv[2], sys.argv[3]
out = []
pat = re.compile(r"^(\| \[" + re.escape(hook) + r"\]\([^)]*\)\s*\|)[^|]*(\|.*)$")
for line in open(path).read().splitlines(keepends=True):
    m = pat.match(line.rstrip("\n"))
    out.append(f"{m.group(1)} {trigger} {m.group(2)}\n" if m else line)
open(path, "w").write("".join(out))
PY
}

mutate_trigger() { mutate_trigger_of "$TARGET_HOOK" "$1"; }

# 1. Baseline: the committed tree passes. Without this the mutations below
#    could be reporting a pre-existing failure.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. A row that drops a registered event fails, and names what is missing.
#    The parenthetical is stripped before grading, so this cell declares
#    nothing and every registered event is missing from it.
mutate_trigger "(claude only, issue #1337)"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "missing_event_nonzero" "$?" "1"
case "$OUT" in
  *"INDEX EVENTS"*"$TARGET_HOOK"*"missing $TARGET_FIRST"*) run_case "missing_event_named" "yes" "yes" ;;
  *) run_case "missing_event_named" "no ($OUT)" "yes" ;;
esac

# 3. A row naming an event the manifest does not register fails too — the
#    reverse direction, which a one-way "is every event mentioned" check
#    would miss.
restore_index || exit 1
mutate_trigger "$TARGET_EVENTS + SessionStart (claude only)"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "extra_event_nonzero" "$?" "1"
case "$OUT" in
  *"INDEX EVENTS"*"names SessionStart which is not registered"*) run_case "extra_event_named" "yes" "yes" ;;
  *) run_case "extra_event_named" "no ($OUT)" "yes" ;;
esac

# 4. PostToolUse is a prefix of PostToolUseFailure. A cell naming only the
#    longer event must NOT also read as the shorter one — otherwise the rule
#    would silently accept a row that dropped PostToolUse. Graded on a
#    PostToolUse-registered row, so the shorter name is the one required.
restore_index || exit 1
mutate_trigger_of "$PREFIX_HOOK" "PostToolUseFailure (claude only, issue #1337)"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "prefix_not_double_counted_nonzero" "$?" "1"
case "$OUT" in
  # The boundary character is what makes this a test: a bare `missing
  # PostToolUse` also prefix-matches `missing PostToolUseFailure`, which is
  # the very message this case exists to rule out.
  *"missing PostToolUse;"* | *"missing PostToolUse "*) run_case "prefix_not_double_counted_named" "yes" "yes" ;;
  *) run_case "prefix_not_double_counted_named" "no ($OUT)" "yes" ;;
esac

# 5. Matchers and prose around the event names are not graded: the same event
#    set written with a matcher and extra notes still passes.
restore_index || exit 1
mutate_trigger "${TARGET_FIRST}(Bash)${TARGET_AFTER_FIRST} — see issue #1337 and the operating matrix"
python3 "$CHECK" >/dev/null 2>&1
run_case "matchers_and_prose_ignored" "$?" "0"

# 6. Prose that names an event does not declare it. A cell may explain a
#    registration change in words, and those words are not a registration —
#    scanning the whole cell read them as one and failed the row as naming an
#    unregistered event.
restore_index || exit 1
mutate_trigger "$TARGET_EVENTS — SessionStart was never registered for this hook"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "prose_mention_is_not_a_declaration" "$?" "0"
case "$OUT" in
  *"names SessionStart"*) run_case "prose_mention_no_stray_report" "no ($OUT)" "yes" ;;
  *) run_case "prose_mention_no_stray_report" "yes" "yes" ;;
esac

# 7. Two rows naming the same hook are BOTH graded. Keying the rows by hook
#    name dropped all but the last, and Rule 7 only asks whether the name
#    appears somewhere in the file — so a stale duplicate left by an edit
#    could keep declaring a registration that no longer exists.
restore_index || exit 1
python3 - "$INDEX" "$TARGET_HOOK" <<'DUP'
import re, sys
path, hook = sys.argv[1], sys.argv[2]
pat = re.compile(r"^\| \[" + re.escape(hook) + r"\]\([^)]*\)\s*\|[^|]*(\|.*)$")
out = []
for line in open(path).read().splitlines(keepends=True):
    m = pat.match(line.rstrip("\n"))
    if m:
        # A stale duplicate ABOVE the correct row: the old keying kept the last.
        head = line.split("|")[1]
        out.append(f"|{head}| SessionStart {m.group(1)}\n")
    out.append(line)
open(path, "w").write("".join(out))
DUP
OUT="$(python3 "$CHECK" 2>&1)"
run_case "duplicate_row_nonzero" "$?" "1"
case "$OUT" in
  *"INDEX EVENTS"*"names SessionStart which is not registered"*) run_case "duplicate_row_named" "yes" "yes" ;;
  *) run_case "duplicate_row_named" "no ($OUT)" "yes" ;;
esac

# 8. A name the event is only a PREFIX of does not declare it. Matching on
#    "the segment opens with this event" alone accepted `PostToolUseFailureNote`
#    as `PostToolUseFailure`, so a typo would have declared the event it is a
#    typo of — the drift this rule exists to catch, waved through.
restore_index || exit 1
mutate_trigger "${TARGET_EVENTS}Note"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "near_match_is_not_the_event_nonzero" "$?" "1"
case "$OUT" in
  *"INDEX EVENTS"*"missing $TARGET_LAST"*) run_case "near_match_is_not_the_event_named" "yes" "yes" ;;
  *) run_case "near_match_is_not_the_event_named" "no ($OUT)" "yes" ;;
esac

# 9. A registered hook whose row does not parse as a hook row is reported.
#    Rule 7 is satisfied by the bare name and this rule never saw the row, so
#    repointing the link left the Trigger cell unread by both. `impl.py` rather
#    than a made-up filename: an existing sibling also survives the offline
#    link check, which is what makes the gap reachable in practice.
restore_index || exit 1
python3 - "$INDEX" "$TARGET_HOOK" <<'REPOINT'
import re, sys
path, hook = sys.argv[1], sys.argv[2]
pat = re.compile(r"^(\| \[" + re.escape(hook) + r"\]\()([^)]*)(\).*)$", re.S)
out = []
for line in open(path).read().splitlines(keepends=True):
    m = pat.match(line.rstrip("\n"))
    if m:
        line = m.group(1) + m.group(2).replace("/spec.md", "/impl.py") + m.group(3) + "\n"
    out.append(line)
open(path, "w").write("".join(out))
REPOINT
OUT="$(python3 "$CHECK" 2>&1)"
run_case "unparsed_row_nonzero" "$?" "1"
case "$OUT" in
  *"INDEX ROW"*"the row does not parse as a hook row"*) run_case "unparsed_row_named" "yes" "yes" ;;
  *) run_case "unparsed_row_named" "no ($OUT)" "yes" ;;
esac

# 10. The boundary against Rule 7: a hook whose row is gone ENTIRELY is one
#     defect, so only Rule 7 names it. Without this the new check would report
#     every absent hook a second time under a different heading.
restore_index || exit 1
python3 - "$INDEX" "$TARGET_HOOK" <<'DROP'
import sys
path, hook = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines(keepends=True)
open(path, "w").write("".join(l for l in lines if f"[{hook}](" not in l))
DROP
OUT="$(python3 "$CHECK" 2>&1)"
run_case "dropped_row_nonzero" "$?" "1"
run_case "dropped_row_rule7_only" \
  "$(printf '%s\n' "$OUT" | grep -c 'MISSING INDEX')/$(printf '%s\n' "$OUT" | grep -c 'INDEX ROW')" \
  "1/0"

# 11. A malformed DUPLICATE is reported even though a good row for the same
#     hook parses. Judging by hook name instead of by row lets the good row
#     vouch for the stale one, which is the drift case 7 exists for.
restore_index || exit 1
python3 - "$INDEX" "$TARGET_HOOK" <<'DUP'
import sys
path, hook = sys.argv[1], sys.argv[2]
out = []
for line in open(path).read().splitlines(keepends=True):
    out.append(line)
    if f"[{hook}](" in line:
        out.append(line.replace("/spec.md", "/impl.py", 1))
open(path, "w").write("".join(out))
DUP
OUT="$(python3 "$CHECK" 2>&1)"
run_case "dup_unparsed_row_nonzero" "$?" "1"
run_case "dup_unparsed_row_named" \
  "$(printf '%s\n' "$OUT" | grep -c 'INDEX ROW')" "1"

# 12. A row whose LABEL still names the hook while its link and Trigger point at
#     another registered hook is reported. Rule 7 reads the label and passes;
#     this row parses and grades against the hook it now names; the original
#     registration is graded by nothing. `block-gh-state-all` is a real sibling,
#     so the repointed link also survives the offline link check.
restore_index || exit 1
python3 - "$INDEX" "$TARGET_HOOK" <<'RETARGET'
import re, sys
path, hook = sys.argv[1], sys.argv[2]
THIEF, THIEF_ROLE, THIEF_TRIGGER = "block-gh-state-all", "preflight-gate", " PreToolUse "
out = []
for line in open(path).read().splitlines(keepends=True):
    if f"[{hook}](" in line:
        cells = line.split("|")
        cells[1] = re.sub(r"\]\([^)]*\)",
                          f"](../../hooks/{THIEF_ROLE}/{THIEF}/spec.md)", cells[1])
        cells[2] = THIEF_TRIGGER
        line = "|".join(cells)
    out.append(line)
open(path, "w").write("".join(out))
RETARGET
OUT="$(python3 "$CHECK" 2>&1)"
run_case "retargeted_row_nonzero" "$?" "1"
case "$OUT" in
  *"INDEX ROW"*"no row LINKS to it"*) run_case "retargeted_row_named" "yes" "yes" ;;
  *) run_case "retargeted_row_named" "no ($OUT)" "yes" ;;
esac

# 13. A cell that names every registered event and then adds a misspelt segment
#     used to compare equal to the manifest and pass. The segment declares
#     nothing, so without reporting it the typo is invisible to both directions.
restore_index || exit 1
python3 - "$INDEX" "$TARGET_HOOK" <<'TYPO'
import sys
path, hook = sys.argv[1], sys.argv[2]
out = []
for line in open(path).read().splitlines(keepends=True):
    if f"[{hook}](" in line:
        cells = line.split("|")
        cells[2] = cells[2].rstrip() + " + PostToolUseFaliure "
        line = "|".join(cells)
    out.append(line)
open(path, "w").write("".join(out))
TYPO
OUT="$(python3 "$CHECK" 2>&1)"
run_case "typo_segment_nonzero" "$?" "1"
case "$OUT" in
  *"declaring no registered event"*"PostToolUseFaliure"*) run_case "typo_segment_named" "yes" "yes" ;;
  *) run_case "typo_segment_named" "no ($OUT)" "yes" ;;
esac

# 14. Restored tree passes.
restore_index || exit 1
python3 "$CHECK" >/dev/null 2>&1
run_case "restored_check_clean" "$?" "0"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
