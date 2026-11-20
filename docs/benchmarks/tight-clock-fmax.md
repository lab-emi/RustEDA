# Tight-clock Fmax comparison across three open PDKs

**IHP SG13G2 (25 ns / 20 ns) · SKY130A (40 ns) · GF180MCU D (65 ns).**
In the [100 ns (10 MHz) three-PDK Ibex benchmark](ibex-three-pdk.md), both flows
targeted a relaxed clock with LibreLane on `SYNTH_STRATEGY = "AREA 0"` and
+35–49 ns of positive setup slack, so LibreLane's resizer performed no setup
optimization. This study compares **RustEDA (`--goal delay`)** against
**LibreLane 3.0.17 (`DELAY 4` / `DELAY 0`, with full setup TNS repair enabled)**
under genuinely tight clock periods where both flows actively optimize setup
timing.

All layouts are judged by the same independent shared signoff scripts
(`signoff_layout.py`: OpenRCX nominal SPEF, OpenSTA 3.1.0 TT/SS timing & DRV,
binary-search SS Fmax sweeps, vendor simulation, ABC/large_lec equivalence,
density, and KLayout/Magic DRC & LVS). Runtime numbers in this study are
**single-run isolated-CPU observations** (4 E-cores on IHP, 2 E-cores on SKY130A
and GF180MCU D, 4 GiB / 0 swap), not three-block quiet-host timing confirmations.

[Structured evidence receipt](evidence/tight-clock-fmax/results.json) ·
[Three-PDK 10 MHz benchmark](ibex-three-pdk.md) ·
[Multi-design generalization](multi-design.md)

## Methodology: proportional tight-clock SDCs

The 100 ns baseline SDC sets fixed I/O delays (`in_max = 10 ns`, `in_min = 5 ns`,
`out_max = 5 ns`, `out_min = 0 ns`). At tight clock periods, keeping a fixed 5 ns
output delay artificially caps I/O endpoints before internal register-to-register
paths saturate. All tight-clock SDCs preserve the exact `10% / 5% / 5% / 0%`
ratios of the 100 ns SDC:

| PDK | Target period | Target frequency | `in_max` / `in_min` | `out_max` / `out_min` |
| --- | ---: | ---: | ---: | ---: |
| IHP SG13G2 | 25.0 ns | 40.00 MHz | 2.50 / 1.25 ns | 1.25 / 0.00 ns |
| IHP SG13G2 | 20.0 ns | 50.00 MHz | 2.00 / 1.00 ns | 1.00 / 0.00 ns |
| SKY130A | 40.0 ns | 25.00 MHz | 4.00 / 2.00 ns | 2.00 / 0.00 ns |
| GF180MCU D | 65.0 ns | 15.38 MHz | 6.50 / 3.25 ns | 3.25 / 0.00 ns |

## Summary verdict

1. **LibreLane's 100 ns `AREA 0` ceilings were genuinely depressed by relaxed
   constraints:** under `DELAY 4` + active resizer, LibreLane's SS Fmax jumps
   from `24.86 → 50.50 MHz` (+2.03×) on IHP SG13G2, `17.21 → 26.06 MHz` (+1.51×)
   on SKY130A, and `10.75 → 16.58 MHz` (+1.54×) on GF180MCU D.
