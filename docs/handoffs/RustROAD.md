# RustROAD handoff

## 2026-10-25 — final RTL-to-GDS integration (feat/rtl2gds-final2; read this first)

Report: [docs/rtl2gds.md](docs/rtl2gds.md); evidence: `docs/evidence/rtl2gds-final2.json`.
Lessons of every stream were in `.work/notes/*-lessons.md` (untracked); the load-bearing
ones are consolidated below. Older sections further down are history.

### State

- Branch `feat/rtl2gds-final2` (worktree `.work/wt/final2`): owner rules, RustSTA 0.4.3
  pin (`26bd6eb`, ruststa `472de8e8`, rusteda `c39dbde4`), GF180 floorplan headroom
  (utilization 0.35), closure-loop changes, docs and evidence. Evidence runs
  `.work/wt/final2/.work/runs/E-*` at `aafcbaa` (clean, binary `36a47edb`).
- Gate (8 designs x 2 PDKs, controls + determinism): pass on both PDKs: pipeline, dense,
  counter, uart_tx; also fifo GF180 and alu SKY130 (11/16 rows). Fail only
  `timing_accuracy` (MPW parity 1.28-1.86 ps, owner RustSTA): fifo, regfile, mult SKY130.
  Fail only `drv` (3/8/12 nets at ss, closure stream): alu, regfile, mult GF180.
  Setup/hold close on all 16 rows in both engines and both SPEFs; DRC/LVS/antenna/XOR/CEC/
  co-simulation/controls/determinism/binding pass on all 16.
- Periods: every SKY130 design and GF180 pipeline/dense 10 ns; GF180 counter 13, uart_tx 12,
  fifo 12, alu 18, regfile 12, mult 30 (SERV 14/42, rv_core 29/93, not through).
- Independent re-run (git archive build in `.work/indep-final2`): counter and fifo identical
  verdicts and artifacts (`scripts/compare_runs.py`).
- SERV GF180: router `BUDGET_EXCEEDED: routing grid node count` (8 M) on the 0.35 floorplan.

### Owner decisions in force (2026-10-24)

- **Clock period rule**, per design x PDK, before any physical step:
  `period = max(10 ns, ceil(1.25 x pre-layout ss critical-path delay, 1 ns))`.
  `scripts/derive_clock_period.py --write` (RustSTA on the gate's mapped netlist, ss
  Liberty, no SPEF, ideal clock; GF180 `ideal` wires, SKY130 the Liberty
  `default_wire_load` because RustSTA cannot force ideal there; delay = 10 ns - worst
  slack). Writes `fixtures/designs/<d>/<pdk>-cts.sdc` and `clock-period.json`; the
  manifest freezes both; the gate's global `clock_period` re-checks the arithmetic, the
  SDC and the tool/policy hashes. No post-route tuning of the period.
- **GF180 DRV dont_use**: families that cannot reach 1.0 ns at ss_125C_3v00 with a 0.2 ns
  input even at minimum load (one smallest synthesizable sink pin, 2.283 fF):
  aoi211 aoi221 aoi222 nor4 oai22 oai221 oai222 oai31 oai32 oai33.
  `scripts/derive_drv_dont_use.py` measures it from the pinned Liberty
  (`fixtures/flow/gf180-drv-dont-use.json`); `cell_policy.py` recomputes and adds it;
  `check_cell_policy.py` holds the owner's list and cross-checks. SDC
  `set_max_transition 1.0` unchanged. oai31 is marginal: 1.012 ns at 2.283 fF, 0.964 ns at
  the table's 1 fF index (below any real sink).

### After changing anything upstream (order matters)

1. cell policy / Liberty -> `derive_drv_dont_use.py`, `cell_policy.py generate
   --producer-receipt .work/tools/rustsys-c5ec23c.build.json`, `build_sizing_profile.py`.
2. synthesis inputs or RustSTA pin -> `derive_clock_period.py --work .work/x --write`.
3. `scale_manifests.py --rustroad BIN --rustsys BIN --design ...` (regenerates floorplans;
   `--refresh-manifest` only re-hashes), `--refresh-manifest --design pipeline`.
4. Commit, then run gates on the clean tree (manifest provenance needs HEAD).

### Pitfalls (process)

- Never edit a worktree while its gate runs are in flight (`artifact_binding` records git
  status incl. untracked files). Develop in a second worktree, fast-forward afterwards.
