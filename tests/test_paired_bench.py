#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Tests for the paired benchmark harness (`integration/paired_bench`).

The statistics and verdict tests use literal numbers. The end-to-end test runs the real
`bench.run_sample` path with fake producers and a fake signoff driver under `timeout` and GNU
time, without systemd or EDA tools: three screening arms, selection, three AB/BA confirmation
blocks and the report. Peak memory is a few tens of MiB; it takes a few seconds.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import textwrap
import unittest

from integration.paired_bench import bench, stats, verdicts


def gds_rec(kind: int, dtype: int, payload: bytes) -> bytes:
    if dtype == 6 and len(payload) % 2:
        payload += b"\x00"
    return struct.pack(">HBB", 4 + len(payload), kind, dtype) + payload


def gds_bytes(date: int, geometry: bytes = b"\x00\x01") -> bytes:
    return (gds_rec(0, 2, b"\x02\x58") + gds_rec(1, 2, struct.pack(">12h", *([date] * 12))) +
            gds_rec(13, 2, geometry) + gds_rec(4, 0, b""))


def gds_hierarchy(date: int, structures: list[tuple[str, list[str], bytes]]) -> bytes:
    """Build a minimal valid GDSII stream from `(strname, referenced_snames, xy_bytes)` tuples."""
    date_payload = struct.pack(">12h", *([date] * 12))
    out = [gds_rec(0, 2, b"\x02\x58"), gds_rec(1, 2, date_payload)]
    for name, refs, geom in structures:
        out.append(gds_rec(5, 2, date_payload))
        out.append(gds_rec(6, 6, name.encode("ascii")))
        for ref in refs:
            out.append(gds_rec(10, 0, b""))  # SREF
            out.append(gds_rec(18, 6, ref.encode("ascii")))
            out.append(gds_rec(17, 0, b""))  # ENDEL
        if geom:
            out.append(gds_rec(16, 3, geom))  # XY
        out.append(gds_rec(7, 0, b""))  # ENDSTR
    out.append(gds_rec(4, 0, b""))  # ENDLIB
    return b"".join(out)


class StatsTest(unittest.TestCase):
    def test_stable_lead_needs_three_blocks_and_no_overlap(self):
        two = stats.compare({"b1": 10.0, "b2": 11.0}, {"b1": 2.0, "b2": 2.1})
        self.assertEqual(two["verdict"], "insufficient_repeats")
        three = stats.compare({"b1": 10.0, "b2": 11.0, "b3": 10.5}, {"b1": 2.0, "b2": 2.1, "b3": 1.9})
        self.assertEqual(three["verdict"], "candidate_faster_in_every_sample")
        self.assertEqual(three["candidate_faster_blocks"], 3)
        self.assertAlmostEqual(three["sign_test_one_sided_p"], 0.125)
        self.assertAlmostEqual(three["conservative_ratio_range"][0], 10.0 / 2.1, places=4)

    def test_small_lead_with_overlap_is_not_established(self):
        out = stats.compare({"b1": 106.0, "b2": 99.0, "b3": 104.0}, {"b1": 100.0, "b2": 101.0, "b3": 98.0})
        self.assertEqual(out["verdict"], "overlap_lead_not_established")
        self.assertLess(out["conservative_ratio_range"][0], 1.0)

    def test_missing_endpoint_is_not_reached_not_zero(self):
        out = stats.compare({"b1": None, "b2": 10.0, "b3": 10.0}, {"b1": 1.0, "b2": 1.0, "b3": 1.0})
        self.assertEqual(out["verdict"], "not_reached")
        self.assertIsNone(out["ratio_of_medians"])
        self.assertEqual(out["missing"]["reference"], ["b1"])

    def test_alternating_blocks(self):
        self.assertEqual(stats.alternating_blocks("L", "R", 3), [["L", "R"], ["R", "L"], ["L", "R"]])


