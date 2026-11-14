"""Profile-driven signoff gates (SKY130, GF180MCU): parsers, policies and profile consistency.

No PDK tree is used: every fixture is a few lines of text written by the test. One class runs the
marker attribution with KLayout on a generated two-macro layout and a three-line deck; it is
skipped where KLayout or GNU time is missing.
The controls fail when a gate would accept what it must refuse (a foreign die, a moved macro, an
unpinned or changed file, a limit row at a standard-cell pin, a short black-box interface) or when
a profile names a file it does not pin.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
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

    def test_host_map_rc_rules_must_be_the_pinned_file(self):
        import pdk_signoff
        rules = self.root / "rules.nom"
        rules.write_text("rules")
        path = self.root / "gates.json"
        path.write_text(json.dumps(dict(schema=common.PROFILE_SCHEMA, files={"rules.nom": common.identity(rules)["sha256"]},
                                        gates=dict(extraction=dict(rules="rules.nom")))))
        profile = common.Profile(path, self.root)
        self.assertEqual(pdk_signoff.rc_rules(profile, {})["path"], str(rules))
        copy = self.root / "copy.nom"
        copy.write_text("rules")
        self.assertEqual(pdk_signoff.rc_rules(profile, dict(rc_rules=str(copy)))["path"], str(copy))
        copy.write_text("other rules")
        with self.assertRaises(SystemExit) as raised:
            pdk_signoff.rc_rules(profile, dict(rc_rules=str(copy)))
        self.assertIn("RC_RULES_DIFFER_FROM_PROFILE", str(raised.exception))

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
        # Netgen cuts long names in the header to the column width
        long = "gf180mcu_fd_ip_sram__sram512x8m8wm1"
        cut = good.replace("Circuit 1: RAM                             |Circuit 2: RAM                             ",
                           "Circuit 1: gf180mcu_fd_ip_sram__sram512x8m |Circuit 2: gf180mcu_fd_ip_sram__sram512x8m ")
        cut = cut.replace("Device classes RAM and RAM are equivalent.", f"Device classes {long} and {long} are equivalent.")
        self.assertEqual(netgen_lvs.black_box_pins(cut, long), (["A[0]", "VDD"], ["A[0]", "VDD"], True))
        self.assertIsNone(netgen_lvs.black_box_pins(cut, "gf180mcu_fd_ip_sram__sram512x8m8wm9"))
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
        self.assertIn("output wire \\u_m.CLK ;", text)   # one-bit ports are scalars
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


KLAYOUT = os.environ.get("RUSTEDA_TEST_KLAYOUT") or shutil.which("klayout")
GNU_TIME = os.environ.get("RUSTEDA_TEST_GNU_TIME") or shutil.which("time")
BUILD = """
import pya
ly = pya.Layout(); ly.dbu = 0.001
m1 = ly.layer(1, 0)
macro = ly.create_cell("RAM")
macro.shapes(m1).insert(pya.Box(0, 0, 1000, 1000))
macro.shapes(m1).insert(pya.Box(1050, 0, 2050, 1000))
macro.shapes(m1).insert(pya.Box(0, 5000, 1000, 6000))
if int(altered):
    macro.shapes(m1).insert(pya.Box(3000, 3000, 4000, 4000))
import os
if not os.path.exists(macro_out):   # a GDS carries its write time: keep the bytes of the first write
    ly.write(macro_out)
top = ly.create_cell("top")
top.insert(pya.CellInstArray(macro.cell_index(), pya.Trans(pya.Vector(0, 0))))
top.insert(pya.CellInstArray(macro.cell_index(), pya.Trans(1, False, pya.Vector(20000, 20000))))
if int(variant) >= 1:
    top.shapes(m1).insert(pya.Box(30000, 0, 31000, 1000))
    top.shapes(m1).insert(pya.Box(31050, 0, 32050, 1000))
if int(variant) >= 2:
    top.shapes(m1).insert(pya.Box(0, 6050, 1000, 7050))
