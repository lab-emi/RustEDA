# Benchmarks

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

## Circuit simulation benchmarks (RustSpice vs Ngspice-47)

Host: 13th Gen Intel(R) Core(TM) i9-13900HX (32 hardware threads).
Reference baseline: `ngspice-47` (SPARSE 1.3 by default, KLU with `.options klu`; each row uses the faster of the two).

| Benchmark | Circuit scale | `ngspice-47`<br>end to end ↓ | `RustSpice`<br>end to end ↓ | Speedup ↑ | Max error: `RustSpice` / `ngspice` default ↓ |
| :--- | :--- | ---: | ---: | ---: | ---: |
| **1. Resistor ladder (DC OP)** | 10,000 resistors, 5,002 MNA unknowns (DC operating point) | 56.66 ms | 22.96 ms | **2.47×** | `1.91e-13` / `1.93e-13` V |
| **2. RC line (transient)** | 4,000 R/C elements, `.tran 10p 5n` (500 steps) | 235 ms | 57.36 ms | **4.10×** at 32.8× the error of ngspice default | `5.40e-2` / `1.65e-3` of swing (vs tight ngspice) |
| **3. Diode array (DC sweep)** | 64 diodes + 128 resistors, 201-point DC sweep | 11.83 ms | 7.06 ms | **1.68×** | `5.89e-11` / `5.44e-5` V |
| **4. CMOS inverter chain (transient)** | 200 MOSFETs + 100 capacitors, `.tran 5p 2n` (400 steps) | 78.09 ms | 45.64 ms | **1.71×** | `4.14e-1` / `3.86e-1` of swing (vs tight ngspice) |
| **5. Sensitivity (adjoint vs. ngspice `.sens`)** | 200 resistor gradients (one adjoint solve) | 28.85 ms | 2.26 ms | **12.8×** vs `.sens` | `9.33e-15` / `1.00e-6` (normalised) |
| **6. PVT batch (128 corners)** | 128 PVT corners of a 32-diode nonlinear net (Rayon) | 8.93 ms | 5.77 ms | **1.55×** (parallel workload; **2.44×** via `.dc` sweep) | `2.47e-11` / `1.63e-5` V |

Speedup = `ngspice-47` / `RustSpice`, median of 9 interleaved repetitions against the faster of ngspice's SPARSE 1.3 and KLU solvers; both tools run as subprocesses with rawfile output on tmpfs, the single-core benchmarks pinned to one idle core (the parallel batch, benchmark 6, is not pinned). The previously published 5.3×, 7.6×, 3.0×, 2.6×, 6,285× and 321× came from a harness with known defects (1 ms-quantised solver times, a cold process compared with a warm in-process call, hard-coded zero errors, single runs multiplied by 128 or 400, a transient compared at t = 0 only) and are withdrawn; the report lists each one with its replacement.

- Benchmark 2 is not at equal accuracy: RustSpice's error at its fixed 10 ps step is 32.8× ngspice default's (both against a tightly converged ngspice).
- Benchmark 4: at the legacy 5 ps step both tools are about 0.4 of the swing away from the converged reference (RustSpice 0.41, ngspice default 0.39); the 20 ps edges make this comparison one of speed at comparable, not good, accuracy.
- Benchmark 5 compares one adjoint solve with the best ngspice alternative (`.sens`, which computes 24 sensitivities per requested gradient); against the finite-difference loop that delivers only the 200 requested gradients the ratio is 391×.
- Benchmark 6 is a parallel workload (32 threads vs ngspice's 1-thread `.dc` sweep; against `ngspice-control-loop-sparse` the ratio is 9.47×, and against `ngspice-par-launches` 21.4×).

See the [RustSpice benchmark report](../modules/RustSpice/docs/benchmark_report.md)
and [RustSpice architecture guide](../modules/RustSpice/docs/architecture.md) for
reproduction commands and solver methodology.

## Amdahl's law and agent iteration time

For a measured tool-time fraction `f` and aggregate tool speedup `s`:

$$S = 1 / ((1-f) + f/s).$$

At `f = 0.8` and `s = 10`, the illustrative iteration speedup is `3.57×`.
At the same fraction, even infinitely fast tools cannot exceed `5×`.
These are model outputs, not measured RustEDA or LLM results. A real agent-loop
comparison must also fix the model, inference settings, task, stopping rule,
quality threshold and number of design iterations.