- `.work/sta-signoff/bin` is replaced by the RustSTA stream (06b1c9f -> 0.4.2 -> 0.4.3 in
  one day): copy the pair into `<worktree>/.work/tools/sta-gate`, check `SHA256SUMS` and
  `fixtures/flow/rtl2gds-tools.json`. A relative tool path is relative to the checkout.
- Rule 9: `systemd-run --user --scope -p MemoryMax=8G -p MemorySwapMax=2G
  --setenv=PATH=$PATH timeout ...`; `export PATH=/home/cgao/.cargo/bin:$PATH`
  (`/usr/bin/rustc` is too old); git identity via `git -c user.name/-c user.email`.
- Harness processes run under RLIMIT_FSIZE 64 MiB (SIGXFSZ, exit 153) unless a step has its
  documented limit; `PhysicalHarness.run` refuses a reused receipt label;
  `json_command` rewrites `--limit` for `report` (use `h.run`).
- Shell: `tail -n 3` (not `tail -3`); `pkill -f` patterns that match your own command line
  kill the shell (use `[r]uns/...`); no `rm -rf dir/*` after `cd`.
- `/tmp` is RAM tmpfs: runs go under `<worktree>/.work/runs`.
- Budgets must be counts, never wall time (the determinism rebuild must make the same
  decisions); quote measured numbers only.
- Independent check: `git archive <sha> | tar -x`, `git init && git add -A && git commit`
  reproduces the same tree hash, so the gate's manifest provenance holds; build with its own
  `CARGO_TARGET_DIR`.

### Pitfalls (black-box tools)

- Yosys 0.67 cannot read `module m import pkg::*; (` (alu, rv_core): ports from Verilator
  XML, exhaustive co-simulation as second CEC oracle (combinational, <= 22 inputs); Yosys
  aborts on SERV. Sequential equivalence needs `splitnets` (pairs `\q[0]`), `async2sync`,
  `memory`, then `equiv_induct`.
- Verilator 5.032 folds variables assigned in `initial` before the first delay: declaration
  initialisers, `-O0`, and the printed cycle/check counts must match.
- RustSTA: without SPEF GF180 is `ideal`, SKY130 `wireload_lumped`; `--require-wire-model`
  asserts it. `report --section drv --limit 0` lists every pin; `whatif` resizes are exact on
  SPEF (`insert_buffer` + SPEF is refused), evaluated sequentially (0.1-2 s per candidate).
  The netlist a report timed is in `<ws>/revisions/rev<NNNN>/meta.json`.
- RustSTA 0.4.3 (26bd6eb) accepts OpenRCX SPEF with resistor loops/disconnected components
  (0.4.2 and older: T0070). Remaining parity residual: SKY130 min-pulse-width required
  times on CTS clock pins at ss differ by 1.1-1.9 ps from OpenSTA (slew-dependent Liberty
  `min_pulse_width`), so `timing_accuracy` fails there. Owner RustSTA.
- OpenSTA 3.1.0: MPW only where a clock ends; strict parity flags `--pulse-groups last
  --pulse-pins clock-ends`. OpenRCX: strip only `*DATE` for determinism.
