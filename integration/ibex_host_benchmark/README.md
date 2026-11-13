# Ibex SG13G2/EZ130 host benchmark harness

This directory reproduces the Ibex RTL-to-GDS campaign on a host that has none
of the first campaign host's local artifacts: no systemd user manager, cgroup v1
instead of v2, four CPUs instead of sixteen, nixpkgs tool builds instead of the
LibreLane devshell AppImage, and no access to the ETH EZ130 download server.
It drives the recipe in [`../ibex_sg13g2_ez130`](../ibex_sg13g2_ez130/README.md)
and the stock LibreLane Classic flow from the same prepared inputs, then applies
the same independent gate scripts to both final layouts.

Results, scope and the module-by-module comparison are published on the
[benchmark page](../../docs/benchmarks.md) and in the machine-readable receipts
[`docs/benchmarks/ibex-sg13g2-ez130-host-20261105.json`](../../docs/benchmarks/ibex-sg13g2-ez130-host-20261105.json)
(newest) and
[`docs/benchmarks/ibex-sg13g2-ez130-host-20261102.json`](../../docs/benchmarks/ibex-sg13g2-ez130-host-20261102.json).

## Results · 2026-11-05

One timed sample per flow on 4 vCPUs, with the committed recipe and engines of
superproject `397bfa6`. **RustEDA passes every shared gate that gives a
pass/fail verdict; LibreLane fails the timing gate and leaves equivalence
unverified, so the receipt's end-to-end ratios are null.** RustEDA's producer
completed all 114 manifest steps in 888.896 s, including `timing_accuracy`
(largest RustSTA/OpenSTA difference 0.1639 ps) and its own foundry main DRC,
antenna DRC, LVS and macro contract, which the receipt verified to be the
identical checks of the shared gates; its layout passes the eight shared gates
that give a pass/fail verdict
(DRC and antenna 0 violations, latch-up LU.a/LU.b 0 uncertified boxes, LVS
`passed`, TT/SS timing with 0 setup, hold, slew, capacitance and fanout
violations, simulation `passed`, synthesis and final equivalence `proven`,
density `passed`); the ninth, OpenRCX extraction, reports
`extracted_not_qualified` on both layouts, as on 2026-11-02. LibreLane's layout
passes DRC, antenna, latch-up, LVS,
simulation and density, fails timing on 28 (TT) and 24 (SS) capacitance and 30
fanout violations with setup and hold met, and its equivalence is `unverified`
(433 renamed registers).

End to end, RustEDA took 974.19 s (sv2v preparation, producer, and the
latch-up, simulation, equivalence and density gates) and LibreLane 4,616.30 s
(preparation, flow through `Checker.XOR`, all nine shared gates); with both
flows' DRC, antenna and LVS charged one after another, 1,352.37 s, and with
LibreLane's charged on the producer's two-lane schedule, 4,104.97 s. No ratio is
published. The native layout has 187,585.2 µm² standard-cell area,
897,357.3 µm of routed signal wire and 118,525 vias, against 197,913.7 µm²,
969,571.6 µm and 122,194. GDS, DEF, all netlists and the native SPEF were
byte-identical in a second producer run after the campaign (through
`be_timing`, which writes them all); the OpenRCX SPEF differed. The host is a different virtual machine from
2026-11-02's, on which every LibreLane stage took longer (host deviation 6).

After the campaign, same-load reruns (5 interleaved repetitions) put the native
frontend STA and ECO stage at 0.49× LibreLane's four resizer steps and
1.18× its two steps before global routing, and OpenRCX extraction of the
native DEF at 1.36× that of LibreLane's; the benchmark page lists every
stage, gate and module comparison.