ly.write(top_out)
"""
DECK = """
source($input, $top_cell)
report("control deck", $report)
deep
input(1, 0).space(0.1.um).output("space", "spacing below 0.1 um")
"""


@unittest.skipUnless(KLAYOUT and GNU_TIME, "needs KLayout and GNU time (RUSTEDA_TEST_KLAYOUT, RUSTEDA_TEST_GNU_TIME)")
class MacroAttribution(Fixture):
    """A three-line deck on a two-macro layout: the attribution rule itself, with the real geometry engine."""

    def layout(self, variant, altered=0):
        script = self.root / "build.py"
        script.write_text(BUILD)
        macro, top = self.root / f"macro{altered}.gds", self.root / f"top{variant}{altered}.gds"
        subprocess.run([KLAYOUT, "-b", "-r", str(script), "-rd", f"macro_out={macro}", "-rd", f"top_out={top}",
                        "-rd", f"variant={variant}", "-rd", f"altered={altered}"], check=True, capture_output=True)
        return macro, top

    def run_gate(self, mode, gds, top, baseline=None, macro=None):
        deck = self.root / "control.drc"
        deck.write_text(DECK)
        plan = dict(schema="rusteda.pdk-gate.klayout-drc.v1", mode=mode, top=top, gds=common.identity(gds),
                    deck=common.identity(deck), deck_files=[], switches=[], top_switch="top_cell", threads_switch="thr",
                    threads=1, timeout_seconds=120,
                    tools=dict(klayout=common.identity(KLAYOUT), gnu_time=common.identity(GNU_TIME)))
        if baseline is not None:
            plan["macros"] = dict(RAM=dict(gds=common.identity(macro), instances=2, baseline=dict(
                report=common.identity(baseline / "drc.lyrdb"), summary=common.identity(baseline / "summary.json"))))
        work = Path(tempfile.mkdtemp(dir=self.root))
        path = work.with_suffix(".json")
        path.write_text(json.dumps(plan))
        subprocess.run([sys.executable, str(GATES / "klayout_drc.py"), "--inputs", str(path), "--workspace", str(work),
                        "--base-rev", "empty"], capture_output=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        return work, json.loads((work / "summary.json").read_text())

    def test_macro_markers_are_attributed_and_every_other_marker_gates(self):
        macro, clean = self.layout(0)
        baseline, recorded = self.run_gate("baseline", macro, "RAM")
        self.assertEqual((recorded["status"], recorded["items"]), ("recorded", 1))
        _, result = self.run_gate("layout", clean, "top", baseline, macro)
        self.assertEqual((result["status"], result["violations"], result["macro_interior"],
                          result["macro_baseline_expected"], result["macro_baseline_reproduced"]), ("passed", 0, 2, 2, 2))
        _, far = self.run_gate("layout", self.layout(1)[1], "top", baseline, macro)
        self.assertEqual((far["status"], far.get("violations"), far.get("macro_interior"), far.get("error")), ("failed", 1, 2, None))
        # a top-level wire 0.05 um from a macro shape, inside the macro's bounding box, gates as well
        _, near = self.run_gate("layout", self.layout(2)[1], "top", baseline, macro)
        self.assertEqual((near["status"], near["violations"], near["macro_interior"]), ("failed", 2, 2))
        self.assertEqual(near["gating_rules"], {"space": 2})

    def test_altered_macro_and_foreign_baseline_are_refused(self):
        macro, _ = self.layout(0)
        baseline, _ = self.run_gate("baseline", macro, "RAM")
        _, altered = self.run_gate("layout", self.layout(0, altered=1)[1], "top", baseline, macro)
        self.assertEqual(altered["status"], "failed")
        self.assertIn("MACRO_GEOMETRY_DIFFERS_FROM_ITS_GDS", altered["error"])
        other_macro, other_top = self.layout(0, altered=1)
        _, foreign = self.run_gate("layout", other_top, "top", baseline, other_macro)
        self.assertIn("MACRO_BASELINE_DIFFERS", foreign["error"])


CONTROL_LAYOUT = """
import pya
ly = pya.Layout(); ly.dbu = 0.001
m2, v2 = ly.layer(36, 0), ly.layer(38, 0)
ram = ly.create_cell("RAM")
sub = ly.create_cell("RAM_bit")
sub.shapes(m2).insert(pya.Box(0, 0, 100, 100))
ram.insert(pya.CellInstArray(sub.cell_index(), pya.Trans(pya.Vector(10, 10))))
ram.shapes(m2).insert(pya.Box(0, 0, 50000, 50000))
diode = ly.create_cell("DIODE")
diode.shapes(m2).insert(pya.Box(0, 0, 500, 500))
top = ly.create_cell("top")
top.insert(pya.CellInstArray(ram.cell_index(), pya.Trans(pya.Vector(100000, 100000))))
for x in (0, 2000, 4000):
    top.insert(pya.CellInstArray(diode.cell_index(), pya.Trans(pya.Vector(x, 90000))))
