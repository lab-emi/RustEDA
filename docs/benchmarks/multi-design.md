# Multi-design open-source generalization benchmark

**`picorv32` (ISC) · `aes_core` (BSD-2-Clause) · `sha256_core` (BSD-2-Clause)
across IHP SG13G2 and SKY130A.** To test whether RustEDA's speed, memory, QoR,
and independent signoff closure generalize beyond Ibex — with zero design-specific
channels, zero hardcoded ECOs, and zero PGO — we benchmarked three permissive-license
open-source RTL designs against **LibreLane 3.0.17** on identical RTL, 50.0 ns
(20 MHz) SDCs, die/core dimensions, and independent signoff gates.

All runtime numbers below are **single-run isolated-CPU observations** (4 E-cores,
4 GiB / 0 swap per run), not three-block quiet-host confirmations.

[Structured evidence receipt](evidence/multi-design/results.json) ·
[Three-PDK Ibex benchmark](ibex-three-pdk.md) ·
[Tight-clock Fmax comparison](tight-clock-fmax.md)

## Pinned designs and inputs

| Target | PDK / standard cells | Source & pinned commit | License | Die (µm) | I/O pins |
| --- | --- | --- | --- | ---: | ---: |
| `ihp_picorv32_top` | `ihp-sg13g2` / `ez130_8t` | `YosysHQ/picorv32` `ef203c2b0a` (`picorv32.v` + thin RV32IMC wrapper) | ISC | 733.74 × 732.48 | 176 |
| `ihp_aes_core` | `ihp-sg13g2` / `ez130_8t` | `secworks/aes` `80dc4718e1` | BSD-2-Clause | 820.26 × 819.84 | 520 |
| `ihp_sha256_core` | `ihp-sg13g2` / `ez130_8t` | `secworks/sha256` `837c5cc396` | BSD-2-Clause | 603.96 × 604.80 | 775 |
| `sky130_sha256_core` | `sky130A` / `sky130_fd_sc_hd` | `secworks/sha256` `837c5cc396` (dual-PDK check) | BSD-2-Clause | 623.30 × 625.60 | 775 |

## Independent signoff and QoR comparison

On IHP SG13G2, RustEDA passed **8/8 independent signoff gates on all three
non-Ibex designs** (KLayout DRC = 0, KLayout antenna = 0, LU.a/LU.b latch-up
PASS, KLayout full-device LVS PASS, OpenRCX nominal SPEF, OpenSTA TT/SS timing &
0/0/0 DRVs, external ABC combinational equivalence PASS, and density PASS).

On the dual-PDK SKY130A check (`sky130_sha256_core`), RustEDA used the standard
non-PGO release `rustroad` binary with its general electrostatic
`pin_density_sites_milli` spreading option and bounded first-pass router scouts,
passing **0 Magic DRC, 0 KLayout DRC, Magic+Netgen LVS PASS, and ABC equivalence
PASS** while running **1.59× faster** with **2.67× less peak RAM**.

| Target | Flow | Gates passed | Wall (s) | Peak RSS (MiB) | Cells (synth→final) | Util (%) | Routed WL (µm) | Vias | DRC | Ant | LVS | ABC LEC | SS Fmax (MHz) | TT power @ 20 MHz (mW) | SS DRVs (sl / cap) |
| --- | --- | :---: | ---: | ---: | :---: | ---: | ---: | ---: | :---: | :---: | :---: | :---: | ---: | ---: | :---: |
| **`ihp_picorv32_top`** | **RustEDA** | **8/8** | **172.6** | **492.5** | 13,146→15,083 | 31.70 | 752,662 | 120,642 | **0** | **0** | **PASS** | **PASS** | **46.03** | **2.59** | **0 / 0** |
| | LibreLane | 7/8 | 1,447.5 | 2,372.8 | 9,754→12,176 | 29.58 | 620,864 | 89,693 | 0 | 0 | PASS | unverified¹ | 38.77 | 3.31 | 0 / 0 |
| **`ihp_aes_core`** | **RustEDA** | **8/8** | **291.6** | **622.9** | 13,059→16,309 | **34.76** | **1,177,206** | **151,440** | **0** | **0** | **PASS** | **PASS** | **64.12** | 14.40 | **0 / 0** |
| | LibreLane | 6/8 | 1,931.3 | 2,468.7 | 14,917→19,427 | 35.81 | 1,334,759 | 164,201 | 0 | 0 | PASS | unverified¹ | 37.63 | **12.14** | 0 / 6 |
| **`ihp_sha256_core`** | **RustEDA** | **8/8** | **114.0** | **358.9** | 7,349→8,899 | 30.31 | 545,432 | 73,248 | **0** | **0** | **PASS** | **PASS** | **65.07** | 17.06 | **0 / 0** |
| | LibreLane | 6/8 | 615.7 | 1,309.0 | 7,117→9,395 | 30.19 | 460,634 | 66,412 | 0 | 0 | PASS | unverified¹ | 46.79 | **13.52** | 0 / 1 |
| **`sky130_sha256_core`** | **RustEDA** | 5/7 | **249.5** | **440.2** | 7,930→10,322 | 29.45 | 707,105 | 106,940 | **0 / 0** | 8² | **PASS** | **PASS** | **37.03** | **1.41** | **3 / 3** |
| | LibreLane | 5/7 | 397.0 | 1,174.3 | 6,719→8,610 | **25.76** | **454,215** | **68,491** | 0 / 0 | **0** | PASS | unverified¹ | 33.01 | 1.70 | 605 / 68 |

