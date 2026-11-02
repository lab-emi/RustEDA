# Ibex SG13G2/EZ130 host benchmark harness

This directory reproduces the Ibex RTL-to-GDS campaign on a host that has none
of the first campaign host's local artifacts: no systemd user manager, cgroup v1
instead of v2, four CPUs instead of sixteen, nixpkgs tool builds instead of the
LibreLane devshell AppImage, and no access to the ETH EZ130 download server.
It drives the recipe in [`../ibex_sg13g2_ez130`](../ibex_sg13g2_ez130/README.md)
and the stock LibreLane Classic flow from the same prepared inputs, then applies
the same independent gate scripts to both final layouts.

Results, scope and the module-by-module comparison are published on the
[benchmark page](../../docs/benchmarks.md) and in the machine-readable receipt
[`docs/benchmarks/ibex-sg13g2-ez130-host-20261102.json`](../../docs/benchmarks/ibex-sg13g2-ez130-host-20261102.json).

## Files

| File | Purpose |
| --- | --- |
| `qualify_sram_lvs.py` | Runs the SRAM-only KLayout LVS and writes the macro qualification record the LVS gate requires. The record is `qualified` only when the run matches. |
| `compose_native.py` | Writes the frontend/backend composer inputs (every file and tool pinned by path, SHA-256 and size) and calls the recipe's `native_frontend.py` and `native_backend.py`. |
| `run_logic.py` | Renders the `rusteda.logic-signoff-inputs.v1` document that `native_flow.py` would render, for a completed producer output. |
| `signoff_layout.py` | Applies the recipe's gate scripts to one final layout: foundry main and antenna DRC, OpenRCX, RustSTA + OpenSTA timing, full-device LVS, vendor-model simulation, synthesis/final equivalence and final density. |
| `netlist_qor.py` | Mapped-netlist QoR measured with one neutral toolchain: area and cell classes (`yosys stat -liberty`), logic depth with sequential cuts, ideal-wire OpenSTA timing (diagnostic). |
| `def_metrics.py` | Routed signal wirelength and vias from a final DEF, with the published receipt's definition. |
| `spef_spice_decks.py` | Transient decks for `rustspice bench-suite` built from extracted Ibex RC nets. |

## Host deviations

Each deviation applies to both flows unless stated otherwise.

1. **EZ130 collateral.** The ETH archive host is unreachable from the benchmark
   host. Six of the eight pinned EZ130 files are byte-identical to the contract:
   TT/SS Liberty, cell LEF, CDL and Verilog come from a public pre-release
   mirror that differs only in a citation placeholder, verified by SHA-256 after
   restoring it, and the TT Liberty also matches the `pulp-platform/croc`
   mirror. The SG13G2 tech LEF and the cell GDS are **reconstructed** from the
   public SG13CMOS5L retarget by reversing its six documented edits (Metal5,
   Via4, TopVia2 and TopMetal2 from IHP's SG13G2 tech LEF; 74 `Via4_*` vias;
   the 220 added `189/4` boundary shapes removed). They are not byte-identical
   to the pinned files (`375ae19d…`, `62876764…`), so this is not a
   reproduction of the 2026-10-30 inputs. All IHP and SRAM collateral matches.
2. **Tools.** nixpkgs builds: OpenROAD 26Q3 (LibreLane pins `dcf36133`),
   OpenSTA 3.1.0, Yosys 0.70 (historical 0.62), KLayout 0.30.12, Magic
   8.3.689, Netgen 1.5.325, Icarus Verilog 13.0, ngspice 45, sv2v 0.0.13.1.
   The converted RTL therefore differs in bytes from the historical
   `8a8fe84b…` file; both flows read the same converted file.
3. **Native recipe.** The original component-qualification evidence
   (`rusteda.ihp-component-qualification.v1`) is not published and is not
   fabricated, so the sixteen-gate composer cannot run. The native run uses the
   backend prefix manifest (every physical step plus DRC, antenna, LVS,
   extracted timing and timing parity), and `signoff_logic.py` separately for
   synthesis/final equivalence and vendor simulation. The eight derived
   qualification gates are not produced; the run is **complete but
   unqualified**. Host-local copies of four helpers are patched: thread
   allow-lists accept 4, `build_rc_profile.py` accepts the explicit calibration
   rules path the composer passes, and `signoff_lvs.py`/`producer.py` read the
   cgroup-v1 memory controller and accept the host executor's owned cgroup
   instead of a systemd scope. The combined cell LEF is rebuilt from the EZ130
   and SRAM LEFs.
4. **LibreLane.** Stock LibreLane 3.0.17 Classic flow, without the
   RustEDA-authored repair loop that the historical matched reference added.
   `DRT_THREADS` is 4 on this host.
5. **Resources.** Each timed run executes alone in its own cgroup-v1 memory
   group (8 GiB RAM for RustEDA as in the recipe, 10 GiB RAM for LibreLane,
   which on the historical host had 8 GiB plus 2 GiB swap; this host has no
   swap). Peak RSS is GNU time's maximum over the waited process tree; the
   cgroup peak includes page cache.