- Decks: sky130 `diode_2`/`conb_1` cannot pass the frozen LVS (device-less pins) — antenna
  repair is diode-free (layer jumpers), constants use rail-input ties. Netgen fails on any
  disconnected node (SERV's 35 unused inputs).

### Pitfalls (flow and physics)

- GF180 ss (125 C, 3.0 V) is the limiting corner: dffrnq clk->Q 2.6-3.5 ns, xor3 3-4 ns per
  stage, _1 gates at the 1.0 ns transition limit with no wire, each buffer on a critical path
  0.6-1.0 ns. The period rule gives 12-30 ns there.
- GF180 closure ECOs need area: from the 0.5 floorplan the DRV buffers, hold cells and
  resizes reached the 0.74 cap (a 0.76 netlist failed CTS packing). GF180 floorplans use
  0.35 now (`pdks.gf180.floorplan_recipe`); SKY130 stays 0.5.
- Closure loop: accept resizes only when `whatif` agrees; bulk DRV upsizing is one whatif
  call; seed placement from the previous layout; never move sinks with < 0.75 ns setup
  slack; keep the best complete layout. Adding a buffer level in front of ECO buffers made
  alu/mult worse (tried, reverted).
- Hold: most-downstream point first; delay cells are weak (<= 4 sinks per point); native
  hold can be 20-30 ps optimistic vs OpenRCX (target raised by the shortfall + 10 ps).
- Track router is chaotic near its budget: any netlist/placement/io-plan change is a
  re-qualification (SERV sky130 does not route with port buffers).
- Extraction: `--max-extract-candidates`, `--max-rc-elements` are CLI options; SERV needs
  them raised (track recipe values in hold_closure.py).

### Next steps

1. RustSTA: MPW required-time parity on SKY130 CTS clock pins at ss (slew-dependent
   `min_pulse_width`), then re-pin and re-run fifo/regfile/mult SKY130.
2. Closure: remaining GF180 DRV nets (largest-member drivers into ECO buffers; hold delay cells
   driving large ECO buffers; aoi22 at ss) - co-size driver and buffer, pick smaller buffers
   behind delay cells, more layouts only if each still improves.
3. SERV: a SERV-sized GF180 routing profile (grid nodes > 8 M; check route RSS against the
   2 GiB per-tool limit first), owner decision on unused input ports for LVS, a second CEC
   oracle (Yosys aborts).
4. Fold `.work/notes/*` of future streams into this file at the end of each stream.

## 2026-10-23 — post-route timing with RustSTA 0.3 (current validation)

- The strict 1 ps endpoint gate now passes with the nonlinear RC/effective-load/
  slew model: Sky130 0.5490975 ps, GF180 0.3017939 ps. Fresh routed geometry and
  actual nominal-rule extraction were used; no zero-R projection or tolerance
  change. Native scope must be spef_rc/nldm_ceff_ramp with propagated clocks.
- scripts/verify_routing.py requires that scope and emits timing_accuracy_status
  separately from functional_status and overall status. Pass current 0.3 native
  binaries explicitly; the frozen .work/routing-tools candidates remain older.
  New binaries used here are in the sibling timing tool's .work/rc-wave/target/release.
- Fresh final flow: .work/rc-timing/ready, 90 receipts, 14.38 s wall clock,
  peak host-process RSS 404008 KiB. CEC, 512 vectors (511 checked pipeline cycles),
  original GDS/DEF/PG/tap checks, all-net routing, repeatability, extraction,
  routed clock skew, full timing consistency and negative controls pass on both.
- Physical results unchanged: 19 signal nets and 46 terminals per PDK;
  4964 wires/101 vias (Sky130), 4982 wires/77 vias (GF180). Eight timing endpoints
  each, zero unconstrained. Clock skew remains under the original limits.
- Accuracy/functional verdicts pass, but total status remains PARTIAL. Sky130
  nominal hold is +0.000246889 ns (below the comparison gate); GF180 has four
  hold violations, worst -0.635534583 ns. Do not claim signoff. Physical hold
  optimization must re-route/re-extract and re-run timing before acceptance.
- The timing engine independently checks nine Ngspice transient circuits,
  actual dual-PDK cell resize/restore with fixed RC topology and CPU/CUDA f64
  identity. The flow's update remains a no-op consistency check. A fixed-topology
  resize does not prove a physically rerouted ECO.
- New report: docs/postroute-timing.html; reproducibility/scope in
  docs/postroute-timing.md; sanitized tool/input fingerprints and verdicts in
  docs/evidence/postroute-timing.json. Previous reports remain historical.
- This integration changes only the verification adapter; RustROAD remains
  version 0.5.0 with the same native binary. Complete GDS/DRC/LVS remain unverified.

## 2026-10-23 — v0.5.0 constrained CTS, routing and external RC extraction (current)

- `clock-tree --sdc FILE` accepts a strict bounded literal primary-clock SDC
  subset. Period/waveform, transition, uncertainty and design limits are retained
  with source identity; unsupported semantics fail. SDC max fanout caps topology
  branching. This is not timing-driven CTS optimization.
- `route --scope clock|all --config FILE` uses a separate bounded CPU lattice.
  Local pin-access search reserves wires/fixed vias before A* net routing.
  Native checks cover selected width/spacing, LEF pin/OBS and PG obstacles,
  integer manufacturing-grid geometry, connectivity and total work budgets.
  Same-net cut spacing is checked too. No unbounded path/cut enumeration.
- Schema 5 stores routed wire/via/access records in `routing.rbin`, checks the
  mirrored metadata and geometry on reload, and exports real DEF ROUTED/NEW
  paths. Failed routing leaves HEAD unchanged; repeated routing is byte-stable.
  Placement invalidates routes. Legacy schemas remain readable.
- `cell_spacing_sites` is an explicit optional tap-row packing policy. Routed
  profiles leave two sites between logical cells, then fill the gaps. It does
  not imply placement optimization. A zero-width public input is rejected
  before lattice modulus operations.
- The verified fixture is now `fixtures/flow/pipeline.v`: four registers,
  two pipeline stages, 19 routed signal nets including a 3-buffer/2-level clock
  tree. Both Sky130/GF180 pass independent CEC, 512-vector simulation (511
  pipeline outputs after initialization), OpenDB/KLayout route geometry,
  original-GDS tap coverage, PG and powered-netlist checks.
- Actual final geometries: Sky130 131 cells, 4964 wire segments/101 vias;
  GF180 134 cells, 4982 wire segments/77 vias. All 19 nets and 46 signal terminals
  are checked. The prior dense six-register fixture can exceed pin-access/
  comparison/expansion bounds; do not claim broad routing coverage or scaling.
- OpenRCX runs only as an external pinned executable. It reads actual routed
  DEF and original PDK nominal extraction rules and produces detailed SPEF.
  GF180's technology LEF explicitly warns against its parasitic placeholders;
  use `rules.openrcx.gf180mcuD.nom`, not those LEF values. No native calibrated
  RC field extractor or complete GDS/DRC/LVS signoff is implemented.
- RustSTA/RustEDA postroute candidate binaries were copied unchanged from
  `.work/postroute/target/x86_64-unknown-linux-musl/release` in the sibling repo
  into `.work/routing-tools/`. They support SPEF + propagated clocks. The old
  frozen release binaries do not. No sibling source or target was modified.
  Tool identities and origins are in `routing-sources.json`.
- Native scope: TT, f64 NLDM/lumped Elmore, grounded coupling factor 1, propagated
  primary clocks, CPPR off. Eight endpoints are constrained, zero uncovered.
  Full vs no-op update consistency passes. Deleted SPEF annotation fails rather
  than silently using ideal wire. This is not changed-ECO verification.
- **Overall result is PARTIAL, not a timing signoff pass.** OpenSTA max skew is
  43.53 ps (Sky130) / 74.71 ps (GF180), under configured 500/1000 ps limits.
  A 10000x branch-R perturbation fails the skew check. Strict RustSTA/OpenSTA
  endpoint parity differs by 3.1487 / 6.7928 ps, over the unchanged 1 ps gate.
  Native worst hold is -0.000906 / -0.640654 ns. Near-zero Sky130 hold changes
  sign in the reference; retain both results, do not hide this accuracy gap.
  No reference implementation source was inspected to explain or close it.
- 79 ordinary tests + 2 real-LEF tests pass, strict Clippy/release pass.
  The old dual-PDK tap/CTS suite also passes (117 receipts). New integration:
  0:13.46 wall clock, 404,064 KiB peak host-process RSS,
  90 receipts. Exit 3 means functional pass with pending parity/closure.
  This small test is not an industrial-scale performance claim.
- Raw final run: `.work/routing-final/`. Report:
  `docs/cts-routing-report.html`; reproduction/scope: `docs/cts-routing.md`.
  Results, compressed exact SPEFs, source pins and resources are in
  `docs/evidence/routing-*`. Final RustROAD SHA256:
  `07788f848ed292a470242fba5e551b9752f0871edd05e7373903128b477e77ca`.

### Routing/extraction lessons

- Reserve pin access before net routing. A same-XY stack is insufficient for
  dense cells: bounded local escape wires may be needed before climbing layers.
  Route and access searches need separate accounting within a total work cap.
- A default tool version flag does not establish SPEF/propagated-clock support.
  Functional import/run probes distinguished old native releases from the
  isolated postroute candidate. Pin the chosen binaries, never overwrite them.
- OpenDB in this container has Tcl bindings but no `-python` mode. Public
  `dbWirePathItr` / `dbWirePathShape` expose real paths and fixed-via geometry.
  Read only public interfaces and standard data, never reference internals.
- Report clock latency for all sinks, not only register-to-register skew pairs.
  Explicitly keep skew acceptance, subset DRC, timing parity and hold closure
  as different outcomes. Constraints/geometry verification is not signoff.
- Keep raw failed large-fixture probes; lowering accuracy requirements or
  discarding unannotated nets must not turn incomplete work into a passing flow.

## 2026-10-23 — v0.4.0 taps and bounded clock topology (historical)

- `PhysicalConfig.tap` selects actual WELLTAP masters and an explicit horizontal
  gap policy. Native reservation places taps at both row edges and between them,
  packs logic into free intervals, then fills remaining gaps. Tap, tie, clock
  and filler additions share a checked total budget. Dimensions remain i64 DBU.
- Sky130 uses `sky130_fd_sc_hd__tapvpwrvgnd_1`; GF180 uses
  `gf180mcu_fd_sc_mcu7t5v0__filltie`. Each eight-row fixture has 32 taps. Actual
  maximum edge-to-edge gaps are 14.72 / 17.36 µm, under the 20 µm profile limit.
- The independent KLayout checker uses original PDK GDS and layer maps, not
  native LEF labels as its oracle. It checks contact-to-correct-rail paths,
  all body components and 15 µm Manhattan active-region contact coverage.
  Per-tap nwell/substrate contact counts are 2/1 (Sky130) and 3/2 (GF180).
  Missing contact, no taps, end-only taps and wrong supply controls reject.
- The 15 µm policy is an engineering scope, not complete foundry latch-up signoff.
  Deep wells are explicitly unsupported; IO guard rings, full DRC/LVS, IR/EM,
  routed skew and extracted timing remain unchecked. Keep this distinction.
- `clock-tree --workspace W --base-rev REV` inserts configured non-inverting
  buffers, builds bounded equal-depth branches, re-links and re-places. Native
  checking is BFS with work/size/depth limits. No SDC clock discovery, skew
  optimization or signal routing is implemented. Status is
  `unrouted_topology_checked`. Changing a built tree requires fresh import.
- Both PDKs have 6 sinks, 6 buffers, 3 levels and fanout <=2. The independent
  OpenDB checker traces drivers backwards and rejects bypass/disconnection.
  Actual Liberty buffer functions are proved with Yosys (wrong-polarity control
  rejects); RTL CEC, 512-vector simulation per PDK and ideal-wire/ideal-clock
  RustSTA/OpenSTA comparisons pass. Repeated placement exports identical DEF.
- Post-clock Sky130: 127 total cells = 18 source + 2 ties + 6 buffers + 32 taps
  + 69 fillers; GF180: 123 = 22 + 2 + 6 + 32 + 61. Primary supply remains one
  component per net; PG pin counts are 444 / 428. GDS active-input cell coverage
  is 24 / 28. Both have 59 FS cells and 20 signal + 2 supply I/O terminals.
- TT timing retains 14 constrained endpoints and 2 constant-driven D pins
  without arrival. Maximum setup/hold differences remain about 6.03e-7 ns /
  8.70e-7 ns. Unsupported pulse-width/minimum-period diagnostics remain visible.
  Incremental testing remains a no-op, not changed-ECO validation.
- Schema 4 retains tap/clock metadata and tap records in `physical.rbin`;
  schemas 1–3 remain readable. Readback regenerates/checks tap geometry and the
  clock topology. Default Verilog omits physical-only taps/fillers and includes
  clock buffers; `--include-power` includes all physical cells and PG wiring.
- 74 ordinary Rust tests + 2 real-LEF tests pass; strict Clippy and release
  build pass. Final combined run: 0:16.56 wall clock, 403,524 KiB maximum
  host process RSS, 117 process receipts. This is a small functional baseline,
  not industrial-scale throughput evidence. Guard settings are in
  `docs/tap-budget.md` and all measured files in `docs/evidence/tap-*`.
- Final binary SHA256:
  `95f75e6816bc27ea1e92503bac648072d1e58fc4595c9682eddccc89a4a216a5`.
  Final raw run: `.work/tap-release-final/`; report: `docs/tap-report.html`;
  reproduction: `docs/tap-integration.md`. No RustSys/RustSTA source or frozen
  binary was modified. No reference implementation source was inspected.

### Lessons from tap and clock integration

- LEF well/rail pin geometry does not prove a physical contact exists. Use GDS
  contact, active, implant and metal polygons as a distinct oracle, then check
  PG connectivity from independently read DEF.
- A primary tap pin's OpenDB ITerm bbox can include both well and metal layers.
  Compare its bbox against all pin geometry; insert only conductor-layer
  rectangles into the metal connectivity graph.
- Sky130 GDS grid converts with factor 1; GF180 needs factor 2 to its LEF DBU.
  KLayout's `find_layer` can return None. Sky130 LYP names and GF180's empty-name,
  source-prefixed LYP entries need separate parsing cases; a missing mapping
  must never silently produce an empty-geometry pass.
- Check empty-row well components too. Logic-only coverage can miss uncontacted
  filler wells. Assert logic-input cells have active GDS geometry before doing
  distance checks. Keep GDS shape/work/process caps on all negative controls.
- Ideal-clock agreement does not validate clock insertion delay, skew, clock
  transition or RC. The reported tree is only a topology milestone. Preserve
  constant-driven no-arrival endpoints instead of hiding them as zero.
- Reject aliased top-level clock ports and unsupported root loads explicitly.
  Do not silently reuse LEF pin names as Boolean buffer-function evidence.
- HTML source/links/JS syntax are checked; browser rendering remains unverified.
  The earlier local-file URL policy block is still not a reason to bypass policy.

## 2026-10-23 — v0.3.0 physical I/O and primary supply (historical)

- `import-pdk --physical-config` binds explicit PDK power/bias, tie, filler,
  layer and fixed-via roles. Literal constants become uniquely named tie cells;
  alias-resolved load fanout is bounded (default 8). Existing native ties remain
  intact. Tie functions are independently proved from actual Liberty data.
- `place` supports N/FS, fills every full row, places boundary signal I/O, and
  builds primary horizontal rails, two vertical supply spines, fixed vias and
  supply terminals. FS requires master `SYMMETRY X`; positions stay lower-left.
- Body bias is logically connected; well/tap continuity, latch-up, foundry
  DRC/LVS, IR/EM and extracted timing remain unchecked. Do not equate primary
  metal connectivity with these separate signoff requirements.
- Default Verilog export omits generated physical-only fillers and retains
  inserted ties. `--include-power` includes all physical cells and PG wiring.
  DEF has PINS, NETS and routed SPECIALNETS. Schema 3 adds validated/mapped
  `physical.rbin`; reads regenerate physical state. Re-placement rebuilds the
  same filler/layout result and keeps logical ordering.
- 65 ordinary Rust tests plus 2 explicitly enabled real-LEF tests passed;
  strict Clippy and release build passed. Source/binary/resource receipts are
  in `docs/evidence/power-validation.json`.
- Final dual-PDK run: 6.90 s, 290,696 KiB maximum host process RSS, 62 receipts.
  Sky130: 97 cells = 18 source + 2 ties + 77 fillers, 388 PG pins, 46 FS cells.
  GF180: 44 = 22 source + 2 ties + 20 fillers, 176 PG pins, 19 FS cells.
  Both have 20 signal I/O + 2 supply terminals, eight continuous filled rows,
  one connected component per supply, and no detected primary metal short.
  Polarity, row-gap, spine-gap, short and interior-I/O controls all reject.
- CEC, tie-function proofs, 512-vector simulation per PDK, powered-Verilog vs
  OpenDB connectivity and post-tie STA pass. Ideal-wire STA retains 14 constrained
  endpoints and 2 constant-driven D pins without arrival. Only those independently
  identified constant endpoints are allowed; unexpected uncovered endpoints fail.
  Incremental STA remains a no-op update check, not changed-ECO verification.
- Final binary SHA256: `fd5f827406a2e90bb6822dfe0c7f2ebdf2d335f4c490fcfe6ad08ed8d53f5654`.
  Raw receipts: `.work/power-io-verified/`. Summaries and compact layouts:
  `docs/evidence/power-io-*`. Report: `docs/power-io-report.html`.
  No sibling RustSys/RustSTA source or frozen release binary was changed.

### Physical-preparation pitfalls

- Rail report success is `pass`, not `passed`; integration tests cover adapters.
  LEF SPACING can be absent with SPACINGTABLE; explicit profile spacing must not
  eagerly evaluate a missing fallback.
- DEF FIXED reads as OpenDB FIRM; FS reads as MX. Transform pins using
  `y + height - local_y`. GF180 rail pins have multiple rectangles: check unions.
- SPECIALNETS longitudinal caps default to flush. Emit explicit half-width end
  extensions to match square-cap geometry. A via changes the active layer on a
  continued path; emit separate singleton via paths and independent spine paths.
- Validate final tie fanout and bundle before commit. Commit also validates
  before replacing HEAD; failures cannot expose an unreadable revision. A
  configured snapshot without its tie report is invalid.
- Use maps for tie endpoints, net aliases and filler names; avoid quadratic
  scans. Added-cell, interval, geometry and work budgets remain hard caps.
- RustSys already emits mapped ties. The literal test input is a documented,
  CEC-proved verification projection; it is not presented as raw RustSys output.
- Keep well/tap and constant-path timing limitations explicit. Complex physical
  rules and signoff are not established by a connected drawing.

## 2026-10-23 — v0.2.0 real-PDK connectivity (historical)

- Implemented `import-pdk`, `export-verilog`, connected `export-def`, and
  `export-connectivity`. `rustroad-formats` adds exact integer-DBU LEF geometry
  and flat mapped-Verilog readers; warnings retain unsupported technology rules.
- Mapped vectors, escaped identifiers and assign aliases are linked with bounded
  bit expansion/union-find. Per-DB generational instance/master/net/pin arenas
  remain separate from the CSR representation. Constants needing tie cells,
  explicit supply wiring, bidirectional signal semantics, nonzero macro origins
  and multi-height cells are rejected explicitly in this milestone.
- Connected workspace schema 2 retains source hashes, used masters, logical AST
  and connectivity. `pins.rbin` is 16-byte u32 owner/net records;
  `net_offsets.rbin` contains u64 offsets with a final sentinel and
  `net_pins.rbin` contains u32 pin indices. Schema 1 remains readable.
- On read, bounded AST re-linking must equal stored ports/nets/pins; binary tables
  must match that model. A regression test changes only a logical pin connection
  to ensure Verilog and DEF cannot silently diverge. Public connectivity APIs
  have an instance cap as well as net/pin/shape budgets.
- 36 ordinary tests passed, plus both explicitly enabled real-LEF corpus tests;
  strict Clippy and release build passed. The corpus covers 437 Sky130 and 229
  GF180 macros plus 2,157 polygons from a GF180 standard LEF corpus. Final source
  hashes and build/RSS receipts are in `docs/evidence/pdk-validation.json`.
- The final RustSys -> RustROAD -> RustSTA campaign on true Ciel/open_pdks inputs
  passed with native/external CEC, 512-vector-per-PDK Verilator checks, OpenDB
  endpoint/direction/DBU/master-size/placement comparisons, and deliberate wrong
  pin connections in Verilog and DEF. Used LEF pin/OBS geometry is independently
  identical: Sky130 4 masters/30 pins/189 rectangles, GF180 5/36/169.
- Sky130: 16 cells, 26 nets, 74 signal endpoints, 138 stored pins; GF180: 20 cells,
  30 nets, 82 signal endpoints, 162 stored pins. Stored counts include unconnected
  LEF supply pins, whose geometry is preserved. Do not describe them as completed
  power networks. Top-level logical DEF pins are explicitly unplaced.
- Final 70-receipt campaign: 12.61 s complete wall time, 289,556 KiB maximum host
  process RSS. RustSTA/OpenSTA ideal-wire setup/hold differences <=6.029405e-7 ns
  (Sky130) /8.695039e-7 ns (GF180), tolerance 0.001 ns, zero unconstrained
  endpoints. Pulse-width/minimum-period warnings remain visible. Incremental
  checks are no-op updates only, not changed timing ECO validation.
- References: `docs/pdk-report.html`, `docs/pdk-import.md`,
  `docs/pdk-import-budget.md`, and `docs/evidence/pdk-import-results.json`.
  Full local receipts: `.work/pdk-import-geometry-final/`. This work did not alter
  or rebuild the sibling RustSys/RustSTA sources or frozen release targets.

### New lessons

- Sky130 source DBU is 1000/um and GF180 is 2000/um. Coordinates are decimal
  microns in LEF, not source-DBU integers. Reject nonintegral conversions instead
  of silently rounding. Validate selected site and each master separately.
- Verilog `alias` is a reserved keyword; use a valid or escaped identifier in
  fixtures. Default expression depth is 32 to fit nested serialized ASTs within
  the JSON recursion limit. Node/work budgets are separate from final pin count.
- Avoid O(ports * aliases): build the alias-to-net map once. Alias union uses size
  balancing/path compression and preserves a deterministic canonical name.
- OpenROAD's allocator and Go-based Podman can fail virtual reservations under a
  2 GiB AS cap even at low RSS. Keep the real container memory cap at 2 GiB/no swap,
  allow 8 GiB VAS for that reference process, and set GOMAXPROCS=2. The native
  Rust/timing tools keep their existing smaller limits. Preserve failed probes.
- I/O placement, power-grid construction, CTS, routing, extraction, GDS and DRC/LVS
  remain outstanding. The successful STA scope is ideal wire/clock, not extracted
  post-route timing. The earlier section below is historical v0.1 evidence.

## 2026-10-23 — initial physical workspace and verified subflows

- New clone of `git@github.com:lab-emi/RustROAD.git`, initial commit `625b5f4`;
  upstream initially contained only Apache-2.0 LICENSE.
- Parent `/mnt/data2/cgao/git/AGENTS.md` is authoritative. Read this file and
  `docs/memory-budget.md` before further work.
- Clean room: no OpenROAD/Yosys/ABC/OpenSTA implementation source, disassembly,
  decompiler output or algorithm porting. REA is restricted to black-box process
  capture and public documentation; it is not authorization to reverse engineer
  reference internals.
- Existing RustSys/RustSTA release binaries are frozen evidence. Do not rebuild
  into their targets or modify their working trees. Use Rust 1.97.1 through
  `/home/cgao/.cargo/bin/cargo`, not `/usr/bin/cargo`.
- The frozen RustSTA binary used for this baseline has no SPEF import. The sibling
  RustSTA source checkout already contains unrelated, uncommitted SPEF work on
  `feat/spef-postroute`; do not overwrite it or assume it has passed this baseline.
  Extracted timing and full RTL-to-GDS signoff remain unverified.
- `answer-me-with-html` is requested by parent preferences but is not an installed
  skill in this session; provide a local HTML engineering report directly.

### Implementation and validation

- Four crates: core (integer DBU, per-DB interner, typed arena, bounded fixed-order
  row packing), workspace (locks/revisions/immutable mmap tables), flow (bounded
  pinned-file assessment), CLI (`rustroad`). No GUI/Python dependency in core.
- Import format is intentionally a strict, small JSON geometry subset. No physical
  connectivity, LEF importer, global placer, CTS/router, extractor or GDS writer
  is implemented. `export-def` emits rows/components only and cannot prove signoff.
- 17 Rust tests pass, including concurrency, failed publication recovery and
  corruption detection. Clippy with warnings denied and release build pass.
- Independent DEF checker plus planted defect controls pass. OpenROAD accepts the
  24-cell geometry fixture and specifically rejects a forced overlap. Both use
  a hand-authored synthetic LEF, not fabrication PDKs.
- Dual-PDK XOR/register smoke passes RustSys/native CEC, independent Yosys CEC
  (including bad-design control), Verilator simulation (including bad-output
  control), RustSTA full/independent checks and same-scope OpenSTA differential.
  Original timed Liberty inputs are retained. A verification-only Sky130 copy
  removes wire-load metadata for the ideal-wire differential; this is not an RC
  model substitution for production. Incremental test is no-op only. Pulse-width
  and minimum-period unsupported diagnostics remain visible; no signoff claim.
- 50,000-cell/2,500-row end-to-end workload, including import, placement,
  publication, mmap/export and independent check: single observation 0.373 s
  (1 worker), 0.373 s (2 workers), same DEF SHA-256. Outer peak RSS 62,592 KiB.
  No OpenROAD speed comparison, GPU or macOS measurement was made.
- Evidence: `evidence/rea/`, `docs/evidence/`, and full local `.work/` receipts.
  Read `docs/report.html`, `docs/architecture.md`, `docs/flow-integration.md` and
  `docs/gui-architecture.md` for scope and next gates.

### Pitfalls caught during development

- Clap forbids a required global argument. Parse workspace as a global option,
  then explicitly reject missing workspace before any operation; CLI test covers
  the argument layout and mandatory mutation base revision.
- Seal invariant-bearing DB/snapshot fields. Publicly replacing the interner or
  metadata could invalidate a previously checked snapshot. Public DEF export
  still validates its complete input to avoid divide-by-zero and illegal output.
- Bound serialization while writing, not after building an arbitrarily large
  Vec. Check regular-file types before opening file inputs; flow identity reads
  additionally use nonblocking Unix opens against FIFO substitution races.
- Use buffered fixed-record and DEF output; one syscall per field would dominate
  a simple linear placer. This is included in the end-to-end benchmark.
- Keep `site.name` in the format and synchronize independent checker syntax with
  the declared identifier contract. Default `rustroad_site` is synthetic only.
- OpenROAD `-version` prints `unknown`. Pin OCI image and target binary SHA-256.
  REA's executable hash covers the podman wrapper, not its nested target. REA
  reports some /proc EACCES observations; retain that limitation in evidence.
- Workspace readers currently hold shared locks through snapshot lifetime.
  A GUI lease/GC protocol is future work; do not claim lock-free publication.
- Never rebuild the sibling benchmark targets. This task changed no RustSys or
  RustSTA source or version; future changes need their independent dual-PDK gates.

- HTML report syntax and local links were checked. Browser file: preview was
  blocked by URL policy; do not claim rendered visual QA or work around that block.