[Host campaign results](https://lab-emi.github.io/RustEDA/benchmarks/#ibex-host-campaign-2026-11-05) ·
[Receipt and evidence inventory](../../docs/benchmarks/ibex-sg13g2-ez130-host-20261105.json) ·
[Same-load comparisons](../../docs/benchmarks/evidence/ibex-host-20261105-same-load/summary.json)

## Results · 2026-11-02

One timed sample per flow on 4 vCPUs. **Neither flow passes every gate, so the
receipt's end-to-end ratios are null.** RustEDA's layout passes every design
gate on the shared scripts: foundry main and antenna DRC with 0 violations, LVS
`passed`, TT/SS timing on OpenRCX SPEF with 0 setup, hold, slew, capacitance and
fanout violations, vendor-model simulation `passed`, synthesis and final
equivalence `proven`, density `passed`. Its producer stops at the
`timing_accuracy` gate (`failed`) after 114 of 119 manifest steps: on identical
SPEF, RustSTA and OpenSTA differ in TT setup worst slack by **1.4052 ps**
(native SPEF) and 1.2838 ps (OpenRCX SPEF) against a 1 ps tolerance; the other
six compared values are within 0.2153 ps. LibreLane's layout passes DRC (0),
antenna (0), LVS, simulation and density, but fails timing on **28 (TT) and
24 (SS) maximum-capacitance and 30 fanout violations** with setup and hold met.
Its equivalence is `unverified`: the checker cannot name-match 433 renamed
registers, which is a checker limitation, not evidence of non-equivalence.

End-to-end time is 2,609.93 s for RustEDA (sv2v preparation, the producer until
it stopped, and the shared gates it did not reach) and 3,286.87 s for LibreLane
(sv2v preparation, the flow through `Checker.XOR` and every shared gate). The
scopes differ; the two times are not a speedup. The native layout has
233,909.2 µm² standard-cell area, 1,679,076.6 µm of routed signal wire and
301,099 vias, against 197,913.7 µm², 969,571.6 µm and 122,194. The largest
flow process peaked at 1,080,408 KiB (RustEDA) and 2,333,000 KiB (LibreLane);
the native producer's cgroup reached its 8 GiB limit with page cache. GDS, DEF,
all netlists and the native SPEF were byte-identical across two complete native
runs on this host; the OpenRCX SPEF differed. Module head-to-heads on identical
inputs (synthesis, STA, extraction, GDS export, SPICE) are on the benchmark
page, with a place, CTS and route stage comparison taken from the two flows;
their netlists and repair differ, so that one is not an identical task and has
no quotient.

[Host campaign results](https://lab-emi.github.io/RustEDA/benchmarks/#ibex-host-campaign-2026-11-02) ·
[Receipt and evidence inventory](../../docs/benchmarks/ibex-sg13g2-ez130-host-20261102.json)

## Files

| File | Purpose |
| --- | --- |
| `qualify_sram_lvs.py` | Runs the SRAM-only KLayout LVS and writes the macro qualification record the LVS gate requires. The record is `qualified` only when the run matches. |
| `compose_native.py` | Writes the frontend/backend composer inputs (every file and tool pinned by path, SHA-256 and size) and calls the recipe's `native_frontend.py` and `native_backend.py`. |
| `run_logic.py` | Renders the `rusteda.logic-signoff-inputs.v1` document that `native_flow.py` would render, for a completed producer output. |
| `signoff_layout.py` | Applies the recipe's gate scripts to one final layout: foundry main and antenna DRC, latch-up tap coverage (`tap_coverage_runner.py`: LU.a and LU.b, 20 µm, LU.a also along paths inside the n-well; the main deck checks LU.b only), OpenRCX, RustSTA + OpenSTA timing, full-device LVS, vendor-model simulation, synthesis/final equivalence and final density. The latch-up gate was added after the 2026-11-02 campaign; both published layouts pass it (LU.a/LU.b: 0 uncertified boxes on each). |
| `netlist_qor.py` | Mapped-netlist QoR measured with one neutral toolchain: area and cell classes (`yosys stat -liberty`), logic depth with sequential cuts, ideal-wire OpenSTA timing (diagnostic). |
| `def_metrics.py` | Routed signal wirelength and vias from a final DEF, with the published receipt's definition. |
| `spef_spice_decks.py` | Transient decks for `rustspice bench-suite` built from extracted Ibex RC nets. |
| `synth_bench.sh` | Synthesis head-to-head on the prepared RTL: RustSys running the native frontend's workspace sequence (2 threads) and its single-command direct mode (all CPUs), against Yosys running LibreLane 3.0.17's own synthesis script with the run's step configuration. One warm-up plus `REPS` interleaved runs per contender under GNU time. |
| `module_bench.py` | Replays commands recorded by a completed native producer, on the same inputs: RustROAD `extract` against OpenRCX, the RustSTA pipeline of `signoff_timing.py` against its two OpenSTA processes, RustROAD `export-gds` against KLayout's LEF/DEF reader. One warm-up plus `--reps` interleaved runs at each `--threads` value; writes `module-bench.json`. RustROAD sizes its pool only from its global `--threads` option (default 2; `RAYON_NUM_THREADS` does not size it), so the extraction replay sets `--threads` to each listed value on the recorded `extract` command (the 2026-11-02 campaign's recorded command passed none, so its 4-thread RustROAD rows were a second 2-thread sample). The GDS replay runs the recorded `export-gds` command unchanged (no `--threads`: the default 2-thread pool) and is not repeated per thread count. |
| `final_campaign.sh` | The timed campaign, one stage at a time: native producer, shared gates on its layout, LibreLane through `Checker.XOR`, shared gates on its layout, then the synthesis, module and SPICE head-to-heads. Each stage starts once the one-minute load average is below 0.5 (at most 20 min wait). |
| `make_receipt.py` | Builds `docs/benchmarks/ibex-sg13g2-ez130-host-<YYYYMMDD>.json` (the `--date` value without dashes) and its evidence directory (each file at most 2 MiB) from a completed campaign directory, recomputing DEF, density and netlist QoR. Ratios are filled only when both flows pass every gate; missing values stay null. The RustEDA configuration text and `rustroad_threads` record the thread count each RustROAD step actually ran with, read from the producer's recorded commands and repair inputs (`default` for a recorded command without `--threads`, the default 2-thread pool; null and `not recorded` when a step left no record). The native end-to-end time counts each check once: when the producer ran `be_main`, `be_antenna` or `be_lvs` to completion as the identical check of a shared gate (same script, deck SHA-256, rule selection, switches, KLayout command line up to the run's own workspace paths, and the same GDS, netlist, CDL and helper bytes), that gate's shared time is not added again (`producer_identical_checks`, recorded in the receipt with the reason for every gate it does not cover); latch-up, simulation, equivalence and density, which the producer does not run, are always added. The LVS identity is input and verdict identity: it ignores only the producer's shorter deadline and its expected-hash preconditions, and it does not compare the LVS databases, because KLayout's LVS database is not byte-deterministic (two shared runs on the same layout differ in layer-index order). A parallel group's steps count their elapsed span once in the stage table. When the producer runs the covered checks concurrently (`foundry_check_schedule.mode` `concurrent`, e.g. `be_main` beside `be_antenna` then `be_lvs` on a four-CPU host), the native end-to-end time contains their elapsed span while LibreLane's shared gates run one at a time, so the receipt reports both conventions: `end_to_end_speedup` charges LibreLane's shared main/antenna DRC and LVS on the native producer's schedule (`end_to_end_seconds_native_check_schedule`, e.g. max(drc, antenna + lvs)), and `end_to_end_speedup_serial_checks` charges both flows those checks one after another (`end_to_end_seconds_serial_checks` on the native side). The shared `extraction` and `timing` gates are never added on the native side (`COMPLETION_GATES`): the producer's `be_openrcx` and `be_timing` run the same scripts, but unlike the foundry checks that identity is not checked by the receipt; LibreLane is charged both gates on top of its in-flow RCX and STA, as in the published end-to-end scope (`docs/benchmarks.md`). Unit tests: `tests/test_make_receipt.py`. |
| `ez130/restore.sh` | Rebuilds the EZ130 8T v1.1 tree from public mirrors: restores the citation placeholder in the mirrored files, rebuilds the tech LEF, strips the cell GDS, then checks every file with `sha256sum -c reconstructed.sha256`. |
| `ez130/rebuild_tech_lef.py` | Rebuilds the SG13G2 tech LEF from the public SG13CMOS5L retarget, taking the removed layer, via and rule text from IHP's SG13G2 stdcell tech LEF at commit `3b59002e`. |
| `ez130/strip_prboundary.py` | KLayout script that removes the `189/4` boundary shapes the retarget added to the cell GDS. |
| `ez130/reconstructed.sha256` | SHA-256 of every restored file: the byte-identical ones and the two reconstructed ones (tech LEF `df2eed23…`, cell GDS `b4bed12d…`). |

