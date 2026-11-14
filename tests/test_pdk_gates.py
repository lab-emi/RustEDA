"""Profile-driven signoff gates (SKY130, GF180MCU): parsers, policies and profile consistency.

No referee tool and no PDK tree is used: every fixture is a few lines of text written by the test.
The controls fail when a gate would accept what it must refuse (a foreign die, a moved macro, an
unpinned or changed file, a limit row at a standard-cell pin, a short black-box interface) or when
a profile names a file it does not pin.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GATES = ROOT / "integration/ibex_pdks/gates"
sys.path.insert(0, str(GATES))
import common  # noqa: E402
import density  # noqa: E402
import logic  # noqa: E402
import magic_antenna  # noqa: E402
import magic_drc  # noqa: E402
import netgen_lvs  # noqa: E402
import profile_pins  # noqa: E402
import timing  # noqa: E402

LEF = """VERSION 5.7 ;
MACRO RAM
  CLASS BLOCK ;
  SIZE 100.0 BY 50.0 ;
  PIN A[0]
    DIRECTION INPUT ;
  END A[0]
  PIN VDD
    DIRECTION INOUT ;
    USE POWER ;
  END VDD
END RAM
MACRO inv
  CLASS core ;
  SIZE 2.0 BY 4.0 ;
  PIN I
    DIRECTION INPUT ;
    USE SIGNAL ;
  END I
END inv
MACRO fill
  CLASS core SPACER ;
  SIZE 1.0 BY 4.0 ;
END fill
MACRO tap
  CLASS CORE WELLTAP ;
  SIZE 1.0 BY 4.0 ;
END tap
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lef = self.root / "cells.lef"
        self.lef.write_text(LEF)

    def write_def(self, units=2000, die=(0, 0, 400000, 400000), ram=("u_ram", 20000, 20000, "FS"), extra=()):
        rows = [f"- {ram[0]} RAM + FIXED ( {ram[1]} {ram[2]} ) {ram[3]} ;",
                "- t0 tap + SOURCE DIST + FIXED ( 0 0 ) N ;"]
        rows += [f"- c{i} inv + PLACED ( 0 0 ) FN ;" for i in range(100)]
        rows += [f"- f{i} fill + SOURCE DIST + PLACED ( 0 0 ) S ;" for i in range(50)]
        rows += list(extra)
        path = self.root / "design.def"
        path.write_text(f"VERSION 5.8 ;\nDESIGN top ;\nUNITS DISTANCE MICRONS {units} ;\n"
                        f"DIEAREA ( {die[0]} {die[1]} ) ( {die[2]} {die[3]} ) ;\nCOMPONENTS {len(rows)} ;\n"
                        + "\n".join(rows) + "\nEND COMPONENTS\nEND DESIGN\n")
        return path

    def plan(self):
        return dict(die_um=[0, 0, 200, 200], macro_halo_um=10,
                    macros=dict(u_ram=dict(cell="RAM", location_um=[10, 10], orientation="FS")))


class DensityGate(Fixture):
    def test_classes_and_units_come_from_the_files(self):
        result = density.utilization(self.write_def(), [self.lef], self.plan(), 70, "top")
        scale = 2000 * 2000
        self.assertEqual(result["dbu_per_um"], 2000)
        self.assertEqual(result["class_count"], dict(macro=1, filler=50, tap=1, endcap=0, antenna=0, tie=0, logic=100))
        self.assertEqual(result["area_dbu2"]["final_nonfiller_standard_cells"], (100 * 8 + 4) * scale)
        self.assertEqual(result["area_dbu2"]["reported_separately_filler_decap"], 50 * 4 * scale)
        # halo box: macro 100 x 50 at (10, 10) grown by 10 um and clipped to the die
        self.assertEqual(result["area_dbu2"]["macro_halos_inclusive"], 120 * 70 * scale)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["excluded_gap_fill_masters"], ["fill"])

    def test_limit_fails(self):
        self.assertEqual(density.utilization(self.write_def(), [self.lef], self.plan(), 2, "top")["status"], "failed")

    def test_foreign_die_and_moved_macro_are_refused(self):
        with self.assertRaisesRegex(common.GateError, "DEF_DIE_DIFFERS"):
            density.utilization(self.write_def(die=(0, 0, 400000, 300000)), [self.lef], self.plan(), 70, "top")
        with self.assertRaisesRegex(common.GateError, "MACRO_PLACEMENT_DIFFERS"):
            density.utilization(self.write_def(ram=("u_ram", 20000, 22000, "FS")), [self.lef], self.plan(), 70, "top")
        with self.assertRaisesRegex(common.GateError, "MACRO_PLACEMENT_DIFFERS"):
            density.utilization(self.write_def(ram=("u_ram", 20000, 20000, "N")), [self.lef], self.plan(), 70, "top")
        with self.assertRaisesRegex(common.GateError, "MACRO_INSTANCE_SET_DIFFERS"):
            density.utilization(self.write_def(extra=["- u_more RAM + FIXED ( 0 0 ) N ;"]), [self.lef], self.plan(), 70, "top")

    def test_overlapping_halos_count_once(self):
        self.assertEqual(density.union_area([[0, 0, 10, 10], [5, 0, 15, 10]]), 150)
        self.assertEqual(density.union_area([[0, 0, 10, 10], [20, 20, 30, 30]]), 200)

    def test_rotated_macro_box(self):
        component = dict(x=0, y=0, orientation="W")
        self.assertEqual(common.placed_box(component, common.lef_macros([self.lef])["RAM"]["size"], 1000), [0, 0, 50000, 100000])


