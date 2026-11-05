#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Unit tests for the receipt's check accounting (`integration/ibex_host_benchmark/make_receipt.py`).

A shared gate is counted once: when the producer ran the identical foundry check on the same
final layout (`be_main`, `be_antenna`, `be_lvs`), its shared-gate time is not added again.
The fixtures mirror the files `signoff_drc.py` and `signoff_lvs.py` write.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from integration.ibex_host_benchmark import make_receipt as mr

GDS_SHA = "a" * 64
DECK = "/pdk/klayout/tech/drc/ihp-sg13g2.drc"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def drc_run(workspace, gds, table="main", deck_sha="d" * 64):
    deck = DECK if table == "main" else "/pdk/klayout/tech/drc/rule_decks/antenna.drc"
    switches = ["no_recommended=true"] if table == "main" else []
    command = ["/opt/eda/bin/eda-multicall", "klayout", "-b", "-r", deck]
    for item in ("threads=2", "run_mode=deep", *switches, "topcell=ibex_sram_bench", "input=" + gds,
                 "report=" + str(workspace / "drc.lyrdb"), "log=" + str(workspace / "deck.log")):
        command += ["-rd", item]
    write(workspace / "command.json", command)
    write(workspace / "summary.json", dict(
        status="passed", complete=True, violations=0, categories={}, engine_exit_code=0, deck_sha256=deck_sha,
        profile=dict(path="/pdk/provenance.json", sha256="p" * 64, bytes=1),
        gds=dict(path=gds, sha256=GDS_SHA, bytes=2), tool=dict(path="/opt/eda/bin/eda-multicall", sha256="t" * 64),
        disabled_rules=["recommended"] if table == "main" else [], scope="complete_foundry_" + table,
        wall_seconds=123.0, raw_report=dict(path=str(workspace / "drc.lyrdb"), sha256="r" * 64)))


def lvs_run(workspace, gds, deadline=3600, cdl_sha="c" * 64):
    sources = {k: dict(path="/x/" + k, sha256=k[:1] * 64) for k in mr.LVS_SOURCES}
    sources["gds"] = dict(path=gds, sha256=GDS_SHA)
    sources.update(cdl_0=dict(path="/x/std.cdl", sha256=cdl_sha), cdl_1=dict(path="/x/sram.cdl", sha256="s" * 64),
                   manifest=dict(path=str(workspace) + ".json", sha256="m" * 64))
    write(workspace / "job.json", dict(
        top="ibex_sram_bench", macro="RM", macro_count=2, qualified_frozen_hashes={"deck": "q" * 64},
        limits=dict(memory_mib=4096, swap_mib=512, deadline_seconds=deadline, threads=2),
        gds=gds, output=str(workspace), sources=sources))
    write(workspace / "klayout-command.json", [
        "/bin/time", "-v", "-o", str(workspace / "klayout.time"), "/opt/eda/bin/eda-multicall", "klayout", "-b",
        "-r", "/pdk/sg13g2.lvs", "-rd", "threads=2", "-rd", "run_mode=deep", "-rd", "input=" + gds,
        "-rd", "schematic=" + str(workspace / "reference/reference.cdl"),
        "-rd", "report=" + str(workspace / "layout.lvsdb")])
    write(workspace / "summary.json", dict(status="passed", complete=True, reference_cdl_sha256="n" * 64,
                                           database_sha256=str(workspace)))


