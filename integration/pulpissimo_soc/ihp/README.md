# PULPissimo IHP SG13G2 / EZ130 SRAM test

This directory adapts the prepared PULPissimo Verilog to the IHP SG13G2
standard-cell and SRAM ecosystem. It is a **synthesis-only** full-SoC test.

## Files

- `tc_sram_ihp.v` — PULPissimo `tc_sram` adapter plus the blackbox declaration
  of the official 1024×32 single-port macro. The adapter maps 8 or 16 macros to
  the PULPissimo 8192- or 16384-word banks, including byte-mask expansion and
  one-cycle read selection.
- `prepare_ihp.py` — replaces the behavioral `tc_sram` in the prepared
  PULPissimo input, marks the macro Liberty cell `is_macro_cell`, and writes an
  input manifest with every hash. The PDK is never edited in place.
- `simulate_ihp_sram.py` and `sim/tb.v` — Verilator smoke test of the adapter
  against the official `-DFUNCTIONAL` macro model: two macros, read/write and a
  byte-masked write.

## Inputs

- Prepared PULPissimo Verilog from `integration/pulpissimo_soc/prepare.py`
  (SHA-256 `bbc8aac0…` for this run).
- EZ130 8T TT Liberty,
  `ez130_8t_tt_1p20v_25c.lib`.
- Official IHP macro Liberty,
  `RM_IHPSG13_1P_1024x32_c2_bm_bist_typ_1p20V_25C.lib`.

The macro Liberty lacks the optional `is_macro_cell` attribute. The preparation
copy adds it so RustSys preserves the macro instead of trying to synthesize the
Liberty memory model. RustSys records the macro as an opaque instance; its
outputs are free cut points.

## Run

```sh
python3 integration/pulpissimo_soc/ihp/prepare_ihp.py \
  --input .work/pulpissimo-soc/pulpissimo_soc.v \
  --wrapper integration/pulpissimo_soc/ihp/tc_sram_ihp.v \
  --macro-lib "$IHP_SRAM_LIB/RM_IHPSG13_1P_1024x32_c2_bm_bist_typ_1p20V_25C.lib" \
  --output .work/pulpissimo-soc-ihp/pulpissimo_soc_ihp.v \
  --out-lib .work/pulpissimo-soc-ihp/RM_IHPSG13_1P_1024x32_c2_bm_bist_macro.lib \
  --manifest .work/pulpissimo-soc-ihp/input.json

python3 integration/pulpissimo_soc/run_synthesis.py \
  --input .work/pulpissimo-soc-ihp/pulpissimo_soc_ihp.v \
  --lib "$EZ130_LIB/ez130_8t_tt_1p20v_25c.lib" \
  --extra-lib .work/pulpissimo-soc-ihp/RM_IHPSG13_1P_1024x32_c2_bm_bist_macro.lib \
  --rustsys /path/to/rustsys --top pulpissimo_soc_wrapper \
  --threads 16 --memory-gib 32 --timeout 900 \
  --output .work/pulpissimo-soc-ihp/pulpissimo_soc.v \
  --report .work/pulpissimo-soc-ihp/report.json \
  --receipt .work/pulpissimo-soc-ihp/receipt.json \
  --label "PULPissimo SoC, IHP SG13G2 TT, EZ130 + 80 official SRAM macros"
```

## Adapter simulation

```sh
python3 integration/pulpissimo_soc/ihp/simulate_ihp_sram.py \
  --wrapper integration/pulpissimo_soc/ihp/tc_sram_ihp.v \
  --ihp-verilog "$IHP_SRAM_VERILOG" \
  --workdir .work/pulpissimo-soc-ihp/sim
```

The test covers a 16384-word bank (16 macros): write/read slices 0 (address 5),
1 (1029) and 11 (11269), plus a byte-masked write at address 7. It requires Verilator and the official
`RM_IHPSG13_1P_1024x32_c2_bm_bist.v` plus
`RM_IHPSG13_1P_core_behavioral_bm_bist.v` models.

## Result

One run: **426.343 s**, peak RSS **1,316,868 KiB**, 161,034 standard cells plus
**80** SRAM macros, total area **13,259,619.34 µm²** (logic 2,045,003.79 µm²,
macros 11,214,615.55 µm²), logic depth 78. Full phase and warning details are in
[the benchmark page](../../../docs/benchmarks/pulpissimo-ihp.md).

The adapter simulation passes (`integration/pulpissimo_soc/ihp/simulate_ihp_sram.py`).
The `merge` pass hit its work budget; the result is bounded, not a quality
sweep. No full-SoC simulation, equivalence, timing closure, placement, routing,
DRC, LVS or GDS is claimed.
