# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Unit tests for the clean single-binary non-PGO SKY130A producer and configs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKY_NATIVE = ROOT / "integration/ibex_pdks/sky130hd/native"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


clean_flow = load_module("clean_sky130_flow", SKY_NATIVE / "clean_sky130_flow.py")


class CleanSky130ConfigTests(unittest.TestCase):
    def test_clean_configs_have_no_manual_vertical_channels_and_fix_met3_spacing(self):
        physical = json.loads((SKY_NATIVE / "physical-clean.json").read_text())
        routing = json.loads((SKY_NATIVE / "routing-clean.json").read_text())
        profile = json.loads((SKY_NATIVE / "profile-clean.json").read_text())

        # Converged electrostatic density without design-specific vertical routing channels.
        self.assertEqual(physical["placer"]["electrostatic"]["target_density_milli"], 400)
        blockages = physical.get("placement_blockages", [])
        # Only the SRAM-band macro keepout remains (no vertical channel strips at x=507/526 um).
        self.assertEqual(len(blockages), 1)
        self.assertLess(blockages[0]["lx"], 100_000)
        self.assertGreater(blockages[0]["ux"], 700_000)

        # Generic bounded routing scout knobs.
        limits = routing["limits"]
        scouts = limits["scouts"]
        self.assertLessEqual(scouts["first_pass_expansions"], limits["max_hard_search_expansions"])
        self.assertGreaterEqual(scouts["first_pass_expansions"], 1)
        self.assertEqual(routing["track_router"]["qor"]["small_net_weight"], 2)

        # Clean profile enables aligned_selects, fixes met3 wide-OBS macro spacing, and pins clean configs.
        self.assertTrue(profile["synthesis"]["aligned_selects"])
        obs = profile["macros"][0]["obstruction_spacing_um"]
        self.assertEqual(obs["met3"], "0.40")
        self.assertEqual(profile["policies"]["physical"]["path"], "profile:physical-clean.json")
        self.assertEqual(profile["policies"]["routing"]["path"], "profile:routing-clean.json")

    def test_clean_paired_plan_and_horizontal_repair_are_generalized(self):
        plan_text = (ROOT / "integration/paired_bench/plans/sky130-clean-single-binary.json").read_text()
        plan = json.loads(plan_text)
        self.assertNotIn("ppa-closure", plan_text)
        self.assertEqual(plan["phases"]["confirm"]["pair"], ["librelane-recipe", "rusteda-clean"])
        self.assertEqual(plan["phases"]["confirm"]["blocks"], 3)
        for key in ("rusteda_bin", "rustsys_bin", "ruststa_bin", "rustroad_bin"):
            self.assertIn("/.work/target/", plan["inputs"][key]["path"])

        comp_text = (SKY_NATIVE / "horizontal_repair/components.py").read_text()
        scout_text = (SKY_NATIVE / "horizontal_repair/scout.py").read_text()
        rm_text = (SKY_NATIVE / "horizontal_repair/ranked_metal.py").read_text()
        gen_text = (SKY_NATIVE / "horizontal_repair/generate.py").read_text()
        self.assertNotIn("u_dmem", comp_text)
        self.assertNotIn("u_imem", comp_text)
        self.assertNotIn("assert len(fixed_macro_boxes)==2", comp_text)
        self.assertNotIn("u_dmem", scout_text)
        self.assertNotIn("== 2", rm_text)
        self.assertNotIn("==2", gen_text)

    def test_clean_paired_plan_and_horizontal_repair_generalization(self):
        plan_path = ROOT / "integration/paired_bench/plans/sky130-clean-single-binary.json"
        plan = json.loads(plan_path.read_text())
        self.assertEqual(plan["phases"]["confirm"]["pair"], ["librelane-recipe", "rusteda-clean"])
        self.assertEqual(plan["phases"]["confirm"]["blocks"], 3)
        # Ensure zero PGO / .work/ppa-closure binaries are referenced in inputs or producer argv.
        for inp_name, inp in plan["inputs"].items():
            p = inp["path"].lower()
            self.assertNotIn("ppa-closure", p, inp_name)
            self.assertNotIn("pgo", p, inp_name)
        for arg in plan["arms"]["rusteda-clean"]["producer"]["argv"]:
            self.assertNotIn("ppa-closure", arg.lower())
            self.assertNotIn("pgo", arg.lower())

        # Ensure horizontal_repair helpers no longer hardcode Ibex macro names or 2-CPU affinity.
        for name in ("scout.py", "components.py"):
            src = (SKY_NATIVE / "horizontal_repair" / name).read_text()
            self.assertNotIn("u_dmem", src)
            self.assertNotIn("u_imem", src)
        for name in ("ranked_metal.py", "generate.py"):
            src = (SKY_NATIVE / "horizontal_repair" / name).read_text()
            self.assertNotIn("sched_getaffinity(0)) == 2", src)
            self.assertNotIn("sched_getaffinity(0))==2", src)