class VerdictTest(unittest.TestCase):
    def test_gate_normalization(self):
        self.assertEqual(verdicts.gate("drc", {"exit_code": 0, "report": {"status": "passed", "violations": 0}})
                         ["verdict"], "passed")
        self.assertEqual(verdicts.gate("drc", {"exit_code": 0, "report": {"status": "passed", "violations": 3}})
                         ["verdict"], "failed")
        self.assertEqual(verdicts.gate("drc", {"exit_code": 0, "report": {"status": "passed"}})["verdict"],
                         "unverified")
        self.assertEqual(verdicts.gate("extraction", {"exit_code": 0, "report": {"status": "extracted_not_qualified"}})
                         ["verdict"], "completed")
        self.assertEqual(verdicts.gate("logic", {"exit_code": 1, "report": {"summary": {"status": "unverified"}}})
                         ["verdict"], "unverified")
        self.assertEqual(verdicts.gate("timing", {"exit_code": 0, "report": {"status": "passed"}})["verdict"], "passed")
        self.assertEqual(verdicts.gate("timing", {"exit_code": 1, "report": {"status": "passed"}})["verdict"],
                         "unverified")
        self.assertEqual(verdicts.gate("lvs", None)["verdict"], "not_run")

    def test_endpoint_sums_only_its_gates(self):
        rows = {"drc": dict(verdict="passed", wall_seconds=5.0), "timing": dict(verdict="failed", wall_seconds=1.0),
                "extraction": dict(verdict="completed", wall_seconds=2.0)}
        self.assertEqual(verdicts.endpoint(10.0, rows, ["drc", "extraction"])["seconds"], 17.0)
        blocked = verdicts.endpoint(10.0, rows, ["drc", "timing", "logic"])
        self.assertIsNone(blocked["seconds"])
        self.assertEqual(blocked["blocking"], {"timing": "failed", "logic": "not_run"})


class GdsDigestTest(unittest.TestCase):
    def test_dates_are_masked_geometry_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = (Path(tmp) / n for n in "abc")
            a.write_bytes(gds_bytes(2025))
            b.write_bytes(gds_bytes(2026))
            c.write_bytes(gds_bytes(2026, b"\x00\x02"))
            self.assertNotEqual(bench.sha256(a), bench.sha256(b))
            self.assertEqual(bench.gds_digest(a), bench.gds_digest(b))
            self.assertNotEqual(bench.gds_digest(b), bench.gds_digest(c))
            c.write_bytes(gds_bytes(2026)[:-2])
            with self.assertRaises(bench.BenchError):
                bench.gds_digest(c)

    def test_magic_prefix_masked_canonicalizes_macro_subcells_without_false_positives(self):
        def sky_like(date: int, pfx: str, extra_cell: str = "RM_IHPSG13_CELL", geom: bytes = b"\x00\x00\x00\x01"):
            return gds_hierarchy(date, [
                (f"{pfx}sky130_sram_macro_contact_1", [], geom),
                (f"{pfx}sky130_fd_bd_openram_cell", [f"{pfx}sky130_sram_macro_contact_1"], b""),
                ("sky130_sram_macro", [f"{pfx}sky130_fd_bd_openram_cell"], b""),
                (extra_cell, [], geom),
                ("ibex_sram_bench", ["sky130_sram_macro", extra_cell], b""),
            ])

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            a, b, c, d = (p / f"{n}.gds" for n in "abcd")
            a.write_bytes(sky_like(2025, "FF_"))
            b.write_bytes(sky_like(2026, "M4_"))
            c.write_bytes(sky_like(2026, "M4_", extra_cell="ZZ_IHPSG13_CELL"))
            d.write_bytes(sky_like(2026, "M4_", geom=b"\x00\x00\x00\x02"))
            id_a, id_b = bench.identity(a, gds=True), bench.identity(b, gds=True)
            id_c, id_d = bench.identity(c, gds=True), bench.identity(d, gds=True)
            # Dates-only differs because Magic randomized FF_ vs M4_.
            self.assertNotEqual(id_a["dates_masked_sha256"], id_b["dates_masked_sha256"])
            # Magic-prefix-masked matches both FF_sky130_sram_macro_* and descendant FF_sky130_fd_bd_*.
            self.assertEqual(id_a["magic_prefix_masked_sha256"], id_b["magic_prefix_masked_sha256"])
            self.assertEqual(id_a["magic_prefixes_masked"], 2)
            # Non-Magic XX_ cell names (RM_IHPSG13_CELL vs ZZ_IHPSG13_CELL) are NOT masked.
            self.assertNotEqual(id_b["magic_prefix_masked_sha256"], id_c["magic_prefix_masked_sha256"])
            # Geometry changes are NEVER masked.
            self.assertNotEqual(id_b["magic_prefix_masked_sha256"], id_d["magic_prefix_masked_sha256"])
            det = bench.determinism([{"layout": {"gds": id_a}}, {"layout": {"gds": id_b}}])
            self.assertFalse(det["gds_dates_masked"]["identical"])
            self.assertTrue(det["gds_magic_prefix_masked"]["identical"])

            # Two distinct macros with swapped random prefixes remain canonical in encounter order,
            # while collapsing both into one prefix changes the digest (bijectivity).
            e, f, g = (p / f"{n}.gds" for n in "efg")
            def two_macros(p1: str, p2: str):
                return gds_hierarchy(2026, [
                    (f"{p1}macro_alpha_sub", [], b"\x00\x00\x00\x01"),
                    ("macro_alpha", [f"{p1}macro_alpha_sub"], b""),
                    (f"{p2}macro_beta_sub", [], b"\x00\x00\x00\x02"),
                    ("macro_beta", [f"{p2}macro_beta_sub"], b""),
                    ("top", ["macro_alpha", "macro_beta"], b""),
                ])
            e.write_bytes(two_macros("AA_", "ZZ_"))
            f.write_bytes(two_macros("ZZ_", "AA_"))
            g.write_bytes(two_macros("AA_", "AA_"))
            self.assertEqual(bench.gds_magic_prefix_digest(e), bench.gds_magic_prefix_digest(f))
            self.assertNotEqual(bench.gds_magic_prefix_digest(e), bench.gds_magic_prefix_digest(g))


