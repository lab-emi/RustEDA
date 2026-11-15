# Remote development reuse audit — 2026-11-14

The three-PDK work must continue from the remote implementations. Fetching all
branches does not by itself prove their development has been absorbed. This
audit therefore compares ancestry, patches and file contents, including archived
branches. It supplements the [merged-main verification](2026-11-14-merged-main-verification.md).

All 162 server branch heads across seven repositories are present locally:
RustEDA 25, Common 24, RustSys 17, RustSTA 21, RustROAD 67, RustSpice 6 and
RustSRAM 2. Five unused local `main` refs were safely fast-forwarded; active
development checkouts and all dirty files were preserved. The raw branch and
content comparisons are in `.work/remote-sync-20261114/reuse-audit/`.

## The implementation baseline to keep

| Remote work | Existing implementation and current use |
| --- | --- |
| Root `4c9ff92` | Ibex/SRAM wrappers, SDC and LibreLane configurations for SKY130 and GF180. Already in main; reused for preparation and vendor-model simulation. |
| Root `b752793`, `66c0626`; Common `24498a5` | PDK profile composition, native Rust flow executor and profile-driven frontend helpers. Already in main; the 114-step IHP replay and GF180 bring-up use them. |
| Root `56cdadc`, subsequent gate fixes | Shared DRC, antenna, LVS, timing, simulation, logic and density checks. Keep these as the referee framework. The new strict macro-limit policy is a narrow extension, not a replacement framework. |
| Root `2c9d7a1`, `d3ecbfb` | Existing SKY130/OpenRAM native profile and the reference Magic GDS decision. Preserve them for continued bring-up and reproduction. The new SRAM22 profile is an isolated candidate and must not silently replace this existing work. |
| Root `778f60a`, WIP `0626556` | GF180 native/micro profile, RC estimate, timing and diode drivers. All eight files of the WIP change match the current working files byte for byte (including the intended deletion). Preserve its provenance when committing integration. |
| RustROAD `5aecc5c`, WIP `55e576a` | SKY130 pin access, macro power, GDS master binding and antenna work, plus GF180 long-pin access, supply-shape choice and partial-report handling. Local merge `83a9990` has both remote commits as parents and retains both behaviors. |
| RustROAD WIP `62879ce` | Fourth-round per-strip accumulation and deterministic finalization. Local merge `6e168d2` retains it as a parent and adds memory admission/validation; `3a85bbd` adds revision guards for receipt writes; `3fe7b21` records exact-IHP correctness. Local integration `7af81be` now combines this line with `83a9990`; its combined fmt, Clippy and release tests pass (613 passed, 11 ignored). This is completion of the remote WIP, not an independently rewritten extractor. |
| RustSTA `76943e2`, `5fa30ad`, `de210a6` and following memory commits | Per-library units, driver fallback and peak-memory reductions. Already in main and used by the current binaries. |

## Historical branches that are already absorbed

SHA differences often come from integration or history cleanup, rather than
missing functionality. The following conclusions use actual content:

- Root archived `a0934f5` has an exact scoped patch equivalent in main at
  `8df7dc9`; its ten `integration/pulpissimo_soc` files match main. Historical
  PULPissimo measurements still require the later width-cast correctness caveat;
  do not borrow them as evidence for this Ibex goal.
- Root `42bb3b4`'s foundry/tap framework has no missing files in main. Three files
  match exactly; other changes add known-engine support, current producer fields
  and a post-route-order guard. Its capture/reuse protocol cannot be enabled
  blindly for the newer `screen_after_extract` order.
- Root `303fc5d`'s bounded antenna metadata scanner is the exact current blob.
  All eight files of the archived single-process antenna-session snapshot remain
  present; later main changes include the lineage-v4 protocol.
- RustSys archived `fd87479` has an exact patch equivalent at `1718201`.
  Fifteen of sixteen elaborator files still match; the other evolved in main.
  The opaque-macro tests from `635e386` remain, with 72 lines added for the newer
  unconnected-inout behavior. Do not reimplement these macro/front-end features.
- RustROAD archived main `f7655cc` and cleaned-up `977bc20` have identical
  `crates/`, `scripts/` and `fixtures/`. Archived GF180 DRV/hold helpers from
  `f843ecd` (`closure_eco.py` and its tests) are exact current blobs.
- The old RustROAD macro-power source in 31 archived heads is the version
  already absorbed at `07e1626`. Across the remote branches there are only three
  macro-supply versions: that old version, current SKY130/main, and GF180 WIP.
  No separate newer supply engine was missed.
- Four archived Common IHP repair snapshots have exactly the same five Liberty
  parser files as `24498a5`. Existing memory/bus support, pin-cap exporters and
  fixtures are already available and must be reused.
