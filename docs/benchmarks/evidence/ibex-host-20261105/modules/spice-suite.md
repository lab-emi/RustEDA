# RustSpice vs ngspice differential benchmark

## 1. Verdict

**PASS** — 10 cases, scale `medium`, seed 1, ngspice `ngspice-45`, rustspice 0.1.0 (0b557b5)

| group | cases | statuses |
|---|---|---|
| performance | 10 | pass=10 |
| **all** | **10** | pass=10 |

## 2. Known gaps (by slug)

No known-gap cases were run.

## 3. Capability matrix

No capability cases were run.

## 4. Accuracy

No accuracy cases were run.

## 5. Robustness

No static robustness cases were run.

## 6. Performance

Process start-up floor (trivial `.op` deck, wall of `-b -r` / `run --raw`): ngspice 12.35 ms / 8.48 ms, rustspice 4.20 ms / 3.05 ms (15 reps, interleaved).

Times are `median / min` over interleaved repetitions. Speedup = median ngspice / median rustspice (>1: rustspice faster) against the FASTER ngspice solver for that metric; both ngspice solvers are shown.

### End-to-end (subprocess wall: start-up + parse + setup + solve + write rawfile)

| case | size | ng SPARSE | ng KLU | rustspice | speedup (median) | speedup (min) | vs |
|---|---|---|---|---|---|---|---|
| `ibex_bulk_10` | 2262 | 435 ms / 403 ms | 495 ms / 467 ms | 86.22 ms / 78.73 ms (noisy) | 5.04x | 5.13x | ngspice_sparse |
| `ibex_bulk_40` | 6865 | 1.70 s / 1.60 s | 1.69 s / 1.54 s | 283 ms / 250 ms (noisy) | 5.96x | 6.17x | ngspice_klu |
| `ibex_net_00` | 291 | 420 ms / 389 ms | 502 ms / 442 ms | 90.02 ms / 82.32 ms (noisy) | 4.66x | 4.72x | ngspice_sparse |
| `ibex_net_01` | 260 | 342 ms / 334 ms | 434 ms / 368 ms | 72.15 ms / 67.71 ms | 4.74x | 4.93x | ngspice_sparse |
| `ibex_net_02` | 240 | 373 ms / 300 ms | 376 ms / 346 ms | 68.35 ms / 64.80 ms (noisy) | 5.45x | 4.63x | ngspice_sparse |
| `ibex_net_03` | 240 | 295 ms / 286 ms | 347 ms / 336 ms | 68.27 ms / 62.69 ms | 4.32x | 4.57x | ngspice_sparse |
| `ibex_net_04` | 237 | 301 ms / 291 ms | 362 ms / 336 ms | 66.02 ms / 62.86 ms | 4.55x | 4.64x | ngspice_sparse |
| `ibex_net_05` | 209 | 312 ms / 254 ms | 344 ms / 304 ms | 57.18 ms / 55.27 ms (noisy) | 5.45x | 4.60x | ngspice_sparse |
| `ibex_net_06` | 199 | 288 ms / 260 ms | 327 ms / 313 ms (noisy) | 57.17 ms / 55.25 ms | 5.04x | 4.71x | ngspice_sparse |
| `ibex_net_07` | 198 | 320 ms / 256 ms | 306 ms / 302 ms | 62.72 ms / 56.89 ms | 4.87x | 4.51x | ngspice_klu |

### Solve-only (ngspice `Total analysis time` vs rustspice `--stats analysis_us`) and overhead split

| case | ng SPARSE solve | ng KLU solve | RS solve | speedup (median) | speedup (min) | ng overhead | RS overhead | RS parse+compile | RSS ng / RS (MiB) | noisy |
|---|---|---|---|---|---|---|---|---|---|---|
| `ibex_bulk_10` | 387 ms / 368 ms | 460 ms / 430 ms | 60.54 ms / 55.45 ms (noisy) | 6.39x | 6.63x | 35.78 ms | 28.42 ms | 16.61 ms | 20.0 / 12.1 | yes |
| `ibex_bulk_40` | 1.56 s / 1.48 s | 1.59 s / 1.42 s | 202 ms / 178 ms (noisy) | 7.72x | 7.99x | 123 ms | 81.42 ms | 50.44 ms | 35.3 / 24.0 | yes |
| `ibex_net_00` | 407 ms / 372 ms | 482 ms / 430 ms | 74.65 ms / 70.05 ms (noisy) | 5.45x | 5.31x | 13.09 ms | 15.37 ms | 3.18 ms | 14.0 / 7.0 | yes |
| `ibex_net_01` | 324 ms / 318 ms | 422 ms / 355 ms | 58.28 ms / 57.15 ms | 5.57x | 5.57x | 16.98 ms | 11.74 ms | 1.61 ms | 13.9 / 6.9 | no |
| `ibex_net_02` | 356 ms / 287 ms | 364 ms / 334 ms | 57.47 ms / 53.30 ms (noisy) | 6.20x | 5.38x | 16.98 ms | 11.65 ms | 1.78 ms | 13.7 / 6.8 | yes |
| `ibex_net_03` | 282 ms / 275 ms | 333 ms / 322 ms | 55.54 ms / 51.52 ms | 5.07x | 5.34x | 12.50 ms | 11.73 ms | 1.75 ms | 13.8 / 6.8 | no |
| `ibex_net_04` | 289 ms / 277 ms | 350 ms / 324 ms | 55.12 ms / 52.14 ms | 5.24x | 5.30x | 12.52 ms | 12.12 ms | 1.50 ms | 13.7 / 6.8 | no |
| `ibex_net_05` | 295 ms / 243 ms | 328 ms / 292 ms | 46.34 ms / 45.35 ms (noisy) | 6.36x | 5.36x | 12.40 ms | 10.45 ms | 1.17 ms | 13.5 / 6.9 | yes |
| `ibex_net_06` | 277 ms / 249 ms | 315 ms / 302 ms (noisy) | 47.45 ms / 46.23 ms | 5.83x | 5.39x | 13.05 ms | 9.63 ms | 1.15 ms | 13.6 / 6.7 | yes |
| `ibex_net_07` | 307 ms / 245 ms | 294 ms / 289 ms | 51.51 ms / 46.62 ms (noisy) | 5.70x | 5.26x | 12.21 ms | 11.28 ms | 2.03 ms | 13.5 / 6.8 | no |