class ProfilePins(Fixture):
    def profile(self, sha):
        path = self.root / "gates.json"
        path.write_text(json.dumps(dict(schema=common.PROFILE_SCHEMA, files={"cells.lef": sha})))
        return common.Profile(path, self.root)

    def test_pinned_file_is_returned_and_a_changed_file_is_refused(self):
        sha = common.identity(self.lef)["sha256"]
        self.assertEqual(self.profile(sha).file("cells.lef")["sha256"], sha)
        with self.assertRaisesRegex(common.GateError, "PROFILE_PIN_MISMATCH"):
            self.profile("0" * 64).file("cells.lef")
        with self.assertRaisesRegex(common.GateError, "PROFILE_UNPINNED_FILE"):
            self.profile(sha).file("other.lef")

    def test_unpinned_include_is_refused(self):
        (self.root / "decks").mkdir()
        (self.root / "decks/a.drc").write_text("a")
        (self.root / "decks/b.drc").write_text("b")
        path = self.root / "gates.json"
        path.write_text(json.dumps(dict(schema=common.PROFILE_SCHEMA, files={"decks/a.drc": "x"})))
        with self.assertRaisesRegex(common.GateError, "PROFILE_INVENTORY_DIFFERS"):
            common.Profile(path, self.root).matching(["decks/*.drc"])

    def test_tracked_profiles_pin_every_file_they_name(self):
        for name in ("gf180mcu", "sky130hd"):
            path = ROOT / "integration/ibex_pdks" / name / "gates.json"
            data = json.loads(path.read_text())
            profile = common.Profile.__new__(common.Profile)
            profile.data, profile.expand = data, lambda globs: []
            named = profile_pins.consumed(profile)
            self.assertFalse(set(named) - set(data["files"]), name)
            self.assertTrue(all(len(sha) == 64 for sha in data["files"].values()))
            self.assertEqual(data["top"], "ibex_sram_bench")
            self.assertTrue((path.parent / "tb.sv").is_file())
            reference = json.loads((path.parent / "profile.json").read_text())
            self.assertEqual(list(data["macros"]), [reference["macro"]["name"]])
            self.assertEqual(data["macros"][reference["macro"]["name"]]["instances"], reference["macro"]["instances"])


class TimingPolicy(Fixture):
    def test_design_limits_and_refused_scopes(self):
        sdc = self.root / "c.sdc"
        sdc.write_text("create_clock -name c -period 100 [get_ports c]\nset_max_fanout 10.0000 [current_design]\n")
        self.assertEqual(timing.sdc_design_limits(sdc), dict(fanout=10.0))
        sdc.write_text("set_max_transition 0.5 [get_ports a]\n")
        with self.assertRaisesRegex(common.GateError, "SDC_LIMIT_SCOPE_UNSUPPORTED"):
            timing.sdc_design_limits(sdc)

    def test_macro_pin_rows_do_not_gate_unless_beyond_the_sdc(self):
        rows = [dict(pin="u_ram/dout0[3]", kind="max_capacitance", value=0.05, limit=0.0276, slack=-0.02),
                dict(pin="u_ram/dout0[4]", kind="fanout", value=6.0, limit=4.0, slack=-2.0),
                dict(pin="u_ram/dout0[5]", kind="fanout", value=12.0, limit=4.0, slack=-8.0),
                dict(pin="_123_/X", kind="slew", value=2.0, limit=1.5, slack=-0.5),
                dict(pin="clk_i", kind="max_transition", value=2.0, limit=1.5, slack=-0.5),
                dict(pin="u_ram2/A[0]", kind="slew", value=2.0, limit=1.5, slack=-0.5)]
        result = timing.split_rows(rows, {"u_ram"}, dict(fanout=10.0))
        self.assertEqual((result["cap"]["total"], result["cap"]["gating"], result["cap"]["macro_pin"]), (1, 0, 1))
        self.assertEqual((result["fanout"]["total"], result["fanout"]["gating"], result["fanout"]["macro_pin_gating"]), (2, 1, 1))
        # a standard-cell pin, a port and a pin of an instance that is not a profile macro all gate
        self.assertEqual((result["slew"]["total"], result["slew"]["gating"], result["slew"]["macro_pin"]), (3, 3, 0))


