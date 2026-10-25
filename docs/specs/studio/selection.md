# Selection sharing through `ui_state.json`

A human selects in the workbench; an agent acts on that exact selection with version safety
(plan §1.4, §4.2 Example 3). The selection travels through `ui_state.json` of the workspace,
written by the workbench and read by the command-line tools with `--target sel<N>`
(WS-REQ-0043). The workspace crate defines the file, its schema `rusteda.ui_state.v1`, its
atomic replacement (`Workspace::write_ui_state`) and its checked reading (`Workspace::ui_state`,
`Workspace::resolve_selection`).

**STUDIO-REQ-0140** A selection belongs to the displayed revision: ranked path rows and instance
rows, each at most once, in the order they were selected.

**STUDIO-REQ-0141** A plain click on a path or an instance makes it the whole selection; a click
with control, the platform key or shift adds it, or removes it when it is selected. `--select`
replaces the selection with the handles it names, in their order.

**STUDIO-REQ-0142** Every change of the selection by the user is written at once to
`ui_state.json` through `Workspace::write_ui_state`, an atomic replacement: the handle `sel<N>`,
the displayed revision, the instance handles `inst<row + 1>` and the path handles `path<row + 1>`
(WS-REQ-0022). Every handle matches `[A-Za-z0-9_-]+` (Rule 4.1). The command line resolves the
written selection with `Workspace::resolve_selection` for that revision only. A failed write is
the error `U0042` and changes nothing else.

**STUDIO-REQ-0143** The number `N` of a new selection is one above both the number of the
selection in the file and the last number the workbench wrote or read, so a handle is never
reused for another selection in a session.

**STUDIO-REQ-0144** When `ui_state.json` changes (STUDIO-REQ-0001), the workbench reads it with
`Workspace::ui_state`. A selection of the displayed revision is shown: its handles that name a
path row or an instance row of the revision are selected and the first path, else the first
instance, is focused; handles that name nothing are reported as `U0043`. A selection of another
revision is reported as `U0041` and the displayed selection stays. A file that cannot be read
(another schema, damaged JSON, a handle that is not shell-safe) is the error `U0040` and changes
nothing.

**STUDIO-REQ-0145** At start the workbench shows the selection of `ui_state.json` when it belongs
to the displayed revision. Otherwise it focuses the worst ranked path for display, without
selecting or sharing it: opening the workbench writes nothing.

**STUDIO-REQ-0146** A selection the workbench wrote itself is not applied again when the watcher
reports the file: the workbench takes the file's new state as seen after it writes, and a read
state equal to the last one written or read is ignored.

**STUDIO-REQ-0147** `ui_state.json` is written only when the user selects: not when the
displayed revision changes, not when the user browses, searches or pins a revision. A selection
written for a revision therefore stays valid for that revision while tools commit new ones, so
that an agent can run `--target sel<N> --base-rev <that revision>` (plan Phase 3, steps 3 to 5).
