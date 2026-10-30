# Independent tap coverage and extra foundry scope

The user approved an isolated staggered-tap experiment after the public IHP rule audit. Native per-row gap policy30um and the unchanged physical diffusion-to-tie limit20um are different quantities. No default tap policy or original signoff gate changes here.

## Independent geometry certificate

`tap_coverage.py` uses actual Activ/pSD/derived nSD, contacts and connected NWell/PWell domains from GDS, according to the public layout-rule definitions. It imports no insertion algorithm, row pitch, phase or tap-instance count. Numerical distance arithmetic uses integer DBU. A center-nearest L1 distance plus the complete closed-box radius certifies every point of an accepted bound. Subdivision children share their boundary: skipping the open interval between integer coordinates would be unsound. Unproved small boxes return unverified.

The admitted geometry is rectilinear polygons with unit orthogonal transforms. Paths, arbitrary angles/magnification and unknown nontext shapes reject explicitly; text is deliberately ignored. This prevents rounded integer transforms or polygon clipping from being described as an exact continuous proof.

Only diffusion targets/tie anchors in the two named SRAM subtrees are excluded; their FULL well/body geometry remains. Name/count is not a content-identity proof. The report explicitly leaves macro identity and complete device-level LVS to the original bound gates. Removing well geometry with excluded targets would falsely connect body domains through a macro hole.

All Python graph and spatial traversals have explicit object/work caps and a hierarchy cycle guard. Topological instance-multiplicity DP detects exponential expansion without path materialization. Native Boolean operations allocate before output-count checks; the input caps are not a general memory-admission proof for native intermediates. Eight source regions, derived domains/dopants, target/tie temporaries and reader state must be accounted for. These real fixtures were estimated below2GiB and measured around0.9GiB; standalone runs used4GiB/1GiB/600s guards. The private product flow retains its whole8GiB cgroup and must separately qualify any tighter pure-CPU address-space guard.

## Actual independent controls

The frozen worker04 is16,861bytes, SHA `36d7b4ac4895577dd953fbf383dac3f50e956c1c78d2ad8e4ab5daa98440e18d`. Eleven mathematical/budget/cycle test methods pass, including a half-DBU continuous-gap counterexample and independent exhaustive half-grid checks of every accepted tiny box. Actual GDS matrix: legal layout and both real placed layouts pass; missing-Pbody, missing-NWell, scaled, rotated, path, diagonal and separate-well controls all exit nonzero/unverified. Ten cases and raw resource logs are bound in `.work/perf-20261030/tap-rule-audit/coverage04-result.json` (SHA `445ffb6f41480d0660682e5b2635e6333e13d3b34f65efb547f80dde84319878`). Real baseline and candidate command times were35.255s and31.850s, including process startup; these are placement-stage coverage checks, not final whole-flow acceptance.

`tap_coverage_runner.py` gives explicit `--workspace`/`--base-rev empty`, pins the external tool SHA/bytes, checks source/config/GDS identities before/after execution, and requires both process success and nonvacuous passed LU.a/LU.b reports. Fresh semantic failure, stale workspace/base revision and wrong-tool controls reject without modifying the previous workspace. Both runner and worker must be frozen as recipe inputs.

## Extra full-layout foundry oracle

`signoff_latchup.py` verifies the complete existing PDK provenance, invokes its unchanged maximal deck, preserves every finding and full category inventory, and requires an actually emitted LU.a category. The original complete main deck remains separately mandatory for LU.b and all original checks. The maximal deck name alone does not imply LU.b coverage.

Actual legal tiny layout passes. Missing-NWell and separate-well controls fail with LU.a findings; unrelated findings remain retained. The official `--table latchup` interface only witnessed LU.b on this pinned tool. A deliberately invalid table returned exit0, a pass message and an empty category inventory. Never accept zero findings or process success without required rule applicability and witnessed negative controls. No reference/deck source was inspected or edited.

Host/maximal control bundle: `.work/perf-20261030/tap-rule-audit/final-controller-controls.json` (SHA `c987628750389245793a2d7379d5b5f1fe3252dfd2cc320e13ad738a6e227515`). CLI rejection bundle: `controller-rejection-controls.json` (SHA `8b14ad74eab9c392409c0207019036d9c4cb3afae5eeec6e78a70b56ef911375`). Maximum tiny-control RSS446,996KiB. Retained initial helper/test failures were plumbing and test-schema assumptions; no EDA result was converted into a pass.

The native staggered-placement component separately passed four unchanged full foundry runs (baseline/candidate main and maximal),440 Rust tests, exact absent-pattern Ibex DEF and36 dual-PDK commands. Those component results do not qualify CTS, routing, timing or the final16-gate flow. The new combined engine adds bounded parallel queries and the wide-layer fix, passes443 tests plus all13 component cases, and is awaiting its private full-flow run. No push or publication.