FAKE_LIBRELANE = """
import json, sys, time
from pathlib import Path
design = Path(sys.argv[sys.argv.index('--design-dir') + 1])
config = json.loads(Path(sys.argv[-1]).read_text())
margin = config.get('DRT_ANTENNA_REPAIR_MARGIN', 10)
time.sleep(0.4 if margin == 50 else 0.3)
run = design / 'runs' / sys.argv[sys.argv.index('--run-tag') + 1]
for d in ('06-yosys-synthesis', '60-klayout-streamout', 'final/def', 'final/nl', 'final/pnl'):
    (run / d).mkdir(parents=True, exist_ok=True)
(run / '60-klayout-streamout/ibex_sram_bench.gds').write_bytes(bytes.fromhex(sys.argv[1]))
(run / 'final/def/ibex_sram_bench.def').write_text('DEF')
(run / 'final/nl/ibex_sram_bench.nl.v').write_text('drv' if margin == 50 else 'clean')
(run / 'final/pnl/ibex_sram_bench.pnl.v').write_text('pnl')
(run / '06-yosys-synthesis/ibex_sram_bench.nl.v').write_text('synth')
(run / '06-yosys-synthesis/runtime.txt').write_text('00:00:01.500')
(run / 'resolved.json').write_text(json.dumps(dict(config, DRT_ANTENNA_REPAIR_MARGIN=margin)))
"""

FAKE_RUSTEDA = """
import os, sys, time
from pathlib import Path
# Like rusteda-flow: the producer creates the ownership directory and refuses an existing one.
assert os.environ['RUSTEDA_WORKER_ID']
Path(os.environ['RUSTEDA_OWNERSHIP']).mkdir()
out = Path(sys.argv[2])
time.sleep(0.05)
for d in ('backend', 'logs', 'frontend'):
    (out / d).mkdir(parents=True, exist_ok=True)
(out / 'backend/ibex_sram_bench.gds').write_bytes(bytes.fromhex(sys.argv[1]))
for n in ('be_def', 'be_netlist', 'be_powered'):
    (out / 'logs' / f'{n}.stdout').write_text(n)
(out / 'frontend/mapped.v').write_text('mapped')
"""

