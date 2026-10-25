# rusteda-studio: the workbench of the suite

`rusteda-studio` (`crates/apps/rusteda-studio`) is the graphical workbench of the RustEDA suite
(plan §1.4, §4.2 Example 3, §6.1, Phase 3). It shows a disk workspace live: it follows `HEAD`
as the tools of the suite commit revisions, and it shows the displayed revision's netlist
hierarchy, its ranked timing paths, the logic cone of a path or an instance as a schematic, and
the RTL lines of what is selected. It reads the workspace only through the shared crate
`rusteda-workspace`, never writes a table, and shares the user's selection with the command-line
tools through `ui_state.json`.

The workbench owns no EDA semantics. The timing tool of the suite defines what a slack, a path
or an endpoint is (`docs/specs/workspace/sta.md`); the synthesis tool defines its names; the
core defines netlists and names (`docs/specs/core/netlist.md`). The workbench only reads, lays
out and draws them, so a difference between the workbench and a tool is a defect of the
workbench.

Requirement IDs `STUDIO-REQ-0001` to `STUDIO-REQ-0299` are reserved for the workbench.
Diagnostic codes `U0001` to `U0199` are its codes ([diagnostics.md](diagnostics.md)).

| Specification | Requirements |
|---|---|
| [Workspace: loading, watching, budgets](workspace.md) | STUDIO-REQ-0001 to 0010 |
| [Netlist hierarchy explorer](hierarchy.md) | STUDIO-REQ-0030 to 0035 |
| [Critical-path table](paths.md) | STUDIO-REQ-0050 to 0054 |
| [Logic cones, their layout and their schematic](cone.md) | STUDIO-REQ-0080 to 0097 |
| [RTL cross-probing](rtl.md) | STUDIO-REQ-0120 to 0126 |
| [Selection sharing through `ui_state.json`](selection.md) | STUDIO-REQ-0140 to 0147 |
| [The window and live updates](gui.md) | STUDIO-REQ-0160 to 0166 |
| [Diagnostics](diagnostics.md) | STUDIO-REQ-0200 to 0203 |
| [Command line](cli.md) | STUDIO-REQ-0220 to 0225 |
| [Architecture and repository checks](#architecture-and-repository-checks) | STUDIO-REQ-0240 to 0252 |

## Architecture and repository checks

The crate has two parts. The headless model (`src/model/`) computes everything the panels show:
the loaded revision, the hierarchy rows, the path rows, the cone scene with its layout, the
cross-probe and the shared selection. The front end (`src/gui/`, feature `gui`, on by default)
renders the model with the GPUI framework and forwards the user's actions to it; it holds no EDA
state of its own. Because the model needs no graphics, it is tested on workspaces that the tests
build with the shared crates, and the `--print` mode of the command line prints its panels as
text.

**STUDIO-REQ-0240** The headless model depends on no user-interface crate: the framework is an
optional dependency that only the feature `gui` turns on, the front end is compiled only with
that feature, no source of `src/model/` names the framework, and the crate builds and passes its
model tests with `--no-default-features`.

**STUDIO-REQ-0241** Of the RustEDA crates, the workbench depends on `rusteda-core` and
`rusteda-workspace` only (plan §2); it uses no tool's engine, and reads timing results through
the shared read-only view of the timing table (WS-REQ-0126 to 0136). `cargo xtask layout`
enforces this, and that the user-interface crates of the workbench are optional dependencies
while no crate outside the `gui` and `apps` groups uses one.

**STUDIO-REQ-0250** `cargo xtask coverage` writes the requirement matrix
`docs/verification/requirements.md` from every specification below `docs/specs/` and fails on a
requirement that no source or test cites; the workbench's requirements are each cited by a test.

**STUDIO-REQ-0251** `cargo xtask layout` checks the layout rules of `CONTRIBUTING.md` on the
tracked and untracked, not ignored files: the top-level allowlist, crate places and names,
source places, SPDX headers with the right copyright holder, file and line limits, the places of
design data, and the dependency rules of STUDIO-REQ-0241.

**STUDIO-REQ-0252** The files shared with the tool repositories (the shared crates, their
specifications and the shared automation that `xtask/src/shared.rs` lists) are byte-identical to
the manifest `verification/shared.sha256`; `cargo xtask shared check` verifies it and
`cargo xtask shared diff <repository>` lists the differences with a clone of a tool.

## Memory and work (Rules 3.4 and 9)

The model holds the displayed revision once: the validated netlist tables in memory (about the
size of `netlist.rbin`), per instance two `u32` of hierarchy indices plus the scope tree, the
timing table mapped read-only with at most `max_path_rows` listed rows, and the RTL files within
their byte budgets. Peak memory is therefore about

  netlist.rbin + 8 B × instances + 200 B × scopes + 200 B × listed paths + RTL bytes,

and twice that during a reload, while the new revision is loaded beside the displayed one: about
250 MB for a netlist of one million instances. Every traversal is bounded by the budgets of
STUDIO-REQ-0003; the cone and its layout are bounded independently of the design size. The tests
build workspaces of at most a few dozen instances; their peak resident memory is reported in the
change that adds a suite (`CONTRIBUTING.md`).
