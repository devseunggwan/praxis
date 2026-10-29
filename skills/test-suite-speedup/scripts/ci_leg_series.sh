#!/usr/bin/env bash
# Per-leg test-step seconds for every completed push run of a workflow, for locating step changes.
# Usage: ci_leg_series.sh <owner/repo> <workflow.yml> <since YYYY-MM-DD> <out.tsv> [step name] [branch ...]
# Output rows: created  branch  run_id  head_sha  conclusion  job=seconds,job=seconds
# Hundreds of runs = hundreds of API calls: run it in the background, never inside a capped tool loop.
# Runs are listed one UTC day per query: a filtered runs query returns at most 1,000 runs, so a
# single since-query silently drops the oldest runs on a busy branch. A day that still hits the
# cap, or any failed API call, exits non-zero instead of leaving a partial series.
set -o pipefail
R=$1 WF=$2 SINCE=$3 OUT=$4 STEP=${5:-Run tests}
shift $(( $# < 5 ? $# : 5 ))
BRANCHES=("$@"); [ ${#BRANCHES[@]} -eq 0 ] && BRANCHES=(main)
# gh api -q has no --arg; inject the step name as a JSON string literal.
STEP_JSON=$(printf '%s' "$STEP" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
JQ='[.jobs[]|select(.name|startswith("test"))|. as $j|.steps[]|select(.name=='"$STEP_JSON"' and .conclusion=="success")|"\($j.name)=\(((.completed_at|fromdate)-(.started_at|fromdate)))"]|join(",")'
DAYS=$(python3 -c '
import datetime as d, sys
day, end = d.date.fromisoformat(sys.argv[1]), d.datetime.now(d.timezone.utc).date()
while day <= end:
    print(day); day += d.timedelta(days=1)' "$SINCE") || { echo "bad since date: $SINCE" >&2; exit 1; }
: > "$OUT"
for br in "${BRANCHES[@]}"; do
  for day in $DAYS; do
    runs=$(gh api --paginate "repos/$R/actions/workflows/$WF/runs?branch=$br&event=push&per_page=100&created=$day" \
      -q '.workflow_runs[]|[.id,.head_sha,.created_at,.conclusion]|@tsv') \
      || { echo "runs query failed for $br $day" >&2; exit 1; }
    [ -z "$runs" ] && continue
    if [ "$(printf '%s\n' "$runs" | wc -l)" -ge 1000 ]; then
      echo "$br $day hit the 1,000-run cap; the series would miss runs" >&2; exit 1
    fi
    while IFS=$'\t' read -r id sha created concl; do
      [ "$concl" = success ] || [ "$concl" = failure ] || continue
      legs=$(gh api "repos/$R/actions/runs/$id/jobs?per_page=50" -q "$JQ") \
        || { echo "jobs query failed for run $id" >&2; exit 1; }
      # Runs whose test job was skipped (reuse/superseded gates) have no legs; drop them.
      [ -n "$legs" ] && printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$created" "$br" "$id" "$sha" "$concl" "$legs" >> "$OUT"
    done <<< "$runs"
  done
done
wc -l "$OUT"
