# Run a design flow

The integrated Linux flow uses RustEDA's revision-bound external worker,
RustSys, RustSTA and RustROAD. Configure the pinned tools, PDK libraries,
verification decks and OCI images described by
[`integration/tools.example.toml`](../integration/tools.example.toml).
Podman and a user systemd manager are required for the qualified worker.

```sh
./rusteda tools configure
./rusteda tools verify
./rusteda flow --workspace .work/counter init --base-rev empty --design counter --pdk sky130
./rusteda flow --workspace .work/counter run --base-rev rev0
./rusteda flow --workspace .work/counter report --summary-only
```

Use `--pdk gf180` for the second baseline. Each design/PDK configuration gets its
own workspace. Every mutation rejects a stale `--base-rev` with `REVISION_CONFLICT`.
Read-only queries inspect committed snapshots while a worker runs. Completed
work can be reused only after its inputs, dependencies, outputs and gate results
are revalidated. A cache hit is not a new physical run.

## Recovery

After interruption, inspect the workspace, then run `recover --base-rev <current>`.
Recovery verifies and cleans only the owned worker. Use the returned revision
with `run --retry-failed --base-rev <new>`. Unknown cleanup prevents a retry.
Changed producer inputs require a new workspace.

## Results

Reports retain all 25 producer gates, source/tool/artifact identities, timing
corners, wire model, unconstrained endpoint counts and declared deviations.
`deviation_declared` is distinct from `passed`. Missing reference tools and
incomplete checks are `unverified`.

The [qualification summary](verification.md) covers small blocks on SKY130 HD
and GF180 7T. It is not an OpenC910 performance or tapeout-signoff result.
The [RustEDA flow worker contract](workbench/flow/rusteda-worker.md) and
[RustROAD physical flow](../modules/RustROAD/docs/rtl2gds.md) document interfaces.