class LvsFacts(Fixture):
    def test_subcircuits_ports_and_uses(self):
        path = self.root / "layout.spice"
        path.write_text(".subckt RAM A[0] A[1]\n+ VDD VSS\n.ends\n.subckt inv I ZN VDD VSS\nX0 ZN I VSS VSS nfet w=1 l=1\n.ends\n"
                        ".subckt top a VDD VSS\nXu_ram a n1 VDD VSS RAM\nXc0 a n1\n+ VDD VSS inv\n.ends\n")
        found = netgen_lvs.subcircuits(path)
        self.assertEqual(found["RAM"]["ports"], ["A[0]", "A[1]", "VDD", "VSS"])
        self.assertEqual(found["RAM"]["cards"], 0)
        self.assertEqual(dict(found["top"]["uses"]), dict(RAM=1, inv=1))
        self.assertEqual(found["inv"]["cards"], 1)

    def test_netgen_verdict_needs_a_clean_unique_match(self):
        clean = json.dumps([dict(name=["top", "top"], devices=[[["inv", 2], ["RAM", 1]], [["inv", 2], ["RAM", 1]]],
                                 nets=[5, 5], pins=[["a", "VDD"], ["a", "VDD"]])])
        table = ("Subcircuit pins:\nCircuit 1: RAM                             |Circuit 2: RAM                             \n"
                 "-------------------------------------------|-------------------------------------------\n"
                 "A[0]                                       |A[0]                                       \n"
                 "VDD                                        |VDD                                        \n"
                 "---------------------------------------------------------------------------------------\n"
                 "Cell pin lists are equivalent.\nDevice classes RAM and RAM are equivalent.\n")
        good = "Cell tie (0) disconnected node: VNB\n" + table + "Final result: Circuits match uniquely.\n"
        facts = netgen_lvs.netgen_facts(clean, good, "top")
        self.assertEqual((facts["problems"], facts["top_devices"][0]["RAM"], facts["library_cells_with_unconnected_pins"]),
                         ([], 1, {"tie": 1}))
        self.assertEqual(netgen_lvs.black_box_pins(good, "RAM"), (["A[0]", "VDD"], ["A[0]", "VDD"], True))
        self.assertFalse(netgen_lvs.black_box_pins(good.replace("lists are equivalent", "lists altered to match"), "RAM")[2])
        self.assertIsNone(netgen_lvs.black_box_pins(good, "OTHER"))
        self.assertTrue(netgen_lvs.netgen_facts(clean, good + "Cell top (0) disconnected node: a\n", "top")["problems"])
        for text in (good + "Property errors were found.\n", good.replace("uniquely.", "uniquely with port errors."),
                     good + "a |(no matching pin)\n", "Final result: Netlists do not match.\n"):
            self.assertTrue(netgen_lvs.netgen_facts(clean, text, "top")["problems"], text)
        swapped = json.dumps([dict(name=["top", "top"], devices=[[["inv", 2]], [["inv", 2]]], nets=[5, 5],
                                   badnets=[["n1", "n2"]], pins=[["a"], ["a"]])])
        self.assertTrue(netgen_lvs.netgen_facts(swapped, good, "top")["problems"])
        self.assertTrue(netgen_lvs.netgen_facts(clean, good, "other")["problems"])

    def test_reference_module_list(self):
        path = self.root / "ref.v"
        path.write_text("// module ghost\nmodule top (a);\n input a;\nendmodule\n")
        self.assertEqual(netgen_lvs.verilog_modules(path), ["top"])


