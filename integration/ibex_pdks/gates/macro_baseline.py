#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Record what each KLayout deck of a gate profile reports on every macro GDS alone.

  macro_baseline.py --profile integration/ibex_pdks/<profile>/gates.json --host host.json

Run once per PDK tree and tool set, before any layout is judged; it writes
`<macro_baselines of the host map>/<gate>_klayout/<master>/{drc.lyrdb, summary.json}` and an
index `baseline.json`. A layout gate attributes to a macro exactly the markers recorded here for
the same deck bytes, switches, tool and macro GDS, and refuses a baseline made with anything
else. The time spent here belongs to neither flow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402
import pdk_signoff  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--gates", default="drc,antenna")
    args = parser.parse_args()
    host, profile, tools = pdk_signoff.load(args.host, args.profile)
    root = Path(host["macro_baselines"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    index = common.read_json(root / "baseline.json") if (root / "baseline.json").is_file() else dict(
        schema="rusteda.pdk-gate.macro-baseline.v1", profile=profile["name"], runs={})
    failed = False
    for gate in args.gates.split(","):
        for spec in profile["gates"][gate]["engines"]:
            if spec["engine"] != "klayout":
                continue
            for master, macro in profile["macros"].items():
                target = pdk_signoff.baseline_dir(host, gate, "klayout", master)
                if target.exists() and any(target.iterdir()):
                    print(f"{gate} {master}: baseline exists at {target}; remove it to record a new one")
                    continue
                plan = pdk_signoff.klayout_plan(profile, tools, spec, host, "baseline", master, profile.file(macro["gds"]))
                plan_path = root / f"{gate}_klayout-{master}.plan.json"
                common.save_json(plan_path, plan)
                run = common.run_tool([tools["python"]["path"], HERE / "klayout_drc.py", "--inputs", plan_path,
                                       "--workspace", target, "--base-rev", "empty"],
                                      root / f"{gate}_klayout-{master}.log", root / f"{gate}_klayout-{master}.time",
                                      tools["gnu_time"]["path"], int(spec.get("timeout_seconds", 14400)) + 600)
                summary = common.read_json(target / "summary.json") if (target / "summary.json").is_file() else {}
                ok = run["exit_code"] == 0 and summary.get("status") == "recorded"
                failed |= not ok
                index["runs"][f"{gate}_klayout/{master}"] = dict(
                    status=summary.get("status"), items=summary.get("items"), categories=summary.get("categories"),
                    deck_key=summary.get("deck_key"), wall_seconds=run["wall_seconds"], peak_rss_kib=run["peak_rss_kib"],
                    error=summary.get("error"))
                common.save_json(root / "baseline.json", index)
                print(json.dumps({f"{gate}_klayout/{master}": index["runs"][f"{gate}_klayout/{master}"]}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