class CheckAccounting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.output, self.signoff = root / "native-final/output", root / "signoff-native"
        gds = str(self.output / "backend/ibex_sram_bench.gds")
        drc_run(self.output / "backend/main", gds)
        drc_run(self.output / "backend/antenna", gds, "antenna")
        lvs_run(self.output / "backend/lvs", gds, deadline=1200)
        drc_run(self.signoff / "drc", gds)
        drc_run(self.signoff / "antenna", gds, "antenna")
        lvs_run(self.signoff / "lvs", gds)
        self.receipts = [dict(id=s, kind="command", exit_code=0, wall_seconds=1.0)
                         for s in ("be_timing", "be_main", "be_antenna", "be_lvs")]
        self.resources = {g: dict(wall_seconds=s) for g, s in dict(
            drc=500.0, antenna=250.0, latchup=40.0, extraction=5.0, timing=30.0, lvs=170.0, simulation=5.0,
            logic=20.0, density=0.25).items()}

    def tearDown(self):
        self.tmp.cleanup()

    def covered(self, receipts=None):
        return mr.producer_identical_checks(self.output, self.signoff, receipts or self.receipts)

    def test_identical_checks_are_counted_once(self):
        covered = self.covered()
        self.assertEqual({g: c["identical"] for g, c in covered.items()}, dict(drc=True, antenna=True, lvs=True))
        seconds, gates = mr.completion_seconds(self.resources, covered)
        self.assertEqual(gates, ["latchup", "simulation", "logic", "density"])
        self.assertEqual(seconds, 65.25)

    def test_producer_that_stopped_early_keeps_every_shared_gate(self):
        covered = self.covered(self.receipts[:1])
        self.assertEqual({c["reason"] for c in covered.values()}, {"producer step not run"})
        seconds, gates = mr.completion_seconds(self.resources, covered)
        self.assertEqual(gates, list(mr.COMPLETION_GATES))
        self.assertEqual(seconds, 985.25)

    def test_failed_producer_step_is_not_coverage(self):
        receipts = copy.deepcopy(self.receipts)
        receipts[1]["exit_code"] = 1
        covered = self.covered(receipts)
        self.assertEqual(covered["drc"], dict(step="be_main", identical=False, reason="producer step failed"))
        self.assertTrue(covered["antenna"]["identical"])

    def test_different_deck_or_switch_or_layout_is_not_identical(self):
        gds = str(self.output / "backend/ibex_sram_bench.gds")
        drc_run(self.signoff / "drc", gds, deck_sha="e" * 64)
        self.assertEqual(self.covered()["drc"]["reason"], "deck_sha256 differs")
        drc_run(self.signoff / "drc", gds)
        command = json.loads((self.signoff / "drc/command.json").read_text())
        command[command.index("threads=2")] = "threads=4"
        write(self.signoff / "drc/command.json", command)
        self.assertEqual(self.covered()["drc"]["reason"], "command differs")
        summary = json.loads((self.signoff / "antenna/summary.json").read_text())
        summary["gds"]["sha256"] = "b" * 64
        write(self.signoff / "antenna/summary.json", summary)
        self.assertEqual(self.covered()["antenna"]["reason"], "gds sha256 differs")

    def test_incomplete_report_is_not_coverage(self):
        summary = json.loads((self.output / "backend/main/summary.json").read_text())
        summary.update(complete=False, status="failed")
        write(self.output / "backend/main/summary.json", summary)
        self.assertEqual(self.covered()["drc"]["reason"], "missing or incomplete report")

    def test_lvs_identity_ignores_deadline_but_not_inputs(self):
        self.assertTrue(self.covered()["lvs"]["identical"])
        lvs_run(self.signoff / "lvs", str(self.output / "backend/ibex_sram_bench.gds"), cdl_sha="z" * 64)
        self.assertEqual(self.covered()["lvs"]["reason"], "cdl_0 sha256 differs")


