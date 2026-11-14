# Shared signoff gates for SKY130 and GF180MCU

One command judges a final layout of the Ibex + SRAM benchmark on SKY130 (`sky130hd`) or GF180MCU
(`gf180mcu`). It is the same command for every flow: RustEDA's layout and LibreLane's layout go
through identical scripts, decks and options, as on IHP SG13G2.

```sh
python3 integration/ibex_host_benchmark/signoff_layout.py \
  --profile integration/ibex_pdks/<profile>/gates.json \
  --host host.json --layout layout.json --workspace WORK/signoff [--gates drc,antenna,...]
```

- `layout.json`: `{gds, def, netlist, powered_netlist, synthesis_netlist}`, the keys of the IHP
  campaign's layout records. `netlist` is the final gate netlist without supply pins,
  `powered_netlist` the same netlist with supply pins, `synthesis_netlist` the mapped netlist
  before physical design.
- `--workspace` must be empty. The driver writes `summary.json` (schema
  `rusteda.host-shared-signoff.v1`, the IHP driver's: `gates.<name>.{exit_code, report}`,
  `resources.<name>.{exit_code, wall_seconds, peak_rss_kib}`, `total_wall_seconds`), one directory
  per gate with the raw tool output, `plans/` with the exact inputs of every gate and `logs/`.
- Without `--profile` the command runs the IHP gates exactly as before.

Run each call alone (the decks use two threads and several GiB).

## Host map

Everything host-specific is in the host map; the profile and the scripts name no host path.

```json
{
  "pdk_root": "/path/to/pdk-root",
  "prepared": "/path/to/prepared-inputs",
  "macro_baselines": "/path/to/baselines/<profile>",
  "gate_threads": 2,
  "tools": {
    "python": "...", "gnu_time": "...", "klayout": "...", "magic": "...", "netgen": "...",
    "openroad": "...", "opensta": "...", "yosys": "...", "abc": "...", "checker": "...",
    "iverilog": "...", "vvp": "...", "rusteda": "...", "ruststa": "..."
  }
}
```

- `pdk_root` holds `sky130A/` or `gf180mcuD/`. Profile paths are relative to it.
- `prepared` is the directory written by `integration/ibex_pdks/prepare.py` (`constraints.sdc`,
  `rtl/design.v`, `rtl/sram_bb.v`, `config.json`, and for `sky130hd` the derived macro Liberty
  under `macro/`). The die and the macro placements of its `config.json` are the floorplan both
  flows must have.
- `checker` is RustSys' `check_config` example binary; `rusteda`, `ruststa`, `opensta` and
  `gnu_time` must be ELF executables (the timing policy refuses wrappers).
- Every tool is recorded with its SHA-256 in the plans and reports.

## Provenance

`gates.json` pins every deck, rule file, technology file, cell view, macro view and vendor model
the gates consume by SHA-256 (`files`). The driver hashes all of them before the first gate and
again after the last one, and each gate re-checks its own inputs before and after its tool runs.
A changed or unpinned file stops the run (`PROFILE_PIN_MISMATCH`, `PROFILE_UNPINNED_FILE`,
`PROFILE_INVENTORY_DIFFERS` for a deck directory that holds a file the profile does not pin).

```sh
python3 integration/ibex_pdks/gates/profile_pins.py --profile <gates.json> --pdk-root <root> [--prepared <dir>]
```

checks the pins against a PDK tree; `--write` re-pins, which is a deliberate move to another PDK
build and changes the gates for both flows.

## Preparation: macro baselines

```sh
python3 integration/ibex_pdks/gates/macro_baseline.py --profile <gates.json> --host host.json
```

runs each KLayout deck of the profile once on every macro GDS alone and stores the markers under
`macro_baselines`. It is needed once per PDK tree and tool set, takes as long as the deck needs
on one macro, and belongs to neither flow's time. A layout gate refuses a baseline that was made
with other deck bytes, switches, KLayout binary or macro GDS.

## Gates