class CleanSky130HelperTests(unittest.TestCase):
    def test_blocked_nets_from_native_drv_uses_relative_cap_headroom(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            report = {
                "sections": {
                    "drv": {
                        "rows": [
                            # Marginal net like g481/X: limit=0.195 pF -> req=0.002+0.035*0.195=0.008825 pF.
                            {"net": "n_marginal_cap", "kind": "max_capacitance", "corner": "ss", "limit": 0.195, "slack": 0.007},
                            # Safe net: limit=0.100 pF -> req=0.0055 pF, slack=0.010 pF -> admitted.
                            {"net": "n_safe_cap", "kind": "max_capacitance", "corner": "ss", "limit": 0.100, "slack": 0.010},
                            # Tight fanout -> blocked.
                            {"net": "n_tight_fo", "kind": "max_fanout", "corner": "tt", "limit": 10.0, "slack": 0.5},
                            # Safe fanout -> admitted.
                            {"net": "n_safe_fo", "kind": "max_fanout", "corner": "tt", "limit": 10.0, "slack": 1.0},
                            # Tight slew -> blocked.
                            {"net": "n_tight_slew", "kind": "max_transition", "corner": "ss", "limit": 1.5, "slack": 0.020},
                            # Safe slew -> admitted.
                            {"net": "n_safe_slew", "kind": "max_transition", "corner": "ss", "limit": 1.5, "slack": 0.050},
                        ]
                    }
                }
            }
            (logs / "check_ss-drv.stdout").write_text(json.dumps(report))
            blocked = clean_flow.blocked_nets_from_native_drv(logs)
            self.assertIn("n_marginal_cap", blocked)
            self.assertNotIn("n_safe_cap", blocked)
            self.assertIn("n_tight_fo", blocked)
            self.assertNotIn("n_safe_fo", blocked)
            self.assertIn("n_tight_slew", blocked)
            self.assertNotIn("n_safe_slew", blocked)

    def test_positions_from_gaps_avoids_existing_cells_and_blockages(self):
        design_json = {
            "design": {
                "site": {"width": 460, "height": 2720},
                "rows": [
                    {"y": 0, "x_start": 0, "x_end": 4600},
                    {"y": 2720, "x_start": 0, "x_end": 4600},
                ],
            }
        }
        physical = {
            "cell_spacing_sites": 1,
            "placement_blockages": [{"lx": 0, "ly": 0, "ux": 2300, "uy": 2720}],
        }
        old_cells = {
            "u_existing": ("INV_X1", 2760, 0, "N"),
            "rr_tap_0": ("TAP_X1", 0, 2720, "FS"),
        }
        master_sizes = {
            "INV_X1": (920, 2720),
            "TAP_X1": (460, 2720),
            clean_flow.DIODE_CELL: (920, 2720),
        }
        ops = {
            "rr_eco_ant_0000": {"op": "insert_diode", "instance": "rr_eco_ant_0000", "cell": clean_flow.DIODE_CELL, "net": "n0"},
            "rr_eco_ant_0001": {"op": "insert_diode", "instance": "rr_eco_ant_0001", "cell": clean_flow.DIODE_CELL, "net": "n1"},
        }
        anchors = {
            "rr_eco_ant_0000": {"preferred_lower_left_dbu": [920, 0]},
            "rr_eco_ant_0001": {"preferred_lower_left_dbu": [1380, 2720]},
        }
        slots = clean_flow.positions_from_gaps(
            design_json,
            physical,
            old_cells,
            existing={"u_existing"},
            master_sizes=master_sizes,
            operations=ops,
            anchors=anchors,
        )
        # Row 0 has blockage [0, 2300) and u_existing with 1-site spacing [2300, 4140), leaving only 460 < 920.
        # So both diodes must land legally on Row 1 (y=2720), avoiding rr_tap_0 [0, 920) and each other + 1-site gap.
        self.assertEqual(slots["rr_eco_ant_0000"][1], 2720)
        self.assertEqual(slots["rr_eco_ant_0001"][1], 2720)
        x0, x1 = sorted([slots["rr_eco_ant_0000"][0], slots["rr_eco_ant_0001"][0]])
        self.assertGreaterEqual(x0, 920)
        self.assertGreaterEqual(x1 - (x0 + 920), 460)
        self.assertEqual(x0 % 460, 0)
        self.assertEqual(x1 % 460, 0)

    def test_select_diode_targets_filters_clock_drv_and_ranks_by_severity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lef = root / "cells.lef"
            lef.write_text(
                "\n".join(
                    [
                        "VERSION 5.8 ;",
                        "UNITS DATABASE MICRONS 1000 ; END UNITS",
                        "MACRO INV_1",
                        "  CLASS CORE ;",
                        "  SIZE 0.92 BY 2.72 ;",
                        "  PIN A",
                        "    DIRECTION INPUT ;",
                        "    ANTENNAGATEAREA 0.10 ;",
                        "    PORT",
                        "      LAYER li1 ;",
                        "      RECT 0.10 0.10 0.30 0.30 ;",
                        "    END",
                        "  END A",
                        "  PIN Y",
                        "    DIRECTION OUTPUT ;",
                        "    PORT",
                        "      LAYER li1 ;",
                        "      RECT 0.50 0.10 0.70 0.30 ;",
                        "    END",
                        "  END Y",
                        "END INV_1",
                        "END LIBRARY",
                    ]
                )
                + "\n"
            )
            def_file = root / "top.def"
            def_file.write_text(
                "\n".join(
                    [
                        "VERSION 5.8 ;",
                        "DESIGN top ;",
                        "UNITS DISTANCE MICRONS 1000 ;",
                        "COMPONENTS 3 ;",
                        "- u_drv INV_1 + PLACED ( 0 0 ) N ;",
                        "- u_rcv INV_1 + PLACED ( 2000 0 ) N ;",
                        "- rr_clkbuf_0 INV_1 + PLACED ( 4000 0 ) N ;",
                        "END COMPONENTS",
                        "PINS 1 ;",
                        "- clk_i + NET clk_i + DIRECTION INPUT ;",
                        "END PINS",
                        "NETS 4 ;",
                        "- n_ok ( u_drv Y ) ( u_rcv A ) ;",
                        "- n_blocked ( u_drv Y ) ( u_rcv A ) ;",
                        "- n_below ( u_drv Y ) ( u_rcv A ) ;",
                        "- clk_i ( PIN clk_i ) ( rr_clkbuf_0 A ) ;",
                        "END NETS",
                        "END DESIGN",
                    ]
                )
                + "\n"
            )
            rep = root / "part0.json"
            rep.write_text(
                json.dumps(
                    {
                        "selection": {
                            "coverage": [{"net": n} for n in ("n_ok", "n_blocked", "n_below", "clk_i")]
                        },
                        "checks": [
                            {
                                "net": "n_ok",
                                "layer": "met3",
                                "kind": "side_area",
                                "ratio": 380.0,
                                "limit": 400.0,
                                "metal_um2": 38.0,
                                "gates": ["u_rcv/A"],
                            },
                            {
                                "net": "n_blocked",
                                "layer": "via4",
                                "kind": "partial_weighted",
                                "ratio": 9.0,
                                "limit": 10.0,
                                "metal_um2": 1.0,
                                "gates": ["u_rcv/A"],
                            },
                            {
                                "net": "n_below",
                                "layer": "met3",
                                "kind": "side_area",
                                "ratio": 200.0,
                                "limit": 400.0,
                                "metal_um2": 20.0,
                                "gates": ["u_rcv/A"],
                            },
                            {
                                "net": "clk_i",
                                "layer": "met3",
                                "kind": "side_area",
                                "ratio": 500.0,
                                "limit": 400.0,
                                "metal_um2": 50.0,
                                "gates": ["rr_clkbuf_0/A"],
                            },
                        ],
                    }
                )
            )
            part_complete = root / "complete.json"
            part_complete.write_text(json.dumps({"reports": [{"report": str(rep)}]}))

            plan, anchors, stats = clean_flow.select_diode_targets(
                part_complete,
                def_file,
                lef,
                blocked_drv={"n_blocked": ["ss:cap_slack=0.00100<0.00550"]},
                top="top",
            )
            self.assertEqual([op["net"] for op in plan["operations"]], ["n_ok"])
            self.assertEqual(stats["admitted_diodes"], 1)
            self.assertEqual(stats["rejected_count"], 2)
            self.assertIn("rr_eco_ant_0000", anchors)
            self.assertEqual(anchors["rr_eco_ant_0000"]["gate_instance"], "u_rcv")
            self.assertEqual(anchors["rr_eco_ant_0000"]["gate_pin"], "A")


if __name__ == "__main__":
    unittest.main()
