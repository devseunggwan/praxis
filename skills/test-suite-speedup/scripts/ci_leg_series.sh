#!/usr/bin/env bash
# Per-leg test-step seconds for every completed push run of a workflow, for locating step changes.
# Usage: ci_leg_series.sh <owner/repo> <workflow.yml> <since YYYY-MM-DD> <out.tsv> [step name] [branch ...]
# Output rows: created  branch  run_id  head_sha  conclusion  job=seconds,job=seconds
# Hundreds of runs = hundreds of API calls: run it in the background, never inside a capped tool loop.
set -o pipefail
R=$1 WF=$2 SINCE=$3 OUT=$4 STEP=${5:-Run tests}
shift $(( $# < 5 ? $# : 5 ))
BRANCHES=("$@"); [ ${#BRANCHES[@]} -eq 0 ] && BRANCHES=(main)
# gh api -q has no --arg; inject the step name as a JSON string literal.
STEP_JSON=$(printf '%s' "$STEP" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
JQ='[.jobs[]|select(.name|startswith("test"))|. as $j|.steps[]|select(.name=='"$STEP_JSON"' and .conclusion=="success")|"\($j.name)=\(((.completed_at|fromdate)-(.started_at|fromdate)))"]|join(",")'
: > "$OUT"
for br in "${BRANCHES[@]}"; do
  gh api --paginate "repos/$R/actions/workflows/$WF/runs?branch=$br&event=push&per_page=100&created=>=$SINCE" \
    -q '.workflow_runs[]|select(.conclusion=="success" or .conclusion=="failure")|[.id,.head_sha,.created_at,.conclusion]|@tsv' \
    | while IFS=$'\t' read -r id sha created concl; do
      legs=$(gh api "repos/$R/actions/runs/$id/jobs?per_page=50" -q "$JQ") \
        || { echo "jobs query failed for run $id" >&2; exit 1; }
      # Runs whose test job was skipped (reuse/superseded gates) have no legs; drop them.
      [ -n "$legs" ] && printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$created" "$br" "$id" "$sha" "$concl" "$legs" >> "$OUT"
    done
done
wc -l "$OUT"
