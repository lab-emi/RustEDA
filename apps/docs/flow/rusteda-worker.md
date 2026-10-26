# RustEDA external-worker contract

`flow: "rusteda"` executes the qualified RustROAD RTL-to-GDS producer, which
invokes RustSys, RustSTA and its pinned independent verification tools. The
RustEDA flow runtime remains Rust-only. The external producer uses Python and
containerized signoff tools.

The qualified producer uses two tool threads. Other `limits.threads` values are
rejected for this flow rather than advertised as effective overrides.

The native process supervisor deliberately prohibits namespace creation.
Container work therefore uses an explicit external-worker protocol, rather
than changing that supervisor's guarantees. No resident server is required.

## State transitions

1. `init --base-rev empty` captures source scripts, fixture definitions, PDK
   data and host-tool identities, with per-file, aggregate, depth and count bounds.
2. `prepare --base-rev revN` validates inputs, allocates an unused attempt,
   publishes a hashed `request.json`, commits `running` and releases the lock.
3. A trusted external worker runs the request under its process/container
   ownership boundary. Queries can read the published snapshot while it runs.
4. `finish --base-rev revN --receipt FILE` checks the exact request, logs,
   cleanup receipt and complete producer contract, then publishes a new revision.

The RustEDA root command `./dev flow` drives these steps. Direct `rusteda flow run`
continues to drive native flows; it does not silently execute external work.
Use `rusteda flow worker` to inspect an outstanding request.

Successful completion requires all 25 producer gates, the global identity and
configuration checks, native and OpenRCX SPEF, TT/SS/FF timing coverage, CPU/f64
timing with zero unconstrained inputs/endpoints, matching manifest identity,
bound artifacts and raw declaration evidence. `deviation_declared` remains an
explicit result. Neither exit zero nor a copied `passed` label is sufficient.

This is an integrity protocol between trusted tools, not cryptographic proof
against a malicious worker. Tool outputs are accepted only through the stated
contract and independent producer oracles.

## Cancellation, recovery and reuse

The root worker uses a uniquely named systemd user scope and task-labelled
containers. Cleanup checks ownership before stopping a scope or removing a
container. A persistent global reservation prevents a new heavy run after its
caller was abruptly killed. `dev flow recover` explicitly cleans that request;
retry requires `--retry-failed` and uses a new attempt directory.
Recovery can inspect the persisted request after producer inputs change; completion
records that change as a failure even if the worker itself returned zero.

An unknown cleanup result remains `running`/`cleanup_unverified`; it cannot be
automatically retried. Old attempts, logs and orphan revisions are preserved.
Host failure, inaccessible container state or uninterruptible I/O cannot be
claimed as successful cleanup. Completed tasks are reused only after input,
tool, output and current acceptance-contract checks. Dependency inventories are
re-enumerated so added files also invalidate reuse. Reuse commits no revision.

External receipts report the maximum observed individual worker/tool/container
RSS, not simultaneous aggregate family RSS. The cgroup bounds aggregate worker
memory, and each producer container keeps its own memory limit.

The generic `classic` recipe adapter remains unqualified for full post-route
signoff. Its historical WIP is preserved separately; the qualified path is
`rusteda`. Current production support is Linux with a user systemd manager,
Podman, SKY130 HD and GF180 7T. No new industrial-scale or foundry signoff claim
is made by this integration.
