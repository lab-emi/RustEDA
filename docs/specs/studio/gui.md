# The window and live updates

The front end (`src/gui/`, feature `gui`) renders the model on the GPUI framework
(`docs/adr/0001-gui-toolkit.md`) and forwards the user's actions to it. It holds no EDA state of
its own.

## Live updates

**STUDIO-REQ-0160** While the workbench follows `HEAD`, a commit or a revert by any program is
followed live: the watcher reports the replaced manifest (STUDIO-REQ-0001, STUDIO-REQ-0002), the
workbench reads the new `HEAD` under the shared lock and, when it differs from the displayed
revision, loads it; the revision panel is refreshed at every manifest change. Commits made while
the workbench polls are all followed, ending at the last `HEAD`.

**STUDIO-REQ-0161** Installing a loaded revision keeps the user's view: the open scopes (by their
path of levels), the path filter and the cone depths stay; the selection and the focus move to
the objects of the same names in the new revision (instances by name, paths by endpoint, corner
and check), and what has no counterpart is dropped; a revision without paths focuses nothing; a
search runs again on the new revision. The change is noted as `U0082`. A load that fails is the
error `U0081` and the displayed revision stays. `ui_state.json` is not rewritten
(STUDIO-REQ-0147).

**STUDIO-REQ-0162** A revision chosen in the revision panel (or with `--rev`) is pinned: the
workbench shows it and does not follow commits, while the revision panel still lists them.
"follow HEAD" loads `HEAD` and follows again.

## The window

**STUDIO-REQ-0163** One window shows: a top bar with the workspace, the displayed revision and
whether it follows `HEAD` or is pinned, the command and tool that made it, its timing status and
the progress of a load in flight; on the left the hierarchy explorer with its search field and
the revision panel; in the middle the corner summary with the analysis scope and the path
filters, the path table, and the logic cone with its depth controls; on the right the path
detail and the RTL source with the hits of the cross-probe; and a status bar with the numbers of
errors, warnings and notes, the latest warning or error, the shared selection and the poll
interval.

**STUDIO-REQ-0164** Interactions: a click on a path row selects it (STUDIO-REQ-0141) and focuses
it; a click on a scope row opens or closes it, on an instance row selects and focuses the
instance; typing edits the search query (backspace deletes, escape clears) and searches at once;
a click on a search result scope opens it in the tree; the filter buttons set the family, the
negative-slack filter and the corner; the depth buttons change the cone's fan-in and fan-out
levels; on the cone, the mouse wheel zooms around the cursor, a drag pans, a click on a node
cross-probes it, a double click makes it the cone's root and a click with control or shift adds
it to the selection; a click on a point of the path detail cross-probes its instance; a click on
a hit shows it in the source pane; a click on a revision pins it.

**STUDIO-REQ-0165** The hierarchy tree, the search results, the path table, the path points and
the source lines are virtualized lists: only their visible rows are built and drawn, so their
cost does not grow with the design.

**STUDIO-REQ-0166** The render thread never waits for a load: the watcher's poll runs on a timer
of the window (two `stat` calls, and the small reads of the manifest or `ui_state.json` when they
changed), and a revision is loaded on a background thread as an observable, cancellable run
(STUDIO-REQ-0008) whose result is installed when it is ready. A load superseded by a newer
revision is cancelled, and the newer one starts when it has stopped.
