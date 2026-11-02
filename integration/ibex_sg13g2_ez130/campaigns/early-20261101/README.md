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
tap proof. The other four files are byte copies of the files R20 read on the campaign
host. A recipe composed from the run16 files reproduces run16-class routing (about
1.68 Mm of signal wire and 301k vias), not R20's 1,190,522 µm and 168,381 vias.

The two prepare helpers are not interchangeable. Each rejects the other recipe's tap
template (`max_gap` 30000 with the staggered pattern, against 15000), and only the R20
helper writes the `routing-writer-audit.json` that the joint tap qualification reads.
`tests/test_r20_recipe.py` runs both directions.

1. In a fresh checkout, `r20_recipe.py verify` must report `match` for every role.
2. Write the composer configs the same way as for run16
   (`rusteda.native-frontend-inputs.v1` for `native_frontend.py`, and the
   backend and flow inputs for `native_backend.py` and `native_flow.py`). Then
   run `r20_recipe.py overlay --config CONFIG --output R20-CONFIG` on each one.
   This replaces every `files.<role>` record in the table with the verified R20
   file. Compose from `R20-CONFIG`. A foundry-first bundle's `manifest.json` pins its
   members by absolute path, so all six `foundry_first_*` roles must come from one
   bundle.
3. `r20_recipe.py verify --manifest WORKSPACE/manifest.json` accepts the composed
   manifest only if `physical`, `routing` and `prepare` are present, and if every
   role from the table that the manifest names hash-matches R20.

`r20_recipe.py import` recreates the files from the paths recorded in
`inputs_frozen.json` under `--source-root`. It writes a file only when the SHA-256 and
size equal the pin, and it never overwrites a file that has different bytes.

### What a composed manifest reproduces

This was checked on the campaign host on 2026-11-02, composing from a fresh clone
through all three composers. PDK files, tools, RTL and the foundry-first bundle stayed
at their recorded host paths. All five roles above match, and 92 of the 96 file roles
and every tool carry R20's bytes. The manifest was composed, not run. It still differs
from R20's manifest:

| Difference from R20 | Cause |
| --- | --- |
| 128 steps and six parallel groups, not 129 and one. No `be_tap_export`. `nf_tap_latchup` reads the final GDS and the postroute profile. | `native_flow.py` composes the serial policy. The campaign harness applied the early schedule and its `tap-policy.json` afterwards. |
| `fe_temporary_run`, `fe_repair_run` and `fe_final_run` use `--threads 2`, not 8. | Campaign harness edit. |
| `be_postroute_inputs` and `be_antenna_inputs` lack `--threads` and the `--extract-query-workers 16`, `--extract-query-batch-runs 8` and `--extract-query-scratch-mib 32` options. | The repository's `native_backend.py` (`323c0c79…`, 23,095 B) is not the one R20 read (`957bdc4b…`, 23,350 B). |

The remaining role differences follow from these: `tap_policy`, `backend_composer`,
and the derived `flow_config` and `flow_backend_fragment`. Hash-matching recipe files
therefore do not carry over the 472 s measurement. A new run must measure its own time.
The repository also does not contain `tap-policy.json`, the foundry-first
`policy.json`/`manifest.json` or that run's `native_backend.py`, and the full early
schedule needs them.

[Native results and scope](https://lab-emi.github.io/RustEDA/benchmarks/) ·
[Portable evidence inventory](https://lab-emi.github.io/RustEDA/benchmarks/ibex-sg13g2-ez130-development.json)