| Gate | `sky130hd` | `gf180mcu` |
| --- | --- | --- |
| `drc` | Magic `drc(full)` with `sky130A.tech`, and KLayout `sky130A_mr.drc` (`feol beol offgrid seal = true`, `floating_met = false`) | KLayout `gf180mcu.drc` (`feol beol dummy dummy_no_sub_prev offgrid conn_drc wedge = true`, `run_mode = deep`) |
| `antenna` | Magic `antennacheck` with the technology file's antenna rules | KLayout `rule_decks/antenna.drc` (`run_mode = deep`) |
| `extraction` | OpenRCX, `rules.openrcx.sky130A.nom.calibre` | OpenRCX, `rules.openrcx.gf180mcuD.nom` |
| `timing` | RustSTA and OpenSTA on the OpenRCX SPEF, `tt_025C_1v80` and `ss_100C_1v60` | the same at `tt_025C_5v00` and `ss_125C_4v50` |
| `lvs` | Magic extraction and Netgen with `sky130A_setup.tcl` | Magic extraction and Netgen with `gf180mcuD_setup.tcl` |
| `simulation` | Icarus, `sky130_fd_sc_hd.v`, OpenRAM model, `sky130hd/tb.sv` | Icarus, `gf180mcu_fd_sc_mcu7t5v0.v`, foundry SRAM model, `gf180mcu/tb.sv` |
| `logic` | synthesis and final netlist against the RTL, SRAM as a black box | the same |
| `density` | final non-filler standard-cell utilization outside macro halos, limit 70 % | the same |

The deck options are those of the PDK's own flow configuration (`KLAYOUT_DRC_OPTIONS` and
`KLAYOUT_ANTENNA_OPTIONS` in `libs.tech/openlane/config.tcl` and `libs.tech/librelane/config.tcl`).
No rule is switched off or waived.

**Latch-up** has no gate of its own on these PDKs: the tap-distance rules are part of the foundry
DRC. The `drc` report lists them under `latchup`: Magic `LU.2`, `LU.2.1`, `LU.3` (diffusion to
tap distance below 15 um), `nwell.4` (every n-well holds a contacted tap) and `licon.16` on
SKY130; `DF.13_LV`, `DF.13_MV`, `DF.14_LV`, `DF.14_MV` (20 um and 15 um) of `gf180mcu.drc` on
GF180MCU.

### `drc` and `antenna` (KLayout): `klayout_drc.py`, `attribute_markers.py`

The deck runs on the complete final GDS, SRAM interiors included; the deck decides every rule.
SRAM bit cells break rules the foundry waives, so the report separates two kinds of marker:

> A marker is attributed to a macro if and only if the same deck with the same options, run on
> that macro's GDS alone, reports a marker of the same rule whose geometry contains it, at one of
> the macro's instances with that instance's transformation. Every other marker gates.

A marker that exists only because top-level geometry is present (a wire too close to a macro
shape, an antenna on a net that enters a macro) is not in a baseline computed without any
top-level geometry, so it gates wherever it lies. Markers without geometry gate. The report gives
`violations` (gating), `macro_interior`, `macro_baseline_expected` (baseline markers times
instances) and `macro_baseline_reproduced`; two layouts with the same macros must show the same
`macro_interior` unless one of them changes what the deck sees inside a macro.

### `drc` and `antenna` (Magic, SKY130): `magic_drc.py`, `magic_antenna.py`

Magic reads the macro LEF first, so the SRAM is its abstract view (pins and obstructions) and
the GDS definition of that cell is not loaded. Magic checks all top-level and standard-cell
geometry, including its spacing to the SRAM's pins and obstructions, and nothing inside the SRAM;
the SRAM interior and its interaction with the routing are covered by the KLayout deck above.

- DRC verdict: the error count of the hierarchical check of the top cell
  (`drc list count total`) and the boxes of `drc listall why` must both be zero. The listing
  names the rules; on hierarchies with rotated children it can be shorter than the count, so
  the count decides.
- Antenna verdict: `antennacheck` ran to its end and printed no violation. Gates inside the SRAM
  are not part of this check (the SRAM LEF carries no antenna data either).

### `extraction`: `openrcx_extract.py`

The IHP recipe's commands (`extract_parasitics -version 1`, one nominal corner, no option
overrides) with the top name and the LEF list from the profile. Status `extracted_not_qualified`;
coverage of every active net is checked by `timing`.

