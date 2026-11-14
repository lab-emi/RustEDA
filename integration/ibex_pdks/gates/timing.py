#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Extracted timing of a final netlist with RustSTA and OpenSTA, macro-pin limit rows split out.

Input schema `rusteda.pdk-gate.timing.v1`:
  top               design name
  files             netlist, sdc, std_tt, ram_tt, std_ss, ram_ss, physical_lef, spef_openrcx (pinned)
  tools             {rusteda, ruststa, opensta, gnu_time, python}
  scripts           {reference_timing, policy}: the IHP recipe's `reference_timing.py` and its
                    pinned `signoff_timing.py`, run unchanged
  budgets, scope    passed through to `reference_timing.py`
  macro_instances   instance names of the hard macros
  corners           {tt: label, ss: label}; decisions: disclosed decisions of the profile

`reference_timing.py` runs both engines on the same SPEF at TT and SS with CPPR off and checks
setup, hold, max slew, max capacitance and max fanout, annotation and constraint coverage. Its
verdict fails on any violated limit row. This wrapper keeps that report and adds one
adjudication, applied to every layout: a slew, capacitance or fanout row whose pin belongs to a
macro instance is reported under `macro_pin_rows` and does not gate unless it also exceeds a
limit the SDC sets for the design. Rows at standard-cell pins and ports, setup and hold
(including checks at macro pins) and every completeness requirement gate as before. The RustSTA
to OpenSTA slack difference is reported against the 1 ps tolerance, as in the IHP gate.

When the two-engine check cannot complete a corner, the corner is `unverified` and
`opensta_only` keeps what OpenSTA reported for it as a diagnostic. If RustSTA refused the inputs
themselves (for example libraries of one corner in different units, D0103), the status is
`blocked` with `blocked_by` naming the refusal: no layout can pass or fail on those inputs until
the engine accepts them. Otherwise the gate fails. The RustSTA binaries are the host map's
`rusteda` and `ruststa`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SCHEMA = "rusteda.pdk-gate.timing.v1"
CORNERS = ("tt", "ss")
KINDS = dict(slew="max_transition", cap="max_capacitance", fanout="max_fanout")
DRV_FAILURE = ": absolute_timing_or_drv_failed"


def sdc_design_limits(path) -> dict:
    """Smallest design-wide SDC limit per kind; a limit on anything but the design is refused."""
    limits = {}
    for line in Path(path).read_text().splitlines():
        match = re.match(r"\s*set_max_(transition|capacitance|fanout)\s+(\S+)\s+(.*)$", line)
        if not match:
            continue
        common.require(match[3].strip() == "[current_design]", "SDC_LIMIT_SCOPE_UNSUPPORTED: " + line.strip()[:120])
        kind = {"transition": "slew", "capacitance": "cap", "fanout": "fanout"}[match[1]]
        limits[kind] = min(float(match[2]), limits.get(kind, float("inf")))
    return limits


def split_rows(rows, macro_instances, sdc_limits):
    """Partition violated limit rows: {kind: {total, gating, macro_pin, macro_pin_gating, rows}}."""
    result = {kind: dict(total=0, gating=0, macro_pin=0, macro_pin_gating=0, macro_pin_rows=[]) for kind in KINDS}
    names = {v: k for k, v in KINDS.items()}
    for row in rows:
        kind = names.get(row["kind"], row["kind"])
        common.require(kind in result, "DRV_ROW_KIND: " + str(row["kind"]))
        entry = result[kind]
        entry["total"] += 1
        instance = row["pin"].rsplit("/", 1)[0] if "/" in row["pin"] else None
        if instance not in macro_instances:
            entry["gating"] += 1
            continue
        entry["macro_pin"] += 1
        beyond_sdc = kind in sdc_limits and float(row["value"]) > sdc_limits[kind]
        if beyond_sdc:
            entry["macro_pin_gating"] += 1
            entry["gating"] += 1
        if len(entry["macro_pin_rows"]) < 512:
            entry["macro_pin_rows"].append(dict(pin=row["pin"], value=row["value"], limit=row["limit"],
                                                slack=row["slack"], gating=beyond_sdc))
    return result


