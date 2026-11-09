# PULPissimo SoC synthesis benchmark inputs

This directory prepares and runs the PULPissimo synthesis benchmark. It is a
**synthesis-only** extension to the Ibex RTL-to-GDS campaign; it is not a
replacement for the qualified Ibex flow and it makes no placement, routing,
extraction or signoff claim.

## Scope

- Upstream design: [pulp-platform/pulpissimo](https://github.com/pulp-platform/pulpissimo)
  at `bfc3d9a1ef72443464509a22d948f0a6f5b65b4c` (2024-05-30).
- Configuration: `CORE_TYPE=1` (Ibex RV32IMC), `USE_XPULP=0`, `USE_FPU=0`,
  `USE_ZFINX=0`, `USE_HWPE=0`, `SIM_STDOUT=0`.
- Completed benchmark top: `ibex_core`, the core instantiated by the SoC.
- Full SoC attempt top: `pulpissimo_soc_wrapper`, a benchmark wrapper around the
  upstream `soc_domain`. Pads, pad multiplexing, clock generation and the FLL
  are excluded; those are board-level IO and clocking, not standard-cell logic.
  The full SoC is **unverified** (see below).
- Technology: SKY130A `sky130_fd_sc_hd`, TT 25 °C / 1.80 V.
- Producers: RustSys and a bounded Yosys 0.70/ABC reference, both invoked as
  ordinary external commands.

The upstream `pulpissimo` top is not the benchmark top. It contains inout pads
and a padframe whose behavioral models use switch-level primitives; including
them would measure IO cells and pad models, not the SoC standard-cell logic.

## Prepare

`prepare.py` performs the deterministic conversion. It needs a working
[Bender](https://github.com/pulp-platform/bender) and
[sv2v](https://github.com/zachjs/sv2v) executable. Bender fetches the pinned
dependency revisions from `Bender.lock`; the source tree itself is never edited.

```sh
python3 integration/pulpissimo_soc/prepare.py \
  --source /path/to/pulpissimo \
  --workdir .work/pulpissimo-soc \
  --bender /path/to/bender \
  --sv2v /path/to/sv2v
```

The script records the source commit, `Bender.lock`, Bender target set, every
conversion counter, and the SHA-256 of the prepared Verilog in
`.work/pulpissimo-soc/input.json`.

### What preparation changes

Only non-synthesizable or unreachable material is changed, and every change is
counted in the input manifest:

- `pragma translate_off` blocks are removed; they are explicitly
  non-synthesizable assertions, prints and coverage code.
- The `adv_dbg_if` `Bender.yml` trailing comma is repaired in the Bender
  checkout so Bender 0.32.1 can list its sources.
- Three undeclared ports/parameters in the unused SCM
  `register_file_1w_64b_1r_32b` module are disconnected; sv2v resolves every
  module binding even when the module is unreachable.
- sv2v identity cast helpers (`sv2v_cast_*`) are removed.
- `initial` blocks that only call `$finish`/`$fatal` are removed.
- Two generated default-index width expressions are rewritten to the local
  `IdxWidth` constant (an sv2v code-generation repair).
- The dropped `APB.Master` object in the sv2v wrapper is repaired: the inlined
  `pulp_soc` APB ports connect directly to the wrapper's plain APB ports.
- Modules in the unreachable FPU path (`fpnew_*`, `cv32e40p_fp_wrapper`) are
  removed. The selected configuration has no FPU; the Ibex path does not
  reference them.

These are benchmark-input preparation changes, not synthesis transformations.
The preparation script never writes into the upstream or dependency source
trees after the initial Bender checkout.

## Run

```sh
python3 integration/pulpissimo_soc/run_synthesis.py \
  --input .work/pulpissimo-soc/pulpissimo_soc.v \
  --lib "$(./rusteda library sky130_fd_sc_hd --corner tt)" \
  --rustsys /path/to/rustsys --top ibex_core \
  --param MHPMCounterNum=10 --param RegFile=2 --param DbgTriggerEn=1 \
  --output .work/pulpissimo-soc/ibex_core.v \
  --report .work/pulpissimo-soc/ibex_core.json \
  --receipt .work/pulpissimo-soc/receipt.json \
  --threads 16 --memory-gib 32 --timeout 7200
```

The runner measures the whole RustSys process with GNU time: read, elaborate,
AIG construction, mapping and export. A timeout leaves the receipt
`status: timeout` and `metrics: null`; it is never reported as zero.

## Reference run

```sh
python3 integration/pulpissimo_soc/run_reference.py \
  --input .work/pulpissimo-soc/pulpissimo_soc.v \
  --lib "$(./rusteda library sky130_fd_sc_hd --corner tt)" \
  --yosys /path/to/yosys --top ibex_core \
  --param MHPMCounterNum=10 --param RegFile=2 --param DbgTriggerEn=1 \
  --output .work/pulpissimo-soc/ibex_core-yosys.v \
  --stat .work/pulpissimo-soc/ibex_core-yosys-stat.json \
  --receipt .work/pulpissimo-soc/ibex_core-yosys-receipt.json
```

The reference runner first keeps the modules reachable from `--top`, then runs
the bounded ABC profile. Its default ABC script did not finish inside 600 s on
this core; the bounded profile is a labelled fast reference, not Yosys's best
quality flow.

## Resource estimate

Before a run, estimate peak memory from the source inventory:

```text
Peak RAM ~= AIG nodes x per-node storage x mapping cuts x threads
          + parsed AST + netlist + export buffer
```

The pinned prepared input is about 4.9 MB of Verilog (about 89k lines) and the
elaborated SoC is much larger than the Ibex core. The campaign ran under a
32 GiB address-space guard and a 2 h deadline. A run that reaches the guard or
the deadline is `unverified`; it is not evidence of a mapping limit.

## Full SoC status

The full `pulpissimo_soc_wrapper` elaboration was attempted twice. Before the
RustSys generate-scope fix it stopped after 2,241.09 s with:

```text
error[S0105]: soc_wrapper_asic_tc3.v:88547:25: 'i_soc_domain' is not declared
```

sv2v inlined the interface-bearing `soc_domain` into a generate scope and left
module-scope hierarchical references. RustSys now resolves named and indexed
generate scopes; `prepare.py` also repairs the dropped APB object. A post-fix
run passed that point, reached its 2 h guard and was stopped without writing a
netlist or report (observed RSS about 3.78 GiB). No full-SoC metrics, ratio or
speedup are published. The remaining limit is runtime scale, not hierarchy
rejection; the separate
[elaboration analysis](../../docs/benchmarks/pulpissimo-elaboration.md) traces it
to the behavioral L2 `tc_sram` banks. The
[IHP SG13G2 / EZ130 test](ihp/README.md) replaces those banks with 80 official
SRAM macros and completes full-SoC synthesis in 426 s; it is synthesis-only.

## Evidence

The published sample and its exact producer, input, library and artifact
hashes are in [`docs/benchmarks/pulpissimo-soc-20261106.json`](../../docs/benchmarks/pulpissimo-soc-20261106.json).
The benchmark page explains what is and is not comparable to the Ibex
RTL-to-GDS campaign.