## Host deviations

Each deviation applies to both flows unless stated otherwise.

1. **EZ130 collateral.** The ETH archive host is unreachable from the benchmark
   host. Five of the seven EZ130 files pinned by the contract are byte-identical:
   TT/SS Liberty, cell LEF, CDL and Verilog come from a public pre-release
   mirror that differs only in a citation placeholder, verified by SHA-256 after
   restoring it (the FF Liberty, pinned by the library catalog, also matches),
   and the TT Liberty also matches the `pulp-platform/croc` mirror. The SG13G2 tech LEF and the cell GDS are **reconstructed** from the
   public SG13CMOS5L retarget: `rebuild_tech_lef.py` reverses the documented
   OpenBLAU tech-LEF edits using IHP's SG13G2 stdcell tech LEF at `3b59002e`,
   and separately `strip_prboundary.py` removes the added `189/4` boundary
   shapes from the cell GDS. They are not byte-identical
   to the pinned files (`375ae19d…`, `62876764…`), so this is not a
   reproduction of the 2026-10-30 inputs. All IHP and SRAM collateral matches.
2. **Tools.** nixpkgs builds: OpenROAD 26Q3 (LibreLane pins `dcf36133`),
   OpenSTA 3.1.0, Yosys 0.70 (historical 0.62), KLayout 0.30.12, Magic
   8.3.689, Netgen 1.5.325, Icarus Verilog 13.0, ngspice 45, sv2v 0.0.13.1.
   The converted RTL therefore differs in bytes from the historical
   `8a8fe84b…` file; both flows read the same converted file.
