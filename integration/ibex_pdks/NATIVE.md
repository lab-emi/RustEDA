# The native flow on another PDK

RustEDA's native Ibex flow is a step graph: a manifest of about a hundred hash-pinned steps
(`rusteda`, `rustsys`, `ruststa` and `rustroad` commands) that `rusteda flow exec` runs. Everything
that depends on the PDK is data in a **native PDK profile**, a directory
`integration/ibex_pdks/<profile>/native/`. This page says how to write one, how to compose its
manifest and how to run it. The published IHP SG13G2/EZ130 flow is the profile
[`ihp_sg13g2_ez130`](ihp_sg13g2_ez130/native/profile.json): its files point at the recipe in
[`../ibex_sg13g2_ez130`](../ibex_sg13g2_ez130/README.md), and composing it gives the published
manifest byte for byte.

What is generic, and what is not yet:

- **Generic**: the executor (`rusteda flow exec`, any `pdk`, `library` and corner names), the
  planning helpers (`rusteda frontend-profiles`, `frontend-physical`, `frontend-pins`,
  `frontend-validate`, `eco-round`, `estimate-spef`: corners, Liberty groups, database unit and
  guard values come from the profile), `rustroad physical-inputs` (any number of cell and macro
  GDS libraries, a projected or tracked layer map, an RC profile built by `rustroad rc-profile`
  for `ihp`, `sky130` or `gf180` or a tracked one), `rustroad postroute-repair`, and the three
  composers.
- **Two corners**: the ECO rounds and the post-route repair compare exactly two Liberty corners.
- **One extraction corner**: one nominal SPEF serves both timing corners, as on IHP.
- **Referee steps** (OpenRCX extraction, signoff timing, foundry DRC, antenna DRC, LVS, macro
  contract) are the IHP wrappers and are composed for IHP only. On another PDK the manifest ends
  with the final exports (`be_gds`) and the shared gates judge the layout afterwards.
- **The sixteen-gate qualification composer** (`native_flow.py`) and the worker protocol of
  `rusteda flow prepare`/`finish` remain bound to the IHP recipe.
- Whether a PDK's data lets every step succeed is a property of that data and of the engines,
  not of this plumbing: `rusteda flow exec --dry-run` proves the plumbing, a run proves the rest.

## Adding a PDK profile

1. **Prepare the design** with [`prepare.py`](README.md): `rtl/design.v`, `constraints.sdc`. The
   SDC must hold the literal clock, driving-cell and load statements the planning helpers
   project (see `selection.json` below).
2. **Create `integration/ibex_pdks/<profile>/native/`** with the files of the reference below.
   Start from the PDK's own files (Liberty, LEF, GDS, KLayout layer map, OpenRCX rules, Magic
   technology file) and from the engine fixtures in `modules/RustROAD/fixtures/pdk/`. Pin every
   PDK collateral file by SHA-256 in `profile.json`.
3. **Write the host map** (`host.json`): where the PDK tree, the prepared design and the tools
   are on this machine.
4. **Compose**:

   ```sh
   python3 integration/ibex_host_benchmark/compose_native.py \
     --host host.json --workspace WORK/compose --base-rev empty \
     --profile integration/ibex_pdks/<profile>/native
   ```

   The driver resolves and hashes every file, writes `frontend-inputs.json` and
   `backend-inputs.json`, runs `native_frontend.py build` and `native_backend.py` with the
   profile and leaves `WORK/compose/be/prefix-manifest.json` (`rusteda.native-flow.v1`). It
   refuses a profile field it does not know, a file whose SHA-256 differs from the pinned one,
   a CPU allocation the profile does not list, and a file set the composers cannot place.
5. **Verify without running** (hashes every pinned input, checks the manifest against the pinned
   profile, prints the resolved step graph; nothing is created):

   ```sh
   rusteda flow exec --manifest WORK/compose/be/prefix-manifest.json --project WORK/compose/be \
     --output WORK/run/output --pdk sky130 --dry-run
   ```

6. **Run** a prefix or the whole graph. The executor needs a worker identity and an ownership
   directory, and the owning controller provides the memory limit and the deadline
   ([`ibex_host_benchmark`](../ibex_host_benchmark/README.md) shows a measured run):

   ```sh
   export RUSTEDA_WORKER_ID=$(python3 -c "import secrets;print(secrets.token_hex(32))")
   export RUSTEDA_OWNERSHIP=WORK/run/ownership
   rusteda flow exec --manifest WORK/compose/be/prefix-manifest.json --project WORK/compose/be \
     --output WORK/run/output --base-rev empty --pdk sky130 --stop-after fe_synth_export
   ```

   `--stop-after be_gds` runs everything. `rusteda flow exec-status --output WORK/run/output`
   reports progress from any process. A failed step leaves its command line, its streams and a
   `failed` summary; fix the data (or the engine) and run again in a new output directory.

