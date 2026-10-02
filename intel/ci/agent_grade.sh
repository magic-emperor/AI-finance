#!/usr/bin/env bash
# Called by .github/workflows/agent_grader.yml. That YAML must live on `main` (GitHub only fires
# schedules from the default branch); keeping the commands here, on the code branch, means grader
# changes no longer need a main-branch PR.
#
#   bash intel/ci/agent_grade.sh <ledger checkout> <agent-data checkout>
set -u
LEDGER="$1"
DATA="$2"

python -X utf8 -m intel.grader --ledger "$LEDGER/ledger" --out "$DATA/scores"
v1=$?

# Grader v2 runs in SHADOW (plan v2 §7.10): a failure is reported, never blocks v1's scores.
if ! python -X utf8 -m intel.grader_v2 --data "$DATA" --ledger "$LEDGER/ledger" \
        --views "$LEDGER/agent/macro_views.jsonl" --out "$DATA"; then
  echo "::warning::grader v2 (shadow) failed -- v1 scores unaffected"
fi
exit $v1
