# Benchmarks

## The headline boundary

OpenC910/SKY130 synthesis and Verilog export is **20.55× faster** in the recorded
RustSys/Yosys comparison. **OpenC910 RTL-to-GDS speedup is unverified.**
An engine speedup cannot be multiplied across stages to manufacture a flow speedup.

The [machine-readable summary](benchmarks/openc910-sky130.json) binds the displayed
values to the published source receipts. The underlying
[RustSys method](../modules/RustSys/docs/guide/benchmarks.md) includes configurations,
CPU allocation, tool identities, exact input hashes and proof scope.

## What is measured

The workload is OpenC910 `ct_core` with default 64 KiB instruction and data
caches, lowered to standard cells. L2, the second CPU core and cluster peripherals
are excluded. Both engines receive 16 physical host CPU cores with no SMT siblings.
The shared host is an AMD Ryzen 9 9950X3D. Each configuration has one warm-up and
three timed repetitions. Compare medians, not the best observed sample.

The synthesis clock includes process startup, workspace initialization/import,
mmap, synthesis and full Verilog export. Preparation, proof and timing are
separate. RustSys's 2026-10-23 refresh retains the original Yosys timing samples;
source campaign identities and byte-bound certificate reuse stay visible.

| Metric | RustSys | Yosys + ABC | Yosys / RustSys |
| --- | ---: | ---: | ---: |
| Wall time | 68.79 s | 1,413.58 s | 20.55× |
| Mapped cell area | 53,453,688 µm² | 59,995,158 µm² | 1.12× |
| Worst process RSS across timed runs | 7,702.1 MiB | 16,339.8 MiB | 2.12× |

The area ratio corresponds to **10.90% less mapped cell area**, not smaller routed
die area. Retained arbitrary-state RTL equivalence certificates cover both
netlists with the documented two-valued/X/Z completion semantics; no reset or
input assumptions are added. A reused certificate is not a fresh proof invocation.

Timing is TT 1.80 V / 25°C, ideal clock, no wire parasitics. Characterization-limit
violations prevent interpreting raw OpenC910 setup-Fmax as achievable frequency.
Unconstrained endpoint counts are in the original check receipts. DRC/LVS is
unchecked in this synthesis campaign.

## A matched RTL-to-GDS comparison

A publishable end-to-end ratio requires the same RTL/top/configuration, PDK and
corners, clocks/I/O constraints, area/density targets, host, allocated threads,
memory policy and completion criteria on both flows. The baseline must name its
exact Yosys, OpenROAD and orchestration versions, rather than treating all flows
as interchangeable.

Start the wall clock from prepared RTL and end only when GDS and the required
verification results are complete. Include import/export, disk I/O, lowering,
tool startup, host/device synchronization, orchestration, extraction, DRC, LVS
and correctness checks. Report fresh runs separately from cache hits. Repeat
with a stated warm-up policy, and publish samples and peak aggregate memory.

Quality must accompany time: routed cell/die area, setup and hold slack at every
required corner, wire model, unconstrained endpoints, DRC/LVS/antenna results,
equivalence status and all deviations. Timeout, missing tool, failed quality
gate or exhausted memory is a non-completion, never a speedup number.

No such OpenC910/SKY130 matched pair is currently recorded. RustSTA, RustROAD,
RustEDA flow and the workbench have no comparable OpenC910 runtime rows to fill in.

## Amdahl's law and agent iteration time

For a measured tool-time fraction `f` and aggregate tool speedup `s`:

$$S = 1 / ((1-f) + f/s).$$

At `f = 0.8` and `s = 10`, the illustrative iteration speedup is `3.57×`.
At the same fraction, even infinitely fast tools cannot exceed `5×`.
These are model outputs, not measured RustEDA or LLM results. A real agent-loop
comparison must also fix the model, inference settings, task, stopping rule,
quality threshold and number of design iterations.
