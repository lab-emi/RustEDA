# Changelog

All notable changes to this project are documented in this file.
The format follows the "Keep a Changelog" convention; versions follow semantic versioning.

## [Unreleased]

### Added
- **`rusteda-studio` 0.1, the workbench of the suite** (`docs/specs/studio/`, STUDIO-REQ-0001 to
  STUDIO-REQ-0252):
  - Headless model without graphics dependencies: read-only loading of a revision through
    `rusteda-workspace` (netlist, hierarchy, timing table, RTL snapshot) as an observable,
    cancellable run of the core job API; polling watcher of `manifest.json` and `ui_state.json`
    with debounce; bounded budgets for every traversal.
  - Netlist hierarchy explorer from flattened instance names (core and flattened separators),
    counts per scope and level, bounded search.
  - Critical-path table through the shared `StaTable` view: handles `path<N>`, kind, corner,
    slack, startpoint, endpoint, levels; per-corner WNS/TNS with the analysis scope; path
    detail; filters.
  - Bounded fan-in/fan-out logic cones and a deterministic layered layout (distance-grouped
    cycle breaking, longest-path layering, barycenter sweeps with exact crossing counts, integer
    coordinates), with the focused path's instances and connections highlighted.
  - RTL cross-probing by RTL-derived name parts against a light index of the `rtl` snapshot
    (modules, instantiations, generate labels, classified occurrences), flagged approximate.
  - Selection sharing through `ui_state.json` (`sel<N>` pinned to the displayed revision,
    shell-safe handles), external selections read and shown.
  - GPUI front end (feature `gui`): hierarchy, revisions, corner summary, path table, cone
    canvas with zoom and pan, path detail, RTL source pane, status bar; live updates loaded off
    the render thread, superseded loads cancelled.
  - Command line: `-w`, `--rev`, `--select`, `--print`, watcher and cone options, coded
    diagnostics `U0001` to `U0199`.
- Repository automation: `cargo xtask layout` (with the workbench's dependency rules),
  `coverage`, `shared check|update|diff`, `pdk fetch`.
- The shared crates and specifications of the suite, byte-identical to the tool repositories,
  with the synthetic fixtures their tests read.
