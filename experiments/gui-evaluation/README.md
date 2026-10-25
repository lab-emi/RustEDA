
This is an isolated, optional research crate. It is not a RustLane runtime
dependency, does not change the CLI, and does not implement a production GUI.
The probe uses egui 0.37 / wgpu 30 APIs checked against the registry on
2026-10-23. It measures
virtualized report rendering and an offscreen GPU canvas, not a cross-framework
FPS competition or native-window input/presentation latency.

## Upfront memory and work estimate (before implementation/build)

- Logical report rows: at most 1,000,000; values are computed by index only for
  visible rows. Do not allocate a million strings or widgets. Hard cap 256 row
  callback invocations per measured frame, accounting for multi-pass layout.
- Logical layout: at most 1,000,000 rectangular cells on a regular grid.
  Coordinates and camera origins are i64 DBU. Only a bounded view is generated:
  at most 8,192 visible primitives/instances per frame. Display projections may
  use f32 after subtracting the integer camera origin. No graph/path traversal.
- Frame work: 20 warm-up frames and at most 240 measured frames per case, plus
  one readback; at most three sequential process repetitions. Sample storage
  < 64 KiB per process; no unbounded recording.
- CPU UI/mesh working state: conservatively < 64 MiB; target texture at 1280x800
  RGBA8 approximately 4 MiB, readback < 4 MiB; font/driver/GPU overhead budgeted
  conservatively at 1.5 GiB host RSS. Runtime cap 2 GiB with no swap, 120 seconds;
  a standard verification binary exceeding 2 GiB is a defect, not a benchmark win.
- Build with CARGO_BUILD_JOBS=2, estimated <= 8 GiB RSS including shader/compiler
  dependencies. Serialize behind RustLane `.work/scale.lock`; cgroup MemoryMax=16G,
  MemorySwapMax=4G, timeout 180 seconds. Check `free -h` first. Do not apply an
  address-space ulimit to native GPU rendering; drivers may reserve large ranges.
- Use `/usr/bin/time -v` for actual build/runtime RSS and elapsed measurements.
  The renderer explicitly waits for GPU completion with a finite timeout, so the
  frame measurements include CPU UI work, tessellation, upload, draw submission
  and GPU completion. Window presentation/OS input remain outside this probe.

The findings report will keep measured facts separate from documentation-derived
claims and engineering judgment. Other frameworks are not called slower without
an equivalent end-to-end comparison.

## Reproduce on the tested Linux host

From the RustLane repository root:

```sh
mkdir -p .work/gui-research
free -h
CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR="$PWD/.work/gui-target" \
  systemd-run --user --scope -p MemoryMax=16G -p MemorySwapMax=4G \
  flock .work/scale.lock /usr/bin/time -v \
  timeout 180s cargo build --locked --release \
  --manifest-path experiments/gui-evaluation/Cargo.toml

systemd-run --user --scope -p MemoryMax=2G -p MemorySwapMax=0 \
  /usr/bin/time -v timeout 120s \
  .work/gui-target/release/rustlane-gui-evaluation > .work/gui-research/probe.json
```

No window is opened. The native eframe shell is compiled and its Wgpu options
type-checked; the measured render path directly uses egui-wgpu offscreen. A native
Vulkan adapter is required for this probe; a software CPU adapter is explicitly
unverified. The native window event loop, input/presentation latency, CJK IME,
accessibility behavior and other platforms still need separate qualification.

The crate depends on `rustlane` in the GUI-to-core direction. The production
RustLane manifest/lockfile and CLI dependency graph remain free of GUI crates.
Use `cargo tree --manifest-path experiments/gui-evaluation/Cargo.toml -p rustlane`
to inspect that boundary. Build/cache artifacts are redirected into `.work/`.