class MagicParsers(Fixture):
    def test_drc_listing_counts_boxes_per_rule(self):
        path = self.root / "errors.txt"
        path.write_text("R Metal1 spacing < 0.14um (met1.2)\nB 0 0 18 200\nB 28 0 46 200\n"
                        "R N-diff distance to P-tap must be < 15.0um (LU.2)\nB 10 10 20 20\n")
        total, categories, samples = magic_drc.parse_listing(path, 0.005)
        self.assertEqual((total, categories), (3, {"LU.2": 1, "met1.2": 2}))
        self.assertEqual(samples["met1.2"][0], [0.0, 0.0, 0.09, 1.0])

    def test_antenna_violation_format(self):
        log = ("Running antenna checks.\nCell: sky130_fd_sc_hd__inv_1_0\nAntenna violation detected at plane metal2\n"
               "Effective antenna ratio 492.425 > limit 400\nGate rect (1212 647) to (1242 777)\n"
               "Antenna rect (1160 810) to (1218 813)\nantennacheck finished.\n")
        rows = list(magic_antenna.VIOLATION.finditer(log))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0][2], float(rows[0][3]), float(rows[0][4])), ("metal2", 492.425, 400.0))

    def test_magic_facts_require_abstract_macros(self):
        log = "GATE_TECH sky130A 1\nGATE_ABSTRACT RAM TRUE\nGATE_COUNT 0\nGATE_DONE\n"
        self.assertEqual(common.magic_facts(log, ["RAM"])["count"], "0")
        with self.assertRaisesRegex(common.GateError, "MACRO_NOT_ABSTRACT"):
            common.magic_facts(log.replace("RAM TRUE", "RAM "), ["RAM"])
        with self.assertRaisesRegex(common.GateError, "MAGIC_RUN_INCOMPLETE"):
            common.magic_facts(log.replace("GATE_DONE\n", ""), ["RAM"])


class LogicCut(Fixture):
    def cut(self, body, exposed=frozenset()):
        rtl = self.root / "design.v"
        rtl.write_text("module sub (x);\n input x;\nendmodule\nmodule top (\n\tclk,\n\td,\n\tq\n);\n\tinput wire clk;\n"
                       "\tinput wire [7:0] d;\n\toutput wire [7:0] q;\n" + body + "endmodule\n")
        job = logic.Job.__new__(logic.Job)
        job.c = dict(rtl=dict(path=str(rtl), sha256="x", bytes=1), macro_instances=["u_m"])
        job.top, job.macro = "top", "RAM"
        ports = dict(CLK=("input", 1), D=("input", 8), Q=("output", 8), Q2=("output", 8))
        target = self.root / "cut.v"
        audit = job.cut_original(ports, target, exposed)
        return target.read_text(), audit

    def test_every_functional_pin_becomes_a_port(self):
        text, audit = self.cut("\tRAM u_m(\n\t\t.CLK(clk),\n\t\t.D(d & 8'hf0),\n\t\t.Q(q),\n\t\t.Q2()\n\t);\n")
        self.assertIn("output wire [7:0] \\u_m.D ;", text)
        self.assertIn("assign \\u_m.D  = d & 8'hf0;", text)
        self.assertIn("assign q = \\u_m.Q ;", text)
        self.assertNotIn("u_m.Q2", text)          # unconnected output, not exposed by the netlist either
        self.assertNotIn("RAM u_m", text)
        self.assertEqual(len(audit["edits"]), 1)
        exposed, _ = self.cut("\tRAM u_m(\n\t\t.CLK(clk),\n\t\t.D(d),\n\t\t.Q(q),\n\t\t.Q2()\n\t);\n", {("u_m", "Q2")})
        self.assertIn("input wire [7:0] \\u_m.Q2 ;", exposed)

    def test_incomplete_or_foreign_instances_are_refused(self):
        with self.assertRaisesRegex(common.GateError, "RTL_MACRO_INPUT_UNCONNECTED"):
            self.cut("\tRAM u_m(\n\t\t.CLK(clk),\n\t\t.Q(q)\n\t);\n")
        with self.assertRaisesRegex(common.GateError, "RTL_MACRO_UNKNOWN_OR_SUPPLY_PIN"):
            self.cut("\tRAM u_m(\n\t\t.CLK(clk),\n\t\t.D(d),\n\t\t.Q(q),\n\t\t.VDD(clk)\n\t);\n")
        with self.assertRaisesRegex(common.GateError, "RTL_MACRO_INSTANCES"):
            self.cut("\tRAM u_other(\n\t\t.CLK(clk),\n\t\t.D(d),\n\t\t.Q(q)\n\t);\n")


class DriverDispatch(unittest.TestCase):
    def test_ihp_defaults_are_untouched_and_profiles_dispatch(self):
        source = (ROOT / "integration/ibex_host_benchmark/signoff_layout.py").read_text()
        self.assertIn('args.gates = "drc,antenna,latchup,extraction,timing,lvs,simulation,logic,density"', source)
        self.assertIn("pdk_signoff.main(args)", source)
        self.assertIn('TOP = "ibex_sram_bench"', source)
        self.assertIn('MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"', source)


if __name__ == "__main__":
    unittest.main()
