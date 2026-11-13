# RustSpice vs ngspice differential benchmark

## 1. Verdict

**PASS** — 10 cases, scale `medium`, seed 1, ngspice `ngspice-45`, rustspice 0.1.0 (71875bf)

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

Process start-up floor (trivial `.op` deck, wall of `-b -r` / `run --raw`): ngspice 6.53 ms / 5.53 ms, rustspice 2.33 ms / 2.05 ms (15 reps, interleaved).

Times are `median / min` over interleaved repetitions. Speedup = median ngspice / median rustspice (>1: rustspice faster) against the FASTER ngspice solver for that metric; both ngspice solvers are shown.

### End-to-end (subprocess wall: start-up + parse + setup + solve + write rawfile)

| case | size | ng SPARSE | ng KLU | rustspice | speedup (median) | speedup (min) | vs |
|---|---|---|---|---|---|---|---|
| `ibex_bulk_10` | 1836 | 299 ms / 282 ms | 349 ms / 300 ms | 46.94 ms / 40.93 ms (noisy) | 6.37x | 6.88x | ngspice_sparse |
| `ibex_bulk_40` | 5614 | 1.16 s / 1.10 s | 1.23 s / 1.19 s | 165 ms / 143 ms | 7.02x | 7.67x | ngspice_sparse |
| `ibex_net_00` | 237 | 257 ms / 232 ms | 307 ms / 286 ms | 47.97 ms / 45.02 ms | 5.36x | 5.14x | ngspice_sparse |
| `ibex_net_01` | 206 | 244 ms / 223 ms | 277 ms / 249 ms | 42.12 ms / 40.54 ms | 5.79x | 5.49x | ngspice_sparse |
| `ibex_net_02` | 190 | 195 ms / 178 ms | 234 ms / 221 ms | 40.92 ms / 33.67 ms | 4.76x | 5.28x | ngspice_sparse |
| `ibex_net_03` | 186 | 221 ms / 202 ms | 257 ms / 226 ms | 53.33 ms / 33.14 ms (noisy) | 4.14x | 6.08x | ngspice_sparse |
| `ibex_net_04` | 175 | 195 ms / 183 ms | 230 ms / 189 ms | 39.40 ms / 31.39 ms (noisy) | 4.96x | 5.84x | ngspice_sparse |
| `ibex_net_05` | 174 | 213 ms / 199 ms | 230 ms / 224 ms | 41.36 ms / 39.37 ms (noisy) | 5.15x | 5.05x | ngspice_sparse |
| `ibex_net_06` | 173 | 201 ms / 179 ms | 222 ms / 206 ms | 37.86 ms / 31.68 ms | 5.31x | 5.66x | ngspice_sparse |
| `ibex_net_07` | 173 | 236 ms / 206 ms | 277 ms / 240 ms | 49.94 ms / 39.61 ms (noisy) | 4.73x | 5.20x | ngspice_sparse |

### Solve-only (ngspice `Total analysis time` vs rustspice `--stats analysis_us`) and overhead split

| case | ng SPARSE solve | ng KLU solve | RS solve | speedup (median) | speedup (min) | ng overhead | RS overhead | RS parse+compile | RSS ng / RS (MiB) | noisy |
|---|---|---|---|---|---|---|---|---|---|---|
| `ibex_bulk_10` | 275 ms / 260 ms | 317 ms / 279 ms | 32.03 ms / 27.12 ms (noisy) | 8.59x | 9.60x | 25.12 ms | 14.90 ms | 8.18 ms | 18.6 / 10.8 | yes |
| `ibex_bulk_40` | 1.09 s / 1.04 s | 1.18 s / 1.13 s | 115 ms / 101 ms | 9.50x | 10.3x | 60.64 ms | 45.64 ms | 25.95 ms | 30.5 / 21.2 | no |
| `ibex_net_00` | 247 ms / 223 ms | 297 ms / 276 ms | 38.80 ms / 36.12 ms | 6.36x | 6.16x | 10.03 ms | 8.91 ms | 1.20 ms | 13.7 / 6.6 | no |
| `ibex_net_01` | 234 ms / 213 ms | 266 ms / 240 ms | 34.51 ms / 32.88 ms | 6.79x | 6.48x | 9.75 ms | 8.00 ms | 1.11 ms | 13.7 / 6.6 | no |
| `ibex_net_02` | 186 ms / 169 ms | 225 ms / 212 ms | 33.25 ms / 27.35 ms | 5.60x | 6.20x | 8.69 ms | 7.67 ms | 1.05 ms | 13.5 / 6.7 | no |
| `ibex_net_03` | 210 ms / 193 ms | 247 ms / 216 ms | 43.21 ms / 26.42 ms (noisy) | 4.85x | 7.29x | 11.31 ms | 8.40 ms | 1.02 ms | 13.5 / 6.6 | yes |
| `ibex_net_04` | 186 ms / 175 ms | 220 ms / 181 ms | 31.97 ms / 24.96 ms (noisy) | 5.82x | 7.00x | 9.29 ms | 7.43 ms | 935 µs | 13.4 / 6.5 | yes |
| `ibex_net_05` | 199 ms / 189 ms | 220 ms / 215 ms | 33.87 ms / 32.03 ms (noisy) | 5.88x | 5.90x | 10.71 ms | 7.65 ms | 1.05 ms | 13.5 / 6.5 | yes |
| `ibex_net_06` | 190 ms / 171 ms | 214 ms / 197 ms | 30.64 ms / 25.46 ms | 6.21x | 6.71x | 9.70 ms | 7.12 ms | 884 µs | 13.5 / 6.5 | no |
| `ibex_net_07` | 225 ms / 196 ms | 266 ms / 226 ms | 37.15 ms / 31.94 ms (noisy) | 6.07x | 6.14x | 11.53 ms | 9.05 ms | 1.27 ms | 13.5 / 6.6 | yes |

Overhead = end-to-end wall minus solve time (process start-up, load, parse, setup, rawfile write, exit); for the faster ngspice solver and for rustspice. rustspice parse+compile comes from `--stats` (`compile` includes the Cranelift JIT / symbolic LU build). Loadavg before/after each timed case is in the JSON.

### Slower than ngspice (end to end)

No timed case with a measured, non-noisy end-to-end median speedup below 1.0 against the faster ngspice solver (cases carrying a performance known-gap slug are judged by their own status).

### Peak RSS versus `bench.est_rss_mib` (Rule 9)

10 cases carry an estimate; it is RustSpice's peak RSS and is compared with the RustSpice child only (ngspice has a 15-20 MiB start-up floor of its own and is reported separately in the JSON `rss`). 0 exceed it by more than 25 %, 0 could not be measured.

## 7. Scaling fits

No family had enough timed size points.

## 8. Methodology and caveats

- Host: Intel(R) Xeon(R) Processor @ 2.10GHz (4 threads); load average 0.5 at start, 1.0 at end. Timing ratios are only comparable within this session; the host is shared.
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