top.shapes(m2).insert(pya.Box(110000, 110000, 110280, 140000))   # long wire over the macro: must not be chosen
top.shapes(m2).insert(pya.Box(5000, 0, 5280, 30000))             # long wire: the stub candidate
top.shapes(m2).insert(pya.Box(0, 0, 280, 1000))                  # short wire: not a candidate
top.shapes(v2).insert(pya.Box(5010, 100, 5270, 360))             # single cut
top.shapes(v2).insert(pya.Box(1000, 5000, 1260, 5260))           # pair of cuts 0.26 um apart: not single
top.shapes(v2).insert(pya.Box(1520, 5000, 1780, 5260))
ly.write(out_path)
"""
FACTS = """
import json, pya
ly = pya.Layout(); ly.read(gds_path)
top = ly.cell("top")
m2, v2 = ly.find_layer(36, 0), ly.find_layer(38, 0)
facts = dict(cells=sorted(c.name for c in ly.each_cell()),
             m2=sorted([s.box.left, s.box.bottom, s.box.right, s.box.top] for s in top.shapes(m2).each()),
             v2=sorted([s.box.left, s.box.bottom, s.box.right, s.box.top] for s in top.shapes(v2).each()),
             instances=sorted(i.cell.name for i in top.each_inst()),
             ram_empty=ly.cell("RAM").is_empty() if ly.cell("RAM") else None)
json.dump(facts, open(out_path, "w"))
"""


MACRO_ONLY = """
import pya
ly = pya.Layout(); ly.read(gds_path)
keep = ly.cell("RAM")
for cell in [c for c in ly.each_cell() if c.cell_index() not in keep.called_cells() and c.cell_index() != keep.cell_index()]:
    cell.delete()
if variant == "other":
    keep.name = "OTHER"
ly.write(out_path)
"""
CHANGE_MACRO = """
import pya
ly = pya.Layout(); ly.read(gds_path)
ram = ly.cell("RAM")
if variant == "empty":
    ram.prune_subcells()
    ram.clear()
else:
    ram.transform(pya.Trans(pya.Vector(5, 0)))
