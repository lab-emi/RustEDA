# Recorded early-check campaign

R20/R21 (2026-11-01) complete 129 steps and 16 gates in 472.2/472.6 seconds.
This directory records the selected step/dependency schedule. The source helpers
are `producer.py`, `qualify_native.py`, `tap_capture.py` and `foundry_first/` in
the parent integration directory. The conservative `native_flow.py` composer
continues to select the serial policy; this is an explicit campaign configuration,
not a new default or a turnkey recipe for other designs.

[schedule.json](schedule.json) preserves the measured commands, placeholders,
resource reservations and order. Its input/tool identities belong to the original
frozen recipe. A new run must prepare and pin its own complete manifest and exact
component controls, not reuse reports or blindly replace paths in a measured run.
Source builds and installation are outside the reported worker timer.

The opt-in policy execution is `early_maximal_same_immutable_gds`. Maximal starts
on the read-only `backend/tap-early.gds` while postroute and later independent
checks run under one 7168 MiB admission budget. The outer worker has an 8192 MiB
RAM cap. Byte equality with the final GDS and a clean foundry-first decision are
mandatory. A real resize or antenna repair rejects this schedule and requires a
fresh serial run. Stdout exports have independent per-file/aggregate byte caps;
commands retain deadlines and cleanup of their own process groups.

The measured setup uses spacing-aware density, target density 0.7, explicit
checkpoint retention at the evaluation horizon, Metal2 access, heuristic weight
2, via cost 1680 and RC detour 1.08. Frontend STA uses eight workers. These values
are recipe inputs, not library-specific engine heuristics.

**Not recomposable from a fresh checkout.** The R20 and R21 frozen-input records
bind three recipe inputs that are not in the repository: `physical.json`
(`0c7a4e6a…`), `routing.json` (`fe59328a…`) and `native_frontend_inputs.py`
(`ed7e7c08…`), all taken from the original host's local `.work/` directories.
The repository's `native-physical.json`, `native-routing.json` and
`native_frontend_inputs.py` are run16's helper set and hash differently
(`5fe299cf…`, `aba6ee1d…`, `ef7d5550…`). The same records also bind other files
with no byte-identical repository copy, for example `rc-policy.json`
(`611de51a…`) and the backend composer `native_backend.py` (`957bdc4b…`). This
campaign therefore cannot be recomposed from a fresh checkout. The
[2026-11-02 host reproduction](../../../ibex_host_benchmark/README.md) composed
the repository recipe instead and reproduced run16-class QoR, not R20/R21's.
Record: [R20 frozen inputs](../../../../docs/benchmarks/evidence/ibex-20261101/R20-early-sta/gates/inputs_frozen.json).

[Native results and scope](https://lab-emi.github.io/RustEDA/benchmarks/) ·
[Portable evidence inventory](https://lab-emi.github.io/RustEDA/benchmarks/ibex-sg13g2-ez130-development.json)