3. **Native recipe.** The original component-qualification evidence
   (`rusteda.ihp-component-qualification.v1`) is not published and is not
   fabricated, so the sixteen-gate composer cannot run. The native run uses the
   backend prefix manifest (every physical step plus DRC, antenna, LVS,
   extracted timing and timing parity), and `signoff_logic.py` separately for
   synthesis/final equivalence and vendor simulation. The eight derived
   qualification gates are not produced; the composition is **complete but
   unqualified**. The run uses a host-local copy of the recipe directory
   (`recipe_dir` in the host map) and of RustROAD's `build_rc_profile.py`. None
   of these edits is committed:
   - Thread allow-lists accept 4 as well as 2 and 16 in `native_frontend.py`,
     `native_flow.py`, `native_antenna.py`, `foundry_first/runner.py` and
     `qualify_native.py`. On 2026-11-02 the `antenna_session/` copies of
     `native_antenna.py` and `verify.py` needed the same edit; the repository
     accepts 4 there since commit `7e195cc`, so the 2026-11-05 host copy no
     longer patches them.
   - `build_rc_profile.py` accepts the explicit calibration rules path the
     composer passes.
   - `signoff_lvs.py` and `producer.py` read the cgroup-v1 memory controller
     (`memory.max_usage_in_bytes`, `memory.memsw.*`). `signoff_lvs.py` accepts
     the host executor's owned memory cgroup
     (`rusteda-worker-<first 24 hex digits of the request>`) instead of a
     systemd user scope.
   - `final_density.py`'s DEF parser accepts the `+ SOURCE` attribute and all
     eight orientations, and treats only SRAM masters (`RM_IHPSG13_*`) as fixed
     macros, because LibreLane's DEF also marks taps `FIXED`.
   - `contract.json` pins the two reconstructed EZ130 hashes and records the
     originals under `host_deviation`. With the repository copy, `prepare.py`
     stops with `pinned collateral identity mismatch`.

   Antenna repair uses the grouped single-process session
   (`antenna_session/`, frozen by `freeze.py` and bound with the qualified group
   policy), because the legacy path's complete antenna report for this layout
   exceeded RustROAD's 128 MiB report cap. The combined cell LEF is rebuilt from
   the EZ130 and SRAM LEFs.
4. **LibreLane.** Stock LibreLane 3.0.17 Classic flow, without the
   RustEDA-authored repair loop that the historical matched reference added;
   its own post-GRT design repair is enabled instead
   (`RUN_POST_GRT_DESIGN_REPAIR=true`). `DRT_THREADS` is 4 on this host.