Overhead = end-to-end wall minus solve time (process start-up, load, parse, setup, rawfile write, exit); for the faster ngspice solver and for rustspice. rustspice parse+compile comes from `--stats` (`compile` includes the Cranelift JIT / symbolic LU build). Loadavg before/after each timed case is in the JSON.

### Slower than ngspice (end to end)

No timed case with a measured, non-noisy end-to-end median speedup below 1.0 against the faster ngspice solver (cases carrying a performance known-gap slug are judged by their own status).

### Peak RSS versus `bench.est_rss_mib` (Rule 9)

10 cases carry an estimate; it is RustSpice's peak RSS and is compared with the RustSpice child only (ngspice has a 15-20 MiB start-up floor of its own and is reported separately in the JSON `rss`). 0 exceed it by more than 25 %, 0 could not be measured.

## 7. Scaling fits

No family had enough timed size points.

## 8. Methodology and caveats

- Host: Intel(R) Xeon(R) Processor @ 2.80GHz (4 threads); load average 0.6 at start, 1.0 at end. Timing ratios are only comparable within this session; the host is shared.
- Temp files live on tmpfs (`/dev/shm`).
- ngspice: `ngspice-45`; as-is runs use default options, `.options klu` selects KLU (default is SPARSE). Solvers tested: sparse, klu.
- Accuracy is judged against a tightly converged ngspice reference (`.options reltol=1e-9 vntol=1e-12 abstol=1e-15`; transients: `reltol=1e-6 abstol=1e-15 vntol=1e-9` with `tmax = tstep/20`), not against default-tolerance ngspice, whose own error is reported separately. Independent f64 oracles are used where one exists.
- Default tolerances: op/dc |a-b| <= 1e-9 + 1e-6*|ref|; tran max error / swing <= 5e-3 (floor 1e-6). Probes whose reference is below the floor are vacuous and never count as a pass.
- Timing: interleaved A/B (execution order rotates every repetition), the correctness run is the warm-up, `n` repetitions per case, noisy = stddev/median > 0.2. Peak RSS is `VmHWM` of `/proc/<pid>/status` polled while the child runs (RustSpice also reports its own final reading in `--stats`), never `wait4` `ru_maxrss`, which is floored by the harness's own high-water mark; a child that ends before the first poll has `rss_status: unmeasured` and a `null` value.
- Transient comparison: both waveforms are linearly interpolated onto the nominal output grid `k * tstep` (the instants at which a fixed-step simulator has samples) inside the window [first ngspice row, tstop]; `.tran` start times are honoured, a `tstep` >= `tstop` or a parameterised card uses the union of the ngspice and rustspice time rows. Series members coarser than the deck's own `.tran` step are work-precision points (`info` when they miss `tol.norm`).
- Decks run with the working directory set to their own directory (relative `.include`/`.lib` targets resolve for both tools). A segfault of ngspice is retried once; a deterministic crash is `ngspice_error`. Tag `ngspice_unbounded` skips ngspice (`ngspice_status: skipped_unbounded`); a caught panic message with a clean exit is a refusal, only exit 101 or a signal is a panic.
- Scope: nominal temperature 27 C (300.15 K; `.temp`, `.options temp/tnom` and instance `temp/dtemp` are honoured by both tools), `.options gmin` is honoured and solver-tolerance `.options` emit warnings in RustSpice, RustSpice transients use adaptive LTE step control and write nominal `k * tstep` rows (or internal steps with `--internal-points`), one analysis per deck unless stated. A missing ngspice yields `unverified`, never a pass.

## 9. Reproduction

```
rustspice bench-suite --cases decks --filter ibex_ --no-fuzz --reps 7 --ngspice /opt/eda/ngspice/bin/ngspice --json suite.json --report suite.md --artifacts artifacts
```

Single case: `rustspice run <deck> --raw out.raw --stats out.json` against `ngspice -b -r ref.raw <deck>`; kept artifacts of non-passing cases are listed in the JSON (`artifacts`).
