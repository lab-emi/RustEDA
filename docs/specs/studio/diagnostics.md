# Diagnostics

The workbench names what it could not show and why (Rule 4.2): an absent table is a note, a
budget that cut a result short is a warning, a file that cannot be read or written is an error.
It never shows a value it did not read as zero and never shows an unanalyzed revision as passing.

**STUDIO-REQ-0200** A diagnostic of the workbench has a code `U` and four digits in `U0001` to
`U0199`, a severity and a message naming the objects involved. The codes are:

| Code | Severity | Meaning |
|---|---|---|
| U0001 | error | the directory is not a workspace, or its manifest cannot be read |
| U0002 | error | the requested revision does not exist |
| U0003 | error | the revision's `meta.json` cannot be read |
| U0010 | note | the revision has no netlist table |
| U0011 | error | the netlist table cannot be read or is damaged |
| U0020 | note | the revision has no timing table: timing `not_analyzed` |
| U0021 | error | the timing table cannot be read or is damaged |
| U0022 | warning | the path table lists only the first `max_path_rows` ranked paths |
| U0030 | note | the revision has no RTL snapshot |
| U0031 | warning | an RTL file is over its budget, unreadable or not UTF-8, and not indexed |
| U0040 | error | `ui_state.json` cannot be read |
| U0041 | warning | the shared selection belongs to another revision than the displayed one |
| U0042 | error | `ui_state.json` could not be written |
| U0043 | warning | a handle of a selection names nothing in the displayed revision |
| U0050 | warning | a logic cone was cut short by its budgets |
| U0051 | warning | the root of a logic cone is not in the netlist |
| U0052 | warning | the layout of a cone drew long edges straight (virtual-node budget) |
| U0060 | warning | instance names deeper than the depth budget were folded |
| U0061 | warning | a search lists only the first matches |
| U0070 | note | the RTL lines of a cross-probe are matched by name and approximate |
| U0071 | note | no RTL line matches a cross-probe |
| U0080 | warning | the manifest disappeared or became unreadable while watched |
| U0081 | error | a revision could not be loaded; the displayed revision stays |
| U0082 | note | the displayed revision changed |
| U0090 | error | the window cannot open (no display, or a build without the `gui` feature) |
| U0091 | error | a command-line argument is invalid |

**STUDIO-REQ-0201** The severity of a diagnostic is the one of its code in the table above. A
diagnostic is printed as `<severity>[U<nnnn>]: <message>`, the line format of the suite's
command lines.

**STUDIO-REQ-0202** Codes are unique, sorted by number in their declaration order, and never
reused for another meaning.

**STUDIO-REQ-0203** The diagnostics of a session are those of the displayed revision, of the
cone, of the cross-probe and of the search, followed by the session's own (watcher, selection,
reloads), newest last; the session keeps its newest 200 (`MAX_NOTES`). The status bar shows
their numbers by severity and the latest warning or error; `--print` lists them all.
