# Workspace: loading, watching and budgets

The workbench is a client of a disk workspace (`-w/--workspace DIR`, plan §1.1, §3.1). It holds
no state the workspace does not have: everything it shows comes from one committed revision,
read through `rusteda-workspace` (`docs/specs/workspace/README.md`), and the only file it ever
writes is `ui_state.json` ([selection.md](selection.md)).

## Watching

**STUDIO-REQ-0001** The watcher follows two files of the workspace by polling: `manifest.json`,
whose atomic replacement is the commit point of every commit and revert (WS-REQ-0076), and
`ui_state.json`. A poll takes the fingerprint of each file (length, modification time in
nanoseconds and file identity, the inode on Unix; none for a missing file or a non-regular
file) and reports `Manifest` when the manifest's fingerprint changed, `ManifestLost` when the
manifest disappeared, and `UiState` when the selection file changed, appeared or disappeared.
The fingerprints at start are taken as seen. Polling runs in the workbench process: no daemon,
no extra dependency, two `stat` calls per poll (default interval 250 ms).

**STUDIO-REQ-0002** A change is reported once its file has kept the new fingerprint for the
debounce interval (default 150 ms; 0 reports at the first poll): a burst of commits within the
interval gives one report, and the workbench then loads the HEAD of that moment. The watcher
takes the current time as an argument, so that its behavior is tested without waiting.

## Budgets

**STUDIO-REQ-0003** Every traversal of the workbench is bounded (Rules 3.4, 9.2). The default
budgets are:

| Budget | Default | Bounds |
|---|---:|---|
| `max_hierarchy_depth` | 64 | levels of one instance name ([hierarchy.md](hierarchy.md)) |
| `max_search_results` | 1000 | listed matches of a search, per kind |
| `max_path_rows` | 10 000 | listed ranked paths ([paths.md](paths.md)) |
| `max_revisions` | 64 | listed revisions, newest first |
| `cone_fanin_depth` / `cone_fanout_depth` | 3 / 2 | levels of a cone ([cone.md](cone.md)) |
| `cone_max_nodes` | 160 | nodes of a cone |
| `cone_max_edges` | 1200 | edges of a cone |
| `cone_max_net_fanout` | 32 | loads of one net followed by a fan-out step |
| `cone_max_visits` | 200 000 | pin, port-bit and net visits of one extraction |
| `layout_max_virtual` | 4000 | virtual nodes of a cone layout |
| `layout_sweeps` | 12 | ordering sweeps of a cone layout |
| `rtl_max_file_bytes` / `rtl_max_total_bytes` | 4 MiB / 64 MiB | RTL text indexed ([rtl.md](rtl.md)) |
| `rtl_max_hits` | 40 | source lines of one cross-probe |

A budget that is reached ends the traversal with a structured result that says so, and with a
diagnostic ([diagnostics.md](diagnostics.md)); it never allocates without bound.

## Loading a revision

**STUDIO-REQ-0004** The workbench never writes a table, a revision, the manifest, the history or
the cache: opening a workspace, browsing, searching, focusing, drawing cones, cross-probing and
reloading leave every file of the workspace byte-identical. It reads the manifest under the
shared lock of `workspace.lock` through `Workspace::manifest` (WS-REQ-0050), tables of committed
revisions through the validated readers of `rusteda-workspace` (WS-REQ-0037, WS-REQ-0122,
WS-REQ-0126), and the RTL snapshot through the checked accessors of directory tables
(WS-REQ-0026). The only file it writes is `ui_state.json`, and only when the user selects
(STUDIO-REQ-0147).

**STUDIO-REQ-0005** A load shows one revision: `HEAD` when the workbench follows the workspace,
or a pinned revision. It reads the revision's `meta.json`, its `netlist` table into the netlist
view (STUDIO-REQ-0010) and its hierarchy, its `sta` table into the path table, and its `rtl`
directory table into the RTL index. A table that the revision does not have is a note (`U0010`
no netlist, `U0020` no timing table, shown as timing `not_analyzed`, `U0030` no RTL snapshot), a
table that cannot be read is an error (`U0011`, `U0021`), and in both cases the rest of the
revision is shown. A workspace that cannot be opened is `U0001`, a revision that does not exist
`U0002`, and one whose `meta.json` cannot be read `U0003`; then nothing is loaded.

**STUDIO-REQ-0006** The revision panel lists the newest revisions first, at most
`max_revisions`, each with its number, the command and tool that made it, its time, its message
and its timing status: the `timing` status of its `sta` table (`met`, `violated`), or
`not_analyzed` when it has none (Rule 4.2). Revisions whose `meta.json` cannot be read are left
out.

**STUDIO-REQ-0007** The model (`Studio`) holds the displayed revision and the user's view of it
(expanded scopes, search, filter, selection, focus, cone depths) and offers the commands of the
panels. Loading is a separate function of the workspace, the revision and the budgets, so that
the front end runs it off the render thread and hands its result to `Studio::install`;
`Studio::reload` is the synchronous form for tests and `--print`.

**STUDIO-REQ-0008** A load is a run of the core job API (`docs/specs/core/README.md`): its
phases `revision`, `netlist`, `hierarchy`, `timing` and `rtl` run in this order on the calling
thread, each between two checkpoints and announced by `PhaseStarted` and `PhaseFinished`; the
hierarchy phase reports `Progress` per instance (JOB-REQ-0004) and checks the token every 4096
instances; the run ends with the `instances` metric. A load observed with a token that is never
set gives the same snapshot as an unobserved load, and its events depend only on the workspace
(JOB-REQ-0005); a cancelled load returns `Cancelled` and no snapshot (JOB-REQ-0006). The window
shows the phase and progress of the load in flight and cancels it when a newer revision is to be
shown.

## The netlist view

**STUDIO-REQ-0010** The netlist view holds the validated tables of the `netlist` table
(`FrozenNetlist`, WS-REQ-0122) and indices built in time linear in the tables: instance rows
sorted by name (lookups by name in O(log n)), the port bits on each net, and the continuous
assignments between nets in both directions. Rows are those of the table, so the handle
`inst<N>` names instance row `N - 1` (WS-REQ-0022). A net is shown as `name`, `name[index]`, or
`net#<row>` when anonymous; a port bit as `name` or `name[index]`, its index counted from the
port's declared msb.