def opensta_view(output, corner, observed, macro_instances, sdc_limits) -> dict:
    """What OpenSTA alone reported for a corner the two-engine check could not complete (diagnostic).

    The worst slacks come from the OpenSTA log of the reference run; the limit rows, when its own
    validation got that far, are split as in the gate."""
    log = output / "reference" / "observation" / "logs" / f"openrcx-{corner}-opensta.stdout"
    view = dict(status="diagnostic_only", validation_error=observed.get("helper_validation_error") or observed.get("error"))
    if log.is_file():
        text = log.read_text(errors="replace")
        for key, label in (("setup_ws_ns", "max"), ("hold_ws_ns", "min")):
            match = re.search(r"^worst slack " + label + r" (\S+)$", text, re.M)
            view[key] = float(match[1]) if match else None
    if "drv_rows" in observed:
        rows = split_rows(observed["drv_rows"], macro_instances, sdc_limits)
        view.update({kind + "_gating_rows": rows[kind]["gating"] for kind in KINDS},
                    **{kind + "_macro_pin_rows": rows[kind]["macro_pin"] - rows[kind]["macro_pin_gating"] for kind in KINDS})
        view.update({k: observed.get(k) for k in ("setup_violations", "hold_violations")})
        view["annotation"] = {k: v for k, v in observed.get("annotation", {}).items() if k != "rows"}
    return view


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    config = common.read_json(args.inputs, 2 * common.MIB)
    output = common.fresh_workspace(args.workspace, args.base_rev)
    result = dict(schema="rusteda.pdk-gate.timing-result.v1", status="failed", complete=False)
    try:
        common.require(config.get("schema") == SCHEMA, "INPUT_SCHEMA")
        files, tools, scripts = config["files"], config["tools"], config["scripts"]
        for group in (files, tools, scripts):
            for name, record in group.items():
                common.verify(record, name)
        macro_instances = set(config["macro_instances"])
        sdc_limits = sdc_design_limits(files["sdc"]["path"])
        base_inputs = dict(schema="rusteda.reference-timing-inputs.v1", top=config["top"], files=files,
                           tools={k: tools[k] for k in ("rusteda", "ruststa", "opensta", "gnu_time")},
                           policy=scripts["policy"], budgets=config["budgets"], scope=config["scope"])
        common.save_json(output / "reference-inputs.json", base_inputs)
        run = common.run_tool([tools["python"]["path"], scripts["reference_timing"]["path"], "--inputs",
                               output / "reference-inputs.json", "--workspace", output / "reference", "--base-rev", "empty"],
                              output / "reference.log", output / "reference.time", tools["gnu_time"]["path"],
                              4 * int(config["budgets"]["timeout_seconds"]) + 120)
        report_path = output / "reference" / "reference-timing.json"
        common.require(report_path.is_file(), "REFERENCE_TIMING_REPORT_MISSING: " + (output / "reference.log").read_text()[-400:])
        base = common.read_json(report_path, 256 * common.MIB)
        other_failures = [item for item in base["failed_items"] if not item.endswith(DRV_FAILURE)]
        corners, all_passed = {}, True
        for corner in CORNERS:
            row, observed = base["corners"].get(corner), base["observations"].get(corner, {})
            if row is None or "drv_rows" not in observed:
                corners[corner] = dict(status="unverified", label=config["corners"][corner],
                                       opensta_only=opensta_view(output, corner, observed, macro_instances, sdc_limits))
                all_passed = False
                continue
            engines = dict(ruststa=split_rows(row["native_drv_rows"], macro_instances, sdc_limits),
                           opensta=split_rows(observed["drv_rows"], macro_instances, sdc_limits))
            for kind in KINDS:
                common.require(engines["ruststa"][kind]["total"] == row["ruststa"][kind + "_violations"]
                               and engines["opensta"][kind]["total"] == row["opensta"][kind + "_violations"],
                               f"DRV_ROW_COVERAGE: {corner} {kind}")
            timing_ok = (row["setup_ws_ns"] >= 0 and row["hold_ws_ns"] >= 0
                         and row["setup_violations"] == 0 and row["hold_violations"] == 0)
            gating = {kind: max(engines[e][kind]["gating"] for e in engines) for kind in KINDS}
            macro = {kind: max(engines[e][kind]["macro_pin"] - engines[e][kind]["macro_pin_gating"] for e in engines)
                     for kind in KINDS}
            passed = timing_ok and not any(gating.values())
            all_passed &= passed
            corners[corner] = dict(
                status="passed" if passed else "failed", label=config["corners"][corner],
                setup_ws_ns=row["setup_ws_ns"], hold_ws_ns=row["hold_ws_ns"],
                setup_violations=row["setup_violations"], hold_violations=row["hold_violations"],
                slew_violations=gating["slew"], cap_violations=gating["cap"], fanout_violations=gating["fanout"],
                macro_pin_rows_not_gating=macro, all_rows=dict(slew=row["slew_violations"], cap=row["cap_violations"],
                                                               fanout=row["fanout_violations"]),
                ruststa={k: row["ruststa"][k] for k in row["ruststa"]},
                opensta={k: row["opensta"][k] for k in ("setup_ws_ns", "hold_ws_ns", "setup_violations", "hold_violations",
                                                         "slew_violations", "cap_violations", "fanout_violations")},
                engines=engines, base_status=row["status"])
        native = base.get("native_analysis") or {}
        compare = base["native_compare"]
        status = "passed" if all_passed and not other_failures and base["coverage_complete"] and base["inputs_unchanged"] else "failed"
        blocked = None
        if native.get("status") == "rejected":
            # RustSTA refused the inputs themselves: the gate cannot judge any layout with them.
            status, blocked = "blocked", dict(engine="ruststa", error=native.get("error"),
                                              diagnostics=[d.get("code") for d in native.get("diagnostics", [])
                                                           if d.get("severity") == "error"][:8])
        result.update(status=status, complete=True, top=config["top"], corners=corners, blocked_by=blocked,
                      failed_items=other_failures + [c + ": timing or gating limit rows failed" for c in CORNERS
                                                     if corners[c]["status"] != "passed"],
                      base_status=base["status"], base_failed_items=base["failed_items"],
                      coverage_complete=base["coverage_complete"], inputs_unchanged=base["inputs_unchanged"],
                      parity=dict(tolerance_ns=compare.get("tolerance_ns"), max_abs_difference_ns=compare.get("max_abs_difference_ns"),
                                  within_tolerance=compare.get("within_tolerance"), status=compare.get("status"),
                                  differences=compare.get("differences")),
                      ruststa=dict(status=native.get("status"), error=native.get("error"),
                                   diagnostics=[d for d in native.get("diagnostics", []) if d.get("severity") == "error"][:8]),
                      sdc_design_limits=sdc_limits, macro_instances=sorted(macro_instances),
                      macro_pin_policy="slew, capacitance and fanout rows at macro pins are listed separately and gate only "
                                       "beyond a design-wide SDC limit; setup and hold at macro pins gate",
                      decisions=config.get("decisions", []), scope=base["scope"], inputs=files, tools=tools, scripts=scripts,
                      reference_report=common.identity(report_path), reference_exit_code=run["exit_code"],
                      wall_seconds=run["wall_seconds"], peak_rss_kib=run["peak_rss_kib"])
        for group in (files, tools, scripts):
            for name, record in group.items():
                common.verify(record, name + " after run")
    except (common.GateError, OSError, KeyError, ValueError, TypeError) as error:
        result.update(status="failed", complete=False, error=str(error))
    common.save_json(output / "summary.json", result)
    brief = {c: {k: v.get(k) for k in ("status", "setup_ws_ns", "hold_ws_ns", "slew_violations", "cap_violations",
                                        "fanout_violations", "macro_pin_rows_not_gating")}
             for c, v in result.get("corners", {}).items()}
    print(json.dumps(dict(status=result["status"], error=result.get("error"), corners=brief)))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