What the steps do with the profile:

| Steps | Reads |
| --- | --- |
| `fe_context` | renders the pinned files, the tools and `selection.json` for the helpers |
| `fe_profiles` (`rusteda frontend-profiles`) | the cell Liberty of every corner (`std_<corner>`), the first cell LEF, the SDC, `floorplan.json` (database unit, pin buses), `interfaces.json`; the profile's `pdk`, `library` and corners label the delay and sizing profiles |
| `fe_caps_<corner>_<group>` | one pin-capacitance table per Liberty file |
| `fe_synth*` | the first corner's Liberty files, `synthesis.dont_use` |
| `fe_*_import`, `fe_*_run`, reports | every corner's Liberty files, the SDC |
| `fe_eco_*` (`rusteda eco-round`) | the tables and reports of both corners, `selection.json` |
| `fe_physical_config` (`rusteda frontend-physical`) | `physical.json`, `routing.json`, checked against `contract.json` |
| `fe_pins` (`rusteda frontend-pins`) | the combined LEF, `floorplan.json` |
| `fe_*_pdk_import`, `place`, `cts` | technology LEF, combined LEF, `floorplan.json`, the materialized physical configuration |
| `fe_*_estimate` | combined LEF, `rc-estimate-policy.json` |
| `be_profiles` (`rustroad physical-inputs`) | the GDS libraries, the layer map and the RC profile sources |
| `be_route`, `be_antenna_*`, `be_extract`, `be_postroute` | the materialized routing configuration, the antenna policies, the RC profile, both corners' Liberty files |
| `be_def`, `be_netlist`, `be_powered`, `be_gds` | the final exports |

## Profile reference

### Directory layout

```
integration/ibex_pdks/<profile>/native/
  profile.json                 index: identity, corners, PDK files, macros, RC source, flow options   (required)
  floorplan.json               RustROAD floorplan                                                      (required)
  physical.json                RustROAD physical configuration (PhysicalConfig)                        (required)
  routing.json                 RustROAD routing configuration                                          (required)
  selection.json               cell selection of the ECO rounds                                        (required)
  interfaces.json              macro pin buses (and top buses, body ties for LVS)                      (required)
  rc-estimate-policy.json      placement-stage RC estimate policy                                      (required)
  contract.json                the qualified values the flow's guards compare the policies against     (required)
  antenna-group-policy.json    grouped antenna session budgets                                         (required)
  antenna-session-policy.json  antenna session execution policy                                        (required)
  gds-map.json                 tracked rustroad.gds-layer-map.v1 (only with gds_layer_map.tracked)     (optional)
  rc-nom.json                  tracked rustroad.rc-profile.v1 (only with rc.profile)                   (optional)
```

The file names are defaults; `profile.json` `policies` may point any of them elsewhere (the IHP profile points at
the recipe directory). Every file is JSON, UTF-8, at most 2 MiB (the floorplan and interfaces at most 1 MiB:
the helpers' own caps).

### File references

Wherever this document says *ref*, the value is either a string `"<root>:<relative path>"` or an object
`{"path": "<root>:<relative path>", "sha256": "<64 hex>"}`. With `sha256` the composer refuses to compose when
the file's digest differs (use it for every PDK collateral file whose provenance matters: Liberty, LEF, GDS,
rules, tech files). The composer records every file in the manifest by absolute path, SHA-256 and size in
any case; the executor re-verifies all of them before the first step.

Roots:

| Root | Directory |
| --- | --- |
| `profile` | this `native/` directory |
| `prep` | the prepared design workspace (host map `prepared`): `rtl/design.v`, `constraints.sdc` |
| `repo` | the repository root |
| `recipe` | the host map's `recipe_dir` (default `integration/ibex_sg13g2_ez130`) |
| any other name | host map `roots.<name>`, else the host map's top-level key `<name>` (a directory, or a file when the relative path is empty) |

By convention the PDK tree (the directory that holds `libs.ref` and `libs.tech`) is the root `pdk`, so a host
map for SKY130 has `"roots": {"pdk": "/path/to/sky130A"}` and for GF180
`"roots": {"pdk": "/path/to/gf180mcuD"}`. A relative path never contains `..`.