ly.write(out_path)
"""


@unittest.skipUnless(KLAYOUT, "needs KLayout (RUSTEDA_TEST_KLAYOUT)")
class LayoutEditors(Fixture):
    """The two GDS editing scripts on a generated layout."""

    def klayout(self, script, **variables):
        argv = [KLAYOUT, "-b", "-r", str(script)]
        for name, value in variables.items():
            argv += ["-rd", f"{name}={value}"]
        subprocess.run(argv, check=True, capture_output=True)

    def facts(self, gds):
        script, out = self.root / "facts.py", self.root / (Path(gds).name + ".json")
        script.write_text(FACTS)
        self.klayout(script, gds_path=gds, out_path=out)
        return json.loads(out.read_text())

    def setUp(self):
        super().setUp()
        build = self.root / "control_layout.py"
        build.write_text(CONTROL_LAYOUT)
        self.gds = self.root / "top.gds"
        self.klayout(build, out_path=self.gds)

    def macro_gds(self, variant=""):
        """The macro's own GDS: the RAM cell of the generated layout, optionally changed."""
        script, out = self.root / "macro.py", self.root / f"ram{variant}.gds"
        script.write_text(MACRO_ONLY)
        self.klayout(script, gds_path=self.gds, variant=variant or "same", out_path=out)
        return out

    def prune(self, gds, reference):
        out, record = self.root / "pruned.gds", self.root / "pruned.json"
        out.unlink(missing_ok=True)
        self.klayout(GATES / "prune_macros.py", gds_path=gds, top_name="top", references=f"RAM={reference}",
                     out_path=out, record_path=record)
        return out, json.loads(record.read_text())

    def test_prune_empties_the_macro_and_nothing_else(self):
        out, record = self.prune(self.gds, self.macro_gds())
        before, after = self.facts(self.gds), self.facts(out)
        self.assertEqual(record["status"], "identical_then_emptied")
        entry = record["masters"]["RAM"]
        self.assertEqual((entry["placements"], entry["private_cells_removed"], entry["differing_layer_count"], entry["empty"]),
                         (1, 1, 0, False))
        self.assertEqual(after["cells"], ["DIODE", "RAM", "top"])
        self.assertTrue(after["ram_empty"])
        self.assertEqual((after["m2"], after["v2"], after["instances"]), (before["m2"], before["v2"], before["instances"]))

    def test_an_empty_or_changed_macro_cell_is_refused(self):
        reference = self.macro_gds()
        for variant in ("empty", "shifted"):
            changed = self.root / f"top-{variant}.gds"
            script = self.root / "change.py"
            script.write_text(CHANGE_MACRO)
            self.klayout(script, gds_path=self.gds, variant=variant, out_path=changed)
            out, record = self.prune(changed, reference)
            self.assertEqual(record["status"], "failed", variant)
            self.assertFalse(out.exists(), variant)
            self.assertTrue(record["errors"][0].startswith("MACRO_DIFFERS_FROM_ITS_GDS: RAM"), record["errors"])
            self.assertEqual(record["masters"]["RAM"]["empty"], variant == "empty")
        _, record = self.prune(self.gds, self.macro_gds("other"))
        self.assertEqual(record["errors"], ["MACRO_REFERENCE_WITHOUT_MASTER: RAM"])

    def test_controls_change_exactly_what_they_record(self):
        out, record = self.root / "bad.gds", self.root / "bad.json"
        self.klayout(GATES / "negative_controls.py", gds_path=self.gds, top_name="top", out_path=out, record_path=record,
                     ops="stub,via,diodes", wire_layer="36/0", cut_layer="38/0", diode_cells="DIODE", avoid="RAM")
        before, after, changes = self.facts(self.gds), self.facts(out), json.loads(record.read_text())["operations"]
        stub, via, diodes = changes
        self.assertEqual((stub["wire_um"], stub["stub_um"], stub["stub_area_um2"]),
                         ([5.0, 0.0, 5.28, 30.0], [5.0, 14.86, 5.28, 15.14], 0.0784))
        self.assertEqual(via["removed_cut_um"], [5.01, 0.1, 5.27, 0.36])
        self.assertEqual(diodes["removed_instances"], 3)
        self.assertEqual(after["instances"], ["RAM"])
        self.assertEqual(len(after["v2"]), len(before["v2"]) - 1)
        self.assertIn([110000, 110000, 110280, 140000], after["m2"])      # the wire over the macro is untouched
        self.assertIn([5000, 14860, 5280, 15140], after["m2"])
        self.assertNotIn([5000, 0, 5280, 30000], after["m2"])


class DriverDispatch(unittest.TestCase):
    def test_ihp_defaults_are_untouched_and_profiles_dispatch(self):
        source = (ROOT / "integration/ibex_host_benchmark/signoff_layout.py").read_text()
        self.assertIn('args.gates = "drc,antenna,latchup,extraction,timing,lvs,simulation,logic,density"', source)
        self.assertIn('rules_nom=identity(host.get("rc_rules") or ihp / "libs.tech/librelane/openrcx/IHP_rcx_patterns.rules")', source)
        self.assertIn("pdk_signoff.main(args)", source)
        self.assertIn('TOP = "ibex_sram_bench"', source)
        self.assertIn('MACRO = "RM_IHPSG13_1P_1024x32_c2_bm_bist"', source)


if __name__ == "__main__":
    unittest.main()
