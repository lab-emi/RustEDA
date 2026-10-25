# RustSTA hand-over notes: pitfalls and lessons

Local working notes (listed in `.git/info/exclude`; `cargo xtask layout` does not accept new
top-level files). Read before starting a task here.

## 2026-10-25: MPW residual on the final E-* layouts (feat/signoff-checks 455c43c, docs/test only)

- Why: the final RustROAD gate failed only timing_accuracy, sky130 ss native: MPW required at flop CLK
  pins 1.28 (fifo), 1.86 (regfile), 1.28 ps (mult) vs OpenSTA 3.1.0; endpoints <= 0.89 ps.
- Root cause (scripts + README in RustROAD .work/sta-signoff/scripts/sta5, evidence runs/sta5-evidence,
  private work in .work/mpw): the MPW lookup is identical (sky130 dfrbp/edfxtp CLK min_pulse_width
  tables are linear in related_pin_transition, slope 1.53 rise / 1.49 fall; both tools' required =
  table(own CLK slew)). The whole difference is the clock buffer's (clkbuf_2) driver transition: the
  reference's "Driver waveform slew" is a piecewise-smooth function of Rpi with STEPS of 0.35-1.2 ps
  between resistances 1 % apart (deterministic, independent of other nets; first step ~87-118 Ohm on
  clkbuf-like linear tables, nearly independent of the far cap). Between steps it equals our refit
  within 0.1 ps; all three failing nets sit just beyond a step (fifo x0.87, regfile x0.90, mult x0.99
  of the extracted Rpi). 24/24 MPW rows > 0.5 ps are explained this way (mpw_explain.py).
- Ngspice (sky130 ss clkbuf_2, the real clock-net pi, SPICE-characterized 1-D Liberty so
  characterization cancels): continuous in R; both models 2.4-5.8 ps low; beyond the step the
  reference moves AWAY from the circuit by the step. So: no engine change (circuit preferred). Spec
  STA-REQ-0728 paragraph + unit test transition_is_continuous_in_the_pi_resistance (driver.rs) +
  CHANGELOG [Unreleased]. Gates: tests 785/0/32 (0:43, 792 MB), clippy all-features, fmt, layout,
  coverage 595/0, shared 209, fixtures, verify sta 202/0/11 (1.0 GB), verify sta --gpu 176/0/11, Ngspice
  rc_transient 2/2, rc_driver_spice 1/1, rc_mesh_spice 1/1.
- Pins unchanged (472de8e8 / c39dbde4, SOURCE_COMMIT 26bd6eb): the 455c43c release build differs only
  in .note.gnu.build-id (all allocated sections identical after objcopy --strip-debug); a test-only
  edit still changes the binary hash because rustc embeds source-file hashes in debuginfo.
- Hypotheses rejected for the reference's waveform duration (all black box on synthetic linear tables,
  wave1/2/3 sweeps): refit at its Ceff with our or scaled Rd (no single factor), pure-ramp/one-step
  state, 2-var Newton iterates at its Ceff, exact/chord Newton k=2..5 (volt/time forms), converged
  charge at t50/t20/t80/D+tau, charge at the end of the pure table ramp (matches regime A to ~1 aF but
  not the steps), damped steps, fixed-point Ceff iteration, computation-order dependence. Do not
  spend more time replicating the steps without new observables.
- Pitfalls: verify --gpu needs LD_LIBRARY_PATH=/home/cgao/eda/tools/cuda-env/lib (NVRTC) or 9 CUDA
  tests fail with Unavailable. The Ngspice suites need FRESH (non-existent) workspace dirs
  (fs::create_dir). xtask verify --gpu rebuilds target/release/ruststa WITH cuda (70 MB): rebuild
  `cargo build --release -p ruststa-cli` before hashing. probe windows: ff steps lie at x0.79, use
  x0.40..1.25. rel=(C-conv)/(tot-conv) is a misleading unit at small R (tot-conv ~ 0); compare Ceff in aF.

## 2026-10-24: non-tree extracted RC networks, 0.4.3 (feat/signoff-checks 26bd6eb)

- Why: RustROAD closure run E6 could not time two reference-extracted SPEFs (0.4.2 refused the run):
  alu sky130 `a_i[0]` "disconnected RC network", regfile gf180 `waddr[1]` "RC mesh/parallel edge".
- Root causes (SPEF black box; tools/classify.py over all 281 reference SPEFs under RustROAD .work):
  - alu: resistor `*357:A *11:122 9.3` is the sink's via; node `*11:122` (cap 0) touches nothing
    else, so {g81/A, a_i[0]:122} is a component with no resistor path to the port. The extractor
    left the via top unconnected (the native extraction of the same net connects g81/A mid-net,
    0.83 ps wire delay). No coordinates in the SPEF, so no coincident-node merge is justified.
  - regfile: `*824:A3 *5:35 0.122666` and `*5:35 *824:A3 0.120687` are two PARALLEL resistors (a
    2-loop), not a real mesh. Only one other net in the whole corpus (S6-serv i_dbus_ack) has one;
    no true loops seen in real data.
- Behaviour: STA-REQ-0730 loops solved exactly (spanning tree + Woodbury + Cholesky on k x k,
  KCL residual 1e-9 of total C with <= 2 refinements; budget from --max-state-gib); STA-REQ-0731
  components without a resistor path join the driver at 0 Ohm (caps kept, sinks 0 wire delay,
  warning T0151 per corner naming net + members); STA-REQ-0732 replaces 0703 (legacy.md). Note
  T0152 counts loops. Tree nets keep the old code path (identity below). No shared file changed,
  so no RustSys sync was needed.
- Reference black box (tools/refprobe.py, OpenSTA 3.1.0): it does NOT solve loops - a parallel
  resistor or a loop through the driver leaves its pi/delays exactly at the tree values (edge
  dropped), a chord between branches RAISES delays (it keeps a different spanning tree). For a
  detached sink it gives 0 wire delay and the driver slew (same as ours) but drops the component's
  caps incl. the sink pin cap from the driver pi ("partially unannotated driver").
- Oracles: tests/rc_mesh_spice.rs (ignored; RUSTSTA_NGSPICE + RUSTSTA_RC_MESH_WORKSPACE): .op
  moment circuits (G tau = C via current sources, second copy with B-sources C*v(copy1)) agree
  3e-15; transient of the Thevenin driver into the full network vs pi+single pole 0.08 ps max.
  Transistor-level (tools/mesh_oracle.py, both PDKs, buf_1, tt): wire-delay error RustSTA <= 0.19 /
  0.12 ps on loops (0.26/0.16 on the tree), OpenSTA 4-29 ps. Absolute offsets (-14 ps sky130, +91 ps
  gf180) are characterization and cancel in differences.
- Results: E6 cases (8, private frozen copies in .work/spef-mesh/cases) all <= 0.87 ps; the two
  formerly refused ones 0.62/0.67 ps, per-sink arrival/slew parity 0.0000 ps (tools/net_parity.py);
  sta4 sweep 32 cases max 0.9677 ps (unchanged); identity vs pinned bdeec7e (tools/sta_ident.py, 44
  tree-only cases: 1672 commands, 1474 files, 798 rbin byte-identical); CPU vs CUDA 0/602340 (sta4)
  and 0/305106 (E6 incl. both non-tree cases); diff_signoff runs/final-mesh: 868 gates all equal to
  final-sta4, acceptance passed, 0 unexplained; real-case incremental==full also on g81 (detached
  sink) and g1 (parallel-R net) resizes (.work/spef-mesh/rc_cases_e6.json). Perf unchanged (+-2 %).
