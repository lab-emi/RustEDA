# Optional physical recipes

These original examples exercise documented external-tool interfaces. **Real Sky130/GF180 execution, QoR, extraction accuracy, DRC and LVS are unverified.** They are not enabled by default and do not supply the missing PDK, CTS, power-grid or signoff implementation.

| Script | Work performed | Required input collateral | Fixed output names |
| --- | --- | --- | --- |
| [`floorplan-place.tcl`](../examples/physical/floorplan-place.tcl) | Geometric floorplan, tracks, pins, global and legal placement | `tech_lef`, `cell_lef`, `floorplan_settings` | `placed.def`, `placed.odb`, `placed.nl.v` |
| [`route-extract.tcl`](../examples/physical/route-extract.tcl) | Routing and single-corner RC extraction of an explicitly prepared database | `prepared_odb`, `rcx_rules`, `route_settings` | `routed.def`, `routed.odb`, `routed.nl.v`, `routed.spef`, `routing.guide`, `router-drc.rpt` |
| [`streamout.py`](../examples/physical/streamout.py) | DEF plus actual cell GDS to hierarchical GDS | Prior `def` artifact; `tech_lef`, `cell_lef`, `cell_gds`, `lefdef_map`, `streamout_limits` | `layout.gds`, `streamout.json` |