¹ On LibreLane netlists, `check_config` returned `undecided` (not a
counterexample) because Yosys `opt_dff` / `dfflibmap` renames or inverts DFFs
without preserving 1:1 RTL state names; reported honestly as `unverified`.  
² On `sky130_sha256_core`, native diode + grouped jumper closure cut Magic
antenna findings from `53 → 8` (and the remaining 8 via-offset candidates were
blocked during Phase 1 by a hardcoded 2-macro assertion in `components.py:300`,
now fixed). RustEDA also had `-0.025 ns` SS hold slack and `3/3` SS slew/cap
DRVs (vs LibreLane's `605/68` SS slew/cap DRVs).

## Key observations and honest trade-offs

- **Speed & memory generalize cleanly:** across all three IHP designs, RustEDA
  ran **5.4×–8.4× faster** than LibreLane 3.0.17 with **3.6×–4.8× lower peak
  RSS** (`359–623 MiB` vs `1,309–2,469 MiB`) and **0/0 timing & DRV violations**.
- **QoR trade-offs are real and design-dependent:**
  - On `ihp_aes_core` (high-fanout combinational crypto datapath), RustEDA beat
    LibreLane on **every physical and timing axis**: `-3.0%` area, `-11.8%`
    wirelength, `-7.8%` vias, `+70.4%` SS Fmax (`64.12` vs `37.63 MHz`), and
    `0` cap DRVs (vs `17` TT / `6` SS for LibreLane), at `+18.6%` higher TT
    vectorless power.
  - On `ihp_picorv32_top`, RustEDA delivered `+18.7%` Fmax and `-21.8%` TT power,
    while LibreLane achieved `-6.7%` smaller utilization (`29.58%` vs `31.70%`)
    and `-17.5%` shorter wirelength (`621k` vs `753k µm`) — partly because Yosys
    honors `(* full_case *)` (`0` latches) while RustSys conservatively infers
    68 `LATNQX1` latches (both formally valid; RustSys passed ABC CEC with
    `assume_full_case=False`).
  - On `sha256_core`, RustEDA delivered `+39.1%` higher Fmax on IHP and `+12.2%`
    higher Fmax / `-16.9%` power on SKY130A, while LibreLane achieved shorter
    routed wirelength (`461k` vs `545k µm` on IHP; `454k` vs `707k µm` on SKY130A).

## Generalization gaps discovered (macro-free `macros: []` audit)

Running `compose_native.py` with `macros: []` generated a valid 103-step
manifest (`rusteda flow exec --dry-run` passed), completed the first 15 steps,
and exposed specific Ibex-era assumptions in the composer/helpers:

1. **Missing `pin_buses: {}` in `rustroad floorplan`** (`native_frontend_inputs.py:265`
   and `rusteda-cli/src/flow/helpers.rs`): `floorplan` omits `"pin_buses"` when
   there are 0 macros, causing `frontend-profiles` to fail with `INPUT: 'pin_buses'`.
2. **Ibex clock/reset topology assertions in `fe_eco_reset`** (`rusteda-cli/src/flow/eco.rs:425-482`
   and `native_frontend_inputs.py:300-308`): currently requires `>= 1000` async
   reset sinks, rejects port-driven clocks/resets (`need(d.instance.is_some())`),
   and requires a clock-gating cell on the primary clock root.
3. **`output reg [N:0]` escaped wire name collision** on bare `picorv32`:
   `rusteda export` emits `wire \mem_addr[0] ;` alongside `output [31:0] mem_addr`,
   which `rustroad` rejects in DEF; wrapping `picorv32` in `picorv32_top` avoids it.
4. **`rustroad hold-eco` vs tie-cell fanout cloning** (`rustroad-workspace/src/eco/binding.rs:593-608`):
   when `rustsys` emits a `TIELO` with fanout > 1 and `import-pdk` clones it under
   `max_tie_fanout: 1`, `hold-eco` returns `STALE_REPORT` unless fed the post-`temp_place`
   Verilog.
5. **SKY130A `horizontal_repair/` Ibex assertions (fixed in this branch):**
   `components.py:300` (`assert len(fixed_macro_boxes)==2`), `ranked_metal.py:63`,
   and `generate.py:56` (`assert len(os.sched_getaffinity(0))==2`) have been
   generalized to support `0+` macros and `>= 1` CPUs.
6. **Top-level signoff wrapper assumptions:** `signoff_layout.py` and
   `pdk_signoff.py` assume Ibex / macro baselines, whereas the underlying gate
   scripts already support arbitrary top modules with `0` macros.