- Gates: tests 784/0/32 (0:47-1:49, 884 MB), clippy all-features, fmt, layout, coverage 595/0,
  shared 209 ok, fixtures clean, verify sta 202/0/11 (944 MB), verify sta --gpu 176/0/11 + the two
  extracted CUDA tests, deny ok, three Ngspice suites pass.
- Pins: ruststa 472de8e8...8fea, rusteda c39dbde4...d11a (cold build in .work/spef-mesh/target-cold
  reproduces); 0.4.2 pair moved to bin/round8.
- Pitfalls: OpenSTA 3.1.0 SEGFAULTS on `get_property <pin> arrival_max_rise` (use report_checks
  -rise_through/-fall_through and parse the pin line). `report_checks -through P -rise_through P`
  found no paths; use -to <port> -rise_to. Ngspice lowercases vector names in `print` output.
  `(cd a && x) & (cd b && y)` inside an `&&` chain backgrounds the whole chain - write it on
  separate lines. sta_ident.py normalized only 0.4.[01]; extend the version regex on every bump.
  The detached merge turned the resistors inside a joined component into "shorted" ones - count
  shorted only for resistors outside joined components. analysis.rs is at the 1200-line layout
  limit (1197 now).

## 2026-10-24: stream sta4, one-step Ceff and driver-waveform wires, 0.4.2 (feat/signoff-checks bdeec7e)

- Why: the unified RustROAD gate (E3) failed timing_accuracy 0.85-5.2 ps on the track designs
  (driver delays into long RC nets a few tenths of a ps per stage lower than the reference).
- Black box (scripts + README in RustROAD .work/sta-signoff/scripts/sta4, evidence runs/sta4-evidence):
  - The reference Ceff is not the converged end-of-ramp charge solution. It is, to 0.06 ps up to
    100 Ohm and 0.3 ps up to 500 Ohm, ONE Newton step of the (t0, dt, Ceff) system (voltage-form
    trip residuals, charge at ramp end) from the pure-ramp start with the exact Jacobian. Clues:
    rel = (ref-conv)/(tot-conv) is R-independent at small R (first order) and jumps at isolated R
    (iteration-count changes); "time-form" residuals or a fixed-point map do not fit; the step's
    table derivative matters at high R (frozen Jacobian is worse).
  - The reference drives every sink of a resistive driver with the driver's pi-root waveform
    through one Elmore pole (0.03 ps with its waveform), not a ramp of the driver slew.
  - Its driver waveform duration is not the exact refit at Ceff (unresolved, <= 1.5 % slew);
    above ~1 kOhm its Ceff lies between one step and the charge solution (unresolved, <= 8 ps at
    3 kOhm on the grid).
- Physics check first: a transistor-level oracle (Ngspice + PDK models, the cell characterized by
  the same simulator into 1-D tables so characterization error cancels) showed the converged
  charge solution over-shields (driver delay low up to 45 ps); one step + driver-waveform wires
  improve the sink arrival error 6.5 -> 0.64 ps mean on RC trees. Parity and circuit agree, so
  the change is not parity-fitting. Wire DELAY alone looked worse vs SPICE (1.23 -> 1.66 ps) -
  compare driver+wire arrivals, the driver-pin 50 % point moves with the model.
- Implementation: shared rc.rs additive `gate_one_step`, `wire_from_driver` (synced to RustSys
  d36d922, identical blobs, RustSys gates 841/0/19, clippy, fmt, layout, coverage 620/0);
  driver.rs `pi_drive` returns (Rd, duration); the RC pass keeps per-node `drives` (96 B/node)
  set by the arc that set the merged slew; launches via SeededStarts.launch_drives ->
  LoweredCorner.start_drives; statics.rs threads drives too. STA-REQ-0728 replaces 0726,
  0729 replaces 0722 (texts in legacy.md); coverage 592/0.
- Results: E3 parity sweep (32 cases x tt/ss/ff, native + OpenRCX) max 5.21 -> 0.97 ps (54 -> 0
  over 1 ps); worst left: regfile sky130 ss r41/RESET_B (dlygate4sd3 waveform slew quirk).
  diff_signoff 868 gates, 0 unexplained, 19 declared gates now pass, none newly failing.
  CPU vs CUDA f64 0/602340 values differ (32 cases); GPU tests 2+2; incremental==full on two
  routed swaps (dual_pdk_extracted_incremental_matches_full with RUSTSTA_RC_CASES); both Ngspice
  suites pass. ruststa run 2.3-3.1x faster (one step instead of 64 bisections), RSS unchanged.
- Pins: ruststa ebb418b8...db48e, rusteda 9ed84d6c...4dc0 (cold build reproduces); 0.4.1 pair in
  bin/round7.
- Pitfalls: `pkill -f` kills your own shell again (killed by PID instead). The sky130 combined
  SPICE lib needs `.spiceinit` with `set ngbehavior=hsa` + `skywaterpdk` (10 s load); the same
  hsa init makes gf180 cells hang (timestep too small) - gf180 runs without it in <1 s; strict
  reltol=1e-6 aborts some large-load decks, retry relaxed. parity.py crashed on tie pins with
  None slews (dense). The reference prints Rpi in the Liberty resistance unit (kOhm for the
  synthetic libs, Ohm for gf180).

## 2026-10-24: merge of the shared convergence (0.4.1) into sta3 (feat/signoff-checks 06b1c9f)

- Merge of feat/shared-convergence (25b0201, 6af9936) onto 268ad9a. Only conflict:
  docs/verification/requirements.md (generated). Take either side, then `cargo xtask coverage`
  regenerates it (590 = 588 + STA-REQ-0726/0727); never hand-edit the count.
- verification/shared.sha256 merges line by line without conflict (convergence did not touch
  rc.rs); `cargo xtask shared update` left it unchanged. The synthesis tool got sta3's rc.rs
  (RustSys 7b53aaf): committed trees have identical blobs for all 209 shared paths + manifest
  (sha256 bc3bee3d...), `shared diff` clean both ways. Whole-manifest grep (case-sensitive for
  third-party program and repository names) is clean; lowercase `ruststa`/`rustsys` in shared
  tests are tool ids in envelopes, pre-existing and accepted.
- Gates (worktree target, systemd 8G/2G, 4 jobs/threads): all-feature tests 778/0/31 (2:02,
  852 MB), all-feature clippy -D warnings, fmt, layout, shared, coverage 590/0, fixtures check,
  verify sta 198/0/11 (1:23, 935 MB).
- Identity vs sta3 pinned binaries (private copy .work/merge-sta3-shared/pinned-ebc7037):
  sta_ident.py routed fixtures 228/228 commands, 198/198 files, 0 diffs. diff_signoff.py
  (runs/final-merge-shared): 868 gates, acceptance passed, status deviation, 0 unexplained, and
  every gate value equals runs/final-sta3 (868/868). Its exit code 4 means "deviation", not a
  crash.
