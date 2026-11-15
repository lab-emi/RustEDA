# PULPissimo Ibex-core synthesis

**Historical synthesis comparison.** The recorded top was `ibex_core`.
Both tools read Verilog prepared by a converter that removed `sv2v_cast_*`
width casts. The removal shifted AXI request fields by 24 bits in the full
SoC, so its historical SKY130 and IHP measurements were withdrawn. The AXI
bridge is outside the standalone core top, and the core comparison remains
unverified against the original RTL.
[IHP full-SoC record](pulpissimo-ihp.md) ·
[Elaboration trace](pulpissimo-elaboration.md)

[Machine-readable receipt](pulpissimo-soc-20261106.json) ·
[Input manifest](evidence/pulpissimo-20261106/input.json) · [RustSys receipt](evidence/pulpissimo-20261106/rustsys-ibex-core-receipt.json) · [Yosys receipt](evidence/pulpissimo-20261106/yosys-ibex-core-receipt.json) · [SoC attempt](evidence/pulpissimo-20261106/full-soc-attempt.json) ·
[Input and run harness](../../integration/pulpissimo_soc/README.md)

## Result

Historical medians from three consecutive single-thread measurements per
producer, using the recorded prepared Verilog and SKY130 HD TT Liberty on
2026-11-06.

| | RustSys | Yosys 0.70 + ABC, bounded mapping |
| --- | ---: | ---: |
| Wall time | 2.874 s | **2.675 s** |
| Peak process RSS | 234,264 KiB (229 MiB) | **148,372 KiB (145 MiB)** |
| Standard cells | **15,041** | 22,235 |
| Standard-cell area | **146,711.96 µm²** | 161,157.06 µm² |
| Logic depth | 45 | not reported by this profile |
| AIG nodes, before → after | 33,249 → 26,703 | — |
| State bits, RTL → retained | 2,579 → 2,557 | — |
| Equivalence | not run in this comparison | not run in this comparison |

## What was compared

- **Design:** PULPissimo `bfc3d9a1ef72443464509a22d948f0a6f5b65b4c`, configured
  as the Ibex-core variant: `CORE_TYPE=1`, `USE_XPULP=0`, `USE_FPU=0`,
  `USE_ZFINX=0`, `USE_HWPE=0`, `SIM_STDOUT=0`.
- **Top:** `ibex_core`, the core instantiated by the PULPissimo SoC. The full
  SoC top is not part of this comparison.
- **Instance parameters:** `MHPMCounterNum=10`, `RegFile=2` (latch register
  file) and `DbgTriggerEn=1`, matching the PULPissimo `fc_subsystem` instance;
  the module's own defaults differ. Both producers receive the same overrides.
- **Technology:** SKY130A `sky130_fd_sc_hd`, TT 25 °C / 1.80 V. The Liberty
  SHA-256 is in the receipt.
- **Input:** the same 4.9 MB sv2v-converted Verilog for both producers. Yosys
  reads the 25-module reachable subset; RustSys reads the full 366-module file
  and selects the same top. The RustSys time therefore includes parsing modules
  that Yosys never reads, so the comparison does not favour RustSys. The
  historical conversion stripped non-synthesizable assertion/fatal blocks,
  the unreachable FPU path, the dropped sv2v APB-interface object and sv2v
  width casts. The counters and input hash are recorded in the
  [input manifest](evidence/pulpissimo-20261106/input.json).
- **Threads:** one for both. Yosys/ABC is single-threaded in this profile.
- **Timer:** the whole process, including read, elaboration, AIG construction,
  mapping and export. GNU time records peak RSS.

## The Yosys profile

Yosys ran this labelled bounded mapping profile:

```yosys
read_liberty -lib LIB
read_verilog -sv pulpissimo_ibex_core.v
hierarchy -check -top ibex_core
proc; flatten; opt; techmap; opt
dfflibmap -liberty LIB
abc -liberty LIB -script "+strash;&get,-n;&nf,-C,4,-F,1;&put"
opt -fast
stat -json -liberty LIB
write_verilog -noattr ibex_core_yosys.v
```

It is **not** Yosys's default quality flow. Yosys's default ABC script did not
finish within the 600 s budget on this core; the bounded profile completes in
seconds. The comparison therefore measures RustSys against a fast, explicitly
bounded reference, not against the best area Yosys can produce with unlimited
time.

## Full SoC attempt on SKY130

A `pulpissimo_soc_wrapper` elaboration was attempted on the same conversion.
The AXI width-cast error applies to both attempts below.
Before the fix it stopped after **2,241.09 s** with
`error[S0105]: 'i_soc_domain' is not declared`; sv2v had inlined the
interface-bearing `soc_domain` into a generate scope and left module-scope
hierarchical references. The RustSys fix adds named and indexed generate scopes,
and the preparation adds the sv2v APB-interface repair. A post-fix run proceeded
past that point, reached its 2 h guard and was stopped; its observed RSS was
about 3.78 GiB. No netlist or report was written. **No full-SoC metrics, ratio
or speedup are published**, and the remaining limit is runtime scale, not
hierarchy rejection. The engine change is covered by generate-block, nested-block,
generate-loop and top-qualified tests in RustSys; see the
[RustSys elaboration update](../../modules/RustSys/docs/development-status.md).
The separate [elaboration analysis](pulpissimo-elaboration.md) shows that the
behavioral L2 `tc_sram` banks dominate: one bank creates 52,460,372 AIG nodes,
while a one-word control elaborates the whole SoC in 4.249 s with 605,650
nodes. Replacing the banks with official IHP SRAM macros is the subject of the
[IHP SG13G2 / EZ130 test](pulpissimo-ihp.md).

## Measurement scope

The archived comparative data covers synthesis of the standalone core on the
shared prepared input. The RustSys and Yosys netlists are unverified for
equivalence. A new measurement requires preparation that preserves width casts
and independent validation against the original RTL.

## Reproduce

These commands record the historical preparation and synthesis setup.

```sh
python3 integration/pulpissimo_soc/prepare.py \
  --source /path/to/pulpissimo \
  --workdir .work/pulpissimo-soc \
  --bender /path/to/bender --sv2v /path/to/sv2v

python3 integration/pulpissimo_soc/run_synthesis.py \
  --input .work/pulpissimo-soc/pulpissimo_soc.v \
  --lib "$(./rusteda library sky130_fd_sc_hd --corner tt)" \
  --rustsys /path/to/rustsys --top ibex_core --threads 1 \
  --param MHPMCounterNum=10 --param RegFile=2 --param DbgTriggerEn=1 \
  --output .work/pulpissimo-soc/ibex_core.v \
  --report .work/pulpissimo-soc/ibex_core.json \
  --receipt .work/pulpissimo-soc/ibex_core-receipt.json

python3 integration/pulpissimo_soc/run_reference.py \
  --input .work/pulpissimo-soc/pulpissimo_soc.v \
  --lib "$(./rusteda library sky130_fd_sc_hd --corner tt)" \
  --yosys /path/to/yosys --top ibex_core \
  --param MHPMCounterNum=10 --param RegFile=2 --param DbgTriggerEn=1 \
  --output .work/pulpissimo-soc/ibex_core-yosys.v \
  --stat .work/pulpissimo-soc/ibex_core-yosys-stat.json \
  --receipt .work/pulpissimo-soc/ibex_core-yosys-receipt.json
```

The host was a 13th Gen Intel Core i9-13900HX with 32 logical CPUs and 94 GiB
RAM, Linux 7.2.11. Other hosts will produce different absolute times.