### `timing`: `timing.py`

Runs the IHP recipe's `reference_timing.py` and its pinned `signoff_timing.py` unchanged: both
engines on the same SPEF, TT and SS, CPPR off, setup, hold, max slew, max capacitance, max
fanout, complete annotation, no unconstrained endpoint, no OpenSTA warning. One adjudication is
added and applied to every layout: a slew, capacitance or fanout row at a pin of a macro
instance is listed under `macro_pin_rows` and gates only if it exceeds a limit the SDC sets for
the whole design (`set_max_fanout 10`). Rows at standard-cell pins and ports gate, and so do
setup and hold checks at macro pins. The RustSTA to OpenSTA slack differences are reported
against 1 ps (`parity`), as in the IHP gate.

Decisions recorded in every report (`profile.decisions`): on SKY130 the OpenRAM macro has a TT
Liberty only, used at both corners; all consumers read the derived copy written by `prepare.py`
without the three pin-level `max_transition : 0.04` attributes; on GF180MCU the corners are the
5 V family.

### `lvs`: `netgen_lvs.py`

Black-box-macro LVS.

- Standard cells are compared at device level: Magic extracts the transistors of every cell from
  the final GDS, Netgen matches them with the PDK cell SPICE under the powered netlist.
- The SRAM master is the only black box. In the layout it is the LEF abstract view, whose ports
  are the LEF pins; in the reference it is a subcircuit declaration with the same LEF pins.
  Netgen (`-blackbox`, pins matched by name) therefore checks every pin of every SRAM instance
  against the net the powered netlist gives it, supplies included.
- Fill, decap, tap and end-cap cells are not black boxes. Which device-less cells Netgen ignores
  is decided by the PDK setup file (SKY130: fill and tap cells when the layout comes from GDS).
  Tie cells are ordinary cells (`conb_1` on SKY130, `tieh`/`tiel` on GF180MCU).
- `extract unique all`: equally named but unconnected nets get distinct names, so an open in a
  labelled net shows up as an extra port.

Passes when the layout holds the expected SRAM instances with exactly the LEF pins, Magic reports
no shorted ports, the top circuits were compared, every compared circuit is clean and the final
result is `Circuits match uniquely.` without property or port errors.

### `simulation`: `simulation.py`

The final netlist, the vendor cell models and the SRAM's behavioural model under the profile's
`tb.sv`: both banks at addresses 0 and 511, byte masks, and an Ibex program that stores 42 and
43. The test benches differ from the IHP one in the address width, in one unchecked access per
bank at the start (the GF180MCU model is not operational before its enable has toggled) and in
the sampling point, 1 ns before the rising edge at which the design consumes the read data (the
OpenRAM model reads on the falling edge and drives X after every rising edge). The OpenRAM model
uses its memory array before declaring it, which Icarus rejects; the gate compiles a copy with
that one declaration line moved up (`model_edits` in the profile, recorded in the report with
both hashes).

### `logic`: `logic.py`

The IHP method with the macro interface read from `rtl/sram_bb.v`: the SRAM instances are cut out
of the RTL text, Yosys exposes the same pins of the netlist, RustSys' RTL interpreter exports the
obligations of both sides, and external ABC `&cec` must prove them equivalent, once for the
synthesis netlist and once for the final netlist. `unverified` means no proof was reached; it is
not a counterexample.

### `density`: `density.py`

Area of every placed cell whose LEF class is neither `SPACER` (fill, decap) nor `BLOCK` (SRAM),
divided by the die minus the union of the SRAM halo boxes. The database unit comes from the DEF,
the classes from the LEF, die, halo and SRAM placements from the reference configuration; a DEF
with another die or a moved SRAM is refused.

## Negative controls

`negative_controls.py` (KLayout batch script) writes corrupted copies of a layout: a routing
wire cut down to a stub below minimum area, one via removed, all antenna diodes removed. Together
with a powered netlist in which two nets are swapped they are the controls the gates must fail.
`tests/test_pdk_gates.py` holds the tool-free controls of the parsers and policies.
