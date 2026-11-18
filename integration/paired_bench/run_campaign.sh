#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# run_campaign.sh PLAN WORKSPACE: screening, the plan's selection rule, the confirmation blocks and
# the report, one sample at a time under the root scale lock (no other timed stage overlaps).
# Rerunning resumes: finished samples and an existing selection are kept.
set -euo pipefail
plan=$(realpath "$1")
ws=$(realpath -m "$2")
here=$(dirname "$(realpath "$0")")
root=$(realpath "$here/../..")
py=${PYTHON:-python3}
mkdir -p "$ws" "$root/.work"
exec 9>>"$root/.work/scale.lock"
flock 9
bench() { "$py" "$here/bench.py" "$@" --plan "$plan" --workspace "$ws"; }
rev() { "$py" "$here/bench.py" status --workspace "$ws" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["head"])'; }
bench run --phase screen --base-rev "$(rev)"
[ -f "$ws/selection.json" ] || bench select --base-rev "$(rev)"
bench run --phase confirm --base-rev "$(rev)"
bench summarize > /dev/null
echo "report: $ws/report.json"
