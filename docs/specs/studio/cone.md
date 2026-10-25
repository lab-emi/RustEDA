# Logic cones, their layout and their schematic

A logic cone is the part of the netlist within a few levels before (fan-in) and after (fan-out)
a root. The workbench extracts it with bounded searches, lays it out in layers with a
deterministic method, and draws it as a schematic in which the selected path stands out.

## Extraction

**STUDIO-REQ-0080** A cone has a root: an instance, a connected pin, or a port bit. Its fan-in
follows the input nets of the root (for an instance, its input and inout pins; for an input pin,
that pin's net only; for an output pin, the input nets of its instance; for an output port bit,
its net) to the instances that drive them and the input port bits on them; its fan-out follows
the output nets of the root (an instance's output and inout pins; an output pin's own net; an
input pin's instance outputs; an input port bit's net) to the instances whose input or inout
pins they load and the output port bits on them. Continuous assignments join nets in their
direction (`lhs = rhs` makes `rhs` drive `lhs`). Constant connections are not followed. Each
node records its distance from the root: negative on the fan-in side, positive on the fan-out
side, 0 for the root, the first distance at which it was found. A root that names nothing in the
netlist gives no cone.

**STUDIO-REQ-0081** Extraction is bounded (Rule 3.4): fan-in and fan-out levels (each from 0 to
16, defaults 3 and 2), nodes, edges, loads followed per net in a fan-out step (the others are
counted on the node that drives them as hidden loads), and a work budget of pin, port-bit and
net visits. A visited set guards against the cycles of sequential feedback. The cone records
which budgets were reached (`depth` only when a node of the last level has a neighbor outside
the cone, `nodes`, `edges`, `fanout`, `visits`), and a cone cut short is reported as `U0050`
with its reasons.

**STUDIO-REQ-0082** A request may pin instances (the instances of the focused path): they are
nodes before the searches start, as far as the node budget allows, so that the whole path is in
the cone even when it is longer than the fan-in levels. A pinned node that the searches did not
reach has no distance.

**STUDIO-REQ-0083** Extraction is deterministic: searches visit nets and pins in row order and
nodes keep their order of discovery, the root first. A node is identified by a stable key, a
port bit (port row and bit offset) or an instance row, port bits ordered before instances; the
same request on the same netlist gives the same cone.

**STUDIO-REQ-0084** The edges of a cone are the connections among its nodes: from each node's
output nets (through assignments) to the loads that are nodes too, in node and pin order, each
ordered pair of nodes once with the net at the driver, without self loops (a node that loads
its own output is marked instead).

## Layout

The layout follows the layered method of Sugiyama, Tagawa and Toda [sugiyama1981methods], with
the barycenter ordering heuristic; minimizing crossings is NP-hard even for two layers
[eades1994edge], so the ordering is a heuristic, as in [gansner1993technique].

**STUDIO-REQ-0090** Cycle breaking: nodes are grouped by their distance from the root, pinned
nodes without a distance first; an edge to an earlier group (fan-out back into fan-in, which only
sequential loops make) is a feedback edge, and within a group a depth-first search in the order
of the stable keys marks the edges that close a cycle as feedback edges. The other edges form a
DAG in which fan-in precedes the root and the root precedes fan-out.

**STUDIO-REQ-0091** Layering: the layer of a node is its topological depth, the length of the
longest DAG path from a source to it, computed in O(V + E) by Kahn's algorithm in the order of
the stable keys. Signals flow from left to right.

**STUDIO-REQ-0092** Ordering: an edge that spans k > 1 layers gets k - 1 virtual nodes, at most
`layout_max_virtual` in all (a long edge beyond the budget is drawn straight and the layout
reports `U0052`). Each layer starts in the order of the stable keys (virtual nodes after real
ones, by edge and step); a sweep reorders the layers top to bottom by the barycenter of each
node's neighbors in the layer above, then bottom to top by the neighbors below, a node without
such neighbors keeping its position; barycenters are compared exactly as fractions and ties keep
the current order, then the stable keys. After each of at most `layout_sweeps` sweeps the
crossings between adjacent layers are counted exactly (inversions of the edge order, O(E log V)
per layer pair), and the order with the fewest crossings is kept, the initial order included;
the sweeps stop at zero crossings.

**STUDIO-REQ-0093** Coordinates are integers in layout units (one pixel at zoom 1): a column per
layer (`node_w + layer_gap` apart), a row per position (`node_h + row_gap` apart), each layer
centered vertically on the tallest one. An edge is a polyline from the right side of its driver
through the centers of its virtual nodes to the left side of its load, both at mid-height; a
feedback edge is a straight line from driver to load, drawn against the flow. A point hits the
node whose box contains it.

**STUDIO-REQ-0094** The same cone gives the same layout, whatever the order in which its nodes
were found: every choice depends on the stable keys and on exact comparisons only.

## Schematic

**STUDIO-REQ-0095** The scene of a cone is its layout with labels and flags: per node its box,
its title (instance name or port bit), its subtitle (the cell, or `input port`, `output port`,
`inout port`), whether it is the root, a port, selected or on the focused path, its distance and
its hidden loads; per edge its polyline, its net and whether it is a feedback edge or a
connection of the path. The scene also carries the counts of nodes, edges, layers and crossings
and the reached budgets.

**STUDIO-REQ-0096** When a path is focused, its cone is the cone of its endpoint pin (or
endpoint port bit), with at least one fan-in level and the path's instances pinned; the instances on the
path (the instances of its points, and the port bits named by its port points) are highlighted,
and so are its connections: the edges between consecutive points on different nodes.

**STUDIO-REQ-0097** A root that is not in the netlist is the diagnostic `U0051` and no scene.