- Pinned: bin/ruststa 3e80fde6...f14b, bin/rusteda 13050846...9864 (cold build in a separate
  target reproduces both); previous (sta3) pair in bin/round6. The rusteda hash changes vs
  c8f71b63 because 0.4.1 adds BLIF/AIGER readers etc., not because of behaviour on our tables.
- PITFALL (RustSys identity): ident_sys.py's "old" flows need the c5ec23c rusteda. Pairing the
  pinned c5ec23c rustsys with a 0.4.1-era rusteda makes its synth step exit 1 with empty stderr
  (store too new): 4 flow cases are neither identical nor in `differ` (279/283, exit 1). Rebuild
  c5ec23c's rusteda from `git archive` (its hash depends on the build path).

## 2026-10-24: shared crates converged with the synthesis tool, version 0.4.1 (branch feat/shared-convergence, worktree .work/wt/shared)

- Both tool repos vendor the same 209 shared files (manifest a6c423a6...; `cargo xtask shared diff`
  clean both ways). Gained from the synthesis tool: BLIF/AIGER readers and `rusteda import --rtl`
  of .blif/.aag/.aig, letters B and G (`ABCDGLSTVW`), streaming frozen `rusteda export`, frozen
  writer, parallel Verilog writer, lexer fix for `:` + comment, its fast flat netlist reader and a
  64 MiB budget on the AST fallback, xtask shared DOCS with blif/aiger.
- RCLI-REQ-0021 belonged to the published BLIF/AIGER import; the show-DRV requirement is now
  RCLI-REQ-0023 (query.rs, tests/show_drv.rs; 0.4.0 changelog keeps the old number, 0.4.1 explains).
- rc_transient.rs env vars renamed (no repo/third-party names in shared files):
  RUSTEDA_SPICE_SIMULATOR, RUSTEDA_RC_ORACLE_WORKSPACE, RUSTEDA_RC_WIRE_WORKSPACE.
- Identity vs pinned 78ea7cc binaries: 16-netlist corpus (rev1/rev2 netlist, constraints, sta,
  limits rbin) 16/16 and `rusteda export` 16/16 identical; a private copy of the RustROAD signoff
  harness (H rewritten; never run it in place: const_suite regenerates probe-r3/design) gives the
  same 710 gate results, 1922/1922 receipts and 186/186 workspace files identical (normalized
  version/time/argv0). Evidence: RustROAD/.work/shared-w19.
- Gates: all-feature workspace tests 772 passed/0 failed (first run), clippy, fmt, layout,
  coverage 587/0, fixtures check, verify all 214/0, verify pdk --profile pr --require, deny, reuse,
  strict docs. Nav now lists rusteda-cli/spef.md, aiger, blif; specs/sta/limits.md still outside nav.
- Pitfall: a version bump changes tool_version in every envelope/history line; compare with
  normalization, the rbin tables do not carry it.
- Fix round 1 (commit 6af9936, manifest 6d8e288b...): a third-party program name in the shared
  test rusteda-cli/tests/import_netlist.rs (from our 0.4.0) failed verification; renamed
  neutrally, and two shared comments naming this repository now say "the timing tool". Grep the
  whole shared set (verification/shared.sha256) for program and repository names before a sync.
- The AST-fallback budget is `rusteda import --max-netlist-ast-mib` (default 64, RCLI-REQ-0024).
  0.4.0 read streaming-declined netlists of any size; >64 MiB ones are now V0300 unless the budget
  is raised (full reader ~42x file size in RSS). The 0.4.1 changelog says so.
- PITFALL: the pinned 78ea7cc binaries moved to RustROAD/.work/sta-signoff/bin/round5 at 08:07
  (sta3 put its 09bf9f85 ruststa in bin/). Compare against a private sha-checked copy
  (RustROAD/.work/shared-w19/pinned-78ea7cc), never against bin/. Round-1 identity: routed
  fixtures 228/228 commands, 198/198 files (108 rbin) identical (r1/sta_ident.py); negative
  control vs bin/ (09bf9f85) shows 197 command and 60 file differences, so the harness is live.

## 2026-10-24: stream sta3, RC driver parity and V4-F1 (feat/signoff-checks ebc7037 + 268ad9a docs)