2. **Where RustEDA wins clearly:**
   - **IHP SG13G2 @ 25 ns (40 MHz):** RustEDA beats LibreLane `DELAY 4` on every
     metric — higher SS Fmax (**45.00 MHz** stock / **49.46 MHz** with `BUFX3+`
     fanout buffer floor vs `41.34 MHz`), **9.5×** faster (`228 s` vs `2,161 s`),
     **-11.1%** smaller area (`186.3k` vs `209.6k µm²`), **-20.8%** lower
     17-cycle VCD power (`1.045` vs `1.319 mW` at 10 MHz), and **9/9 signoff
     PASS with 0 SS DRVs** (vs LibreLane's 107 SS DRVs).
   - **SKY130A @ 40 ns (25 MHz):** Clean single-binary non-PGO RustEDA
     (`--goal delay`, zero manual channels, zero hardcoded rewires) beats
     LibreLane `DELAY 4` by **+22.5% SS Fmax** (**31.93 MHz** vs `26.06 MHz`)
     at matched standard-cell area (`194,508` vs `194,560 µm²`), **4.15×**
     faster (`612 s` vs `2,542 s`), **0 hold violations** (vs `2` hold),
     **150× fewer SS DRVs** (`28 / 5 / 0` vs `4,326 / 526 / 140`), and **0/0**
     Magic/KLayout DRC — while LibreLane achieves **-23.0% lower TT vectorless
     power** (`5.16` vs `6.70 mW`).
3. **Where LibreLane `DELAY 4` leads on Fmax (and why):**
   - **IHP SG13G2 @ 20 ns (50 MHz):** LibreLane `DELAY 4` closes 50 MHz at
     **50.50 MHz SS** (`+0.200 ns`), beating stock RustEDA (`45.05 MHz`) by
     `+12.1%` and `BUFX4+` RustEDA (`49.04 MHz`) by `+3.0%` — at **13.5×**
     longer runtime (`3,039 s` vs `225 s`), `+13.6%` area, `+26.3%` VCD power,
     and `107` SS DRVs (vs RustEDA's `0/0/0`).
   - **GF180MCU D @ 65 ns (15.38 MHz):** LibreLane `DELAY 4` (requiring
     `PL_TARGET_DENSITY_PCT = 60` because default density `45` fails `DPL-0036`)
     reaches **16.58 MHz SS** (`+4.674 ns`), `-7.2%` smaller area (`531.5k` vs
     `572.5k µm²`), and `-23.7%` lower TT vectorless power (`70.66` vs `92.55 mW`)
     vs RustEDA (`14.65–14.86 MHz`), though LibreLane fails SS hold (`-0.228 ns`),
     carries `107` SS DRVs and `3` metal-density DRCs, and fails zero-delay
     `-DFUNCTIONAL` simulation due to a 21-stage CTS delay chain (see below).
   - **Root cause in RustEDA:** `rustsys synth --timing-period` currently rejects
     macro-bearing designs (`error[S0134]`), `rustsys opt-timing` lacks an
     electrical model for opaque SRAM macros (`error[W0002]`), and RustEDA has
     no post-placement setup cell-upsizing ECO (leaving `1×` combinational cells
     such as `AOI221X1` / `nand3_1` on critical paths).

## IHP SG13G2 / EZ130 (25 ns and 20 ns)

Pre-PNR strategy screening of LibreLane 3.0.17 at 25.0 ns showed `DELAY 4`
dominating `DELAY 0/1/2` by >24 ns (`+11.96 ns` pre-PNR reg-to-reg SS slack vs
`-12.06` to `-15.09 ns`, and ~1,200 fewer cells; `DELAY 3` timed out at >12 min,
and full PNR of `DELAY 0` timed out at 4,500 s in detailed routing after
inserting 3,598 timing buffers).

| Run | Target | Wall (s) | Stdcell area (µm²) | SS Fmax tight-SDC (MHz) | SS Fmax reg2reg / 100 ns IO (MHz) | SS setup WNS (ns) | SS hold WNS (ns) | SS DRVs (sl/c/fo) | TT VCD 10 MHz (mW) | Signoff gates (9) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `ihp-rusteda-25ns-base` | 25 ns | 227.8 | 186,295 | **45.00** | 45.00 / 43.30 | +2.779 | +0.216 | **0 / 0 / 0** | **1.045** | **9/9 PASS** |
| `ihp-rusteda-25ns-base-buf3` | 25 ns | 227.9 | **186,267** | **49.46** | 49.46 / 45.23 | +4.783 | +0.202 | **0 / 0 / 0** | **1.047** | **9/9 PASS** |
| `ihp-rusteda-25ns-hi` | 25 ns | 260.8 | 191,851 | 44.05 | 44.05 / 41.67 | +2.300 | +0.206 | **0 / 0 / 0** | 1.054 | **9/9 PASS** |
| `ihp-librelane-25ns-delay4` | 25 ns | 2,161.0 | 209,625 | 41.34 | 41.74 / 35.79 | +0.809 | +0.287 | 3 / 21 / 83 | 1.319 | 7/9 (timing DRVs, logic unverified) |
| `ihp-rusteda-20ns-base` | 20 ns | 224.9 | 186,325 | 45.05 | 45.05 / 43.41 | −2.194 | +0.171 | **0 / 0 / 0** | **1.044** | 8/9 (setup; 4/4 phys + ABC PASS) |
| `ihp-rusteda-20ns-base-buf4` | 20 ns | 221.5 | 188,714 | 49.04 | 49.04 / 44.61 | −0.391 | +0.014 | **0 / 0 / 0** | 1.052 | 8/9 (setup; 4/4 phys + ABC PASS) |
| `ihp-librelane-20ns-delay4` | 20 ns | 3,039.5 | 211,703 | **50.50** | 50.50 / 42.36 | **+0.200** | +0.280 | 0 / 18 / 89 | 1.320 | 7/9 (timing DRVs, logic unverified) |

All RustEDA tight-clock runs required the per-endpoint hold-ECO fix (now ported
into `integration/ibex_sg13g2_ez130/native_ecos.py`), replacing the chip-global
`worst_setup` check that previously aborted hold planning on unrelated positive-slack
endpoints.

## SKY130A (40 ns / 25 MHz)

Both arms ran with 2 E-cores, 4 GiB / 0 swap, using the same scaled 40.0 ns SDC
and identical die/macro placements. RustEDA used the clean single-binary non-PGO
producer (`clean_sky130_flow.py`, `--goal delay`, zero manual placement channels,
zero hardcoded rewires).

| Run | Wall (s) | Peak RSS (MiB) | Stdcell area (µm²) | SS Fmax 40 ns / 100 ns IO (MHz) | SS setup WNS (ns) | SS hold WNS (ns / ep) | TT DRVs (sl/c/fo) | SS DRVs (sl/c/fo) | TT vec power 10 MHz (mW) | DRC (Magic / KL) & LVS / Sim / ABC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `sky130-rusteda-40ns-delay` | **612.2** | **684** | **194,508** | **31.93** / **31.61** | **+8.683** | **+0.286** (0) | **0 / 0 / 0** | **28 / 5 / 0** | 6.699 | **0 / 0**, LVS/Sim/ABC **PASS** (ant unchecked) |
| `sky130-librelane-40ns-delay4` | 2,542.2 | 1,884 | 194,560 | 26.06 / 26.06 | +1.631 | −0.145 (2) | 659 / 111 / 140 | 4,326 / 526 / 140 | **5.159** | **0 / 0**, LVS/Sim PASS, ABC unverified (ant unchecked) |

## GF180MCU D (65 ns / 15.38 MHz)

All runs include `floating_fill.py` on 2 E-cores, 4 GiB / 0 swap.

| Run | Wall (s) | Stdcell area (µm²) | SS Fmax tight-SDC (MHz) | SS setup WNS (ns) | SS hold WNS (ns / ep) | SS DRVs (sl/c/fo) | TT vec power 10 MHz / 15.38 MHz (mW) | Physical & functional gates |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `gf180-rusteda-65ns-delay` | 632.8 | 572,506 | 14.65 | −3.238 | −0.186 (3) | 50 / 11 / 0 | 92.55 / 142.39 | Sim/ABC/LVS/dens PASS; ant=6, drc=7 |
| `gf180-rusteda-65ns-delay-buf3` | 610.1 | 585,848 | 14.86 | −2.301 | −0.084 (4) | 61 / 15 / 0 | 93.70 / 144.15 | Sim/ABC/LVS/dens PASS; ant=12, drc=13 |
| `gf180-rusteda-65ns-delay-hi` | **599.5** | 611,739 | 13.68 | −8.096 | −0.052 (1) | **13 / 2 / 0** | 98.49 / 151.51 | Sim/ABC/LVS/dens/ant=0 PASS; **drc=1** |
| `gf180-librelane-65ns-delay4` (d=45) | 275.2 | — | FAILED (`DPL-0036`) | — | — | — | — | Failed in `28-openroad-diodeinsertion` |
| `gf180-librelane-65ns-delay4-d60` | 741.5 | **531,478** | **16.58** | **+4.674** | −0.228 (6) | 77 / 27 / 3 | **70.66 / 108.70** | LVS/dens/ant=0 PASS; drc=3; logic unverified; **sim FAIL¹** |
| `gf180-librelane-65ns-delay0` | 803.7 | 575,162 | 14.29 | −4.966 | −0.461 (5) | 98 / 26 / 0 | 111.22 / 171.11 | Sim/LVS/dens/ant=0 PASS; drc=3; logic unverified |

¹ **Root cause of `gf180-librelane-65ns-delay4-d60` simulation failure:** not a
logic bug, but zero-delay `-DFUNCTIONAL` delta-cycle shoot-through. OpenROAD CTS
left a `buf_1` at the root of `u_cpu.clk` (8.89 ns latency across 8 gates) and
balanced `clk_i_regs` with an 18-stage `clkbuf_16` delay chain (21 gates, 9.79 ns).
In physical silicon (`OpenSTA` with SPEF), hold slack on the launch→capture pair is
`+3.59 ns`, whereas zero-delay Verilog advances data in 4 deltas while the capture
clock arrives 13 deltas later. Collapsing the 285 pure-identity CTS buffers in the
post-route netlist immediately passes `tb.sv`. That same 9.8 ns CTS latency does
cause genuine input-path SS hold failures (`-0.228 ns` on `prog_be_i[1] → u_imem1/WEN[7]`).

## Actionable engine roadmap uncovered

1. **`rustsys synth --timing-period` & `rustsys opt-timing` on macro-bearing designs:**
   remove the opaque-macro-bus guard (`S0134` / `W0002`) so SRAM-bearing SoCs get
   period-aware mapping, carry planning, and load-aware sizing.
2. **Post-placement / post-route setup cell-upsizing ECO:** upgrading critical-path
   `1×` combinational cells (e.g. `AOI221X1 → AOI221X3` on IHP, `nand3_1/or4_1` on
   GF180) addresses the exact 0.4–2.0 MHz gap seen at IHP 20 ns and GF180 65 ns.
3. **Avoid `--effort high --delay-relax 0.0` without `--timing-period`:** on both
   IHP and GF180 it reduced post-route Fmax by ~0.95 MHz while increasing area
   and power; keep `--effort medium` until period-aware synthesis supports macros.
