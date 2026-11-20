# Shared foundation workflow

`modules/RustEDA-Common` is the single source of truth for `rusteda-core`, `rusteda-workspace`, `rusteda-formats`, `rusteda-compute`, and `rusteda-cli-common`. Downstream repositories (`RustSys`, `RustSTA`, and `apps/`) pin one exact Git revision rather than duplicating shared crates.

## Routine checks

```sh
./dev status
./dev test RustEDA-Common
./dev test RustSys
./dev test RustSTA
./dev test RustEDA --no-default-features
./dev shared
```

## Updating a shared change

1. Implement, test, and push the change on `modules/RustEDA-Common` first so the exact commit SHA is reachable over HTTPS.
2. Update the `rev = "<sha>"` pin for all five foundation crates in `modules/RustSys/Cargo.toml`, `modules/RustSTA/Cargo.toml`, and `apps/Cargo.toml`, then refresh each lockfile (`cargo update --workspace`).
3. Run bounded regression checks in each consumer repository; thin CLI adapters must continue reporting their own product version.
4. Update the submodule gitlinks in `RustEDA`, verify convergence with `./dev shared`, and commit the superproject.

Never replace exact `rev` pins with floating branches or commit local path overrides in `Cargo.lock`.

## Manifests and verification boundaries

- Maintain `RustEDA-Common`'s source manifest with `python3 scripts/shared_manifest.py`; consumers update their thin adapters and shared specs with `cargo xtask shared update`.
- CPU unit tests in `RustEDA-Common` do not verify CUDA, Metal, or physical signoff; engine-level changes still require independent dual-PDK / signoff verification within bounded resource limits.
- CLI compatibility across upgrades is verified by `integration/check_cli_compat.py`, which compares real executables while normalizing only timestamps, workspace paths, and binary paths.
