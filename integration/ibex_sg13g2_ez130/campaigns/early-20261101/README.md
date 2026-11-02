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

## Composing the R20 recipe

R20 and R21 read the same recipe bytes, pinned by path, SHA-256 and size in
[`inputs_frozen.json`](../../../../docs/benchmarks/evidence/ibex-20261101/R20-early-sta/gates/inputs_frozen.json).
Five of these files differ from the run16 files in the parent directory. Older
receipts still pin the run16 files, so the R20 files sit beside them:

| Role | R20 file | SHA-256 | Bytes | run16 file |
| --- | --- | --- | --- | --- |
| `physical` | `native-physical-r20.json` | `0c7a4e6a…` | 2,622 | `native-physical.json` (`5fe299cf…`, 1,883) |
| `routing` | `native-routing-r20.json` | `fe59328a…` | 3,175 | `native-routing.json` (`aba6ee1d…`, 3,175) |
| `prepare` | `native_frontend_inputs_r20.py` | `ed7e7c08…` | 28,480 | `native_frontend_inputs.py` (`ef7d5550…`, 26,240) |
| `rc_policy` | `rc-estimate-policy-r20.json` | `611de51a…` | 904 | `rc-estimate-policy.json` (`21504492…`, 926) |
| `contract` | `contract-r20.json` | `96d227ad…` | 5,666 | `contract.json` (`a58f53fe…`, 5,666) |

`contract-r20.json` is the common contract with `native_tap_max_gap_um` set to 30
instead of 15. This is the T1 contract that `native_flow.py` requires for the joint
tap proof, and its SHA-256 equals the pin. The other four files came from the
campaign host's `.work` directory. Do not reconstruct them from the values listed
above: a host that composes from the run16 files reproduces run16-class routing
(about 1.68 Mm of signal wire and 301k vias), not R20's 1,190,522 µm and 168,381
vias. Until the files are imported, `verify` reports them as `missing`.

1. On the campaign host, import the files once and commit the result:

   ```bash
   python3 integration/ibex_sg13g2_ez130/campaigns/early-20261101/r20_recipe.py import
   ```

   The tool reads each recorded path (the R20 recipe first, then R21's) under
   `--source-root`, which defaults to `/home/cgao/git/RustEDA`. It writes a file
   only when the SHA-256 and size equal the pin, and it never overwrites a file
   that has different bytes.
2. In a fresh checkout, `r20_recipe.py verify` must report `match` for every role.
3. Write the composer configs the same way as for run16
   (`rusteda.native-frontend-inputs.v1` for `native_frontend.py`, and the
   backend and flow inputs for `native_backend.py` and `native_flow.py`). Then
   run `r20_recipe.py overlay --config CONFIG --output R20-CONFIG` on each one.
   This replaces every `files.<role>` record in the table with the verified R20
   file. Compose from `R20-CONFIG`.
4. `r20_recipe.py verify --manifest WORKSPACE/manifest.json` accepts the composed
   manifest only if `physical`, `routing` and `prepare` are present, and if every
   role from the table that the manifest names hash-matches R20.

The R20 prepare helper stays beside the run16 helper. Replace the run16 helper
only after checking that the R20 helper is backward compatible with the run16
recipe. This tool covers recipe policy only. `inputs_frozen.json` also pins other
host-local files that the repository does not contain, such as `tap-policy.json`,
the foundry-first `policy.json`/`manifest.json` and that run's `native_backend.py`.
The full early schedule needs them as well.

[Native results and scope](https://lab-emi.github.io/RustEDA/benchmarks/) ·
[Portable evidence inventory](https://lab-emi.github.io/RustEDA/benchmarks/ibex-sg13g2-ez130-development.json)