5. **Resources.** Each timed run executes alone in its own cgroup-v1 memory
   group (8 GiB RAM for RustEDA as in the recipe, 10 GiB RAM for LibreLane,
   which on the historical host had 8 GiB plus 2 GiB swap; this host has no
   swap). Peak RSS is GNU time's maximum over the waited process tree; the
   cgroup peak includes page cache.
6. **Host of the 2026-11-05 campaign.** The 2026-11-05 campaign ran on a
   different 4-vCPU virtual machine of the same kind: Intel Xeon @ 2.80 GHz
   (33 MiB L3, AVX-512), 15.7 GiB RAM, kernel 6.18.44-fc-v236, against a
   2.10 GHz Xeon and kernel 6.18.44-fc-v185 on 2026-11-02 (`host-facts.json`
   of each receipt). Despite the higher nominal clock this host was slower:
   with the same configuration and tool builds every LibreLane stage took
   longer, and every step that took 2 s or more on 2026-11-02 took 1.11× to
   1.69× as long (detailed routing 1.38×), apart from the first Yosys step at
   flow start (2.24×, 4.52 s against 2.02 s), which overlapped a cold start and
   brief operator activity (last item below); compare the two flows within one
   campaign and read the 2026-11-02 figures only as history. The deviations above apply unchanged, with these
   differences in how they were applied:
   - the host copy is `git archive HEAD integration` of the campaign commit
     (a superset of step 2's `integration/ibex_sg13g2_ez130`) and carries 12
     hunks in 9 files (the `antenna_session/` hunks are upstream, see
     deviation 3); the patch is otherwise the 2026-11-02 one;
   - the patched `build_rc_profile.py` is byte-identical to 2026-11-02's
     (RustROAD's script is unchanged since then);
   - the run wrappers also export `PYTHONDONTWRITEBYTECODE=1`, so no bytecode
     is written into module script directories during a run;
   - the external inputs (Ibex and IHP checkouts, the restored EZ130 tree, the
     combined cell LEF and the LibreLane installation) are the 2026-11-02
     ones, and the preparation steps were re-run into a new campaign
     directory: the converted RTL (`da905268…`), the testbench, SDC, PDN and
     the LibreLane configuration are byte-identical to 2026-11-02's up to the
     campaign directory's paths, and `contract.json` differs only by the
     recipe's own `native_tap_max_gap_um` (42 instead of 15, committed since);
   - each timed stage ran with no other measured job, but operator activity was
     not fully excluded: creating scratch copies of the documentation
     repositories (about 63 MB written in about 11 s) overlapped the native
     `be_route` step (302.24 s, against 304.91 s in the second producer run
     after the campaign), and a 1.8 MB copy overlapped LibreLane's first Yosys
     step.

## Reproduce

This command sequence reproduces the 2026-11-05 campaign (and, with the
2026-11-02 recipe and host patch, the 2026-11-02 one) with the current scripts,
run from the repository root. Replace `/path/to/…` with local paths.
`WORK` is a new, empty campaign directory (the 2026-11-02 campaign used
`.work/ibex-host`, the 2026-11-05 campaign `.work/campaign-20261105`) and
`HOSTCOPY` holds the patched recipe copy; every preparation step refuses a
nonempty workspace with `REVISION_CONFLICT`.

The 2026-11-05 campaign ran `final_campaign.sh` once, without interruption and
without rerunning any stage; its preparation steps were re-run into a new
campaign directory with the 2026-11-02 external inputs (host deviation 6). Its
determinism record (`bringup-native-record.json`) comes from a second producer
run of the same composition after the campaign, stopped after `be_timing`, and
it repeated no prepared-config diagnostic.

In the original 2026-11-02 run, the SPICE head-to-head and the GDS replay were rerun after
the campaign script had finished, once the missing `bench-spice` directory
creation and KLayout's `.def` input path were fixed; the repository versions of `final_campaign.sh` and `module_bench.py`
include both fixes. A first campaign attempt was abandoned after its native
producer exited, and the campaign was restarted from the beginning; the
published figures come from the restart.

Before starting, provide:

- a clean Ibex checkout at `38c070939183cc10940b66c8e9e04eeca6b65470` and an
  IHP Open PDK checkout at `5cccb161f7492697cfa52eb14dc03beb00bdca9e` whose
  Git database also holds `3b59002e` and the DRC fix
  `a6a7e6ccf95b480f786039890b40670db6a54419`;
- the tools listed under deviation 2, LibreLane 3.0.17, GNU time and xxHash's
  `xxhsum` (the composer pins it by hash as `tools.xxhsum`);
- release builds of RustSys (with its `check_config` example), RustSTA
  (`ruststa`, `rusteda`), RustROAD, RustSpice and RustEDA-Common's
  `export_pin_caps` example under `.work/target/<Engine>/release/`;
- the combined cell LEF: the EZ130 cell LEF with the SRAM macro's `MACRO`
  block inserted before `END LIBRARY` (no script for it is in the repository).

The harness scripts hard-code this host's layout: the repository at
`/home/user/RustEDA`, Python at `/usr/local/bin/python3`, tools and
`env.sh` under `/opt/eda/`, the combined LEF at
`/opt/eda/derived/native-combined.lef` and the cgroup executor at
`/opt/eda/recon/measured_run.sh`. Edit those paths in `final_campaign.sh`,
`synth_bench.sh`, `module_bench.py` and `make_receipt.py` on another host.

These host-local files are not in the repository; provide your own with the
same interface:

- `/opt/eda/env.sh`, sourced by the generated LibreLane launcher, puts the
  tools on `PATH` and sets `QT_QPA_PLATFORM=offscreen`;
- `/opt/eda/wrap/openroad`, `/opt/eda/wrap/klayout` and `/opt/eda/wrap/yosys`
  (used by `module_bench.py` and through `PATH`) add the Python package
  directories the tools' scripts import to `PYTHONPATH` (KLayout:
  `KLAYOUT_PYTHONPATH`) and run the tool;
- the multi-call stand-in named as `appimage` in the host map (step 5);
- the cgroup executor `measured_run.sh` and the `run_native.sh` and
  `run_signoff.sh` wrappers (step 6).

**1. EZ130 collateral**, only when the ETH archive is unreachable (deviation 1):

```bash
git -C /path/to/IHP-Open-PDK show \
  3b59002e:ihp-sg13g2/libs.ref/sg13g2_stdcell/lef/sg13g2_tech.lef > /path/to/sg13g2_tech-3b59002e.lef
integration/ibex_host_benchmark/ez130/restore.sh \
  /path/to/IHP__MPC9724/ip/ez130_8t_v1p1 /path/to/sg13g2_tech-3b59002e.lef \
  /path/to/klayout /path/to/ez130_8t_v1p1
```

**2. Host copy of the recipe.** Apply the edits listed under deviation 3 to the
copies; the repository files are unchanged.

```bash
WORK=/path/to/work HOSTCOPY=/path/to/hostcopy
mkdir -p "$HOSTCOPY/integration" "$WORK/patched"
cp -r integration/ibex_sg13g2_ez130 "$HOSTCOPY/integration/"
ln -s "$PWD/modules" "$HOSTCOPY/modules"   # prepare.py reads RustSys's EZ130 latch map
cp modules/RustROAD/scripts/build_rc_profile.py "$WORK/patched/"
```

**3. Prepared inputs and signoff collateral.** `prepare.py` runs from the host
copy because of its re-pinned `contract.json`. On this host `--klayout` was a
multi-call wrapper, selected with `--klayout-prefix klayout`.

```bash
python3 "$HOSTCOPY/integration/ibex_sg13g2_ez130/prepare.py" \
  --workspace "$WORK/prep" --base-rev empty --ibex-source /path/to/ibex \
  --ihp-root /path/to/IHP-Open-PDK/ihp-sg13g2 --ez130-root /path/to/ez130_8t_v1p1 \
  --sv2v /path/to/sv2v --gnu-time /path/to/gnu-time
python3 integration/ibex_sg13g2_ez130/prepare_signoff_deck.py \
  --pdk-source /path/to/IHP-Open-PDK --workspace "$WORK/signoff" --base-rev empty
python3 integration/ibex_sg13g2_ez130/prepare_sram_lvs.py \
  --pdk /path/to/IHP-Open-PDK/ihp-sg13g2 --workspace "$WORK/sram-lvs" --base-rev empty
python3 integration/ibex_sg13g2_ez130/prepare_ez_lvs.py \
  --lef /path/to/ez130_8t_v1p1/lef/ez130_8t.lef --workspace "$WORK/ez-lvs" --base-rev empty
python3 integration/ibex_host_benchmark/qualify_sram_lvs.py \
  --klayout /path/to/klayout --deck "$WORK/sram-lvs/sg13g2.lvs" \
  --gds /path/to/IHP-Open-PDK/ihp-sg13g2/libs.ref/sg13g2_sram/gds/RM_IHPSG13_1P_1024x32_c2_bm_bist.gds \
  --cdl "$WORK/sram-lvs/sram.cdl" --workspace "$WORK/sram-qual"
```

**4. Antenna session.** Freeze the patched helpers, then add the group policy.
It must equal `GROUP_POLICY` in `native_backend.py`; the composer rejects any
other value.

```bash
python3 "$HOSTCOPY/integration/ibex_sg13g2_ez130/antenna_session/freeze.py" \
  --workspace "$WORK/antenna-frozen" --base-rev empty
cat > "$WORK/antenna-frozen/antenna-group-policy.json" <<'EOF'
{
  "group_names": 128,
  "max_groups": 1024,
  "max_attempts": 64,
  "max_total_jumpers": 4096,
  "max_commands": 4096,
  "max_report_bytes": 8589934592,
  "max_selected_report_bytes": 134217728,
  "max_metadata_bytes": 4194304,
  "max_cumulative_work": 200000000000
}
EOF
```

**5. Host map and composition.** Write `$WORK/host.json`; `compose_native.py`,
`signoff_layout.py`, `run_logic.py` and `make_receipt.py` read it. `appimage`
is the LibreLane devshell AppImage or a multi-call stand-in whose first
argument names `openroad`, `klayout`, `python3` (with KLayout's Python module),
`sta` or `yosys`; both campaigns used the stand-in.

```bash
cat > "$WORK/host.json" <<'EOF'
{
  "recipe_dir": "/path/to/hostcopy/integration/ibex_sg13g2_ez130",
  "prepared": "/path/to/work/prep",
  "ez130_root": "/path/to/ez130_8t_v1p1",
  "ihp_root": "/path/to/IHP-Open-PDK/ihp-sg13g2",
  "combined_cell_lef": "/path/to/native-combined.lef",
  "rc_builder": "/path/to/work/patched/build_rc_profile.py",
  "signoff_deck": "/path/to/work/signoff",
  "sram_lvs": "/path/to/work/sram-lvs",
  "ez_lvs": "/path/to/work/ez-lvs",
  "sram_qualification": "/path/to/work/sram-qual/qualification.json",
  "antenna_frozen": "/path/to/work/antenna-frozen",
  "threads": 4,
  "cpu_affinity": [0, 1, 2, 3],
  "tools": {
    "rustsys": "/path/to/RustEDA/.work/target/RustSys/release/rustsys",
    "rusteda": "/path/to/RustEDA/.work/target/RustSTA/release/rusteda",
    "ruststa": "/path/to/RustEDA/.work/target/RustSTA/release/ruststa",
    "rustroad": "/path/to/RustEDA/.work/target/RustROAD/release/rustroad",
    "pin_caps": "/path/to/RustEDA/.work/target/RustEDA-Common/release/examples/export_pin_caps",
    "checker": "/path/to/RustEDA/.work/target/RustSys/release/examples/check_config",
    "python": "/path/to/python3",
    "gnu_time": "/path/to/gnu-time",
    "xxhsum": "/path/to/xxhsum",
    "appimage": "/path/to/eda-multicall",
    "opensta": "/path/to/sta",
    "yosys": "/path/to/yosys",
    "abc": "/path/to/yosys-abc",
    "iverilog": "/path/to/iverilog",
    "vvp": "/path/to/vvp"
  }
}
EOF
python3 integration/ibex_host_benchmark/compose_native.py \
  --host "$WORK/host.json" --workspace "$WORK/compose" --base-rev empty
```

**6. Timed campaign and receipt.** `final_campaign.sh` also expects two
host-local wrappers in `$WORK`, which are not in the repository:

- `run_native.sh RUNDIR` runs the host copy's `producer.py --project
  $WORK/compose/be --manifest $WORK/compose/be/prefix-manifest.json --output
  RUNDIR/output --base-rev empty --pdk ihp --rustroad … --gnu-time …
  --stop-after be_macro_contract` under the executor with an 8192 MiB limit,
  after exporting a random 64-hex-digit `RUSTEDA_WORKER_ID`,
  `RUSTEDA_OWNERSHIP=RUNDIR/ownership`,
  `CGROUP_NAME=rusteda-worker-<first 24 hex digits>` and `SWAP_MIB=2048`.
- `run_signoff.sh NAME LAYOUT_JSON WORKSPACE` writes the ownership record
  `WORKSPACE.ownership/native.json` (`request_id`, `implementation`, and the
  prefix manifest's path, SHA-256 and size), then runs `signoff_layout.py
  --host $WORK/host.json --layout LAYOUT_JSON --workspace WORKSPACE` the same
  way.

The executor is host-local too; write your own with this interface.
`measured_run.sh NAME OUTDIR MEM_MIB -- COMMAND` runs the command in a new
child of the caller's cgroup-v1 memory group (`memory.limit_in_bytes`, plus
`SWAP_MIB` in `memory.memsw.limit_in_bytes`) under `time -v`, samples the
load average and cgroup usage every 5 s, and writes `OUTDIR/resources.json`
(`rusteda.host-measured-run.v1`), which `make_receipt.py` reads. A failed
producer does not stop the campaign: `signoff_layout.py` then runs every shared
gate on the native layout.

```bash
integration/ibex_host_benchmark/final_campaign.sh "$WORK"
python3 integration/ibex_host_benchmark/make_receipt.py --work "$WORK" --date YYYY-MM-DD
```

`make_receipt.py` writes `docs/benchmarks/ibex-sg13g2-ez130-host-<YYYYMMDD>.json`
and deletes and rewrites `docs/benchmarks/evidence/ibex-host-<YYYYMMDD>/`, where
`<YYYYMMDD>` is the `--date` value without dashes, so use a new date rather than
overwrite the published 2026-11-02 receipt. `--map` overrides the run directory
names.

The receipt's native determinism check and LibreLane prepared-config
diagnostic come from two bring-up runs that no repository script performs.
`make_receipt.py` adds each only when its inputs are present in `$WORK`:

- **Determinism.** A second native producer run (on 2026-11-02, an earlier
  complete `run_native.sh` bring-up under shared host load; on 2026-11-05, a
  run of the same composition after the campaign, stopped after `be_timing`,
  which writes every compared artifact). Record its
  artifacts in `bringup-native-record.json`: `run` (a label) and `artifacts`,
  which maps `gds`, `def_`, `netlist_synth`, `netlist_final`,
  `netlist_powered`, `spef_native` and `spef_openrcx` to `{sha256, bytes}`.
  Both campaigns' records also hold the run's `summary` and timing `parity`
  documents (the 2026-11-05 record also `resources`), which `make_receipt.py`
  does not read.
- **Prepared-config diagnostic.** LibreLane on the prepared `config.json` with
  only `DRT_THREADS` set to 4, run tag `full` in `$WORK/ll-full` (read from
  `ll-full/runs/full`); on 2026-11-02 it was resumed once with `--last-run
  --from KLayout.StreamOut`. List its final views in
  `layout-librelane-bringup.json` (the keys of `layout-librelane.json`) and
  check them with `run_signoff.sh signoff-librelane-bringup
  layout-librelane-bringup.json signoff-librelane-bringup`. The receipt has no
  equivalence or density result for this run.

Memory caps were 8 GiB RAM for the RustEDA producer and for
both shared-gate runs and 10 GiB for the LibreLane flow; the largest shared-gate process
peaked at 2,615,468 KiB on the native layout and 2,290,552 KiB on LibreLane's on
2026-11-02, and at 2,245,620 KiB and 2,258,996 KiB on 2026-11-05.
