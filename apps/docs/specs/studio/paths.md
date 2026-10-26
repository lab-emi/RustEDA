# Critical-path table

The timing table `sta` of a revision is read through the shared read-only view `StaTable`
(`docs/specs/workspace/sta.md`, WS-REQ-0126 to WS-REQ-0136). The timing tool of the suite owns
the meaning of every value; the workbench lists them as the table records them.

**STUDIO-REQ-0050** The path table lists the ranked paths in the table's rank order (setup
family first, then by slack), at most `max_path_rows` of them (`U0022` names the total when the
budget cut the list). A row shows the handle `path<row + 1>` (WS-REQ-0133), the check (`setup`,
`hold`, `recovery`, `removal`), the corner name, the slack in ns, whether that slack is the exact
`f64` of a materialized path or the `f32` of the rank record (shown with a leading `~`), the
startpoint, the endpoint and the logic levels. The startpoint is the path's first point (the
convention of the timing tool's reports), and the logic levels are the cell arcs of the path:
its points reached through a cell.

**STUDIO-REQ-0051** The summary shows one entry per corner from the corner's first summary row
(WS-REQ-0131): setup WNS, TNS and violating endpoints, hold WNS and TNS, checked endpoints and
unconstrained endpoints. A WNS without endpoints is absent and shown as `n/a`, never as 0
(Rule 4.2). Times are shown with three decimals, `-0.000` as `0.000`.

**STUDIO-REQ-0052** The summary shows the analysis scope that the timing tool recorded in the
table (`SCOPE`, WS-REQ-0131), such as the corners, the wire model and the clock model, as
`key=value` pairs in key order (Rule 4.2).

**STUDIO-REQ-0053** The detail of a path lists its handle, check, corner and slack, its required
time and check value (WS-REQ-0134), whether it is materialized, the instances on it in path order
(each once), and its points from the startpoint to the endpoint: name, instance, the instance's
cell and row in the revision's netlist when the netlist has it, what reaches the point (port,
net, cell, clock launch), its transition, and for a materialized path its arrival and the
increment over the previous point.

**STUDIO-REQ-0054** The table can be filtered by corner, by check family (all, setup and
recovery, hold and removal) and to negative slack only; the filter keeps the rank order.
