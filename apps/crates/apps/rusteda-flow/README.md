# RustEDA flow engine

Internal implementation of `rusteda flow`, built with the RustEDA applications.
See the [root flow guide](../../../../docs/flows.md), the
[worker contract](../../../docs/flow/rusteda-worker.md) and the
[native step-graph executor](../../../docs/flow/native-executor.md) (`rusteda flow exec`).

The existing `rustlane.*` schema strings are retained solely for disk/receipt
compatibility. This crate does not publish a separate product or binary.
