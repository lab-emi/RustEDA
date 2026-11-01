# C7 foundry-first implementation (isolated, 2026-10-31)

Base ab0df54520fdc8e6d8855e12b4c42063f7cbfd76, branch codex/ibex-foundry-first. Preserve qualified engines, legacy v2/v3/default behavior, all physical/timing/equivalence thresholds and sixteen fresh gate obligations. No full EDA/build/fullflow launch authorized. Parent owns selection.

Upfront memory/work estimate before edits: new helpers stream SHA256/copies through64KiB buffers and retain bounded file manifests, argv and small metadata only. Revision snapshot max64regular files and512MiB aggregate; one supported metadata prefix≤64KiB; no IR/geometry decoding. Command/config/report JSON≤2MiB each, categories≤4096/findings≤500k and64MiB raw XML (existing foundry reader bounds). Estimated protocol/test RSS<128MiB, guarded Python tests1GiB/60s, one/two test workers CPU28–29; no graph/path materialization. Native/reference engine caps unchanged. New parallel precheck admits two existing bounded main/antenna children only within product8GiB ownership; estimate2×(48×GDS bytes+256MiB)+128MiB, maxGDS64MiB gives6.625GiB, no new child scopes. Snapshot hashing/copy budget512MiB per inventory, artifact copies use existing GDS/SPEF caps. Unsupported schema/inventory/options are explicit misses/fallback or errors, never ignored. Available55GiB RAM/38GiB disk; source-only work<20MiB. All future tests timed by pinned GNUtime; no >4GiB new build/run authorized.

Evidence/design: .work/perf-20261030/c7-preant-baseline (rev4 main0/antenna0, positive139, native285flagged; one-layout scope), and c7-foundry-first-design/design.html rev2 SHA6b80abfc… independently reviewed. No general false-negative claim. External effective inputs require owned immutable copies or full-interval snapshot lock; before/after hashes alone cannot exclude ABA.

Implementation scope: explicit new foundry-first source roles/policy select a new postroute/capture, qualification and typed-proof schema. With those roles absent the v2/v3 path, recipes, result/stdout shapes and gate thresholds retain their legacy dispatch. The opt-in source bundle is prepared by `freeze.py`; add its five `sources` records and `foundry_first_sources` manifest FileID to the backend files before ordinary native-flow assembly. No numerical route/timing/extraction knob is changed. The original123-step recipe grows to125; the existing128-step cap remains. Early original main/antenna jobs use two disjoint two-core lanes when16CPU slots exist, or serialize on the same two lanes for2CPU allocations. Product owns all existing cgroup/scale-lock resources; helpers do not create scopes/daemons.

Strict scope: only exact audited c2ae/f47 ELF identities may claim whole-input reuse. c2ae's original omitted CLI thread/query settings are explicitly bound to its qualified threads2/serial defaults without adding argv tokens. f47 requires explicit global threads and all three actual query options. Unknown engine/default/format/inventory or changed complete input misses reuse; final extraction actually runs. Dirty/missing/unverified early checks invoke the unchanged actual repair/session verifier and fresh final checks. The explicit experimental64MiB GDS hard cap is admitted before hashing; excess fails bounded without a checker-pass claim. Initial/final GDS and copied SPEF require owned paths, unique inodes and read-only effective bytes. Snapshot inventories and all counters reject bool/float equivalence. XML work has250k nodes/depth64,4096 categories,500k finding and64MiB byte caps; no geometry is interpreted. Mutable-source hash/copy work uses admitted length plus one EOF probe; bootstrap executes only already verified protocol bytes (≤1MiB).

Authority: opt-in postroute actual stdout additionally pins actual input_manifest and full result FileIDs; legacy stdout remains unchanged. Independent review binds that stdout to the actual producer command/exit/captures, requires the input render receipt, then binds selected extraction argv to the actual controller recipe (only output marker and explicitly frozen RC-path substitution), selected final round, actual raw report and SPEF. Ordered prior/final requests normalize workspace/out only. A standalone eligibility bit is never execution authority. Both raw foundry scopes/category sets/deck/profile/tool are checked; typed final bindings retain the actual early raw paths and require exact final-content equality. No synthetic native report, process or revision is generated. Native analysis skipped by the clean branch is explicitly unchecked; residual flags from actual fallback remain honest. All16 gate obligations still apply.

Current source-only validation:77 tests passed without skips under CPUs28–29/1GiB RAM/128MiB swap/60s; actual8.83s/RSS34,668KiB (c7-integration-tests11). These are metadata/mock-process/scheduler controls, not EDA or full-flow acceptance. The final source bootstrap input-symlink tightening is followed by the final frozen regression receipt. Prior failed control logs remain: fixture schema correction/test02; no independent tool result was changed; test09 was a Python fixture syntax error corrected before test10. Independent review identified shared-inode GDS and missing full postroute-output authority; both have rejection controls. Future isolated C7 actual fullflow, physical/timing/equivalence16gates, fallback behavior and end-to-end overhead remain unverified until a separately authorized run. One clean measured pre-antenna layout does not establish general false-negative safety. No model/margin/rule threshold, Rust ELF, published input or prior evidence changed.

Producer metadata correction (2026-10-31): the first real C7 flow failed at
`be_antenna_verify` because exact `{argv,cwd}` comparison rejected the producer's
genuine scheduled-command CPU/reservation fields. Keep the failed run/freeze
unchanged. The corrected consumer binds the actual product ownership record's
manifest FileID and pinned producer SHA to the config, independently validates
at most128steps/8groups/16384 earlier-dependency checks, and derives ordered CPU
slots, reservation and dependencies from that recipe. Both command metadata and
actual receipt must agree exactly; serial commands retain their two-key shape.
Unknown/missing/bool scheduler fields, changed arguments/cwd/source and altered
ownership fail. Producer source is hashed only: no dynamic import, compile,
entrypoint execution or bytecode writes are introduced. Additional state remains
<128MiB for the actual pinned120KiB recipe. Independently, rendered argument
construction admits at most64KiB per argument and1MiB total before appending
replacement chunks; repeated long placeholders cannot create an unbounded
transient. Receipt wall/start times must be finite nonnegative numbers, not bool.
No geometry/path materialization or original engine/physical-budget changes.
Real stdlib-only producer subprocess controls cover serial export, singleton
postroute/decision/verifier/extraction and parallel early/final table command
names, alongside adversarial metadata controls. These are protocol controls, not
EDA or a retroactive qualification of the failed flow.
