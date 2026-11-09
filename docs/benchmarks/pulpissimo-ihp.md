# PULPissimo SoC synthesis on IHP SG13G2 / EZ130

This is a **synthesis-only** full-SoC test of PULPissimo on IHP SG13G2 with the
EZ130 8T standard-cell library and **80 official IHP SRAM macros**. It is not an
RTL-to-GDS result and it has no equivalence or timing-closure claim.

[Summary receipt](pulpissimo-ihp-20261106.json) ·
[Raw synthesis receipt](evidence/pulpissimo-ihp-20261106/synthesis-receipt.json) ·
[Report](evidence/pulpissimo-ihp-20261106/synthesis-report.json) ·
[Input manifest](evidence/pulpissimo-ihp-20261106/input.json) ·
[Adapter simulation](evidence/pulpissimo-ihp-20261106/adapter-simulation.json) ·
[Netlist record](evidence/pulpissimo-ihp-20261106/netlist.json) ·
[Harness](../../integration/pulpissimo_soc/ihp/README.md)

## Result

One run, 16 threads, 32 GiB address-space guard, 2026-11-06:

| | Value |
| --- | ---: |
| Wall time | **426.343 s** |
| Peak process RSS | 1,316,868 KiB (1.26 GiB) |
| Standard cells | 161,034 |
| SRAM macro instances | **80** |
| Total mapped instances | 161,114 |
| Combinational / sequential | 131,004 / 30,028 |
| Inverters / latches | 11,598 / 1,105 |
| Standard-cell area | 2,045,003.79 µm² |
| SRAM macro area | 11,214,615.55 µm² |
| Total area | **13,259,619.34 µm²** |
| Logic depth | 78 |
| Estimated delay | 119.08 ns, unconstrained |
| AIG nodes, before → after | 604,986 → 330,146 |
| State bits, RTL → retained | 36,389 → 30,028 |
| Warnings | 157 |

Phase breakdown: read 0.188 s, elaborate 4.268 s, optimize 416.377 s (merge
414.109 s), map 5.186 s, emit 0.112 s.

The `merge` optimization reached its work budget
(`pass_budget_reached: true`), so the area and depth are a **bounded**
synthesis result, not a quality-sweep optimum.

## Configuration

- **Design:** PULPissimo `bfc3d9a1ef72443464509a22d948f0a6f5b65b4c`, full SoC
  wrapper, `CORE_TYPE=1`, no FPU, XPULP or HWPE extensions.
- **PDK:** IHP SG13G2, TT 1.20 V / 25 °C.
- **Standard cells:** EZ130 8T, `ez130_8t_tt_1p20v_25c.lib`.
- **Memory:** full L2 capacity, 320 KiB total:
  - 4 interleaved banks × 16 × 1024 × 32 = 2,097,152 bits;
  - 2 private banks × 8 × 1024 × 32 = 524,288 bits;
  - **80** `RM_IHPSG13_1P_1024x32_c2_bm_bist` instances.
- **Adapter:** [tc_sram_ihp.v](../../integration/pulpissimo_soc/ihp/tc_sram_ihp.v)
  replaces the behavioral `tech_cells_generic` `tc_sram`. It maps the
  PULPissimo request/write/byte-mask interface to the macro, uses one-cycle
  read latency, ties BIST inputs low, and selects one macro per 1024-word slice.
  A [Verilator smoke test](../../integration/pulpissimo_soc/ihp/simulate_ihp_sram.py)
  passes against the official behavioral macro model.
- **Macro model:** the official macro Liberty copy is marked
  `is_macro_cell : true` by
  [prepare_ihp.py](../../integration/pulpissimo_soc/ihp/prepare_ihp.py). The PDK
  installation is not modified.

## What this does not say

- It is **not** placement, routing, extraction, DRC, LVS, equivalence,
  simulation or signoff. No GDS was produced.
- The 80 macro areas are Liberty areas. The total area is dominated by SRAM:
  logic is 2.045 mm² of the 13.260 mm² total.
- The macro internal delay, setup and hold are **outside** the RustSys timing
  estimate (`S0134`). The 119.08 ns number is unconstrained and is not a clock
  period result.
- The `tc_sram_ihp` adapter passed a Verilator smoke test against the official
  `-DFUNCTIONAL` macro model on a full 16384-word bank: write/read slices 0
  (address 5), 1 (1029) and 11 (11269), plus a byte-masked write at address 7.
  This checks the adapter mapping, not the full SoC. No full-SoC simulation or
  equivalence proof was run.
- 157 warnings remain, including inferred latches and undriven boundary bits.
  They are not signoff-clean.
- The earlier SKY130 full-SoC run used a behavioral 16384×32 memory and did not
  finish in 2 h. This IHP result is **not** a PDK comparison: it changes PDK,
  standard-cell library and memory implementation together.

## Reproduce

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

The exact producer is RustSys `fd2ecbd50c12caf0c1ee6299098eef1c1d1598ac1c00166ee9ca75168b596010`.
The prepared input SHA-256 is
`e1695a2f768e2cf21b0bfc9da5a515a2af82b6d4f69fe09dd4b547eaa31f494a`; the
EZ130 Liberty SHA-256 is
`847cb30b38f15ef0b9c4a735cfaf96c1782c013b1952261b25e5e38cd3ae072c`; the
patched macro Liberty SHA-256 is
`9f4ff72463b637f05774c8ff4d6fbb12e4057a120d6f8a3e041ef9bd327105c3`.