FAKE_SIGNOFF = """
import json, os, sys
from pathlib import Path
a = sys.argv
layout = json.loads(Path(a[a.index('--layout') + 1]).read_text())
work = Path(a[a.index('--workspace') + 1]); work.mkdir(parents=True)
owner = json.loads((Path(os.environ['RUSTEDA_OWNERSHIP']) / 'native.json').read_text())
assert owner['request_id'] == os.environ['RUSTEDA_WORKER_ID']
drv = Path(layout['netlist']).read_text() == 'drv'
librelane = 'design' in layout['gds']
gates = {n: {'exit_code': 0, 'report': {'status': 'passed', 'violations': 0}} for n in ('drc', 'antenna')}
gates['latchup'] = {'exit_code': 0, 'report': {'summary': {'status': 'passed'}}}
gates['lvs'] = {'exit_code': 0, 'report': {'status': 'passed'}}
gates['extraction'] = {'exit_code': 0, 'report': {'status': 'extracted_not_qualified'}}
gates['timing'] = {'exit_code': int(drv), 'report': {'status': 'failed' if drv else 'passed'}}
gates['logic'] = {'exit_code': int(librelane), 'report': {'summary': {'status': 'unverified' if librelane else 'passed'}}}
res = {n: {'exit_code': g['exit_code'], 'wall_seconds': 2.0 if librelane else 1.0} for n, g in gates.items()}
(work / 'summary.json').write_text(json.dumps({'gates': gates, 'resources': res}))
"""


