# RustSpice vs ngspice differential benchmark

## 1. Verdict

**PASS** — 10 cases, scale `medium`, seed 1, ngspice `ngspice-45`, rustspice 0.1.0 (cc2426f)

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

Process start-up floor (trivial `.op` deck, wall of `-b -r` / `run --raw`): ngspice 6.77 ms / 5.40 ms, rustspice 2.47 ms / 2.30 ms (15 reps, interleaved).

Times are `median / min` over interleaved repetitions. Speedup = median ngspice / median rustspice (>1: rustspice faster) against the FASTER ngspice solver for that metric; both ngspice solvers are shown.

### End-to-end (subprocess wall: start-up + parse + setup + solve + write rawfile)

| case | size | ng SPARSE | ng KLU | rustspice | speedup (median) | speedup (min) | vs |
|---|---|---|---|---|---|---|---|
| `ibex_bulk_10` | 5843 | 1.11 s / 1.01 s | 1.20 s / 1.11 s | 197 ms / 160 ms | 5.63x | 6.33x | ngspice_sparse |
| `ibex_bulk_40` | 20023 | 4.07 s / 3.78 s | 4.10 s / 3.93 s | 704 ms / 650 ms | 5.78x | 5.81x | ngspice_sparse |
| `ibex_net_00` | 649 | 594 ms / 528 ms | 666 ms / 628 ms | 161 ms / 133 ms | 3.68x | 3.96x | ngspice_sparse |
| `ibex_net_01` | 639 | 602 ms / 576 ms | 728 ms / 666 ms | 153 ms / 144 ms | 3.94x | 4.00x | ngspice_sparse |
| `ibex_net_02` | 610 | 618 ms / 526 ms | 703 ms / 596 ms | 148 ms / 132 ms | 4.18x | 3.97x | ngspice_sparse |
| `ibex_net_03` | 600 | 563 ms / 539 ms | 631 ms / 527 ms | 140 ms / 112 ms | 4.02x | 4.69x | ngspice_sparse |
| `ibex_net_04` | 580 | 558 ms / 451 ms | 643 ms / 538 ms | 137 ms / 112 ms | 4.06x | 4.02x | ngspice_sparse |
| `ibex_net_05` | 575 | 534 ms / 464 ms | 656 ms / 581 ms | 136 ms / 118 ms | 3.92x | 3.93x | ngspice_sparse |
| `ibex_net_06` | 570 | 564 ms / 516 ms | 649 ms / 534 ms | 141 ms / 135 ms | 3.99x | 3.81x | ngspice_sparse |
| `ibex_net_07` | 548 | 571 ms / 484 ms | 645 ms / 578 ms | 140 ms / 132 ms | 4.09x | 3.68x | ngspice_sparse |

### Solve-only (ngspice `Total analysis time` vs rustspice `--stats analysis_us`) and overhead split

| case | ng SPARSE solve | ng KLU solve | RS solve | speedup (median) | speedup (min) | ng overhead | RS overhead | RS parse+compile | RSS ng / RS (MiB) | noisy |
|---|---|---|---|---|---|---|---|---|---|---|
| `ibex_bulk_10` | 1.04 s / 953 ms | 1.14 s / 1.04 s | 135 ms / 106 ms | 7.74x | 9.01x | 62.18 ms | 62.26 ms | 31.25 ms | 30.0 / 45.1 | no |
| `ibex_bulk_40` | 3.82 s / 3.62 s | 3.89 s / 3.77 s | 489 ms / 467 ms | 7.82x | 7.75x | 217 ms | 184 ms | 96.40 ms | 69.9 / 140.8 | no |
| `ibex_net_00` | 580 ms / 517 ms | 655 ms / 614 ms | 126 ms / 102 ms | 4.60x | 5.08x | 13.38 ms | 35.35 ms | 3.31 ms | 15.3 / 37.5 | no |
| `ibex_net_01` | 589 ms / 563 ms | 714 ms / 653 ms | 123 ms / 116 ms | 4.80x | 4.85x | 13.54 ms | 29.89 ms | 3.01 ms | 15.3 / 37.1 | no |
| `ibex_net_02` | 604 ms / 510 ms | 686 ms / 582 ms | 118 ms / 107 ms | 5.10x | 4.78x | 13.96 ms | 29.65 ms | 3.13 ms | 15.3 / 35.8 | no |
| `ibex_net_03` | 549 ms / 528 ms | 617 ms / 514 ms | 110 ms / 87.67 ms | 5.01x | 5.87x | 12.70 ms | 29.95 ms | 2.85 ms | 15.2 / 35.3 | no |
| `ibex_net_04` | 547 ms / 440 ms | 630 ms / 527 ms | 107 ms / 88.85 ms | 5.14x | 4.95x | 11.41 ms | 29.31 ms | 2.86 ms | 15.2 / 34.4 | no |
| `ibex_net_05` | 523 ms / 452 ms | 646 ms / 563 ms | 110 ms / 91.56 ms | 4.74x | 4.94x | 11.66 ms | 26.83 ms | 2.76 ms | 15.1 / 34.1 | no |
| `ibex_net_06` | 550 ms / 502 ms | 630 ms / 522 ms | 113 ms / 107 ms | 4.88x | 4.69x | 13.81 ms | 29.84 ms | 2.87 ms | 15.2 / 33.9 | no |
| `ibex_net_07` | 558 ms / 469 ms | 633 ms / 565 ms | 110 ms / 99.60 ms | 5.06x | 4.70x | 13.07 ms | 30.20 ms | 2.73 ms | 15.0 / 32.9 | no |

Overhead = end-to-end wall minus solve time (process start-up, load, parse, setup, rawfile write, exit); for the faster ngspice solver and for rustspice. rustspice parse+compile comes from `--stats` (`compile` includes the Cranelift JIT / symbolic LU build). Loadavg before/after each timed case is in the JSON.

### Slower than ngspice (end to end)

No timed case with a measured, non-noisy end-to-end median speedup below 1.0 against the faster ngspice solver (cases carrying a performance known-gap slug are judged by their own status).

### Peak RSS versus `bench.est_rss_mib` (Rule 9)

10 cases carry an estimate; it is RustSpice's peak RSS and is compared with the RustSpice child only (ngspice has a 15-20 MiB start-up floor of its own and is reported separately in the JSON `rss`). 0 exceed it by more than 25 %, 0 could not be measured.

## 7. Scaling fits

No family had enough timed size points.

## 8. Methodology and caveats

- Host: Intel(R) Xeon(R) Processor @ 2.10GHz (4 threads); load average 0.4 at start, 1.0 at end. Timing ratios are only comparable within this session; the host is shared.
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