The Tcl scripts do not load SDC/Liberty, report timing, estimate timing parasitics, resize cells, invoke timing-driven placement or run clock-tree synthesis. Routing explicitly sets `-critical_nets_percentage 0`, consistent with the documented [global-placement](https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html) and [global-routing](https://openroad.readthedocs.io/en/latest/main/src/grt/README.html) interfaces. RustSys remains the synthesis engine and RustSTA remains the reported STA engine. This command selection is not proof about every internal implementation detail of an arbitrary OpenROAD binary; pin and qualify the supplied tool.

`prepared_odb` is a deliberate prerequisite. It must already have the design-specific physical preparation, including a power grid, clock network where needed, ties, tap/endcap cells and legal placement. The routing recipe does not infer readiness from the existence of the file. It does not automatically consume `placed.odb`: connecting these starters into a complete flow requires those intervening steps. The emitted post-route netlist must still pass RustLane's independent post-physical CEC gate against the RustSys netlist.

## Configuring the adapters

Add explicit collateral paths to the existing design configuration. Paths are resolved relative to that configuration file and snapshotted by RustLane. There is no implicit PDK discovery or include-file search. The following is a `physical_steps` fragment for a configuration stored at the RustLane repository root; it requires the real collateral listed above and the ordinary RustSys/RustSTA configuration fields:

```json
{
  "flow": "classic",
  "physical_steps": [
    {
      "id": "route_extract",
      "tool": "openroad",
      "script": "examples/physical/route-extract.tcl",
      "arguments": ["-exit", "{driver}"],
      "outputs": [
        {"kind": "netlist", "path": "routed.nl.v"},
        {"kind": "spef", "path": "routed.spef"},
        {"kind": "def", "path": "routed.def"},
        {"kind": "odb", "path": "routed.odb"},
        {"kind": "other", "path": "routing.guide"},
        {"kind": "other", "path": "router-drc.rpt"}
      ]
    },
    {
      "id": "streamout",
      "tool": "klayout",
      "script": "examples/physical/streamout.py",
      "arguments": ["-b", "-r", "{script}", "-rd", "context_path={context}"],
      "outputs": [
        {"kind": "gds", "path": "layout.gds"},
        {"kind": "other", "path": "streamout.json"}
      ]
    }
  ]
}
```

This fragment is not a standalone valid design configuration. Supply one explicit matching parasitic corner, actual synthesis libraries/SDC/RTL, engine/tool identities and all required collateral. A router's diagnostic file is declared `other`, because it is not a foundry signoff DRC verdict. Missing outputs or tool failures must fail the run.

For a geometric placement experiment, the first script uses the same OpenROAD arguments and declares the three `placed.*` outputs. Its existence does not satisfy Classic's netlist/SPEF/GDS requirements.

RustLane supplies Tcl globals `rustlane_top`, `rustlane_netlist`, `rustlane_sdc`, `rustlane_spef`, `rustlane_output_dir`, `rustlane_libraries`, `rustlane_collateral`, `rustlane_artifacts`. The KLayout script receives the corresponding context JSON through the documented [batch arguments](https://www.klayout.de/command_args.html), `-rd context_path={context}`. It executes in the external KLayout interpreter; RustLane's core crates do not depend on Python or a GUI library. Arguments expand without a shell.

## Data-only settings

`floorplan_settings` and `route_settings` are UTF-8 files containing a Tcl dictionary, not an executable Tcl script. Write whitespace-separated key/value pairs; brace list values. Do not include `set`, comments or a surrounding dictionary brace pair. Each file is limited to 8192 characters and unknown keys are rejected.

| Floorplan setting | Required value |
| --- | --- |
| `site` | Actual placement-site identifier from the cell LEF |
| `dbu_per_micron` | Technology LEF database units: one of `100`, `1000`, `10000`, `100000`, `1000000` |
| `die_area_dbu` | Four nonnegative integer DBU coordinates `{x0 y0 x1 y1}`, positive rectangle, each at most `1000000000000` |
| `core_area_dbu` | Positive integer DBU rectangle contained in the die |
| `density_pct` | Integer in `1..80` |
| `horizontal_pin_layers` / `vertical_pin_layers` | Lists of `1..8` actual layer identifiers |
| `seed` | Integer in `1..2147483647` |

Geometry settings remain integer DBU. At the documented [OpenROAD floorplan API](https://openroad.readthedocs.io/en/latest/main/src/ifp/README.html) boundary, the script renders exact decimal micron strings using integer division; it does not perform floating-point coordinate arithmetic. The caller must qualify that `dbu_per_micron` matches the technology LEF. Only nonnegative rectangular die/core shapes are supported by this starter. Macros and specialized physical structures need a separate recipe.

| Routing setting | Required value |
| --- | --- |
| `signal_layers` / `clock_layers` | Explicit `lower-upper` routing layer identifiers matching the prepared ODB |
| `grt_iterations` | Integer in `1..1000` |
| `drt_iterations` | Integer in `1..64` |
| `seed` | Integer in `1..2147483647` |
| `extraction_corner` | Safe corner handle associated with model index 0 of `rcx_rules` |

The extraction deck must match the exact technology, metal stack, corner and extraction-engine version. The script uses the documented [`set_extraction_rules_file` API](https://openroad.readthedocs.io/en/latest/main/src/rcx/README.html); a tool lacking this API must fail rather than substitute LEF-based guessed RC. One extracted SPEF is produced; its association to the RustSTA corner is an explicit caller responsibility.

`streamout_limits` is strict JSON, for example:

```json
{"max_cells": 10000, "max_edges": 50000, "max_input_bytes": 134217728}
```

Hard supported maxima are 100,000 cells, 500,000 unique child edges and 1 GiB aggregate input bytes. Context/settings JSON has separate small byte caps. These are work/memory ceilings, not PDK readiness checks. Additional explicit macro files can be supplied as `macro_lef_NAME` and `macro_gds_NAME`, up to 32 of each, processed in sorted-key order.

The LEF/DEF map must be a real PDK-specific mapping of routing, pin, special-net and via purposes to the correct GDS layer/datatype values. It is not a `.lyp` display-color file. The recipe disables discovery of neighboring LEFs and uses KLayout's documented [macro-resolution mode 2](https://www.klayout.de/doc/code/class_LEFDEFReaderConfiguration.html) with actual GDS files. It does not substitute LEF outlines for missing physical cell geometry. It rejects empty/unresolved cells and validates the compressed cell hierarchy with bounded topological processing; it never flattens arrays or enumerates paths. Unknown layer-map omissions and incorrect GDS/LEF combinations still require independent PDK qualification and signoff.

GDS output includes the configured top and its hierarchy, with current timestamps disabled using documented [save options](https://www.klayout.de/doc/code/class_SaveLayoutOptions.html). The script's `streamout.json` deliberately records `drc_status: unchecked`, `lvs_status: unchecked`, and `pdk_validation: unverified`.

## Resource and validation boundary

Before a real run, estimate native physical objects and geometry, not only input file size: cells × per-cell state plus wire/via/polygon counts × native shape storage, plus extraction vectors and corner/thread concurrency. A conservative planning allowance is at least 16 times the uncompressed input bytes plus tool startup and per-cell/per-edge metadata, followed by measured refinement on the actual tool/PDK. This is not a guaranteed upper bound. Keep the RustLane process memory/wall-time guards enabled and serialize estimated runs over 4 GiB behind the scale lock.

The streamout checker adds at most approximately 64 MiB metadata at its hard cell/edge limits with one worker. Native import happens before topology counts can be checked, so the process cgroup/OS memory cap is still required. It cannot be replaced by the Python node budget.

Local validation on 2026-10-23 used installed KLayout `0.30.0`: a synthetic one-cell LEF/DEF and a primitive GDS rectangle produced a readable two-cell GDS, preserving the cell's integer-coordinate geometry and the DEF instance. No PDK was used. The final invocation was guarded by `MemoryMax=1G`, `MemorySwapMax=0`, `timeout 30s`; `/usr/bin/time -v` measured 393,356 KiB peak RSS and 0.74 s wall time. This exceeded the initial 256 MiB startup estimate, so use at least 512 MiB for this host's small KLayout startup when planning subsequent runs. Complete local smoke receipts are in `.work/physical-smoke/`.

Tcl parser completeness and rejection of missing collateral were checked with `tclsh`. OpenROAD was unavailable, so floorplan, placement, routing and RCX API execution remain **unverified**. The examples require qualification against both `sky130_fd_sc_hd` and a GF180 `mcu7t5v0`/`mcu9t5v0` library with real physical collateral. No full-chip timing closure, CTS, PDN, DRC, LVS, antenna closure, IR drop or manufacturability result is claimed.

## Public API sources

Only public interface documentation informed these original scripts; no reference-tool implementation source was inspected or copied.

- [OpenROAD design read/write API](https://openroad.readthedocs.io/en/latest/main/src/README.html), [floorplan](https://openroad.readthedocs.io/en/latest/main/src/ifp/README.html), [pin placement](https://openroad.readthedocs.io/en/latest/main/src/ppl/README.html), [global placement](https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html), [placement legality](https://openroad.readthedocs.io/en/latest/main/src/dpl/README.html).
- [OpenROAD global routing](https://openroad.readthedocs.io/en/latest/main/src/grt/README.html), [detailed routing](https://openroad.readthedocs.io/en/latest/main/src/drt/README.html), [RC extraction](https://openroad.readthedocs.io/en/latest/main/src/rcx/README.html).
- [KLayout batch arguments](https://www.klayout.de/command_args.html), [LEF/DEF reader configuration](https://www.klayout.de/doc/code/class_LEFDEFReaderConfiguration.html), [layout API](https://www.klayout.de/doc/code/class_Layout.html), [cell API](https://www.klayout.de/doc/code/class_Cell.html), [GDS save options](https://www.klayout.de/doc/code/class_SaveLayoutOptions.html).
