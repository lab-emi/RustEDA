# Ibex with SRAM macros on SKY130 and GF180MCU

Benchmark inputs for the Ibex RTL-to-GDS comparison on two more open PDKs, next to the IHP SG13G2 recipe in
[`../ibex_sg13g2_ez130`](../ibex_sg13g2_ez130/README.md). Each profile gives the same Ibex v2.1.0 RV32IMC core
(revision `38c0709`) two 2 KiB memory banks built from SRAM macros that have been taped out on that process, the
IHP benchmark's timing constraints, and a LibreLane reference configuration.

| Profile | PDK | Cell library | SRAM macro | Banks |
| --- | --- | --- | --- | --- |
| `sky130hd` | SkyWater `sky130A` | `sky130_fd_sc_hd` | OpenRAM `sky130_sram_2kbyte_1rw1r_32x512_8` (512 × 32, byte write mask; 683.1 × 416.54 µm) | one macro each |
| `gf180mcu` | GlobalFoundries `gf180mcuD` | `gf180mcu_fd_sc_mcu7t5v0` | foundry `gf180mcu_fd_ip_sram__sram512x8m8wm1` (512 × 8; 431.86 × 484.88 µm) | four macros each, one per byte lane |

The IHP benchmark has two 4 KiB banks (one `RM_IHPSG13_1P_1024x32_c2_bm_bist` each); here the banks are 2 KiB,
because 512 words is the largest size both macro families offer. Results are compared between the two flows on
one PDK, never between PDKs.

## Files

- `<profile>/ibex_sram_bench.sv`: the top module. Same ports as the IHP benchmark's, with a 9-bit loader address.
- `<profile>/profile.json`: PDK variant, cell library, macro, its instances, the driving cell of the constraints.
- `<profile>/config.json.in`: the LibreLane reference configuration (`@WORKSPACE@` and `@PDK@` are filled in).
- `gf180mcu/pdn.tcl`: the power grid for the reference flow, written against OpenROAD's public PDN commands; the
  foundry SRAM's supply pins are Metal3 straps, which LibreLane's default grid does not connect.
- `prepare.py`: writes `rtl/design.v` (sv2v of the pinned Ibex sources, the wrapper and a blackbox declaration cut
  from the PDK's own macro Verilog), `constraints.sdc` (the IHP benchmark's constraints with this library's
  driving cell), `config.json` and `preparation.json` (every input with its SHA-256) into a fresh workspace.
- `figures/render_gds.py`: KLayout batch script that renders a GDS with the PDK's layer properties.

```sh
python3 integration/ibex_pdks/prepare.py --profile gf180mcu --workspace WORK/prep \
  --ibex-source /path/to/ibex --pdk-root /path/to/pdk-root --gnu-time /path/to/time
```

## PDK trees used

| PDK | Source | Notes |
| --- | --- | --- |
| `gf180mcuD` | `wafer-space/gf180mcu` at `ac7d869` (open_pdks `40cee97` with the gf180mcuD changes that were later upstreamed) | Includes `gf180mcu_fd_ip_sram` and the LibreLane configuration. LibreLane 3.0.17 pins open_pdks `5443591` for gf180mcu. |
| `sky130A` | open_pdks 1.0.471 (`97d0844`), the `open_pdks.sky130a` package of the litex-hub conda channel, build `20240223_100318`; only `sky130_fd_sc_hd` (three Liberty corners) and `libs.tech` are installed | LibreLane 3.0.17 pins open_pdks 1.0.530 (`8afc834`). The LibreLane configuration files of the two versions (`config.tcl`, the library's `config.tcl`, exclusion lists, tracks, OpenRCX rules) are identical except `FILL_CELL` and `DECAP_CELL`; the reference configuration sets both to the pinned version's values. |
| SRAM macros for `sky130A` | `VLSIDA/sky130_sram_macros` at `965df15` | GDS, LEF, Verilog, LVS netlist and the TT 1.8 V 25 °C Liberty (the only corner published). |

## Reference configuration notes

Both configurations follow the IHP reference: absolute die, manual macro placement, 10 µm macro halos, the
benchmark's SDC for implementation and signoff, CPPR off as in the native flow, typical and slow corners,
LibreLane's own post-global-routing design repair, four detailed-routing threads. Two things differ from
LibreLane's defaults and are stated here because they affect the competitor:

- **Delay cells are excluded** (`EXTRA_EXCLUDED_CELLS`). With the OpenROAD build on the benchmark host (26Q3),
  the resizer takes a delay cell as its buffer (`gf180mcu_fd_sc_mcu7t5v0__dlyb_1`,
  `sky130_fd_sc_hd__clkdlybuf4s25_1`): LibreLane's own smoke test then inserts 29,453 buffers into 37 nets on
  gf180mcuD (4,353 on sky130A) and cannot legalize. Neither PDK's exclusion list names its delay cells. Hold
  repair therefore uses ordinary buffers in the reference flow. The IHP benchmark is not affected: with the
  EZ130 library, which has one delay cell (`DLY1X2`), the resizer of the same OpenROAD build buffered with
  `BUFX1` in the reference run.
- **STA corners** are the nominal-RC typical and slow corners, as in the IHP reference, not the PDK's nine.
- **The OpenRAM macro's Liberty is used as a derived copy** (`sky130hd` only). The published file limits the
  transition at the macro's address, data-in and write-mask inputs to 0.04 ns, the last index of its
  characterisation table. No `sky130_fd_sc_hd` cell can drive that (OpenROAD: best achievable 0.069 ns at
  0.01 pF), and LibreLane's post-global-routing design repair stops with `RSZ-0090`. `prepare.py` writes a copy
  without these three attributes (`macro/…_TT_1p8V_25C.lib`, source and derived SHA-256 in `preparation.json`);
  the library's `default_max_transition` of 0.5 ns then applies to those pins. Both flows and the shared timing
  gate read the copy. The macro publishes a typical-corner Liberty only, so it is used at both corners.
- **The KLayout stream-out is the reference layout on `sky130hd`, and the Magic/KLayout XOR is skipped there**
  (`PRIMARY_GDSII_STREAMOUT_TOOL`, `RUN_KLAYOUT_XOR`). Magic's stream-out does not reproduce the OpenRAM macro's
  GDS: the XOR of the two stream-outs reported 7,213,274 differences, the largest counts on the mask-data layers
  33/43 and 33/42, and wrote a 2.2 GB report. KLayout copies the macro's GDS as it is, so its stream-out is the
  layout the shared gates judge. On `gf180mcu` and IHP the two stream-outs are identical (0 differences) and
  the XOR stays in the reference flow.
- **Detailed placement may move a cell across a macro** (`PL_MAX_DISPLACEMENT_X/Y`), as in the IHP reference:
  with the default, a clock buffer that lands on a macro cannot be legalized (`DPL-0036`).