#### `profile.json` (`rusteda.native-pdk-profile.v1`)

```json
{
  "schema": "rusteda.native-pdk-profile.v1",
  "name": "sky130hd",
  "pdk": "sky130A",
  "library": "sky130_fd_sc_hd",
  "top": "ibex_sram_bench",
  "corners": [
    {"name": "tt", "rc": "nom",
     "liberty": {"std": "pdk:libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib",
                 "ram": "pdk:libs.ref/sky130_sram_macros/lib/sky130_sram_2kbyte_1rw1r_32x512_8_TT_1p8V_25C.lib"}},
    {"name": "ss", "rc": "nom",
     "liberty": {"std": "pdk:libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__ss_100C_1v60.lib",
                 "ram": "pdk:libs.ref/sky130_sram_macros/lib/sky130_sram_2kbyte_1rw1r_32x512_8_TT_1p8V_25C.lib"}}
  ],
  "lef": {
    "technology": "pdk:libs.ref/sky130_fd_sc_hd/techlef/sky130_fd_sc_hd__nom.tlef",
    "cells": ["pdk:libs.ref/sky130_fd_sc_hd/lef/sky130_fd_sc_hd.lef"],
    "combined": null
  },
  "gds": {"cells": ["pdk:libs.ref/sky130_fd_sc_hd/gds/sky130_fd_sc_hd.gds"]},
  "macros": [
    {"id": "ram", "master": "sky130_sram_2kbyte_1rw1r_32x512_8", "instances": ["u_imem", "u_dmem"],
     "lef": "pdk:libs.ref/sky130_sram_macros/lef/sky130_sram_2kbyte_1rw1r_32x512_8.lef",
     "gds": "pdk:libs.ref/sky130_sram_macros/gds/sky130_sram_2kbyte_1rw1r_32x512_8.gds"}
  ],
  "gds_layer_map": {"klayout": "pdk:libs.tech/klayout/tech/sky130A.map",
                    "layers": ["li1", "mcon", "met1", "via", "met2", "via2", "met3", "via3", "met4", "via4", "met5"]},
  "rc": {
    "builder": "sky130",
    "resistance_model": "magic",
    "rules": {"path": "pdk:libs.tech/openlane/rules.openrcx.sky130A.nom.calibre", "sha256": "<64 hex>"},
    "magic_tech": "pdk:libs.tech/magic/sky130A.tech",
    "magic_extract": null
  },
  "synthesis": {"dont_use": ["sky130_fd_sc_hd__*dbl*"]},
  "policies": {},
  "allocations": [2, 4, 16],
  "referee_steps": []
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | string | exactly `rusteda.native-pdk-profile.v1` |
| `name` | string | the profile directory name (`sky130hd`, `gf180mcu`, `ihp_sg13g2_ez130`) |
| `pdk`, `library` | string, `[A-Za-z0-9_.-]{1,64}` | manifest identity. They are written into the manifest (`pdk`, `library`), its profile record, and the labels of the delay and sizing profiles. Use the PDK variant and the cell library name (`sky130A`/`sky130_fd_sc_hd`, `gf180mcuD`/`gf180mcu_fd_sc_mcu7t5v0`). |
| `top` | string | top module; must equal `selection.json` `top` and `floorplan.json` `name` |
| `corners` | list, 1 to 4 entries (exactly 2 for a complete flow today) | timing corners in order. The **first** corner is the typical one: synthesis maps with its libraries, and it is the reference corner of the buffer and sizing profiles. `name`: `[a-z][a-z0-9]{0,7}`, unique (it appears in step identities such as `fe_caps_<corner>_std` and in report names). `rc`: the extraction corner its SPEF comes from; only `nom` is implemented (one SPEF for all corners, as on IHP). `liberty`: one ref for `std` (the cell library) and one for **each** macro `id`, in any key order; all corners have the same keys. The same file may serve two corners (SKY130 SRAM: the TT Liberty at `tt` and `ss`). Liberty files are at most 64 MiB each. |
| `lef.technology` | ref | technology LEF (`rustroad import-pdk --tech-lef`; also the source of the database unit check and of the cut layers of the GDS layer map) |
| `lef.cells` | list of refs, at least one | cell LEFs. The first is the library LEF from which the helpers read cell widths, classes and antenna data of the selected buffers and the port diode. |
| `lef.combined` | ref or null | one LEF with every cell and every macro `MACRO` (what `rustroad import-pdk --cell-lef`, `rusteda estimate-spef --lef` and the pin planner read). `null`: the composer derives it into the compose workspace: the first `lef.cells` file without its final `END LIBRARY`, then the `MACRO <name> ... END <name>` blocks of the remaining `lef.cells` files and of every macro LEF in profile order, then `END LIBRARY`. Give a ref when that rule does not produce a LEF the reader accepts, and say why in your report. |
| `gds.cells` | list of refs, at least one | cell GDS files (32 MiB each at most) |
| `macros` | list, 0 to 8 masters | hard-macro masters. `id`: `[a-z][a-z0-9]{0,7}`, unique, not `std` or `std<digits>` (it names the manifest file keys `<id>_<corner>`, `<id>_lef`, `<id>_gds`; use `ram` for a single SRAM master). `master`: the LEF `MACRO` name. `instances`: the instance names in the netlist, in the order of `floorplan.json` `macro_placements`. `lef`, `gds`: refs. The pin buses of each master are in `floorplan.json` `pin_buses` and `interfaces.json` `cell_buses` (below). |
| `gds_layer_map` | object | either `{"klayout": ref, "layers": [...]}`: the PDK's KLayout LEF/DEF layer map, projected in the flow by `rustroad physical-inputs` onto the listed LEF layers (routing and cut layers, bottom to top; a layer is a cut layer when the technology LEF declares it `TYPE CUT`); or `{"tracked": ref}`: a `rustroad.gds-layer-map.v1` file used as it is (for example a copy of `modules/RustROAD/fixtures/pdk/<pdk>-gds-map.json`). |
| `rc` | object | source of the nominal extraction profile (`rustroad.rc-profile.v1`), built in the flow by `rustroad physical-inputs`: `builder` is the table entry of `rustroad rc-profile` (`ihp`, `sky130`, `gf180`); `resistance_model` is `magic` (sheet and contact resistance from the Magic `extract` section; the only model qualified outside IHP) or `openrcx-pdk`; `rules` is the nominal OpenRCX calibration table **with its SHA-256** (the pinned RC calibration: the backend composer refuses another file); `magic_tech` is the Magic technology file with the `extract` section, `magic_extract` a second file when the PDK splits it (IHP), else `null` (the key must be present); `calibration` is IHP only (the name its recipe contract gives the rules). Alternative: `{"profile": ref}`, a tracked `rustroad.rc-profile.v1` used as it is; its `technology_lef_sha256` must equal the digest of `lef.technology`. |
| `synthesis.dont_use` | list of globs | passed as `rustsys synth --dont-use <glob>` (cells the placer cannot place, such as double-height masters; cells excluded in the reference flow too). Optional, default empty. |
| `policies` | object | overrides of the default file names of section 1: any of `floorplan`, `physical`, `routing`, `selection`, `interfaces`, `rc_estimate`, `contract`, `antenna_group`, `antenna_session`, each a ref. Optional. |
| `allocations` | list of integers | CPU allocations (`host.json` `threads`) the flow is qualified for. `4` runs the frontend as one dependency graph with four-thread timing runs; other values run it serially with two workers. Optional, default `[2, 4, 16]`. |
| `referee_steps` | list | which referee steps the backend appends after the final exports: a prefix of `["openrcx", "timing", "main", "antenna", "lvs", "macro_contract"]`. They are the IHP wrappers; a new PDK uses `[]` (the manifest ends at `be_gds`, and the shared gates of `integration/ibex_pdks/gates/` judge the layout afterwards). Optional, default `[]`. |
| `referee_files` | object | IHP only: the pinned files of its referee steps (`<manifest key>: ref`). Omit. |
| `flow_schema` | string | IHP only: `rusteda.ihp-flow.v1` composes the published IHP manifest (no profile pin, its own file names). Omit: every other profile composes `rusteda.native-flow.v1`, which pins `profile.json` and `contract.json` for the executor and the planning helpers. |

Unknown fields are refused.

### Manifest file keys the profile produces

For orientation (you do not write these): `rtl`, `sdc` (from `prep`), `std_<corner>`, `<macro id>_<corner>`,
`tech_lef`, `cell_lef` (the combined LEF), `std_lef` (the first cell LEF),
`std_gds` (further cell libraries `std2_gds`, `std3_gds`, ...), `<macro id>_gds`, `layer_map` or `gds_map`,
`rules_nom`, `magic_tech`, `magic_extract`, `rc_profile`, `floorplan`, `physical`, `routing`, `interfaces`,
`rc_policy`, `profile`, `contract`, `antenna_group_policy`, `antenna_session_policy`, plus the pinned reference
sources of the recipe (`<macro id>_lef` only with referee steps).
A command line in your bring-up driver that uses these roles maps one to one onto the composed flow.

### Policy files

#### `floorplan.json` (RustROAD floorplan, read by `rustroad import-pdk --floorplan`)

As `integration/ibex_sg13g2_ez130/native-floorplan.json`: `name`, `units_per_micron` (database units per
micron: 1000 on SKY130, 2000 on GF180; it must equal the technology LEF's `DATABASE MICRONS`, and it is the
unit of every coordinate in this file, in `physical.json` and in `routing.json`), `die_area` and `core_area`
(`lx, ly, ux, uy`), `site` (`name, width, height`), `rows` (`y, x_start, x_end`), `macro_placements`
(`<instance>: {x, y, orientation, halo}`), `pin_buses` (`<master>: {<bus>: [msb, lsb]}`). Die area and macro
placement are those of the profile's LibreLane configuration (fairness rule). The planning helpers take the
database unit from `units_per_micron`.

#### `physical.json` (RustROAD `PhysicalConfig`) and `routing.json`

As `native-physical.json` and `native-routing.json` of the IHP recipe; the engine defines the fields. The flow
adds two requirements: `physical.json` has a `clock` object (the flow fills in `clock.root_net`), a `tap` object
and a `filler_cells` list; `routing.json` has `track_router.min_routing_layer` and
`track_router.max_routing_layer`. Both files are checked against `contract.json` (below). An optional
`limits.replay` block must be the template of the IHP file or absent.

#### `selection.json` (cell selection of the ECO rounds)

The former `SELECTION` constant of `compose_native.py`, as data. All cell names are Liberty/LEF cell names of
`library`; pins are that library's pin names.

```json
{
  "top": "ibex_sram_bench", "primary_clock": "clk_i", "clock_sink_pin": "CK",
  "gating_cells": ["AND2X3"],
  "buffers": [{"cell": "BUFX1", "input": "A", "output": "Y"}],
  "primary_buffer": "BUFX24",
  "reset": {"pin": "RB", "leaf": "BUFX8", "trunk": "BUFX16"},
  "delay_cells": ["DLY1X2"],
  "target_fanout": 9, "cap_fraction": 0.8, "hold_margin_ns": 0.075, "setup_margin_ns": 0.05,
  "port_diode": {"cell": "ANTENNA2", "pin": "A"},
  "electrical": {
    "driver_cells": ["BUFX3", "BUFX4"], "sram_buffer": "BUFX4", "sram_clock_buffer": "BUFX8",
    "sram_masters": ["RM_IHPSG13_1P_1024x32_c2_bm_bist"],
    "load_fraction": 0.6, "max_wire_um": 1200, "antenna_cells": ["ANTENNA2"],
    "drv_margin": 0.15, "hold_drv_margin": 0.1,
    "clock_balance": {"cell": "DLY1X2", "max_stages": 32, "margin_ns": 0.1, "wire_pf": 0.002}
  },
  "launch_chain_max": 32
}
```

Rules the helpers enforce (so that you can check your data before composing):

- `buffers` plus `delay_cells`: at most 32 names, all distinct. Every one must be, in **every** corner's cell
  Liberty, a cell without `dont_use`, with exactly one input and one output pin, an output `function` that is
  the input's name, and one `positive_unate` combinational arc; it must have a `MACRO` with `SIZE` in the first
  cell LEF whose width is a whole number of database units. Delay cells are listed in `delay_cells` only (not in
  `buffers`); they need not be sizable.
- `reset.leaf` must be a combinational cell with a function in the first corner's Liberty (it gives the sizing
  family's signature). `primary_buffer`, `reset.leaf`, `reset.trunk`, the `electrical` buffers are members of
  `buffers`. `clock_balance.cell` is one of `delay_cells`.
- `port_diode` is optional (omit the key or give `null` for a diode-free flow). When present: its LEF class is
  `CORE ANTENNACELL` (compared case-insensitively), it has exactly one signal pin, an `INPUT` with
  `ANTENNADIFFAREA`, and in every corner's Liberty a single function-free pin. `electrical.antenna_cells` lists
  the diode masters the ECO may meet.
- `clock_sink_pin` is the clock pin name of the library's flops; `reset.pin` the asynchronous reset pin;
  `gating_cells` the cells the RTL's clock gates map to; `electrical.sram_masters` the macro masters (every
  `macros[].master`).
- `primary_clock` is the clock port; the SDC must hold one literal `create_clock -name <it> -period P
  [get_ports {<it>}]`, `set_clock_transition`, `set_clock_uncertainty`, `set_max_fanout`, and literal
  `set_driving_cell`/`set_load` lines in ns and pF (no `set_units`), as the prepared SDC has.

#### `interfaces.json`

`{"cell_buses": {<master>: {<bus>: [msb, lsb]}}, "top_buses": {<bus>: [msb, lsb]}, "body_ties": {...}}`.
`cell_buses` is required and must equal `floorplan.json` `pin_buses` exactly (same masters, buses, order);
the flow exports every netlist with these bus ranges. `top_buses` and `body_ties` are read by LVS reference
builders only and may be empty objects.

#### `rc-estimate-policy.json` (`rusteda.rc-estimate-policy.v1`)

As `integration/ibex_sg13g2_ez130/rc-estimate-policy.json`: `r_ohm_per_um`, `c_pf_per_um`, `detour`,
`max_segment_um`, `limits {max_nets, max_pins_per_net, max_nodes}`, optional `macro_blockage {grid_um,
max_searches}`, free-text `evidence` fields. Values are per PDK (derive them from a routed design or from the
PDK's layer RC table, and say which in `evidence`).

#### `contract.json` (`rusteda.native-pdk-contract.v1`)

The values the flow's guards compare against. They state which physical and routing policy is the qualified
one; `rusteda frontend-physical` refuses a `physical.json` or `routing.json` that differs, so a policy edit
needs a contract edit, visibly.

```json
{
  "schema": "rusteda.native-pdk-contract.v1",
  "physical": {
    "filler_cells": 9,
    "tap": {
      "body_view": "gds",
      "lattices": [
        {"max_gap": 15000, "pattern": null},
        {"max_gap": 28980, "pattern": {"kind": "staggered", "phase": 14490}},
        {"max_gap": 42000, "pattern": {"kind": "staggered", "phase": 21000, "unpaired": "cell_top"}}
      ]
    }
  },
  "routing": {"min_routing_layer": "Metal2", "max_routing_layer": "TopMetal2"}
}
```

- `physical.filler_cells`: the number of entries `physical.json` `filler_cells` must have.
- `physical.tap.body_view`: the required `physical.json` `tap.body_view` (`gds` or `lef`).
- `physical.tap.lattices`: the admitted tap lattices; `physical.json` `tap.max_gap` and `tap.pattern` (absent
  counts as `null`) must equal one entry. One entry is enough.
- `routing.min_routing_layer`, `routing.max_routing_layer`: the required `routing.json` `track_router` values.

(The example shows the IHP values.)

#### `antenna-group-policy.json`, `antenna-session-policy.json`

PDK-independent budgets of the native antenna session; write them with the values below. The backend
composer accepts exactly the qualified values: group policy `{"group_names": 128, "max_groups": 1024,
"max_attempts": 64, "max_total_jumpers": 4096, "max_commands": 4096, "max_report_bytes": 8589934592,
"max_selected_report_bytes": 134217728, "max_metadata_bytes": 4194304, "max_cumulative_work": 200000000000}`
and session policy `{"schema": "rusteda.antenna-session-policy.v1", "execution_mode": "single_native_process",
"actual_native_processes": 1}`.

### Host map (not part of the profile)

`host.json` for a new PDK: `profile` (path of the `native/` directory, or the profile name; `--profile` on the
command line overrides it), `prepared` (`prepare.py` workspace), `roots` (`{"pdk": "<PDK tree>"}`), `threads`,
`cpu_affinity`, `tools` (`rustsys`, `rusteda`, `ruststa`, `rustroad`, `python`, `gnu_time`, `xxhsum`, `pin_caps`,
and for referee steps `appimage`, `opensta`, `yosys`), optional `recipe_dir`. The IHP host maps keep working
unchanged. A checkout without the module sources (a worktree without submodules) names the superproject
checkout that has them with `--source-root`.

### What the profile does not cover

- The shared signoff gates have their own profile (`integration/ibex_pdks/<profile>/gates.json`).
- The SDC and the RTL come from the prepared workspace (`integration/ibex_pdks/prepare.py`).
- One extraction corner (`nom`) for all timing corners; one cell library per corner.
- Per-PDK tuning of command-line budgets (expansion limits, timeouts) is not data yet; say in your report
  if a default of the IHP recipe does not fit.