class CheckSchedule(unittest.TestCase):
    """The end-to-end conventions: native checks on their own schedule, LibreLane's on the same one."""

    def setUp(self):
        self.covered = {g: dict(step=s, identical=True, reason=None) for g, s in
                        dict(drc="be_main", antenna="be_antenna", lvs="be_lvs").items()}
        self.ll = {g: dict(wall_seconds=s) for g, s in dict(
            drc=520.0, antenna=250.0, latchup=40.0, extraction=4.0, timing=28.0, lvs=180.0, simulation=5.0,
            logic=18.0, density=0.2).items()}

    def test_two_lane_group(self):
        receipts = [dict(id="be_main", kind="command", wall_seconds=500.0, started_seconds=100.0, cpu_affinity=[0, 1]),
                    dict(id="be_antenna", kind="command", wall_seconds=250.0, started_seconds=100.5,
                         cpu_affinity=[2, 3]),
                    dict(id="be_lvs_inputs", kind="render", wall_seconds=0.1),
                    dict(id="be_lvs", kind="command", wall_seconds=200.0, started_seconds=351.0, cpu_affinity=[2, 3])]
        schedule = mr.check_schedule(receipts, self.covered)
        self.assertEqual(schedule, dict(mode="concurrent", lanes=[["antenna", "lvs"], ["drc"]], serial_gates=[],
                                        serial_sum_seconds=950.0, span_seconds=500.0))
        seconds = {g: v["wall_seconds"] for g, v in self.ll.items()}
        # LibreLane under the same schedule: max(drc, antenna + lvs) instead of their sum.
        self.assertEqual(mr.scheduled_seconds(seconds, schedule), 520.0)
        self.assertEqual(mr.shared_gate_seconds_on_schedule(self.ll, schedule),
                         round(sum(seconds.values()) - 950.0 + 520.0, 2))

    def test_serial_checks_keep_the_plain_sum(self):
        receipts = [dict(id=s, kind="command", wall_seconds=w) for s, w in
                    (("be_main", 500.0), ("be_antenna", 250.0), ("be_lvs", 200.0))]
        schedule = mr.check_schedule(receipts, self.covered)
        self.assertEqual(schedule["mode"], "serial")
        self.assertEqual((schedule["serial_sum_seconds"], schedule["span_seconds"]), (950.0, 950.0))
        self.assertEqual(mr.shared_gate_seconds_on_schedule(self.ll, schedule),
                         round(sum(v["wall_seconds"] for v in self.ll.values()), 2))

    def test_only_covered_checks_are_scheduled(self):
        receipts = [dict(id="be_main", kind="command", wall_seconds=500.0, started_seconds=100.0, cpu_affinity=[0, 1]),
                    dict(id="be_antenna", kind="command", wall_seconds=250.0, started_seconds=100.0,
                         cpu_affinity=[2, 3]),
                    dict(id="be_lvs", kind="command", wall_seconds=200.0, started_seconds=350.0, cpu_affinity=[2, 3])]
        self.covered["drc"].update(identical=False, reason="deck_sha256 differs")
        schedule = mr.check_schedule(receipts, self.covered)
        # One lane left: antenna then LVS, no overlap, so nothing is saved.
        self.assertEqual(schedule["mode"], "serial")
        self.assertEqual(schedule["lanes"], [["antenna", "lvs"]])
        self.assertEqual((schedule["serial_sum_seconds"], schedule["span_seconds"]), (450.0, 450.0))
        for check in self.covered.values():
            check["identical"] = False
        none = mr.check_schedule(receipts, self.covered)
        self.assertEqual((none["mode"], none["lanes"], none["serial_gates"]), ("none", [], []))
        self.assertEqual(mr.shared_gate_seconds_on_schedule(self.ll, none),
                         round(sum(v["wall_seconds"] for v in self.ll.values()), 2))


class StageTimes(unittest.TestCase):
    def test_parallel_group_counts_its_elapsed_span_once(self):
        receipts = [dict(id="be_timing", kind="command", wall_seconds=30.0),
                    dict(id="be_main", kind="command", wall_seconds=500.0, started_seconds=100.0),
                    dict(id="be_antenna", kind="command", wall_seconds=250.0, started_seconds=100.5),
                    dict(id="be_lvs_inputs", kind="render", wall_seconds=0.5),
                    dict(id="be_lvs", kind="command", wall_seconds=200.0, started_seconds=351.0),
                    dict(id="be_macro_contract", kind="command", wall_seconds=0.5)]
        stages = mr.native_stage_times(receipts)
        # span 100..600 (antenna and LVS run inside it) + the two serial rows (0.5 + 0.5)
        self.assertEqual(stages["foundry DRC, LVS, macro contract"], 501.0)
        self.assertEqual(stages["signoff timing (RustSTA + OpenSTA)"], 30.0)

    def test_serial_receipts_are_summed_as_before(self):
        receipts = [dict(id=s, kind="command", wall_seconds=w) for s, w in
                    (("be_main", 500.0), ("be_antenna", 250.0), ("be_lvs", 200.0))]
        self.assertEqual(mr.native_stage_times(receipts)["foundry DRC, LVS, macro contract"], 950.0)


if __name__ == "__main__":
    unittest.main()
