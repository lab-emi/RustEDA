# Netlist hierarchy explorer

The netlist table holds one flat module; the design hierarchy survives only in the names of its
instances. The explorer rebuilds a tree of scopes from these names, counts what each level
holds, and searches the names.

## Levels of a name

The core naming rules (CORE-REQ-0111, CORE-REQ-0112) make a hierarchical name of segments
separated by unescaped `/`, where `\/` and `\\` escape a slash and a backslash inside a segment.
Netlists that come from flattening often carry their hierarchy inside one segment instead: the
synthesis tool of the suite joins the RTL instance path with `.` in the names it keeps (its state
nets, such as `u_fifo.mem[2][7]`), and netlists from other flows write it as escaped identifiers
with `/`, which the structural reader stores as `\/` (VERILOG-REQ-0318). Instance names that the
synthesis tool generates carry no hierarchy at all, so its netlists show a flat tree.

**STUDIO-REQ-0030** A name is split into levels in one of two modes. In mode `core`, levels are
the segments of the core rules: split at unescaped `/`. In mode `flattened`, the default, a name
is also split at `.` and at the escaped slash `\/`, both only outside brackets, so that
`u_core.gen_lane[2].u_add/g7` has the levels `u_core`, `gen_lane[2]`, `u_add` and `g7`, and
`mem[1.5]` stays one level. A separator at the start or the end of the name, or right after
another separator, is kept as text, so that no level is empty. A level is shown with its escapes
undone (CORE-REQ-0112). The last level is the leaf; the levels before it are scopes.

## The tree

**STUDIO-REQ-0031** The hierarchy is a tree of scopes rooted at the module (scope 0, named after
the module). Each instance belongs to the scope of its levels before the leaf, created on first
use; each scope lists its child scopes and its instances in natural order (digit runs compared
as numbers, `u2` before `u10`, then by bytes and by row) and the number of instances in it and
below it. The tree is built in time linear in the total length of the names.

**STUDIO-REQ-0032** Counts are shown per scope (its instances in total and its child scopes) and
per level: for each depth below the module, the scopes at that depth and the instances directly
in scopes one level above.

**STUDIO-REQ-0033** The tree panel lists rows for the scopes the user opened: at depth 0 the
module's child scopes, then its instances; an open scope is followed by its child scopes and
then its instances, one level deeper. Rows are computed for open scopes only, and the panel
draws only its visible rows (STUDIO-REQ-0165), so a scope with a million instances is listed
without building a million row widgets. Focusing an instance opens the scopes that enclose it.

**STUDIO-REQ-0034** A search lists the instances and the scopes whose names contain the query,
ignoring the case of ASCII letters, at most `max_search_results` of each, with the number of all
matches and whether some were left out (`U0061` when the budget cut the list); an empty or blank
query matches nothing.

**STUDIO-REQ-0035** A name with more levels than `max_hierarchy_depth` keeps its first
`max_hierarchy_depth - 1` levels as scopes and its remaining levels together as its leaf; the
number of such names is reported once as `U0060`.