class EndToEndTest(unittest.TestCase):
    def make_plan(self, tmp: Path) -> Path:
        scripts = tmp / "scripts"
        scripts.mkdir()
        for name, text in (("librelane.py", FAKE_LIBRELANE), ("rusteda.py", FAKE_RUSTEDA),
                           ("signoff.py", FAKE_SIGNOFF)):
            (scripts / name).write_text(textwrap.dedent(text))
        (tmp / "template.json.in").write_text(json.dumps({"VERILOG_FILES": ["@WORKSPACE@/rtl.v"],
                                                          "DRT_ANTENNA_REPAIR_MARGIN": 50, "DRT_THREADS": 16}))
        (tmp / "manifest.json").write_text("{}")
        gds = gds_bytes(1).hex()
        py = sys.executable
        layout_ll = {k: "{sample}/design/runs/{run_tag}/" + v for k, v in dict(
            gds="*-klayout-streamout/ibex_sram_bench.gds", def_="final/def/ibex_sram_bench.def",
            netlist="final/nl/ibex_sram_bench.nl.v", powered_netlist="final/pnl/ibex_sram_bench.pnl.v",
            synthesis_netlist="*-yosys-synthesis/ibex_sram_bench.nl.v").items()}
        layout_ll["def"] = layout_ll.pop("def_")

        def ll(overlay):
            return dict(flow="librelane", role="reference",
                        librelane_config=dict(template="{root}/template.json.in", substitute={"@WORKSPACE@": "/prep"},
                                              overlay=overlay),
                        producer=dict(argv=[py, "{root}/scripts/librelane.py", gds, "--run-tag", "{run_tag}",
                                            "--design-dir", "{sample}/design", "{sample}/design/config.json"],
                                      env={"TMPDIR": "{sample}/tmp"}, deadline_seconds=30),
                        layout=layout_ll)
        plan = dict(
            schema=bench.PLAN_SCHEMA, name="fake", root=str(tmp),
            resources=dict(cpus=[0], memory_mib=256, swap_mib=0), quiet_host={}, budgets=dict(min_free_disk_gib=0),
            inputs={"manifest": dict(path="{root}/manifest.json", sha256=bench.sha256(tmp / "manifest.json"))},
            signoff=dict(argv=[py, "{root}/scripts/signoff.py", "--layout", "{sample}/layout.json",
                               "--workspace", "{sample}/signoff/gates"], deadline_seconds=30,
                         ownership_manifest="{inputs.manifest}",
                         gates=["drc", "antenna", "latchup", "lvs", "extraction", "timing", "logic"]),
            endpoints=dict(gds=[], physical_signoff=["drc", "antenna", "latchup", "lvs"],
                           common_signoff=["drc", "antenna", "latchup", "lvs", "extraction", "timing"],
                           full_signoff=["drc", "antenna", "latchup", "lvs", "extraction", "timing", "logic"]),
            arms={
                "rusteda": dict(flow="rusteda", role="candidate",
                                producer=dict(argv=[py, "{root}/scripts/rusteda.py", gds, "{sample}/output"],
                                              deadline_seconds=30, worker_scope=True),
                                layout=dict(gds="{sample}/output/backend/ibex_sram_bench.gds",
                                            **{"def": "{sample}/output/logs/be_def.stdout"},
                                            netlist="{sample}/output/logs/be_netlist.stdout",
                                            powered_netlist="{sample}/output/logs/be_powered.stdout",
                                            synthesis_netlist="{sample}/output/frontend/mapped.v"),
                                expected_outputs={"gds": bench.hashlib.sha256(bytes.fromhex(gds)).hexdigest()}),
                "ll-recipe": ll({"DRT_THREADS": 4}),
                "ll-default": ll({"DRT_THREADS": 4, "DRT_ANTENNA_REPAIR_MARGIN": None}),
            },
            phases=dict(screen=dict(samples=["ll-recipe", "rusteda", "ll-default"]),
                        confirm=dict(pair=["@selected", "rusteda"], blocks=3)),
            selection=dict(from_phase="screen", flow="librelane", baseline_arm="ll-recipe",
                           physical_endpoint="physical_signoff", target_endpoint="common_signoff"),
        )
        path = tmp / "plan.json"
        path.write_text(json.dumps(plan))
        return path

    def test_screen_select_confirm_report(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            plan = bench.Plan(self.make_plan(tmp))
            ws = tmp / "ws"
            fast = dict(period=0.05, launcher=[])
            with self.assertRaises(bench.BenchError) as raised:
                bench.cmd_run(plan, ws, "screen", "rev3", None, False, **fast)
            self.assertEqual(raised.exception.code, "REVISION_CONFLICT")
            out = bench.cmd_run(plan, ws, "screen", "empty", None, False, **fast)
            self.assertEqual(out["head"], "rev3")
            done = bench.completed(ws)
            recipe = done["screen-01-ll-recipe"]
            self.assertEqual(recipe["endpoints"]["common_signoff"]["blocking"], {"timing": "failed"})
            self.assertIsNotNone(recipe["endpoints"]["physical_signoff"]["seconds"])
            default = done["screen-03-ll-default"]
            self.assertEqual(default["config_delta_from_baseline"],
                             {"DRT_ANTENNA_REPAIR_MARGIN": {"baseline": 50, "arm": None}})
            self.assertEqual(default["librelane_diagnostics"]["resolved"], {"DRT_ANTENNA_REPAIR_MARGIN": 10})
            self.assertEqual(default["endpoints"]["full_signoff"]["blocking"], {"logic": "unverified"})
            native = done["screen-02-rusteda"]
            self.assertEqual(native["matches_expected"], {"gds": True})
            self.assertAlmostEqual(native["endpoints"]["full_signoff"]["gate_seconds"], 7.0)

            selection = bench.cmd_select(plan, ws, "rev3")
            self.assertEqual(selection["selected"], "ll-default")
            self.assertEqual(selection["reason"], "fastest to common_signoff")
            # A partial sample is refused, then moved aside (never deleted) on request.
            (ws / "samples" / "confirm-b1-1-ll-default").mkdir(parents=True)
            with self.assertRaises(bench.BenchError) as raised:
                bench.cmd_run(plan, ws, "confirm", "rev4", None, False, **fast)
            self.assertEqual(raised.exception.code, "PARTIAL_SAMPLE")
            out = bench.cmd_run(plan, ws, "confirm", "rev4", None, True, **fast)
            self.assertEqual(out["head"], "rev10")
            self.assertTrue((ws / "samples" / "confirm-b1-1-ll-default.partial-1").is_dir())
            order = [r["id"] for r in out["ran"]]
            self.assertEqual(order, ["confirm-b1-1-ll-default", "confirm-b1-2-rusteda", "confirm-b2-1-rusteda",
                                     "confirm-b2-2-ll-default", "confirm-b3-1-ll-default", "confirm-b3-2-rusteda"])

            changed = json.loads(plan.path.read_text())
            changed["selection"]["target_endpoint"] = "gds"
            plan.path.write_text(json.dumps(changed))
            with self.assertRaises(bench.BenchError) as raised:
                bench.cmd_select(bench.Plan(plan.path), ws, "rev10")
            self.assertEqual(raised.exception.code, "PLAN_CHANGED")
            report = bench.cmd_summarize(plan, ws)
            confirm = report["phases"]["confirm"]
            self.assertEqual((confirm["reference"], confirm["candidate"]), ("ll-default", "rusteda"))
            self.assertEqual(confirm["comparisons"]["gds"]["blocks"], 3)
            self.assertEqual(confirm["comparisons"]["full_signoff"]["verdict"], "not_reached")
            self.assertEqual(confirm["comparisons"]["full_signoff"]["missing"]["reference"], ["b1", "b2", "b3"])
            self.assertIn(confirm["comparisons"]["common_signoff"]["verdict"],
                          ("candidate_faster_in_every_sample", "overlap_lead_not_established"))
            self.assertTrue(confirm["arms"]["rusteda"]["determinism"]["gds"]["identical"])
            self.assertTrue(confirm["arms"]["ll-default"]["determinism"]["gds_dates_masked"]["identical"])

    def test_amend_appends_only_and_invalidate_moves_aside(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            path = self.make_plan(tmp)
            plan = bench.Plan(path)
            ws = tmp / "ws"
            fast = dict(period=0.05, launcher=[])
            bench.cmd_run(plan, ws, "screen", "empty", 2, False, **fast)
            data = json.loads(path.read_text())
            changed = copy.deepcopy(data)
            changed["arms"]["ll-recipe"]["producer"]["deadline_seconds"] = 31
            path.write_text(json.dumps(changed))
            with self.assertRaises(bench.BenchError) as raised:
                bench.cmd_amend(bench.Plan(path), ws, "rev2", "edit an arm")
            self.assertEqual(raised.exception.code, "AMENDMENT_REFUSED")
            added = copy.deepcopy(data)
            added["arms"]["ll-jumpers"] = copy.deepcopy(data["arms"]["ll-default"])
            added["arms"]["ll-jumpers"]["librelane_config"]["overlay"]["DRT_ANTENNA_REPAIR_JUMPER_ONLY"] = True
            added["phases"]["screen"]["samples"].append("ll-jumpers")
            path.write_text(json.dumps(added))
            row = bench.cmd_amend(bench.Plan(path), ws, "rev2", "root cause found in screen-01")
            self.assertEqual((row["added_arms"], row["added_samples"], row["head"]),
                             (["ll-jumpers"], {"screen": ["ll-jumpers"]}, "rev3"))
            gone = bench.cmd_invalidate(ws, "rev3", "screen-02-rusteda", "harness defect")
            self.assertTrue(Path(gone["moved_to"]).is_dir())
            plan = bench.Plan(path)
            out = bench.cmd_run(plan, ws, "screen", "rev4", None, False, **fast)
            self.assertEqual([r["id"] for r in out["ran"]],
                             ["screen-02-rusteda", "screen-03-ll-default", "screen-04-ll-jumpers"])
            bench.cmd_select(plan, ws, out["head"])
            with self.assertRaises(bench.BenchError) as raised:
                bench.cmd_amend(plan, ws, bench.head(ws), "too late")
            self.assertEqual(raised.exception.code, "AMENDMENT_REFUSED")
            bench.cmd_note(ws, bench.head(ws), "the amendment reason overstated the cause")
            report = bench.cmd_summarize(plan, ws)
            self.assertEqual(report["notes"][0]["note"], "the amendment reason overstated the cause")
            self.assertEqual(len(report["amendments"]), 1)
            self.assertEqual(report["invalidated"][0]["sample"], "screen-02-rusteda")

    def test_producer_ownership_path_and_overlay_placeholders(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            path = self.make_plan(tmp)
            data = json.loads(path.read_text())
            data["arms"]["rusteda"]["producer"]["ownership"] = "{sample}/output-owner"
            data["arms"]["ll-default"]["librelane_config"]["overlay"]["PDN_CFG"] = "{root}/pdn.tcl"
            path.write_text(json.dumps(data))
            plan = bench.Plan(path)
            self.assertEqual(bench.render_librelane_config(plan, plan.arms["ll-default"])["PDN_CFG"],
                             f"{tmp}/pdn.tcl")
            ws = tmp / "ws"
            record = bench.run_sample(plan, ws, dict(id="x-rusteda", arm="rusteda", block=None),
                                      period=0.05, launcher=[])
            self.assertTrue((ws / "samples/x-rusteda/output-owner").is_dir())
            self.assertFalse((ws / "samples/x-rusteda/ownership").exists())
            self.assertIsNotNone(record["endpoints"]["gds"]["seconds"])

    def test_config_drift_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            plan = bench.Plan(self.make_plan(tmp))
            arm = copy.deepcopy(plan.arms["ll-recipe"])
            (tmp / "published.json").write_text(json.dumps({"DRT_THREADS": 4}))
            arm["librelane_config"]["expect_equal"] = "{root}/published.json"
            with self.assertRaises(bench.BenchError) as raised:
                bench.render_librelane_config(plan, arm)
            self.assertEqual(raised.exception.code, "CONFIG_DRIFT")


class SelectionFallbackTest(unittest.TestCase):
    def _write_workspace(self, tmp: Path, samples: dict[str, dict], *, baseline: str | None = "ll-recipe",
                         fallback_policy: str | None = None) -> tuple[bench.Plan, Path]:
        ws = tmp / "ws"
        (tmp / "manifest.json").write_text("{}")
        (tmp / "template.json.in").write_text("{}")
        layout = {k: "{sample}/" + k for k in bench.LAYOUT_KEYS}
        arms = {
            name: dict(flow="librelane", role="reference",
                       librelane_config=dict(template="{root}/template.json.in", overlay={}),
                       producer=dict(argv=["true"], deadline_seconds=10), layout=layout)
            for name in samples
        }
        selection = dict(from_phase="screen", flow="librelane", physical_endpoint="physical_signoff",
                         target_endpoint="common_signoff")
        if baseline is not None:
            selection["baseline_arm"] = baseline
        if fallback_policy is not None:
            selection["fallback_policy"] = fallback_policy
        plan_data = dict(
            schema=bench.PLAN_SCHEMA, name="fallback-test", root=str(tmp),
            resources=dict(cpus=[0], memory_mib=256, swap_mib=0), quiet_host={}, budgets=dict(min_free_disk_gib=0),
            inputs={"manifest": dict(path="{root}/manifest.json", sha256=bench.sha256(tmp / "manifest.json"))},
            signoff=dict(argv=["true"], deadline_seconds=10, ownership_manifest="{inputs.manifest}",
                         gates=["drc", "antenna", "lvs", "timing"]),
            endpoints=dict(gds=[], physical_signoff=["drc", "antenna", "lvs"],
                           common_signoff=["drc", "antenna", "lvs", "timing"]),
            arms=arms,
            phases=dict(screen=dict(samples=list(samples)), confirm=dict(pair=["@selected", list(samples)[0]], blocks=3)),
            selection=selection,
        )
        plan_path = tmp / "plan.json"
        plan_path.write_text(json.dumps(plan_data))
        plan = bench.Plan(plan_path)
        for i, (arm, spec) in enumerate(samples.items(), 1):
            sid = f"screen-{i:02d}-{arm}"
            gates = {
                "drc": dict(verdict="passed" if spec["drc"] == 0 else "failed", violations=spec["drc"], wall_seconds=1.0),
                "antenna": dict(verdict="passed" if spec["antenna"] == 0 else ("unverified" if spec["antenna"] is None else "failed"),
                                violations=spec["antenna"], wall_seconds=1.0),
                "lvs": dict(verdict=spec.get("lvs", "passed"), wall_seconds=1.0),
                "timing": dict(verdict="failed", wall_seconds=1.0),
            }
            endpoints = {
                "gds": dict(seconds=spec["gds"], blocking={}),
                "physical_signoff": verdicts.endpoint(spec["gds"], gates, ["drc", "antenna", "lvs"]),
                "common_signoff": verdicts.endpoint(spec["gds"], gates, ["drc", "antenna", "lvs", "timing"]),
            }
            bench.write(ws / "samples" / sid / "sample.json", dict(
                schema=bench.SAMPLE_SCHEMA, id=sid, arm=arm, flow="librelane", role="reference",
                block=None, status="complete", endpoints=endpoints, signoff=dict(gates=gates), flags=[],
            ))
        return plan, ws

    def test_default_fallback_rejects_physical_regression_from_baseline(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            plan, ws = self._write_workspace(tmp, {
                "ll-recipe": dict(gds=1730.6, drc=0, antenna=13),
                "ll-tuned": dict(gds=2312.5, drc=0, antenna=8),
                "ll-jumpers": dict(gds=1371.4, drc=0, antenna=105),
                "ll-unverified": dict(gds=1100.0, drc=0, antenna=None),
                "ll-bad-lvs": dict(gds=1200.0, drc=0, antenna=10, lvs="failed"),
            })
            sel = bench.cmd_select(plan, ws, "empty")
            self.assertEqual(sel["selected"], "ll-recipe")
            self.assertEqual(sel["fallback_policy"], "no_physical_regression")
            by_arm = {c["arm"]: c for c in sel["candidates"]}
            self.assertTrue(by_arm["ll-recipe"]["eligible_no_physical_regression"])
            self.assertTrue(by_arm["ll-tuned"]["eligible_no_physical_regression"])
            self.assertFalse(by_arm["ll-jumpers"]["eligible_no_physical_regression"])
            self.assertFalse(by_arm["ll-unverified"]["eligible_no_physical_regression"])
            self.assertFalse(by_arm["ll-bad-lvs"]["eligible_no_physical_regression"])

    def test_default_fallback_picks_faster_non_regressing_arm(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            plan, ws = self._write_workspace(tmp, {
                "ll-recipe": dict(gds=1730.6, drc=0, antenna=13),
                "ll-better": dict(gds=1500.0, drc=0, antenna=10),
                "ll-jumpers": dict(gds=1371.4, drc=0, antenna=105),
            })
            sel = bench.cmd_select(plan, ws, "empty")
            self.assertEqual(sel["selected"], "ll-better")

    def test_legacy_fastest_gds_fallback_opt_in(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            plan, ws = self._write_workspace(tmp, {
                "ll-recipe": dict(gds=1730.6, drc=0, antenna=13),
                "ll-jumpers": dict(gds=1371.4, drc=0, antenna=105),
            }, fallback_policy="fastest_gds")
            sel = bench.cmd_select(plan, ws, "empty")
            self.assertEqual(sel["selected"], "ll-jumpers")

    def test_no_baseline_minimizes_physical_violations_then_time(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            plan, ws = self._write_workspace(tmp, {
                "ll-slow-clean": dict(gds=2300.0, drc=0, antenna=8),
                "ll-fast-clean": dict(gds=2100.0, drc=0, antenna=8),
                "ll-jumpers": dict(gds=1370.0, drc=0, antenna=105),
            }, baseline=None)
            sel = bench.cmd_select(plan, ws, "empty")
            self.assertEqual(sel["selected"], "ll-fast-clean")

    def test_invalid_fallback_policy_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            with self.assertRaises(bench.BenchError) as raised:
                self._write_workspace(tmp, {"ll-recipe": dict(gds=100.0, drc=0, antenna=1)},
                                      fallback_policy="bogus")
            self.assertEqual(raised.exception.code, "PLAN_SELECTION")


if __name__ == "__main__":
    unittest.main()