- Why: GF180 ss + max on reference-extracted SPEF missed the 1 ps endpoint gate (6.8 ps setup,
  5.3 ps hold; rr_clkbuf_2/Z rise 0.5279 vs 0.5424 ns); V2-F6 (sky130 gater 0.85 ps); V4-F1 (a
  flop/latch Q under case analysis still launched its arrival and took the clock's DRV limit).
- Black box (all from report_dcalc output: "Pi model C2=near Rpi C1=far, Ceff", the two "Table
  value" lines, "Driver waveform slew"; scripts in RustROAD .work/sta-signoff/scripts/sta3/):
  - lumped switch: Ceff == C1+C2 and slew == table iff Rpi < 1e-3 * Rd, Rd = ln(2) * forward
    delay slope over 1 fF at Ctotal (k = 6.9315e-4 = ln2/1000 on linear synthetic tables, both
    PDKs; piecewise tables locate the 1 fF forward step exactly). Per arc: the GF180 case was
    lumped for rise (16.875 < 17.77 Ohm) and not for fall (15.97 Ohm threshold).
  - the reference model's Rd is the same forward value at Ctotal (not at Ceff): that alone
    explains V2-F6 (dlclkp_1 GCLK 0.101374 vs ref 0.101370) and the sky130 dfxtp_1 Q fall rows.
  - RustSTA's model (two-point ramp fit + end-of-ramp charge) reproduces the reference driver
    waveform to 0.001 ps at moderate R, i.e. it is the same model; remaining differences only
    beyond ~100 Ohm with far-dominant pi loads: the reference Ceff is above the converged charge
    solution and its waveform slew jumps between neighbouring R (non-smooth; 0.9 ps at 92.4 Ohm on
    copied dfxtp_1 tables, 1.5/10 ps at 225/1948 Ohm on linear tables). Declared, gated.
- Implementation: engine/delay/driver.rs `pi_response` (both RC call sites: delay/gate.rs and
  prop/lower/rc.rs arc_value). Shared rc.rs only got the additive `gate_detail_with_resistance`
  (old `gate_detail` bit-identical; manifest updated). STA-REQ-0726 replaces 0721 (legacy.md),
  new 0727. V4-F1: prop/starts.rs step 5 now skips disabled launches (case/constant on Q, false
  when, set_disable_timing); logic-disabled launches keep their transition via the slew-only arc.
  set_disable_timing -from CK -to Q also launched before (same bug): now untimed (T0145), the
  reference gives slew 0 (declared class).
- Evidence: Ngspice suite unchanged and passing (rc_transient 2/2) + new engine case
  tests/rc_driver_spice.rs (forward-1fF circuits within 1e-6 ns/1e-8 pF; lumped regime: pi vs
  lumped total within 1e-4 of the transition, fitted slew 1.5 % below the table). Parity: b0 and
  hold-final workspaces (10 cases x tt/ss/ff, native + OpenRCX) max 0.32 ps (was 6.8 ps);
  verifier's vcq/vprobe6/vrv all equal. Harness runs/final-sta3: 868 gates, acceptance passed,
  0 unexplained. CPU vs CUDA f64 0/12570 values differ; GPU equality tests 2/2.
- Gates: tests 737/0/31 (0:53, 919 MB), clippy all-features -D warnings (incremental + cold),
  fmt, layout, fixtures, coverage 549/0, shared 195, verify sta 198/0/11 (0:57, 982 MB), deny ok.
  Binaries: ruststa 09bf9f85...c0c7 (cold build reproduces), rusteda c8f71b63...9baf unchanged;
  previous in bin/round5.
- Pitfalls: `pkill -f <pattern>` killed my own shell (pattern in the command line) - never use it.
  A probe module named bisect.py shadowed the stdlib (`bisect.bisect_right` missing): rename
  helper modules. OpenSTA's "C2" is the NEAR cap and "C1" the FAR cap in report_dcalc. Absolute
  paths for Liberty files in OpenSTA tcl when cwd differs. RustSTA `pins` gives late/early (max/min
  over rise/fall), so compare with max/min of the reference's per-arc values.
- Open: high-R residual (> 100 Ohm, far-dominant pi): up to 3.1 % of the transition on the grid;
  needs either the reference's iteration (not reproducible black box without trading the converged
  solution) or an owner decision. The shared rc.rs addition must be ported to RustSys when the
  shared crates converge.

## 2026-10-23: signoff checks 0.4.0 (branch feat/signoff-checks, worktree .work/wt/signoff)

### Stream sta2 fix round 2: held resets, clock gates, constant pins, flop-gated and stopped clocks

- Why: the verifier's own probes (verify-2/sta2r2: vprobe2/3, vrv0) found five undeclared
  differences from the reference on tie/constant structures: F1 a flop/latch with clear or preset
  held active was a constant in RustSTA (cap/fanout unchecked, cone untimed; rv_core with
  `set_case_analysis 0 [get_ports rst_ni]` + the flow clock limit missed 20 gf180 max_transition
  violators, slacks up to 1 ns / 390 ps optimistic); F2 an ICG with a tied-low enable stayed live;
  F3 the loads of a case-analysis port with an input delay took the clock's limit; F4 a flop output
  into a clock gate/mux select was missing from the propagated clock transitions (1.5-7.4 ps,
  optimistic hold); F5 with ideal clocks, pins behind a constant-stopped clock gate were checked.
- Black box (RustROAD .work/sta-signoff/scripts/exp_seq4.py, exp_icg_const.py; probe-r5/):
  - no constant ever passes a sequential output, held clear/preset included (STA-REQ-0162);
  - clock gate output = AND of the pins flagged `clock_gate_clock_pin`/`clock_gate_enable_pin`
    on a pin flagged `clock_gate_out_pin`; Liberty copies proved the cell attribute, the
    state_function, the statetable and the test pin irrelevant, and the value is 0 also for the
    negative-edge gf180 icgtn (an AND gate after it stays constant);
  - a constant pin passes no arrival in or out: wires of constant nets, wires into case-analysis
    load pins, cell arcs into case-analysis output pins (STA-REQ-0164); the port itself keeps its
    input-delay arrival (its own limit is the clock's);
  - `when`-false checks (gf180 dffrnq `when : "RN"`) are not timed: RustSTA timed every graph check
    that arrivals reached; prop/checks.rs now skips `graph.is_check_disabled`, and the endpoint is
    `disabled` (endpoints.rs collect_disabled_nodes);
  - the clear/preset arc RN -> Q contributes no transition (it is not a graph arc; the
    reference's early Q transition equals its late one);
  - ideal clocks: the ideal network for DRV is the clock's reach through enabled and
    logic-disabled arcs (ICG with tied enable, AND with a tie, clock port under case analysis)
    and a flop clocked there launches its Q transition at the ideal clock transition, not at the
    propagated CLK slew (ric0/Q == ric1/Q in the reference) (STA-REQ-0822).
- Implementation: constants/propagate.rs (output_func, clock_gate_func), constants.rs (held()
  src/dst rule incl. net arcs), sequential.rs ideal_launches, TimingGraph::{set_ideal_launches,
  is_ideal_launch} (launch_carries_transition false for them; start rows seed them),
  IdealClockModel::logic_network_clocks, clocks/propagated.rs: repeated clock propagation with the
  launch transitions (starts::launch_transitions / launch_value / ideal_launch_transition) until
  the seeds are unchanged, bounded by 1 + feeding launches and the work budget (STA-REQ-0714);
  old values are dropped before each repetition. Checker: clock-gate rule, cites 0164.
- Requirement changes: 0117 replaces 0116, 0162 replaces 0151, 0163 replaces 0161, 0822 replaces
  0818 (texts in legacy.md); new 0164, 0714. held_sequential.rs is the hand-derived test file.
- Pitfall: the reference prints limits in single precision (1.506103038788 vs 1.506103) and
  max_fanout limits as integers: limit equality needs a relative tolerance (seq_suite.py).
- Pitfall: tests/constants.rs is at the 1200-line layout limit; compact helpers (mux_reasons,
  wire) instead of growing it.
- Harness: seq_suite.py (called by diff_signoff.py; run_seq.py alone): exp_seq4 design ideal + SPEF,
  variants tight/case/realistic/ideal/ideal_caseclk/caseclk/planted, rv_core with the reset under
  case analysis, loop/self-clocked declared rows (T0145). runs/final-sta2r2.

### Stream sta2 fix round 1: transitions ignore logic values, latches with constant enables (commit ff8453b)

- Why: the verifier showed (V1-F1) live pins behind gates with constant side inputs had
  optimistic transitions (3-84 ps on rv_core with case analysis, violator sets flipped at
  round-number limits) and (V1-F2) a latch with a tied active enable dropped out of the clock
  domain. Black box (RustROAD .work/sta-signoff/scripts/exp_const3.py): the reference's pin
  transitions do not depend on logic values (constant gate == its twin with the constant input on
  a port of the same transition, late and early; case analysis on/off identical); arrivals do.
- STA-REQ-0116: arcs disabled only by logic values (constant, case_analysis, false_when), the
  transitions a non-unate refinement removes, and clock-to-output arcs that a constant disables
  or that no clock reaches are CSR arcs with an EMPTY tag map (`LoweredCorner::slew_only_map`):
  the kernel merges their slew, no arrival passes, the backward passes ignore them (store
  builder's untagged required pass had to skip them explicitly). No change to rusteda-compute.
  Tie outputs are untagged starts with slew 0 (`TimingGraph::tie_outputs`); constant ports keep
  their input transition. Levels: `Levels::compute_carrying` orders enabled + logic-disabled arcs
  + carrying launches (incoming()/outgoing() stay enabled-only); a loop drops launches, then the
  logic-disabled arcs (T0149). Unclocked launches: BFS from clock sources in
  ConstantPropagation::apply (same reach as IdealClockModel), then relevelize.
- The propagated clock model seeds the other input ports and tie cells (untagged) per clock, so a
  clock mux's transitions merge its data input like the data propagation. Not covered: a launched
  data signal (flop output) entering a clock-network gate.
- STA-REQ-0159: a declared unate arc contradicted by the residual function is disabled (xor2 next
  to a tie timed both senses: 28-756 ps pessimistic). The checker also had a bug here: it stored
  the residual sense on every arc (a positive arc became negative); now only non-unate arcs.
- STA-REQ-0161: latch enable held active -> LatchData arc made Comb (`make_latch_transparent`)
  plus late-only identity tag-map records (open early = 1e30): max arrivals pass, no hold path;
  held inactive -> disabled (opaque), slew only. STA-REQ-0230 + T0150: the reference derives the
  D->Q sense from data_in alone (Liberty-copy experiment); gf180 latq_1 (data_in "(!D)", Q=IQN1)
  is timed inverted there. RustSTA keeps the library sense (declared; this is the pre-existing
  GF180 latch parity difference).
- STA-REQ-0229 (replaces 0211): setup/recovery/latch-closing-setup use the EARLY capture clock
  transition, hold/removal the LATE one (confirmed black box). Visible once several arcs merge
  into a clock pin (clock mux with tied select: 1.3-2 ps before the fix). store steps.rs follows.
- Gates: workspace tests 726/0/30 (2:06, 1.27 GB); clippy -D warnings clean (incremental and cold
  in a separate target); fmt; layout (files split: prop/lower/{slew_only,start_builder}.rs,
  graph/constants/sequential.rs, ruststa-check rev/eval/transitions.rs); fixtures; coverage 541/0;
  shared 195; `xtask verify sta` 190/0/11 (1:49, 981 MB); deny ok.
- Binaries (cold build reproduces): ruststa e92c5cc5d4dee44e626d7e06f9b1b2151816942550b2280ad077a721bd829bca,
  rusteda c8f71b63...9baf (unchanged); previous in bin/round3.
- Harness runs/final-sta2r1: 460 gates, acceptance passed, status deviation, 0 unexplained; new
  asym suite (ideal strict + SPEF explained). rv_core (verifier's case analysis on imem_rdata_i[0..1],
  rst_ni, planted 2.0/1.25, -data_path domain; both PDKs, tt/ss/ff): violator sets equal
  everywhere, no transition value > 1 ps (max 0.05 ps), endpoints max 0.017 ps. Verifier's own
  vprobe: ideal-wire value differences 0.00 ps; only remaining difference is the declared
  set_disable_timing cell output (untimed, T0145; the reference reports slew 0).
- Pitfall: python file surgery that cuts code before writing the new module lost the code once
  (directory missing); write the new file first.

### Stream sta2: DRV parity on constants, clock limit scope, unconnected pins (commit 697aad4)

- Binaries (cold rebuild into a separate target reproduces the incremental build): ruststa
  9535131c...d8ed, rusteda c8f71b63...baf (unchanged), copied to the physical-design tool's
  .work/sta-signoff/bin (round-2 binaries in bin/round2). Gates: all-feature workspace tests 722
  passed / 0 failed / 30 ignored (2:16, 1.34 GB); all-feature clippy 0 warnings (also in a
  separate target); `xtask verify sta` 189 passed (1:18, 987 MB); coverage 536 requirements / 0
  gaps; shared 195 files; layout, fixtures, fmt, cargo-deny ok.
- Constant nets (STA-REQ-0809): the reference (black box) does not check cap/fanout on drivers of
  constant nets (tie outputs, buffers of ties, gates with a controlling constant, literals, case
  analysis) and does check the transition of pins that the propagation leaves without one, with
  a delay-calculated slew. RustSTA: `limits::statics::static_transitions` (O(V+E), topological,
  through arcs that only logic values disable plus clock-to-output arcs; tie outputs 0; literal
  cones 0) and `arc_value` in prop/lower/rc.rs, the single RC arc evaluation shared with the
  waveform preparation. Anything still without a transition is "untimed": counted, T0145,
  unevaluated (status partial). Never a silent skip.
- Ideal clock network (STA-REQ-0818): left out of all design rules, counted (T0146), limited
  checks unevaluated, so drv_status is never `met` with an ideal clock.
- Clock limit scope (STA-REQ-0808): the reference applies `-clock_path` on a clock nowhere and the
  `-data_path` (or option-less) limit to the whole domain including the clock network. Default
  `--clock-limits paths` keeps the constraint definitions (never looser); `domain` reproduces the
  reference (T0144 for each -clock_path-alone command). Both build the same slots; only the read
  differs, and the pins where the two differ are counted under either scope
  (`completeness.drv_clock_scope_pins`, T0147). The flow SDC (option-less clock limit) gives the
  same result under both.
- Unconnected pins (STA-REQ-0819): the reference lists unconnected instance outputs (zero-load
  table slew; they violate tight planted limits) and ff/latch state pins (slew 0); RustSTA checks
  netlist pins only and counts unconnected outputs (`drv_unconnected_output_pins`, T0148 with
  five names). The netlist has no node for an unconnected pin, so checking them would need a
  row identity without a NodeId; declared instead, gated in the harness.
- V2-F6 investigated, not fixed: on the sky130 probe the gater output slew is 0.102215 ns in
  RustSTA vs 0.101370 ns in the reference; the reference's own NLDM table value at its effective
  capacitance is 0.104373 ns and its reported pin slew is its driver-waveform slew. The 0.42 ps
  input-slew difference at g0/CLK contributes only ~0.007 ps. It is a driver-model difference of
  the RC waveform model (0.3.0), not a defect with a clear fix; endpoint parity stays < 1 ps.
- Reference capacitance values carry single-precision sums: on rv_core the 1024-load clock net
  differs by 1.8e-5 pF (sky130) / 3.3e-5 pF (gf180) relative 1e-5; violator sets are equal.
- Envelope key-order contract test and LIMITS record-size test were updated again (CORNERS 80
  bytes). Any new completeness key must be added to cli_contract.rs.
- Harness: const_suite.py (constant probe: ties, literal, case analysis, unclocked/tied flops,
  realistic and clock_* variants; unconnected-pin probe with Liberty-parsed expectations).

### Fix round 2 (commit d1f61e6, after the second independent verification failed)

- Binaries (cold rebuild into a separate target reproduces them): ruststa ac76f0f3...a838,
  rusteda c8f71b63...baf, copied to the physical-design tool's .work/sta-signoff/bin. Gates:
  all-feature workspace tests 717 passed / 0 failed / 30 ignored (1:38, 974 MB); fresh
  all-feature clippy 0 warnings (31.2 s, 522 MB); `xtask verify sta` 189 passed (1:38, 935 MB);
  coverage 532 requirements / 0 gaps; shared 195 files; layout, fixtures, fmt, cargo-deny ok.
- Pin pulse attributes are now `Pin` fields (LIBERTY-REQ-0049) carried by LIBERTY v4 rlib
  (WS-REQ-0117); the synthetic-arc approach of round 1 changed what the frozen legacy engine saw
  and forced a frozen test change. With fields, `sta_view_model.rs` is restored byte for byte.
  A v3 .rlib is "older" and recompiled transparently; a v3-only reader rejects v4.
- Pulse rules (STA-REQ-0816/0817): `--pulse-groups worst|last`, `--pulse-pins all|clock-ends`.
  `last` = per sense the last listed group stating it, else the attribute; period reads
  rise_constraint only. `clock-ends` skips pins whose clock passes on through a cell arc to an
  output pin that the same clock reaches (`passes_clock_on`), counted as pass-through (T0141,
  status partial). These reproduce the reference's observed choices (black-box experiments in
  the physical-design tool's scripts/exp_*.py). Defaults unchanged.
- Store backward pass: never lower a corner twice by different code. `prop::latch::lower_corner`
  (lower + pin latch G->Q slews + set_wire_model) is the only lowering of the analysis and the
  store; the missing `pin_latch_gq_slews` made update (re-lowered) and run (kept) differ by
  ~1e-4 ns behind latches with SPEF. Test: tests/signoff_latch.rs.
- inspect of an endpoint: required times come from the worst path (`query_path_to`), because the
  node required time of a latch D pin also covers paths through the transparent latch. The
  `pins` section keeps node semantics (node slack + node required).
- Status: a design rule with checked rows and unlimited pins is `partial` (status.rs Totals).
- Synthetic SPEF tests with Wt15 need `set_clock_transition`: a zero clock slew extrapolates the
  latch/flop output transition below zero and RC fitting fails with T0077.
- Envelope key-order contract test (cli_contract.rs) and the LIMITS record-size test must be
  updated whenever scope/completeness keys or table records change.

### Fix round 1 (commit f6087e4, after the first independent verification failed)

- Binaries (cold rebuild reproduces them): ruststa 6ea002d0...0014, rusteda 45afed9d...be65,
  copied to the physical-design tool's .work/sta-signoff/bin. Gates: all-feature workspace tests
  708 passed / 0 failed / 30 ignored (1:40, 809 MB); fresh all-feature clippy 0 warnings (32.7 s,
  518 MB); `xtask verify sta` 189 passed (2:00, 948 MB); coverage 528 requirements / 0 gaps;
  shared check 195 files; fixtures unchanged; cargo-deny offline ok.
- Toolchain trap: /usr/bin/rustc (1.93.1) shadows ~/.cargo/bin on PATH, so plain `cargo` fails
  ("rustc 1.93.1 is not supported"). Use .work/wt/signoff/.work/fix1/run.sh (toolchain bin first,
  RUSTC/RUSTDOC set, cgroup MemoryMax=8G, /usr/bin/time -v logs).
- Statuses: one function, `engine::limits::status::limit_statuses`, feeds the revision summary
  (store/writer.rs) and every envelope (ws_io add_limit_headers). Never compute a status from
  violating counts alone: unclocked pulse pins, unevaluated (non-finite) rows and rules without
  any limit must make it `unchecked`/`partial`/`unconstrained` (STA-REQ-0813).
- Pin pulse attributes: the shared Liberty reader now turns min_pulse_width_high/low and
  min_period into synthetic scalar `min_pulse_width`/`minimum_period` arcs (LIBERTY-REQ-0049,
  line 0, after the pin's timing groups) - no model or rlib format change. Side effect: the frozen
  legacy engine emits its "ignored arc" notes for the wt_full flops (attributes only), which
  follow its last checkpoint; `cancel_at_every_point` now cancels only before the last phase ends.
- Ideal clocks: the reference tool (black-box) takes MPW widths from the propagated network even
  for an ideal clock and ignores set_clock_latency there; RustSTA uses the declared waveform with
  the latencies (STA-REQ-0815, note T0139). Only propagated mode is gated.
- `path --to` used the endpoint's overall worst rank and filtered by corner (empty for other
  corners; without --corner it searched only the worst-setup corner). It now scans the ranked
  paths of the family from the worst rank for the corner (STA-REQ-0814, T0138).
- Changed rule procedure: STA-REQ-0013 (arc classification) became STA-REQ-0017; the old text is
  in docs/specs/sta/legacy.md and the checker keeps citing 0013. Requirements created in the same
  unreleased version (STA-REQ-08xx, RCLI-REQ-0075) were edited in place.
- The gf180 raw-Liberty MPW gate still fails by design (last-listed vs largest `when` group); the
  harness reports status `deviation`, never `passed`. Accepting it is an owner decision.

- Version 0.4.0 adds pin limit checks (min pulse width, min period, max_transition,
  max_capacitance, max_fanout), the `limits` table (`limits.rbin`, LIMITS v1), report sections
  `limits|pulse_width|min_period|drv|max_*|hold_fix`, multi-corner SPEF (`PARASIT` v2 via
  repeatable `--spef/--spef-corner`), `--require-wire-model`, and fixes the swapped
  `required_ns`/`required_hold_ns` of inspect/pins and `--kind` on the endpoints section.
  Spec: docs/specs/sta/limits.md (STA-REQ-0800..0832), RCLI-REQ-0021/0075, WS-REQ-0137.
- Commits c66e7f6, 0fe57c4, 0924966. Release binaries (worktree target/release, copied unchanged to the
  physical-design tool's .work/sta-signoff/bin): ruststa sha256 f1d762fc...9deb0, rusteda
  be1a3b7b...63b7. Full CPU workspace 696 passed (1:46, 951 MB); `xtask verify sta` 189 passed
  (2:19, 933 MB); fresh all-feature clippy 31.5 s / 513 MB; cargo-deny offline ok; coverage 523/0.
- New results go to a separate table instead of new STA v2 sections: the shared reader validates
  the exact 24-section directory, so extra sections would break the other tool. Keep it that way.
- `rusteda show` merges every table summary into one metrics map: a key set by two tables
  silently overwrites. DRV counts live only in `summary.sta`; the limits table summary uses
  distinct keys (`limit_rows`, `limit_rows_truncated`).
- Liberty `when` groups: the reference tool (black-box) takes the LAST listed conditional
  min_pulse_width/minimum_period group (order-dependent, proven by swapping groups in a copied
  Liberty); RustSTA takes the worst group, which equals the GF180 pin attributes
  (min_pulse_width_high/low, min_period). GF180 differs by 0.143-0.530 ns on low MPW/period until
  the reference reads an attribute-only Liberty; then <= 0.023 ps. Pin-level attributes are
  "ignorable" in the shared reader and not in the rlib; a scan of both default PDKs (tt/ss/ff)
  found every attribute pin also has groups and attribute == worst group. Follow-up for the shared
  model owner: carry the attributes (needs rlib v4).
- Reference tool observations to keep in mind (documented deviations, not gated): it applies
  `set_max_transition -clock_path [get_clocks C]` to nothing and `-data_path` to clock pins too,
  ignores `set_fanout_load` on output ports, reports Liberty-internal IQ/IQ_N pins under
  design-level limits (slew 0), prints no slack for violated max_fanout rows, and rejects
  `set_max_capacitance` on clocks. RustSTA follows the SDC definitions.
- Re-importing an identical netlist keeps parasitics (same hash). Negative controls for a dropped
  SPEF need a really changed netlist (resize one cell).
- With SPEF, extracted timing needs load-dependent NLDM (T0077 "no positive delay sensitivity");
  constant-table synthetic cells only work with ideal wires.
- Input delays include the launch clock's network latency (STA-REQ-0218): hold slack of an
  input-fed register is `(lat_early + delay_min) - (lat_late + hold)`.
- Clap `requires = "spef"` on the RC budget flags makes `--max-rc-nodes` without `--spef` a usage
  error; harnesses must drop the budgets for no-SPEF imports.

## 2026-10-23: RC waveform 0.3.0 support

- Current version is 0.3.0, branch feat/rc-waveforms. Real full-R endpoint
  accuracy is now under the unchanged 1 ps gate: Sky130 0.5490975 ps,
  GF180 0.3017939 ps. Same Liberty/SDC/SPEF, default reference calculator;
  no R=0 projection or relaxed tolerance is used for this acceptance.
- Raw receipts: .work/rc-wave/acceptance-ready; complete physical rerun is in
  the implementation tool's .work/rc-timing/ready. The 20-process
  STA acceptance took 1.24 s /120312 KiB. The 90-process physical flow took
  14.38 s /404008 KiB host-process RSS (small/shared-machine fixture only).
- Native model: three-moment passive pi, charge over the source's active ramp,
  finite-ramp Thevenin driver fitted to NLDM lower/delay trip points, nonlinear
  root slew and one-pole receiver threshold response. The driver resistance
  is an explicitly declared local NLDM delay-sensitivity proxy, not a measured
  transistor DC resistance. No CCS, crosstalk or signoff claim.
- TimingLib::from_corner used to infer trip points from library names and lost
  real parsed metadata. Always use Library fields; GF180 needs 30/70 and derate
  0.5. The parsed and persisted-library paths are covered by the actual PDK run.
- Keep raw first moments separate from threshold delay: WireModel::elmore is
  the moment; load_delay is the voltage-threshold/slew response. The primary
  port exponential model uses -tau*ln(1-p), not tau. Existing clock expectations
  had to be corrected using the separately verified circuit equations.
- Prepared ArcValues are 128 bytes/CSR arc in f64, computed on CPU independently
  of tags, then consumed unchanged by CPU/CUDA. Forward, backward, incremental
  seeding and persisted-store reconstruction must all use the same records.
  A prepared compute session rejects edits to loads/starts/classes until a new
  preparation; never patch those inputs and retain stale prepared values.
- Fixed nested bisections were correct but slow (actual dual-PDK resize: 14.92 s).
  Bracketed Newton duration fitting and residual-based crossing termination
  reduce that verification to 0.44 s /138184 KiB, without changing the gates.
  Hard iteration/state/work ceilings and final charge residual checks remain.
- Independent Ngspice: five driver +four wire transient circuits, max time
  error 8.4412e-8 ns; max charge error 1.4922e-9 pF. Threshold tolerances are
  1e-6 ns and 1e-8 pF. This validates the declared circuit, not transistor models.
- CUDA engine tests plus prepared-ABI test pass bitwise in f64 (3 tests). Use
  the CUDA NVRTC LD_LIBRARY_PATH and physical-memory scopes from the prior note.
  Waveform preparation stays on CPU; do not claim a GPU waveform solver.
- Cargo feature unification changes test executable hashes. Locate executables
  in the exact cargo build log, not by assuming an earlier suffix. Early oracle
  receipts ran an older test executable; published evidence uses oracle-verified
  and wire-verified, matched to workspace-verified source/build artifacts.
- Source-only tools are isolated at .work/rc-wave/target/release. Original
  normal target/release and version 0.2 evidence binaries remain untouched.
- Nominal Sky130 setup/hold now meets constraints, but hold margin is only
  0.246889 ps, below the comparison gate. GF180 has four hold violations, worst
  -0.635534583 ns. Accuracy and functional feedback pass; total flow stays partial.
  A cell-resize consistency check uses fixed extraction topology and does not
  establish a physically rerouted ECO. Full DRC/LVS/RTL-to-GDS signoff is unverified.
- Final CPU workspace: 674 passed, 47.97 s /840368 KiB including builds;
  all-feature Clippy, 194 shared-file hashes, 504 requirements/zero gaps and
  fixture/layout checks pass. Report source/site links: 65/67 pages, no failures.
- Extracted set_driving_cell is an explicit T0077 error until its waveform
  convention is independently supported. Tracked/untracked pi delay APIs now
  use the same model and have a bitwise regression check.
- cargo-deny's license store failed under a 2 GiB virtual-address limit; it
  passes under a 2 GiB physical-memory cgroup (offline cached advisory database).
  Never try to raise a hard ulimit for CUDA in the same shell; start a fresh
  runtime command with a physical-memory cgroup instead.
- Clean room: only published circuit literature and public CLI observable
  reports (including report_dcalc) were used. No reference source was read or ported.

## 2026-10-23: SPEF 0.2.0 support

- Version is now 0.2.0. This completes and validates the pre-existing uncommitted
  SPEF / primary propagated-clock work. Do not apply the older 0.1.0 launch note.
- Plain/gzip detailed SPEF is imported atomically into PARASIT v1. The encoded
  and decoded caps both apply; validate CRC/truncation and reject symlinks/FIFOs.
  Typed RC public inputs must be validated before indexing; derive memory from
  actual vectors, not caller-controlled counters. Check component and subtree
  sums for nonfinite overflow even if each input scalar is finite.
- The declared model is lumped NLDM + Elmore first moment, unchanged wire slew,
  grounded coupling factor 1. It is not a ceff/transient/crosstalk signoff solver.
  The independent checker remains undecided on extracted/propagated scope;
  full recomputation and independent external equations are separate evidence.
- Four independent Ngspice nodal G*tau=C cases pass at 1e-9 ns (max difference
  2.78e-17 ns). This is a first moment, not a 50%-threshold transient delay.
  Real sky130/gf180 imports and actual clock-buffer resizing match full runs.
  R=0 calibration agrees with OpenSTA within 1 ps, but full-R maximum differences
  remain 3.148661/6.792814 ps. Preserve overall strict reference status partial;
  never loosen the gate or treat the calibration projection as real extraction.
- Raw receipts and inputs: .work/spef-release/. Final independent harness run:
  acceptance-final (75 processes, 2.26 s, max RSS 120300 KiB). CPU workspace:
  669 passed, 76.01 s, 674404 KiB. Actual dual-PDK resize: 136940 KiB. All-feature
  clippy, offline cargo-deny, shared/coverage/layout/fixtures pass. Two extracted
  CPU/CUDA f64 tests are bit-identical (245648 KiB).
- CUDA test runtime needs LD_LIBRARY_PATH pointing to the local CUDA NVRTC lib
  directory. Use a systemd physical-memory scope, not RLIMIT_AS, for CUDA virtual
  mappings. The initial missing-NVRTC failure is retained in the raw receipts.
- Use CARGO_BUILD_JOBS=2 and RUST_TEST_THREADS=2 for this verification. Isolated
  CPU release binaries are .work/spef-release/target/release/{rusteda,ruststa};
  normal target/release historical binaries were not overwritten.
- Coverage scanners require definitions to begin **REQ-ID**, not '- **REQ-ID**'.
  The three new specification pages are normalized; 497 requirements have zero
  gaps. The old CompiledClock test initializer needed propagated: false.
- docs/verification/spef-results.json is sanitized and source/binary-pinned;
  docs/_tools/spef_report.py generates the Chinese HTML report. The site hook
  rewrites its relative Markdown/source links only in site output. Source and
  generated-site link checks pass; no browser visual review is claimed.

## 2026-10-23: OpenC910 support and performance work

### Measuring and running
- The system `/usr/bin/cargo` is 1.93 and fails on this workspace. Use the pinned 1.97.1 toolchain
  through `.work/cargo.sh`, which sets `RUSTC`/`RUSTDOC` and `CARGO_BUILD_JOBS=8`; it also works
  inside `systemd-run --user --scope`.
- `/tmp` (and the session scratchpad under it) is RAM-backed tmpfs with a 30 GiB quota. Regression
  workspaces left there used about 17 GB of RAM and caused "Disk quota exceeded". Put workspaces in
  `.work/tmp/` and delete them after use.
- Never use `pgrep -f`/`pkill -f` with a pattern that also appears in your own command line: it
  matches the calling shell (a wait loop on `pgrep -f "python3 c910.py"` waited on itself).
- Another session may hold `/mnt/data2/cgao/git/RustSys/.work/scale.lock` for hours (OpenC910
  Yosys runs take about 50 min each). Queue heavy runs behind it and check `MemAvailable` first;
  `RustSys/.work/independent-verify-20261022/scripts/c910.py` does both.
- `RUSTSTA_PHASES=1 ruststa run ...` prints wall time, RSS and peak RSS per phase on stderr; use it
  instead of ad-hoc timers. Helper scripts: `.work/scripts/golden.sh` (16-netlist `sta.rbin`
  hashes, compare with `.work/scripts/cmp.sh` against `.work/logs/golden-v2b.txt`) and
  `.work/scripts/prof.sh`.
- `/usr/bin/time` output is lost when the timed command's stderr goes to `/dev/null`; use
  `/usr/bin/time -o <file>`.

### Correctness traps found
- `sta.rbin` v1 silently truncated endpoint indices (20 bits), chain node ids (24 bits), name
  buckets (22 bits), tag slots (5 bits) and `u32` chain pointers. Format v2 stores them at full
  width; `check_store_capacity` and `check_store_tables` reject the remaining fixed-width overflows
  with `T0022`. Any new packed field needs the same pre-flight check.
- A path trace capped at 4096 steps silently cut deep paths; bound traces by the level count.
- `DesignIndex` once copied the pin list of an `assign`-equivalence class into every alias. The
  Yosys OpenC910 netlist (654k assigned bits) then needed > 29 GB. Keep per-class data in one
  table (`net_pins`, `net_port_bits`).
- `ruststa report --section paths` reconstructed every ranked path before applying `--limit`
  (17.9 GB on OpenC910). Keep only the shown window.
- The fast netlist reader (`rusteda-cli` `import_netlist/fast.rs`) rejected Yosys `assign`
  concatenations, part-selects and sized constants. The fallback parser then needed 42 bytes per
  input byte (OpenC910 Yosys: > 29 GB). The fast path now handles them; verify any change against
  the extended reader by byte-comparing `netlist.rbin` from the old binary.

### Verification oracles and their limits
- Byte-identical `sta.rbin` on the 16 README netlists (`golden.sh`), at 1, 2 and 4 threads, is the
  regression oracle for performance changes. It does not exercise `get_pins`/`get_cells` queries;
  the unit tests do.
- `ruststa check` (independent checker) is `undecided` on SDCs with `set_false_path` on designs
  with more than 200k enumerated paths, rejects some Yosys netlists in its own netlist conversion,
  and on exception-free SDCs disagrees with the engine by up to 4.1 ns where OpenSTA agrees with
  the engine. This is a checker defect; per AGENTS rule 8.2 the engine author does not edit it.
- RustSTA vs OpenSTA: inside the Liberty table ranges they agree to < 1 ps. Far outside them
  (max_capacitance violated 100-1000x) the difference grows to about 1e-6 relative per stage;
  OpenSTA values lie on single-precision grid points. See
  `RustSys/.work/independent-verify-20261022/sta-diff/extrapolation/README.md`. When probing with
  a single path, compare max(rise, fall): OpenSTA's default `report_checks` may show the other
  transition when the clock period is huge.

## 2026-10-23: Public documentation and branding launch

- RustSys Signal Forge branding is the visual reference: preserve the dark terminal/chip frame,
  copper input and teal output. RustSTA's glyph is a clock; its banner shows a bounded reconvergent
  timing graph and path report. `docs/_tools/generate_brand.py` reproduces original SVG/GIF assets.
- Use the actual workspace version 0.1.0, not the stale 0.2.0 in the previous README.
- The quickstart pairing matters: `net/chain_8.v` requires `lib/wt_15.lib`; `wt_full.lib` has `wtf_*`
  cells and cannot load that chain. The `netlists/` corpus uses `wt_full.lib`. Confirmed the chain's
  full import/run/path/report/check-both flow; verdict `agree`, zero numerical difference.
- The current fixture check syntax is `cargo xtask fixtures check`, not `fixtures --check`.
- New MkDocs site preserves all manual/specification paths. A build hook rewrites only links that
  leave docs/ to GitHub source URLs, keeping the 184 shared files byte-identical. A directory link
  in roadmap.md needed an explicit README target for strict MkDocs validation.
- Source link check: 58 pages, 275 references. Generated HTML: 58 pages, 5725 local references;
  strict build and both checks pass. Existing three docs-example tests, fmt, layout, requirement
  coverage (478 requirements, zero gaps), shared manifest and fixture checks pass locally.
- Memory estimates/guards and receipts: `.work/launch/`. Asset render: 158844 KiB RSS / 4.09s,
  142078-byte GIF (84 frames, 8.4s). Static docs build: about 45 MiB RSS / 0.6s.
- Browser QA verified desktop light/dark themes, pause-to-SVG behavior and a 390px responsive
  viewport with no horizontal overflow or broken images. Reset viewport overrides after QA.
- A terminated preview left an IAB tab on a blocked data: network-error page; create a fresh tab
  in the same browser after restoring the foreground server, rather than retrying that error tab.
- The main snapshot is published as a parentless initial commit; Pages uses Actions, not a gh-pages
  branch, so generated HTML does not add a second source-history branch. Local notes and launch
  receipts remain ignored. No new timing algorithm or performance claim is introduced.
- First public CI exposed a pre-existing packaging gap: REUSE requires a canonical
  `LICENSES/Apache-2.0.txt`, not only a root LICENSE. Added the identical license text there and
  allowed LICENSES/ in xtask's top-level directory list. Local REUSE lint now passes. The shared
  source_rules test contains a literal SPDX header that REUSE logs as an unparsable extraction;
  aggregate annotations still cover it and it is not a compliance failure. Do not edit shared
  source or its hash manifest merely to silence this packaging-tool log.
- Old gh does not support `--accept-visibility-change-consequences`; use the repository PATCH API
  for an explicitly authorized visibility change. The parentless initial push did not schedule
  the path-filtered docs workflow; manually dispatch docs.yml with publish=true for first launch.