- RustSTA's archived `mixed_library_derates.py`, its documentation,
  `clock_probe.rs` and `rc_arc_probe.rs` are exact current blobs. Extend the
  existing mixed-library differential harness for new macro cases; do not
  publish a duplicate full driver just because an exploratory one was written.

## Useful remote assets not yet absorbed

| Remote asset | Reuse decision |
| --- | --- |
| RustROAD `710deb0`, changes `20a39f4`, `32fce9c`, `40d9152`: older `extract/stripes.rs` | Preserve and compare its halo checks, ordered merges and validation against fourth-round extraction. Its absence is a real content difference, not just a renamed SHA. Do not replace the new extractor without checking contracts and measurements. |
| RustROAD `710deb0`, `acd3c1a`: `fixtures/routing/openc910-a222oi-landing/` and `tests/access_landing.rs` | Replayed the 10-cell fixture against the current router on 15 November. All seed sites matched. Removing the retired JSON key `max_batch_blocked_pins` admitted the fixture, which still fails reservation. The pin trace shows a neighbor's escape covering first-via landing metal. The Ibex profile already excludes `a222oi_1`, and the live Ibex router passed reservation. Keep this regression isolated for a future pin-access repair. Raw evidence is under `.work/three-pdk-20261114/sky130-original-restore/archive-a222oi-acd3c1a/`. |
| RustROAD `5c862d2`, `8399332` | Diagnostic routing checkpoints/resume and long-net ordering. Retain as debugging/optimization candidates. A resumed checkpoint is not a fresh end-to-end benchmark. |
| Common G1 CPU/CUDA crossing experiments; RustROAD G1 capture/stream/crossings branches | Reuse component tests and bounded geometry interfaces if the measured bottleneck requires them. Their own records do not establish a finished GPU RTL-to-GDS flow or real-Ibex acceptance. |
| RustROAD `961583c` | Reuse its exact-gate receipts and dual-PDK/replay harnesses as validation assets. Its production candidate-query optimization has already been absorbed and later superseded. |
| RustSTA `11b8e1f`, `3e7e9b5`, `a009a03`: bounded `prop/lower/rc/trace.rs` | Retain for diagnosing actual RC mismatches. These probes target TT and particular historical instances; adapt the diagnostic narrowly rather than merging their old engine trees. |

The archive closeout receipts describe small designs, not Ibex with SRAM. Their
controls and harnesses are reusable; their successful numbers are not new
three-PDK benchmark results.

The older spatial-stripe extractor partitions the input working set and checks
halos/whole-net trees; fourth-round extraction retains the global graph and
changes unit order and coupling finalization. They are not interchangeable.
Reuse its 160-net partition controls, one-DBU-short halo, missing partner net,
wide obstruction and concurrent-memory refusal cases. The old core has no
current `min_coupling_pf` threshold and can choose different attachment nodes
for parallel plates, so importing it wholesale would exceed a low-order-only
numerical change. The complete extraction audit and five locally hash-verified
historical validation assets are indexed under
`.work/three-pdk-20261114/extraction-r4/remote-audit/`.

## New work that remains justified

All remote Common/STA trees were searched for SRAM22 and all remote Liberty
classifier blobs inspected. None contains SRAM22 support; each still marks
`sdf_edges` and retaining tables unsupported. The narrow local format fix
`ff8ea8b` therefore addresses a real missing capability. It keeps raw vendor
libraries and ordinary timing/limits, validates retained tables and emits an
explicit scope diagnostic. It does not establish full output-retention waveform
analysis. Existing difference/oracle tools should supply its final validation.

The GF180 layout runs reuse those closure and PDK mapping tools. Native run 07
passed frontend closure and reached detailed routing, where `core_sleep_o`
could not be routed. The next run uses the existing Metal2 I/O access layer.
Reference run 05 used the restored tool versions, completed Magic stream-out,
and passed the shared simulation. Its shared timing check recorded seven
slow-corner hold violations. The [benchmark receipts](../benchmarks/ibex-three-pdk.md)
carry the current layout evidence.

## Layout and documentation reuse

The remote `integration/ibex_pdks/figures/render_gds.py` is available and should
render the final layouts. No final GDS, annotated PNG/SVG or `compose_figure.js`
was found in the remote root trees searched under `integration/` and `docs/`.
The original handoff explicitly places the reference layouts and figure
composer in untracked cloud `.work/multipdk/`. Preserve its reported provenance
and recover available artifacts; do not invent absent layout images or treat
the absence of tracked binaries as absence of the implementation work.

Continue with the original profiles, composers, gates and WIP algorithms;
add only changes justified by reproducible failures or measured bottlenecks.
