# rusteda-compute: compute backends

`rusteda-compute` (`crates/core/rusteda-compute`) runs the compute-heavy tasks of the RustEDA
engines behind task traits, so that no engine depends on a GPU binding. Its specifications:

- this file: backend kinds and errors shared by every task, GPU runtime selection, the device
  self-test and the CUDA device layer;
- [timing.md](timing.md): the timing propagation task (COMPUTE-REQ-0010 to COMPUTE-REQ-0029);
- [decisions.md](decisions.md): the decision record of the crate.

The backends are:

- `cpu`: pure Rust, always available, the authoritative implementation of every task;
- `cuda`: kernels written in CUDA C on NVIDIA GPUs (Cargo feature `cuda`, COMPUTE-REQ-0002);
- `gpu`: the first GPU runtime of the build whose preparation succeeds (COMPUTE-REQ-0001).

A task is a trait with one implementation per backend (`TimingPropagationBackend` for the timing
task). Engines whose GPU work is not yet a task implement `GpuTask`, with one implementation per
GPU runtime; this crate decides which compiled runtime executes it. The crate tests the device of
each runtime once per process, turns set-up failures and panics into errors, and provides the CUDA
device layer: device arrays, kernel compilation and launches. The `unsafe` code of this glue lives
in the modules `cuda` and `timing::cuda` only, each block with a `// SAFETY:` comment.

**COMPUTE-REQ-0001** Runtime selection and failures. The build feature `cuda` includes the CUDA
runtime (COMPUTE-REQ-0002), the one GPU runtime so far. A task runs on the runtime its engine
requests or, when none is requested, on the first runtime of the build whose preparation succeeds;
it is not retried on another runtime. A requested runtime that the build lacks, and a build without
any GPU runtime, are failures. Before first use in a process, a runtime must pass its preparation:
a device self-test (a small kernel whose results are compared exactly with the expected values) on
the runtime's default device. The result is kept for the rest of the process. A runtime that fails
its preparation, a missing CUDA driver or NVRTC library, a kernel that does not compile, an error
of the CUDA driver (allocation, copy, launch or execution), a task without an implementation for
the runtime, or a panic inside the runtime is a failure of the task, which the engine reports as an
error, never as wrong values: every GPU task waits for its launches and collects their errors
before it reads results.

**COMPUTE-REQ-0002** CUDA runtime. `cuda` runs kernels written in CUDA C, compiled at run time by
NVRTC for the architecture of the device (`sm_<major><minor>`; from compute capability 9.0 on the
architecture-specific target `sm_<major><minor>a`) and launched through the CUDA driver API on
the default CUDA device. The default code generation options are fused multiply-add contraction,
correctly rounded division and square root and no flush to zero; a module may set its own
(COMPUTE-REQ-0004). The device is opened once per process and each kernel source is compiled once
per process and set of options. The CUDA driver and NVRTC libraries are loaded at run time, so
that a build needs no CUDA toolkit; running needs the CUDA driver and the NVRTC library of a CUDA
toolkit. The runtime is available on NVIDIA GPUs only.

**COMPUTE-REQ-0003** Preparation overlap. An engine may prepare its GPU backend on a second thread
while it does other work: runtime selection, the self-test and the compilation of its kernels. What
a preparation establishes is kept for the rest of the process: the result of each runtime's
preparation (COMPUTE-REQ-0001) and, for `cuda`, the opened device and every compiled kernel module.
Work that needs a runtime while another thread prepares it waits for that preparation instead of
repeating it, and a failed preparation is reported by the work that needs the runtime
(COMPUTE-REQ-0001).

**COMPUTE-REQ-0004** Device layer. The CUDA device layer (module `cuda`) changes additively: every
item keeps its signature and behaviour, and the layer adds:
- `Device::function_with(source, options, name)`: a kernel of a module compiled with extra NVRTC
  options (for example `-DREAL_F64` and `--fmad=false`), which replace the default options they
  name; modules are cached per process by source and complete option list, so that
  `function(source, name)` is the module with the default options;
- `Device::pinned(len)`: page-locked host memory that is not write-combined, so that the host
  reads it at full speed after a device-to-host copy;
- `Device::new_stream()`: a stream of its own for each session of a task;
- `Device::mem_info()`: the free and total device memory in bytes;
- `Device::describe()`: the device name and architecture.
A compilation that fails at any step releases its NVRTC program.

**COMPUTE-REQ-0005** Shared kinds and errors. Every task names its backends with `BackendKind`
(`Cpu`, `Cuda`, `Gpu`; `cpu`, `cuda`, `gpu`), its arithmetic with `Precision` (`F64`, `F32`;
`f64`, `f32`) and its failures with `ComputeError` (`NotCompiled`, `Unavailable`, `TooLarge`,
`Compile`, `Device`, `Invalid`, `Cancelled`), whose envelope codes and diagnostics are those of
COMPUTE-REQ-0025: an uncompiled or unusable backend is `BACKEND_UNAVAILABLE` with diagnostic A0501.

## Cut-pass task (`cut`)

**COMPUTE-REQ-0101** Problem validation. Before opening a session on any backend, the cut-pass task
validates the `CutPassProblem` (node count, level slices, fanin indices, match-table key order and
bounds, cell input counts, areas and delays, and `max_cuts` in `1..=MAX_CUTS`) and rejects a
malformed problem with `ComputeError::Invalid` or `ComputeError::TooLarge`.

**COMPUTE-REQ-0102** Candidate ranking and tie-breaking. For each node of a level, feasible cut
merges of fanin cut pairs are deduplicated and ordered by pass cost (delay or area flow), leaf
count, secondary cost, and pair index; cell matches are ordered by pass cost, secondary cost, and
match index, with an inverter from the opposite phase considered when it improves the choice.

**COMPUTE-REQ-0103** Packed representation. The problem and match table are packed into 32-bit word
arrays shared by the CPU and GPU backends so that both backends read identical integer inputs.

**COMPUTE-REQ-0104** Cross-backend bit-exactness. For the same `CutPassProblem` and pass sequence,
every backend (`Cpu` for any thread count and `Cuda`) returns word-identical cuts and choices.

**COMPUTE-REQ-0105** CPU backend. The CPU backend evaluates each level in parallel across nodes with
`rayon`, using the same integer costs and tie-breaking rules as the CUDA kernel.

**COMPUTE-REQ-0106** CUDA backend. The CUDA backend keeps the cut table and choices resident on the
device across passes of a session and launches one thread block per node of a level.
