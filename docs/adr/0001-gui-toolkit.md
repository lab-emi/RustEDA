# ADR 0001: The user-interface framework of the workbench

- Status: accepted (workbench 0.1)
- Context: plan §6.1 (Stage 1 workbench), Phase 3

## Context

The plan builds the workbench with the GPUI framework paired with its component library, pinned
in `Cargo.toml`. The RustEDA repositories take dependencies from crates.io only, pinned to exact
versions (`deny.toml` refuses git sources). On crates.io:

- `gpui` 0.2.2 is the latest release of the framework.
- The component library's latest release, 0.7.3, depends on `gpui-pre` `=0.3.9`, a snapshot of
  the framework published under another package name, not on the released `gpui`. Its types and
  traits are those of the snapshot, so it does not build against `gpui` 0.2.2: the two cannot be
  paired. Its last release that depends on the released framework is 0.5.1 (`gpui ^0.2.2`).

## Decision

The workbench uses `gpui` `=0.2.2` alone, without the component library, behind the feature
`gui` of `rusteda-studio`. The few widgets it needs are written with the framework's own
elements: virtualized lists (`uniform_list`) for the hierarchy, the path table, the path points
and the source lines; a canvas for the schematic; plain elements for buttons and the search
field. The headless model does not depend on the framework at all.

`libc` is held at 0.2.189 (`Cargo.toml`, `Cargo.lock`): 0.2.190 removed `ENOATTR` on Linux, and
the extended-attribute crate 0.2.3 under the framework's HTTP client still uses it. The workbench
names `libc` in its `gui` feature only to hold that version.

## Consequences

- Fewer dependencies and a smaller build than with the component library; no docking, tabs or
  themes from the library: the layout is fixed.
- The component library can be adopted when a release of it depends on a released framework
  version that the workbench also uses (0.5.1 does today, for `gpui` 0.2.x); its widgets would
  replace the hand-written lists and buttons without touching the model.
- The framework's Linux backend draws through Vulkan. Without a display it runs under `xvfb-run`
  with a software Vulkan driver; its X11 backend starts drawing only after the window is mapped
  through a window manager, so a headless run also starts one. The framework links
  `libxkbcommon-x11` at build time (CONTRIBUTING, "Linux system libraries").
- The view tests use the framework's test platform (`test-support`), which needs no display.
