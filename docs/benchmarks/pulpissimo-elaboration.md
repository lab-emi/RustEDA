# PULPissimo full-SoC elaboration analysis

The [full SoC attempt](pulpissimo.md#full-soc-attempt-on-sky130) passed the former
generate-scope rejection but did not finish inside a 2 h synthesis budget. This
page records where the time goes. It is a **diagnostic**, not a benchmark: the
control run changes the L2 memory size and is not a PULPissimo result.

[Trace](evidence/pulpissimo-20261106/elaboration-trace.log) ·
[Analysis record](evidence/pulpissimo-20261106/elaboration-analysis.json) ·
[Control receipt](evidence/pulpissimo-20261106/elaboration-control-receipt.json)

## Method

RustSys was built with an opt-in elaboration trace (`RUSTSYS_ELAB_TRACE=1`) that
records each module instance, generate block and generate loop with wall time,
signal count and AIG node count. The trace is off by default. The full input is
the same [prepared Verilog](evidence/pulpissimo-20261106/input.json) used by the
published core benchmark.

## Finding

The first L2 bank dominates:

```text
enter instance i_soc_domain.i_pulp_soc.l2_ram_i.CUTS[0].bank_i (tc_sram)
leave generate block ...bank_i.gen_read_lat in 2.609 s
leave generate block ...bank_i.genblk2 in 24.615 s
leave instance ...bank_i (tc_sram): 27.257 s, 1163 signals, 52,460,372 AIG nodes
```

A second bank starts immediately after. `tc_sram` is the behavioral
`tech_cells_generic` memory: `reg [NumWords*DataWidth-1:0] sram` with read/write
muxing. At `NumWords=1024, DataWidth=32` each bank becomes **52.46 million AIG
nodes**, almost all from the memory read/write logic. Three L2 banks therefore
exceed 150 million nodes before the core and peripherals are mapped.

## Control

The same prepared Verilog was run with each `tc_sram` instance changed to
`NumWords=1` while keeping `AddrWidth` at 14/13, so port widths and the rest of
the SoC are unchanged. Elaboration then finishes in **4.249 s** with **605,650
AIG nodes**, and synthesis continues into optimization.

The comparison isolates the memory model:

| | full L2 banks | one-word L2 control |
| --- | ---: | ---: |
| Elaboration | > 2 h (not completed) | 4.249 s |
| Optimize / merge | not reached | 476.909 / 474.706 s |
| Mapping | not reached | 6.076 s |
| AIG nodes | 52,460,372 for the first bank alone | 605,650 total |
| Standard cells | not measured | 180,669 |
| Cell area | not measured | 1,697,673.20 µm² |
| Logic depth | not measured | 98 |

The control completed: elaboration 4.249 s, optimize 476.909 s (merge alone
474.706 s), mapping 6.076 s, export 0.110 s; 180,669 cells, 1,697,673.20 µm²,
logic depth 98 and 30,460 retained state bits. The merge optimization is thus a
second, independent bottleneck on the non-memory logic.

The control is not a PULPissimo benchmark. It only shows that hierarchy
elaboration, the core, the peripherals and the interconnect are small compared
with the behavioral L2 memory, and it sizes the remaining logic. The 2 h timeout
is a **memory modeling** limit, not a generate-scope limit.

## What this means

For a full PULPissimo synthesis benchmark, the L2 banks must be treated as
SRAM macros, exactly as the Ibex campaign treats its two real SRAM macros. Two
paths are possible:

1. Add a module-level opaque/blackbox mode so `tc_sram` instances become cut
   points with preserved ports, without requiring one fixed Liberty interface
   per parameterization.
2. Replace `tc_sram` with a technology SRAM macro whose Liberty pin interface
   matches the bank configuration, or add memory inference to RustSys.

The [IHP SG13G2 / EZ130 test](pulpissimo-ihp.md) now takes the second path for
the L2 banks: an adapter maps each bank to official 1024×32 SRAM macros, and the
full SoC synthesizes in 426 s. It is synthesis-only and unverified for physical
signoff. A generic module blackbox remains useful for memories that have no
matching Liberty macro. Until then, the SKY130 behavioral-memory full SoC stays
`unverified`; no full-SoC ratio is published for it. The core benchmark remains
the published comparison.

## Reproduce

```sh
RUSTSYS_ELAB_TRACE=1 /path/to/rustsys --threads 16 synth \
  --top pulpissimo_soc_wrapper \
  --lib "$(./rusteda library sky130_fd_sc_hd --corner tt)" \
  -D SYNTHESIS --progress log -v \
  .work/pulpissimo-soc/pulpissimo_soc.v \
  --out .work/pulpissimo-soc/full.v \
  --report .work/pulpissimo-soc/full.json
```

The trace goes to standard error. The control is produced by changing each
`tc_sram` instance's `NumWords` to 1 and adding the matching `AddrWidth`; the
exact control input SHA-256 is in the analysis record.
